"""OpenAI-compatible /v1/chat/completions endpoint."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, resolve_identity
from app.core.content_policy import ContentPolicy, get_content_policy
from app.core.exceptions import ProxyError
from app.core.rate_limiter import RateLimiter, get_rate_limiter
from app.db.engine import get_db
from app.db.repositories.usage import record_usage
from app.llm.client import LLMClient, get_llm_client
from app.metrics import prometheus as m
from app.pii.restorer import PIIRestorer, get_restorer
from app.pii.scrubber import PIIScrubber, get_scrubber
from app.rag.retriever import RAGRetriever, get_retriever
from app.analytics.langfuse import build_trace_metadata
from app.schemas.openai import ChatCompletionRequest, ChatCompletionResponse

router = APIRouter(tags=["chat"])


def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
    return ""


def _inject_rag_context(messages: list[dict], context: str) -> list[dict]:
    """Prepend RAG context to the system message, or insert a new system message."""
    if not context:
        return messages
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        new_system = context + "\n\n" + existing if existing else context
        return [{**messages[0], "content": new_system}] + messages[1:]
    return [{"role": "system", "content": context}] + messages


def _messages_to_dicts(request: ChatCompletionRequest) -> list[dict]:
    result = []
    for msg in request.messages:
        d: dict = {"role": msg.role}
        if msg.content is not None:
            d["content"] = msg.text_content()
        if msg.tool_calls:
            d["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        result.append(d)
    return result


@router.post("/chat/completions")
async def chat_completions(
    request_body: ChatCompletionRequest,
    raw_request: Request,
    raw_response: Response,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
    scrubber: PIIScrubber = Depends(get_scrubber),
    restorer: PIIRestorer = Depends(get_restorer),
    retriever: RAGRetriever = Depends(get_retriever),
    llm_client: LLMClient = Depends(get_llm_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    policy: ContentPolicy = Depends(get_content_policy),
):
    request_id = raw_request.headers.get("x-request-id", str(uuid.uuid4()))
    start_time = time.monotonic()

    m.ACTIVE_REQUESTS.inc()
    try:
        model = llm_client.resolve_model(request_body.model or settings.default_model)
        # 1. Content policy check
        policy.check(request_body.messages)

        # 2. Accurate token count for rate limiting
        messages_for_counting = _messages_to_dicts(request_body)
        estimated_tokens = llm_client.count_tokens(model, messages_for_counting)

        # 3. Rate limiting
        await rate_limiter.check_and_consume(
            identity.user_id,
            identity.team_id,
            estimated_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
        )

        # 4. Messages already converted above for token counting
        messages = messages_for_counting

        # 5. PII scrubbing
        scrubbed_messages, restoration_map, pii_count = scrubber.scrub_messages(messages)
        if pii_count > 0:
            m.PII_ENTITIES_SCRUBBED.inc(pii_count)
            m.PII_REQUESTS_AFFECTED.inc()

        # 6. RAG context retrieval
        rag_used = False
        rag_chunks = 0
        if settings.rag_enabled:
            query_text = _last_user_message(scrubbed_messages)
            context, rag_chunks = await retriever.retrieve_context(query_text)
            if context:
                scrubbed_messages = _inject_rag_context(scrubbed_messages, context)
                rag_used = True
                m.RAG_RETRIEVALS.labels(status="hit").inc()
            else:
                m.RAG_RETRIEVALS.labels(status="miss").inc()
        m.RAG_CHUNKS_RETRIEVED.observe(rag_chunks)

        # 7. LLM call
        llm_kwargs = {}
        if request_body.tools:
            llm_kwargs["tools"] = [t.model_dump() for t in request_body.tools]
        if request_body.tool_choice:
            llm_kwargs["tool_choice"] = request_body.tool_choice

        trace_metadata = build_trace_metadata(
            user_id=identity.user_id,
            team_id=identity.team_id,
            request_id=request_id,
            model=model,
            rag_used=rag_used,
            stream=request_body.stream,
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
                    db=db,
                    request_id=request_id,
                    start_time=start_time,
                    rag_used=rag_used,
                    pii_count=pii_count,
                    trace_metadata=trace_metadata,
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
                    choice.message.content = restorer.restore(
                        choice.message.content, restoration_map
                    )

        # 9. Record metrics + usage
        latency_ms = int((time.monotonic() - start_time) * 1000)
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost_usd = llm_client.estimate_cost(model, prompt_tokens, completion_tokens)
        cache_hit = bool(
            getattr(getattr(response, "_hidden_params", None), "cache_hit", False)
        )

        m.REQUEST_COUNT.labels(model=model, status="success").inc()
        m.REQUEST_LATENCY.labels(model=model, stream="false").observe(
            time.monotonic() - start_time
        )
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)
        m.TOKENS_USED.labels(model=model, token_type="completion").inc(completion_tokens)
        m.COST_USD.labels(model=model).inc(cost_usd)
        if cache_hit:
            m.CACHE_HITS.labels(model=model).inc()

        asyncio.create_task(
            record_usage(
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
            )
        )

        raw_response.headers["X-Request-ID"] = request_id
        if cache_hit:
            raw_response.headers["X-Cache-Hit"] = "true"
        return response

    except ProxyError as exc:
        _record_error(exc, model, identity, request_id, start_time, pii_count=0)
        raise
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
    db: AsyncSession,
    request_id: str,
    start_time: float,
    rag_used: bool,
    pii_count: int,
    trace_metadata: dict | None = None,
    **kwargs,
) -> AsyncGenerator[str, None]:
    prompt_tokens = 0
    completion_tokens = 0
    buffer = ""  # partial-placeholder buffer

    try:
        async for chunk in llm_client.stream(
            model=model,
            messages=messages,
            max_tokens=request_body.max_tokens,
            temperature=request_body.temperature,
            trace_metadata=trace_metadata,
            **kwargs,
        ):
            # Extract usage from final chunk if present
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            delta_content = getattr(delta, "content", None) or ""
            finish_reason = chunk.choices[0].finish_reason

            if delta_content:
                buffer += delta_content
                # Only flush when we're confident there's no partial placeholder
                from app.pii.restorer import _PLACEHOLDER_RE
                if not ("<<PII_" in buffer and ">>" not in buffer.split("<<PII_")[-1]):
                    flushed = restorer.restore(buffer, restoration_map)
                    buffer = ""
                    sse_chunk = {
                        "id": chunk.id,
                        "object": "chat.completion.chunk",
                        "created": chunk.created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": flushed},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(sse_chunk)}\n\n"

            if finish_reason:
                # Flush remaining buffer
                if buffer:
                    flushed = restorer.restore(buffer, restoration_map)
                    buffer = ""
                    sse_chunk = {
                        "id": chunk.id,
                        "object": "chat.completion.chunk",
                        "created": chunk.created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": flushed},
                            "finish_reason": finish_reason,
                        }],
                    }
                    yield f"data: {json.dumps(sse_chunk)}\n\n"
                else:
                    sse_chunk = {
                        "id": chunk.id,
                        "object": "chat.completion.chunk",
                        "created": chunk.created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    }
                    yield f"data: {json.dumps(sse_chunk)}\n\n"

        yield "data: [DONE]\n\n"

    finally:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        cost_usd = llm_client.estimate_cost(model, prompt_tokens, completion_tokens)

        m.REQUEST_COUNT.labels(model=model, status="success").inc()
        m.REQUEST_LATENCY.labels(model=model, stream="true").observe(
            time.monotonic() - start_time
        )
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)
        m.TOKENS_USED.labels(model=model, token_type="completion").inc(completion_tokens)
        m.COST_USD.labels(model=model).inc(cost_usd)

        asyncio.create_task(
            record_usage(
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
            )
        )


def _record_error(
    exc: ProxyError,
    model: str,
    identity: ResolvedIdentity | None,
    request_id: str,
    start_time: float,
    pii_count: int,
) -> None:
    from app.core.exceptions import ContentPolicyError, RateLimitError

    status = exc.error_code
    if isinstance(exc, RateLimitError):
        m.RATE_LIMIT_HITS.labels(limit_type="general").inc()
    elif isinstance(exc, ContentPolicyError):
        m.POLICY_BLOCKS.inc()

    m.REQUEST_COUNT.labels(model=model, status=status).inc()

    if identity:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        asyncio.create_task(
            record_usage(
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
            )
        )
