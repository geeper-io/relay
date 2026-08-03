"""OpenAI-compatible /v1/chat/completions endpoint."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.analytics.langfuse import build_trace_metadata
from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, rag_filter_for_identity, require_scope
from app.core.content_policy import ContentPolicy, get_content_policy
from app.core.exceptions import ProxyError
from app.core.rate_limiter import RateLimiter, get_rate_limiter
from app.core.routing import RoutingDecision
from app.db.repositories.usage import record_usage
from app.llm.client import LLMClient, get_llm_client
from app.metrics import prometheus as m
from app.pii.restorer import PIIRestorer, get_restorer
from app.pii.scrubber import PIIScrubber, get_scrubber
from app.rag.context import guard_rag_context
from app.rag.retriever import RAGRetriever, get_retriever
from app.schemas.openai import ChatCompletionRequest
from app.telemetry import annotate_current_span

router = APIRouter(tags=["chat"])

def _chat_capabilities(request: ChatCompletionRequest) -> set[str]:
    capabilities = {"chat"}
    if request.stream:
        capabilities.add("streaming")
    if request.tools:
        capabilities.add("tools")
    if request.response_format and request.response_format.type != "text":
        capabilities.add("structured_outputs")
    if any(
        isinstance(message.content, list) and any(part.type == "image_url" for part in message.content)
        for message in request.messages
    ):
        capabilities.add("vision")
    return capabilities


def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
    return ""


def _inject_rag_context(messages: list[dict], context: str) -> list[dict]:
    """Append clearly delimited, untrusted RAG data after application instructions."""
    if not context:
        return messages
    guarded = guard_rag_context(context)
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        new_system = existing + "\n\n" + guarded if existing else guarded
        return [{**messages[0], "content": new_system}] + messages[1:]
    return [{"role": "system", "content": guarded}] + messages


def _messages_to_dicts(request: ChatCompletionRequest) -> list[dict]:
    # Preserve typed content parts (especially images) for the upstream provider.
    return [message.model_dump(exclude_none=True) for message in request.messages]


@router.post("/chat/completions")
async def chat_completions(
    request_body: ChatCompletionRequest,
    raw_request: Request,
    raw_response: Response,
    identity: ResolvedIdentity = Depends(require_scope("chat")),
    settings: Settings = Depends(get_settings),
    scrubber: PIIScrubber = Depends(get_scrubber),
    restorer: PIIRestorer = Depends(get_restorer),
    retriever: RAGRetriever = Depends(get_retriever),
    llm_client: LLMClient = Depends(get_llm_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    policy: ContentPolicy = Depends(get_content_policy),
):
    request_id = raw_request.headers.get("x-request-id", str(uuid.uuid4()))
    start_time = time.monotonic()
    model = request_body.model or settings.default_model
    decision: RoutingDecision | None = None

    m.ACTIVE_REQUESTS.inc()
    try:
        decision = llm_client.route(
            model,
            required_capabilities=_chat_capabilities(request_body),
            team_id=identity.team_id,
        )
        model = decision.model
        annotate_current_span(
            **{
                "relay.requested_model": decision.requested_model,
                "relay.model": decision.model,
                "relay.deployment": decision.deployment or "direct",
                "relay.policy.version": decision.policy_version,
                "relay.endpoint": "chat",
                "relay.user_id": identity.user_id,
                "relay.team_id": identity.team_id,
            }
        )
        m.ROUTING_DECISIONS.labels(
            deployment=decision.deployment or "direct",
            policy_version=decision.policy_version,
            endpoint="chat",
        ).inc()
        # 1. Content policy check
        policy.check(request_body.messages)

        # 2. Convert the complete request without dropping multimodal parts.
        messages_for_counting = _messages_to_dicts(request_body)

        # 3. Messages already converted above for token counting
        messages = messages_for_counting

        # 4. PII scrubbing
        scrubbed_messages, restoration_map, pii_count = scrubber.scrub_messages(messages)
        # 5. RAG context retrieval
        rag_used = False
        rag_chunks = 0
        if settings.rag_enabled:
            query_text = _last_user_message(scrubbed_messages)
            rag_repo = raw_request.headers.get("x-relay-repo")
            rag_filters = rag_filter_for_identity(identity, rag_repo, require_acl=settings.rag_require_acl)
            context, rag_chunks = await retriever.retrieve_context(query_text, filters=rag_filters)
            if context and policy.contains_blocked_pattern(context):
                rag_chunks = 0
                m.RAG_RETRIEVALS.labels(status="blocked").inc()
            elif context:
                context, context_pii_count = scrubber.scrub_untrusted_text(context)
                pii_count += context_pii_count
                scrubbed_messages = _inject_rag_context(scrubbed_messages, context)
                rag_used = True
                m.RAG_RETRIEVALS.labels(status="hit").inc()
            else:
                m.RAG_RETRIEVALS.labels(status="miss").inc()
        m.RAG_CHUNKS_RETRIEVED.observe(rag_chunks)
        if pii_count > 0:
            m.PII_ENTITIES_SCRUBBED.inc(pii_count)
            m.PII_REQUESTS_AFFECTED.inc()

        # Count the actual enriched prompt so RAG tokens are reserved and limited.
        estimated_tokens = llm_client.count_tokens(model, scrubbed_messages)
        policy.check_token_count(estimated_tokens)

        # 6. Rate limiting applies to the complete provider-bound prompt.
        await rate_limiter.check_and_consume(
            identity.user_id,
            identity.team_id,
            estimated_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
            daily_token_limit=identity.daily_token_limit,
            team_tpm_limit=identity.team_tpm_limit,
            team_daily_token_limit=identity.team_daily_token_limit,
        )

        # 7. LLM call
        llm_kwargs = {}
        if request_body.tools:
            llm_kwargs["tools"] = [t.model_dump() for t in request_body.tools]
        if request_body.tool_choice:
            llm_kwargs["tool_choice"] = request_body.tool_choice
        if identity.passthrough_key:
            llm_kwargs["api_key"] = identity.passthrough_key
        llm_kwargs["fallback_models"] = decision.fallback_models

        trace_metadata = build_trace_metadata(
            user_id=identity.user_id,
            team_id=identity.team_id,
            request_id=request_id,
            model=model,
            rag_used=rag_used,
            stream=request_body.stream,
            extra={
                "endpoint": "chat",
                "deployment": decision.deployment,
                "policy_version": decision.policy_version,
            },
        )

        if request_body.stream:
            return StreamingResponse(
                _stream_response(
                    llm_client=llm_client,
                    model=model,
                    messages=scrubbed_messages,
                    request_body=request_body,
                    restoration_map=restoration_map,
                    restorer=restorer,
                    identity=identity,
                    request_id=request_id,
                    start_time=start_time,
                    rag_used=rag_used,
                    pii_count=pii_count,
                    rate_limiter=rate_limiter,
                    estimated_tokens=estimated_tokens,
                    trace_metadata=trace_metadata,
                    decision=decision,
                    **llm_kwargs,
                ),
                media_type="text/event-stream",
                headers={"X-Request-ID": request_id},
            )

        response = await llm_client.complete(
            model=model,
            messages=scrubbed_messages,
            max_tokens=request_body.max_tokens,
            temperature=request_body.temperature,
            trace_metadata=trace_metadata,
            **llm_kwargs,
        )

        # 8. PII restoration in response
        if response.choices:
            for choice in response.choices:
                if choice.message and choice.message.content:
                    choice.message.content = restorer.restore(choice.message.content, restoration_map)

        # 9. Record metrics + usage
        latency_ms = int((time.monotonic() - start_time) * 1000)
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        await rate_limiter.reconcile_tokens(
            identity.user_id,
            identity.team_id,
            reserved_tokens=estimated_tokens,
            actual_tokens=prompt_tokens + completion_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
            daily_token_limit=identity.daily_token_limit,
            team_tpm_limit=identity.team_tpm_limit,
            team_daily_token_limit=identity.team_daily_token_limit,
        )
        cost_usd = llm_client.estimate_cost(model, prompt_tokens, completion_tokens)
        cache_hit = bool(getattr(getattr(response, "_hidden_params", None), "cache_hit", False))

        m.REQUEST_COUNT.labels(model=model, status="success").inc()
        m.REQUEST_LATENCY.labels(model=model, stream="false").observe(time.monotonic() - start_time)
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)
        m.TOKENS_USED.labels(model=model, token_type="completion").inc(completion_tokens)
        m.COST_USD.labels(model=model).inc(cost_usd)
        if cache_hit:
            m.CACHE_HITS.labels(model=model).inc()

        if not identity.passthrough_key:
            await record_usage(
                user_id=identity.user_id,
                team_id=identity.team_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                request_id=request_id,
                cost_usd=cost_usd,
                cache_hit=cache_hit,
                was_rag_used=rag_used,
                pii_entities_scrubbed=pii_count,
                status="success",
                audit_metadata={"endpoint": "chat", **decision.audit_metadata()},
            )

        raw_response.headers["X-Request-ID"] = request_id
        raw_response.headers["X-Relay-Deployment"] = decision.deployment or "direct"
        raw_response.headers["X-Relay-Policy-Version"] = decision.policy_version
        if cache_hit:
            raw_response.headers["X-Cache-Hit"] = "true"
        return response

    except ProxyError as exc:
        await _record_error(exc, model, identity, request_id, start_time, pii_count=0, decision=decision)
        from app.core.exceptions import RateLimitError

        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitError) else {}
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"type": exc.error_code, "message": exc.message}},
            headers=headers,
        )
    finally:
        m.ACTIVE_REQUESTS.dec()


async def _stream_response(
    *,
    llm_client: LLMClient,
    model: str,
    messages: list[dict],
    request_body: ChatCompletionRequest,
    restoration_map: dict[str, str],
    restorer: PIIRestorer,
    identity: ResolvedIdentity,
    request_id: str,
    start_time: float,
    rag_used: bool,
    pii_count: int,
    rate_limiter: RateLimiter,
    estimated_tokens: int,
    decision: RoutingDecision,
    trace_metadata: dict | None = None,
    **kwargs,
) -> AsyncGenerator[str, None]:
    prompt_tokens = 0
    completion_tokens = 0
    buffer = ""  # partial-placeholder buffer for text content
    tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments}

    def _sse(chunk_id, created, delta, finish_reason=None):
        return (
            "data: "
            + json.dumps(
                {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
            )
            + "\n\n"
        )

    try:
        async for chunk in llm_client.stream(
            model=model,
            messages=messages,
            max_tokens=request_body.max_tokens,
            temperature=request_body.temperature,
            trace_metadata=trace_metadata,
            **kwargs,
        ):
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            delta_content = getattr(delta, "content", None) or ""
            delta_tool_calls = getattr(delta, "tool_calls", None) or []
            finish_reason = chunk.choices[0].finish_reason

            # ── Text content ──────────────────────────────────────────────────
            if delta_content:
                buffer += delta_content
                if not ("<<PII_" in buffer and ">>" not in buffer.split("<<PII_")[-1]):
                    flushed = restorer.restore(buffer, restoration_map)
                    buffer = ""
                    yield _sse(chunk.id, chunk.created, {"content": flushed})

            # ── Tool call deltas ──────────────────────────────────────────────
            for tc in delta_tool_calls:
                idx = tc.index
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                name = getattr(tc.function, "name", "") or ""
                args = getattr(tc.function, "arguments", "") or ""
                if name:
                    tool_calls[idx]["name"] = name
                tool_calls[idx]["arguments"] += args

                tc_delta: dict = {"tool_calls": [{"index": idx, "function": {}}]}
                if tc.id:
                    tc_delta["tool_calls"][0]["id"] = tc.id
                    tc_delta["tool_calls"][0]["type"] = "function"
                if name:
                    tc_delta["tool_calls"][0]["function"]["name"] = name
                if args:
                    tc_delta["tool_calls"][0]["function"]["arguments"] = args
                yield _sse(chunk.id, chunk.created, tc_delta)

            if finish_reason:
                if buffer:
                    flushed = restorer.restore(buffer, restoration_map)
                    buffer = ""
                    yield _sse(chunk.id, chunk.created, {"content": flushed}, finish_reason)
                else:
                    yield _sse(chunk.id, chunk.created, {}, finish_reason)

        yield "data: [DONE]\n\n"

    finally:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        cost_usd = llm_client.estimate_cost(model, prompt_tokens, completion_tokens)
        await rate_limiter.reconcile_tokens(
            identity.user_id,
            identity.team_id,
            reserved_tokens=estimated_tokens,
            actual_tokens=prompt_tokens + completion_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
            daily_token_limit=identity.daily_token_limit,
            team_tpm_limit=identity.team_tpm_limit,
            team_daily_token_limit=identity.team_daily_token_limit,
        )

        m.REQUEST_COUNT.labels(model=model, status="success").inc()
        m.REQUEST_LATENCY.labels(model=model, stream="true").observe(time.monotonic() - start_time)
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)
        m.TOKENS_USED.labels(model=model, token_type="completion").inc(completion_tokens)
        m.COST_USD.labels(model=model).inc(cost_usd)

        if not identity.passthrough_key:
            await record_usage(
                user_id=identity.user_id,
                team_id=identity.team_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                request_id=request_id,
                cost_usd=cost_usd,
                cache_hit=False,  # streaming responses are not cached
                was_rag_used=rag_used,
                pii_entities_scrubbed=pii_count,
                status="success",
                audit_metadata={"endpoint": "chat", **decision.audit_metadata()},
            )


async def _record_error(
    exc: ProxyError,
    model: str,
    identity: ResolvedIdentity | None,
    request_id: str,
    start_time: float,
    pii_count: int,
    decision: RoutingDecision | None = None,
) -> None:
    from app.core.exceptions import ContentPolicyError, RateLimitError

    status = exc.error_code
    if isinstance(exc, RateLimitError):
        m.RATE_LIMIT_HITS.labels(limit_type="general").inc()
    elif isinstance(exc, ContentPolicyError):
        m.POLICY_BLOCKS.inc()

    m.REQUEST_COUNT.labels(model=model, status=status).inc()

    if identity and not identity.passthrough_key:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        await record_usage(
            user_id=identity.user_id,
            team_id=identity.team_id,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            request_id=request_id,
            was_rag_used=False,
            pii_entities_scrubbed=pii_count,
            status="error",
            error_code=exc.error_code,
            audit_metadata={"endpoint": "chat", **(decision.audit_metadata() if decision else {})},
        )
