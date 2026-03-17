"""OpenAI-compatible /v1/embeddings endpoint."""

from __future__ import annotations

import time

import litellm
from fastapi import APIRouter, Depends, Request

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, resolve_identity
from app.core.exceptions import UpstreamError
from app.metrics import prometheus as m

router = APIRouter(tags=["embeddings"])


@router.post("/embeddings")
async def embeddings(
    request_body: dict,
    raw_request: Request,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
):
    model = request_body.get("model") or settings.default_embedding_model
    if not model:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="model is required")

    input_ = request_body.get("input")
    if input_ is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="input is required")

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
        m.TOKENS_USED.labels(model=model, token_type="prompt").inc(prompt_tokens)

        return response
    except litellm.exceptions.AuthenticationError as e:
        raise UpstreamError(f"Embedding authentication failed: {e}") from e
    except Exception as e:
        raise UpstreamError(f"Embedding request failed: {e}") from e
    finally:
        m.ACTIVE_REQUESTS.dec()
