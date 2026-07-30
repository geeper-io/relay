"""OpenAI-compatible /v1/embeddings endpoint."""

from __future__ import annotations

import time
import uuid

import litellm
from fastapi import APIRouter, Depends, Request, Response

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, require_scope
from app.core.exceptions import UpstreamError
from app.core.rate_limiter import RateLimiter, get_rate_limiter
from app.db.repositories.usage import record_usage
from app.llm.client import LLMClient, get_llm_client
from app.metrics import prometheus as m
from app.telemetry import annotate_current_span

router = APIRouter(tags=["embeddings"])


def _estimate_tokens(input_: object) -> int:
    if isinstance(input_, str):
        return max(1, len(input_) // 4)
    if isinstance(input_, list):
        if all(isinstance(item, int) for item in input_):
            return len(input_)
        return max(1, sum(_estimate_tokens(item) for item in input_))
    return 1


@router.post("/embeddings")
async def embeddings(
    request_body: dict,
    raw_request: Request,
    raw_response: Response,
    identity: ResolvedIdentity = Depends(require_scope("embeddings")),
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    llm_client: LLMClient = Depends(get_llm_client),
):
    requested_model = request_body.get("model") or settings.default_embedding_model
    if not requested_model:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="model is required")
    decision = llm_client.route(
        requested_model,
        required_capabilities={"embeddings"},
        team_id=identity.team_id,
    )
    model = decision.model
    annotate_current_span(
        **{
            "relay.requested_model": decision.requested_model,
            "relay.model": decision.model,
            "relay.deployment": decision.deployment or "direct",
            "relay.policy.version": decision.policy_version,
            "relay.endpoint": "embeddings",
            "relay.user_id": identity.user_id,
            "relay.team_id": identity.team_id,
        }
    )
    m.ROUTING_DECISIONS.labels(
        deployment=decision.deployment or "direct",
        policy_version=decision.policy_version,
        endpoint="embeddings",
    ).inc()

    input_ = request_body.get("input")
    if input_ is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="input is required")

    request_id = raw_request.headers.get("x-request-id", str(uuid.uuid4()))
    estimated_tokens = _estimate_tokens(input_)
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

    start_time = time.monotonic()
    m.ACTIVE_REQUESTS.inc()
    try:
        call_kwargs: dict = dict(model=model, input=input_)
        if identity.passthrough_key:
            call_kwargs["api_key"] = identity.passthrough_key

        response = await litellm.aembedding(**call_kwargs)

        m.REQUEST_COUNT.labels(model=model, status="success").inc()
        m.REQUEST_LATENCY.labels(model=model, stream="false").observe(time.monotonic() - start_time)
        prompt_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", 0) or 0
        await rate_limiter.reconcile_tokens(
            identity.user_id,
            identity.team_id,
            reserved_tokens=estimated_tokens,
            actual_tokens=prompt_tokens,
            rpm_limit=identity.rpm_limit,
            tpm_limit=identity.tpm_limit,
            daily_token_limit=identity.daily_token_limit,
            team_tpm_limit=identity.team_tpm_limit,
            team_daily_token_limit=identity.team_daily_token_limit,
        )
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)

        if not identity.passthrough_key:
            await record_usage(
                user_id=identity.user_id,
                team_id=identity.team_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - start_time) * 1000),
                request_id=request_id,
                status="success",
                audit_metadata={"endpoint": "embeddings", **decision.audit_metadata()},
            )

        raw_response.headers["X-Request-ID"] = request_id
        raw_response.headers["X-Relay-Deployment"] = decision.deployment or "direct"
        raw_response.headers["X-Relay-Policy-Version"] = decision.policy_version
        return response
    except litellm.exceptions.AuthenticationError as e:
        if not identity.passthrough_key:
            await _record_embedding_error(
                identity,
                model,
                request_id,
                start_time,
                "upstream_authentication_error",
                decision.audit_metadata(),
            )
        raise UpstreamError(f"Embedding authentication failed: {e}") from e
    except Exception as e:
        if not identity.passthrough_key:
            await _record_embedding_error(
                identity,
                model,
                request_id,
                start_time,
                "upstream_error",
                decision.audit_metadata(),
            )
        raise UpstreamError(f"Embedding request failed: {e}") from e
    finally:
        m.ACTIVE_REQUESTS.dec()


async def _record_embedding_error(
    identity: ResolvedIdentity,
    model: str,
    request_id: str,
    start_time: float,
    error_code: str,
    routing_metadata: dict,
) -> None:
    await record_usage(
        user_id=identity.user_id,
        team_id=identity.team_id,
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=int((time.monotonic() - start_time) * 1000),
        request_id=request_id,
        status="error",
        error_code=error_code,
        audit_metadata={"endpoint": "embeddings", **routing_metadata},
    )
