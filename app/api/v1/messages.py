"""Anthropic Messages API /v1/messages endpoint.

Drop-in target for Claude Code and Anthropic SDK clients:
    export ANTHROPIC_BASE_URL=http://your-proxy:8000
    export ANTHROPIC_AUTH_TOKEN=llmp-<your-key>
"""
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
from app.schemas.openai import ChatMessage as OAIMessage
from app.schemas.anthropic import (
    AnthropicRequest,
    AnthropicTextBlock,
    _finish_reason_to_stop_reason,
    anthropic_to_openai_messages,
    anthropic_tools_to_openai,
    anthropic_tool_choice_to_openai,
    openai_response_to_anthropic,
)
from app.api.v1.chat import _last_user_message, _inject_rag_context

router = APIRouter(tags=["messages"])


def _msg_text(msg) -> str:
    """Extract plain text from an AnthropicMessage for policy check."""
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.text for b in content if isinstance(b, AnthropicTextBlock))
    return ""


@router.post("/messages")
async def messages(
    request_body: AnthropicRequest,
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
        policy_msgs = [OAIMessage(role="user", content=_msg_text(msg)) for msg in request_body.messages]
        if request_body.system:
            policy_msgs.insert(0, OAIMessage(role="system", content=request_body.system))
        policy.check(policy_msgs)

        # 2. Convert to OpenAI-format dicts for the pipeline
        oai_messages = anthropic_to_openai_messages(request_body)

        # 3. Token count for rate limiting
        estimated_tokens = llm_client.count_tokens(model, oai_messages)

        # 4. Rate limiting
        await rate_limiter.check_and_consume(
            identity.user_id,
            identity.team_id,
            estimated_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
        )

        # 5. PII scrubbing
        scrubbed_messages, restoration_map, pii_count = scrubber.scrub_messages(oai_messages)
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
        llm_kwargs: dict = {}
        if request_body.tools:
            llm_kwargs["tools"] = anthropic_tools_to_openai(request_body.tools)
        if request_body.tool_choice:
            llm_kwargs["tool_choice"] = anthropic_tool_choice_to_openai(request_body.tool_choice)
        if request_body.stop_sequences:
            llm_kwargs["stop"] = request_body.stop_sequences

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
                _stream_anthropic(
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

        # 8. PII restoration
        if response.choices:
            for choice in response.choices:
                if choice.message and choice.message.content:
                    choice.message.content = restorer.restore(
                        choice.message.content, restoration_map
                    )

        # 9. Metrics + usage
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
        return openai_response_to_anthropic(response, model)

    except ProxyError as exc:
        _record_error(exc, model, identity, request_id, start_time, pii_count=0)
        raise
    finally:
        m.ACTIVE_REQUESTS.dec()


async def _stream_anthropic(
    *,
    llm_client: LLMClient,
    model: str,
    messages: list[dict],
    request_body: AnthropicRequest,
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
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    prompt_tokens = 0
    completion_tokens = 0
    buffer = ""
    finish_reason = None

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _sse("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    yield _sse("ping", {"type": "ping"})

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
            chunk_finish = chunk.choices[0].finish_reason
            if chunk_finish:
                finish_reason = chunk_finish

            if delta_content:
                buffer += delta_content
                from app.pii.restorer import _PLACEHOLDER_RE  # noqa: F401
                if not ("<<PII_" in buffer and ">>" not in buffer.split("<<PII_")[-1]):
                    flushed = restorer.restore(buffer, restoration_map)
                    buffer = ""
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": flushed},
                    })

        # Flush any remaining buffered content
        if buffer:
            flushed = restorer.restore(buffer, restoration_map)
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": flushed},
            })

        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": _finish_reason_to_stop_reason(finish_reason),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": completion_tokens},
        })
        yield _sse("message_stop", {"type": "message_stop"})

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
                cache_hit=False,
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
