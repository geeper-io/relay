"""
Optional Langfuse analytics integration via LiteLLM's built-in callback system.

When enabled, every LLM call is automatically traced in Langfuse with:
  - user_id, session_id (request_id), model, input/output tokens, latency
  - custom tags: team, rag_used, stream

Set ANALYTICS__ENABLED=true + LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to activate.
Self-host: set LANGFUSE_HOST=http://langfuse:3000
EU cloud: set LANGFUSE_HOST=https://cloud.langfuse.com (default is https://cloud.langfuse.com)

Requires langfuse>=2.59.7,<3.0.0 — see requirements.txt.
"""
from __future__ import annotations

import logging

import litellm

log = logging.getLogger(__name__)

_initialized = False


def init_langfuse(settings) -> bool:
    """
    Register Langfuse as a LiteLLM callback if analytics are enabled and
    the Langfuse credentials are present. Returns True if activated.
    """
    global _initialized

    if not settings.analytics_enabled:
        return False

    if settings.analytics_provider != "langfuse":
        log.warning(
            "analytics.provider=%s is not supported, only 'langfuse' is available",
            settings.analytics_provider,
        )
        return False

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        log.warning(
            "Langfuse analytics enabled but LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
            "are not set — skipping"
        )
        return False

    import os
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

    if "langfuse" not in (litellm.success_callback or []):
        litellm.success_callback = list(litellm.success_callback or []) + ["langfuse"]
    if "langfuse" not in (litellm.failure_callback or []):
        litellm.failure_callback = list(litellm.failure_callback or []) + ["langfuse"]

    _initialized = True
    log.info(
        "Langfuse analytics enabled (host=%s)",
        settings.langfuse_host or "https://cloud.langfuse.com",
    )
    return True


def build_trace_metadata(
    *,
    user_id: str,
    team_id: str | None,
    request_id: str,
    model: str,
    rag_used: bool = False,
    stream: bool = False,
    extra: dict | None = None,
) -> dict:
    """
    Build the metadata dict to pass to litellm.acompletion(metadata=...).
    LiteLLM forwards these keys to Langfuse automatically.
    See: https://docs.litellm.ai/docs/observability/langfuse_integration
    """
    tags = [f"model:{model}"]
    if team_id:
        tags.append(f"team:{team_id}")
    if rag_used:
        tags.append("rag:true")
    if stream:
        tags.append("stream:true")

    metadata = {
        "trace_id": request_id,
        "session_id": request_id,
        "trace_user_id": user_id,
        "tags": tags,
        "generation_name": "chat_completion",
    }
    if extra:
        metadata.update(extra)
    return metadata
