"""OpenAI-compatible /v1/responses endpoint."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.analytics.langfuse import build_trace_metadata
from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, rag_filter_for_identity, require_scope
from app.core.content_policy import ContentPolicy, get_content_policy
from app.core.exceptions import ProxyError, UpstreamError
from app.core.rate_limiter import RateLimiter, get_rate_limiter
from app.core.routing import RoutingDecision
from app.db.repositories.usage import record_usage
from app.llm.client import LLMClient, get_llm_client
from app.metrics import prometheus as m
from app.pii.restorer import PIIRestorer, get_restorer
from app.pii.scrubber import PIIScrubber, get_scrubber
from app.rag.retriever import RAGRetriever, get_retriever
from app.schemas.responses import (
    ResponsesRequest,
    inject_response_context,
    last_user_text,
    response_capabilities,
    response_policy_messages,
    scrub_response_payload,
)
from app.telemetry import annotate_current_span

router = APIRouter(tags=["responses"])


@router.post("/responses")
async def responses(
    request_body: ResponsesRequest,
    raw_request: Request,
    raw_response: Response,
    identity: ResolvedIdentity = Depends(require_scope("responses")),
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
    requested_model = request_body.model or settings.default_model
    decision = llm_client.route(
        requested_model,
        required_capabilities=response_capabilities(
            request_body,
            default_store=settings.responses_default_store,
        ),
        team_id=identity.team_id,
    )
    model = decision.model
    annotate_current_span(
        **{
            "relay.requested_model": decision.requested_model,
            "relay.model": decision.model,
            "relay.deployment": decision.deployment or "direct",
            "relay.policy.version": decision.policy_version,
            "relay.endpoint": "responses",
            "relay.user_id": identity.user_id,
            "relay.team_id": identity.team_id,
        }
    )
    m.ROUTING_DECISIONS.labels(
        deployment=decision.deployment or "direct",
        policy_version=decision.policy_version,
        endpoint="responses",
    ).inc()

    m.ACTIVE_REQUESTS.inc()
    try:
        policy_messages = response_policy_messages(request_body)
        policy.check(policy_messages)
        count_messages = [message.model_dump(exclude_none=True) for message in policy_messages]
        estimated_tokens = llm_client.count_tokens(model, count_messages)
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

        scrubbed_input, scrubbed_instructions, restoration_map, pii_count = scrub_response_payload(
            request_body,
            scrubber,
        )
        if pii_count:
            m.PII_ENTITIES_SCRUBBED.inc(pii_count)
            m.PII_REQUESTS_AFFECTED.inc()

        rag_used = False
        rag_chunks = 0
        if settings.rag_enabled:
            rag_repo = raw_request.headers.get("x-relay-repo")
            rag_filters = rag_filter_for_identity(identity, rag_repo, require_acl=settings.rag_require_acl)
            context, rag_chunks = await retriever.retrieve_context(last_user_text(request_body), filters=rag_filters)
            if context:
                scrubbed_input = inject_response_context(scrubbed_input, context)
                rag_used = True
                m.RAG_RETRIEVALS.labels(status="hit").inc()
            else:
                m.RAG_RETRIEVALS.labels(status="miss").inc()
        m.RAG_CHUNKS_RETRIEVED.observe(rag_chunks)

        trace_metadata = build_trace_metadata(
            user_id=identity.user_id,
            team_id=identity.team_id,
            request_id=request_id,
            model=model,
            rag_used=rag_used,
            stream=request_body.stream,
            extra={
                "endpoint": "responses",
                "deployment": decision.deployment,
                "policy_version": decision.policy_version,
                **(request_body.metadata or {}),
            },
        )
        call_kwargs = _responses_kwargs(request_body, settings, identity)
        if scrubbed_instructions is not None:
            call_kwargs["instructions"] = scrubbed_instructions

        headers = _routing_headers(request_id, decision)
        if request_body.stream:
            return StreamingResponse(
                _stream_responses(
                    llm_client=llm_client,
                    model=model,
                    input_=scrubbed_input,
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
                    decision=decision,
                    trace_metadata=trace_metadata,
                    **call_kwargs,
                ),
                media_type="text/event-stream",
                headers=headers,
            )

        result = await llm_client.respond(
            model=model,
            input_=scrubbed_input,
            max_output_tokens=request_body.max_output_tokens,
            trace_metadata=trace_metadata,
            fallback_models=decision.fallback_models,
            **call_kwargs,
        )
        payload = result.model_dump(exclude_none=True) if hasattr(result, "model_dump") else dict(result)
        _restore_value(payload.get("output", []), restorer, restoration_map)
        prompt_tokens, completion_tokens = _response_usage(payload.get("usage"))
        await _record_success(
            identity=identity,
            model=model,
            request_id=request_id,
            start_time=start_time,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_tokens=estimated_tokens,
            rate_limiter=rate_limiter,
            rag_used=rag_used,
            pii_count=pii_count,
            decision=decision,
            stream=False,
            llm_client=llm_client,
        )
        for key, value in headers.items():
            raw_response.headers[key] = value
        return payload
    except ProxyError as exc:
        await _record_failure(exc, identity, model, request_id, start_time, decision)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"type": exc.error_code, "message": exc.message}},
        )
    finally:
        m.ACTIVE_REQUESTS.dec()


def _responses_kwargs(
    request: ResponsesRequest,
    settings: Settings,
    identity: ResolvedIdentity,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "store": request.store if request.store is not None else settings.responses_default_store,
    }
    for name in (
        "instructions",
        "previous_response_id",
        "temperature",
        "top_p",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "background",
        "include",
        "max_tool_calls",
        "prompt",
        "reasoning",
        "safety_identifier",
        "service_tier",
        "text",
        "truncation",
        "user",
    ):
        value = getattr(request, name)
        if value is not None:
            kwargs[name] = value
    if identity.passthrough_key:
        kwargs["api_key"] = identity.passthrough_key
    return kwargs


async def _stream_responses(
    *,
    llm_client: LLMClient,
    model: str,
    input_: str | list[dict],
    request_body: ResponsesRequest,
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
    trace_metadata: dict,
    **kwargs: Any,
) -> AsyncGenerator[str, None]:
    prompt_tokens = completion_tokens = 0
    buffers: dict[tuple[str, str], str] = {}
    succeeded = False
    try:
        async for event in llm_client.response_stream(
            model=model,
            input_=input_,
            max_output_tokens=request_body.max_output_tokens,
            trace_metadata=trace_metadata,
            fallback_models=decision.fallback_models,
            **kwargs,
        ):
            payload = event.model_dump(exclude_none=True) if hasattr(event, "model_dump") else dict(event)
            event_type = str(payload.get("type", "message"))
            if event_type in {"response.output_text.delta", "response.function_call_arguments.delta"}:
                key = (event_type, str(payload.get("item_id", "")))
                buffers[key] = buffers.get(key, "") + str(payload.get("delta", ""))
                if _has_partial_placeholder(buffers[key]):
                    continue
                payload["delta"] = restorer.restore(buffers.pop(key), restoration_map)
            else:
                _restore_value(payload, restorer, restoration_map)

            if event_type in {"response.completed", "response.incomplete"}:
                response_payload = payload.get("response", {})
                prompt_tokens, completion_tokens = _response_usage(response_payload.get("usage"))
                succeeded = True
            elif event_type == "response.failed":
                await _record_failure(
                    UpstreamError("Responses API stream failed"),
                    identity,
                    model,
                    request_id,
                    start_time,
                    decision,
                )
            yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
    except ProxyError as exc:
        await _record_failure(exc, identity, model, request_id, start_time, decision)
        payload = {"type": "error", "error": {"type": exc.error_code, "message": exc.message}}
        yield f"event: error\ndata: {json.dumps(payload)}\n\n"
    finally:
        if succeeded:
            await _record_success(
                identity=identity,
                model=model,
                request_id=request_id,
                start_time=start_time,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_tokens=estimated_tokens,
                rate_limiter=rate_limiter,
                rag_used=rag_used,
                pii_count=pii_count,
                decision=decision,
                stream=True,
                llm_client=llm_client,
            )


async def _record_success(
    *,
    identity: ResolvedIdentity,
    model: str,
    request_id: str,
    start_time: float,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_tokens: int,
    rate_limiter: RateLimiter,
    rag_used: bool,
    pii_count: int,
    decision: RoutingDecision,
    stream: bool,
    llm_client: LLMClient,
) -> None:
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
    m.REQUEST_LATENCY.labels(model=model, stream=str(stream).lower()).observe(time.monotonic() - start_time)
    m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)
    m.TOKENS_USED.labels(model=model, token_type="completion").inc(completion_tokens)
    cost_usd = llm_client.estimate_cost(model, prompt_tokens, completion_tokens)
    m.COST_USD.labels(model=model).inc(cost_usd)
    if not identity.passthrough_key:
        await record_usage(
            user_id=identity.user_id,
            team_id=identity.team_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.monotonic() - start_time) * 1000),
            request_id=request_id,
            cost_usd=cost_usd,
            was_rag_used=rag_used,
            pii_entities_scrubbed=pii_count,
            status="success",
            audit_metadata={"endpoint": "responses", **decision.audit_metadata()},
        )


async def _record_failure(
    exc: ProxyError,
    identity: ResolvedIdentity,
    model: str,
    request_id: str,
    start_time: float,
    decision: RoutingDecision,
) -> None:
    m.REQUEST_COUNT.labels(model=model, status=exc.error_code).inc()
    if not identity.passthrough_key:
        await record_usage(
            user_id=identity.user_id,
            team_id=identity.team_id,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=int((time.monotonic() - start_time) * 1000),
            request_id=request_id,
            status="error",
            error_code=exc.error_code,
            audit_metadata={"endpoint": "responses", **decision.audit_metadata()},
        )


def _response_usage(usage: dict | None) -> tuple[int, int]:
    usage = usage or {}
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _restore_value(value: Any, restorer: PIIRestorer, restoration_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = _restore_value(item, restorer, restoration_map)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _restore_value(item, restorer, restoration_map)
    elif isinstance(value, str):
        return restorer.restore(value, restoration_map)
    return value


def _has_partial_placeholder(value: str) -> bool:
    return "<<PII_" in value and ">>" not in value.rsplit("<<PII_", 1)[-1]


def _routing_headers(request_id: str, decision: RoutingDecision) -> dict[str, str]:
    return {
        "X-Request-ID": request_id,
        "X-Relay-Deployment": decision.deployment or "direct",
        "X-Relay-Policy-Version": decision.policy_version,
    }
