from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import litellm
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.core.exceptions import ModelNotAllowedError, UpstreamError
from app.core.routing import ModelRouter, RoutingDecision

litellm.set_verbose = False
log = logging.getLogger(__name__)


def init_cache(settings: Settings) -> None:
    """Configure litellm.cache if caching is enabled. Called once at startup."""
    if not settings.cache_enabled:
        return
    from litellm import Cache

    kwargs: dict[str, Any] = {"type": settings.cache_type, "ttl": settings.cache_ttl}
    if settings.cache_type == "redis":
        kwargs["host"] = settings.cache_redis_host
        kwargs["port"] = settings.cache_redis_port

    litellm.cache = Cache(**kwargs)
    log.info("LiteLLM cache enabled type=%s ttl=%ds", settings.cache_type, settings.cache_ttl)


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._router = ModelRouter(settings)
        if settings.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
        if settings.anthropic_api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        if settings.azure_openai_api_key:
            os.environ.setdefault("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key)
        if settings.azure_openai_endpoint:
            os.environ.setdefault("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint)

    def resolve_model(self, model: str) -> str:
        return self.route(model).model

    def route(
        self,
        model: str,
        *,
        required_capabilities: set[str] | None = None,
        team_id: str | None = None,
    ) -> RoutingDecision:
        return self._router.route(
            model,
            required_capabilities=required_capabilities,
            team_id=team_id,
        )

    def count_tokens(self, model: str, messages: list[dict]) -> int:
        """Exact token count using the model's actual tokenizer via LiteLLM."""
        try:
            return litellm.token_counter(model=model, messages=messages)
        except Exception:
            # Fallback to rough estimate if tokenizer unavailable for this model
            combined = " ".join(m.get("content", "") or "" for m in messages)
            return max(1, len(combined) // 4)

    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Return estimated cost in USD using LiteLLM's pricing table."""
        # Try the full model name first, then strip provider prefix as fallback
        for m in (model, model.split("/", 1)[-1]):
            try:
                prompt_cost, completion_cost = litellm.cost_per_token(
                    model=m,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return round(prompt_cost + completion_cost, 8)
            except Exception:
                continue
        return 0.0

    def _max_tokens_for(self, model: str, requested: int | None) -> int | None:
        limit = self._settings.llm__per_model_max_tokens.get(model)
        if limit and requested:
            return min(requested, limit)
        return requested or limit

    def _context_window_fallbacks(self) -> dict[str, str]:
        """Map each allowed model to the next one in the list as a context-window fallback."""
        models = self._settings.allowed_models
        return {models[i]: models[i + 1] for i in range(len(models) - 1)}

    @retry(
        retry=retry_if_exception_type(litellm.exceptions.ServiceUnavailableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def complete(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
        trace_metadata: dict | None = None,
        api_key: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> litellm.ModelResponse:
        try:
            call_kwargs: dict[str, Any] = dict(
                model=model,
                messages=messages,
                max_tokens=self._max_tokens_for(model, max_tokens),
                temperature=temperature,
                metadata=trace_metadata or {},
                cache={"no-cache": False} if self._settings.cache_enabled else None,
                **kwargs,
            )
            if api_key:
                call_kwargs["api_key"] = api_key
            # Don't use litellm fallbacks with a passthrough key — litellm doesn't
            # propagate api_key to fallback calls, so they'd use the proxy's own key.
            effective_fallbacks = fallback_models or self._settings.fallback_models
            if effective_fallbacks and not api_key:
                call_kwargs["fallbacks"] = list(effective_fallbacks)
            if len(self._settings.allowed_models) > 1 and not api_key:
                call_kwargs["context_window_fallback_dict"] = self._context_window_fallbacks()

            response = await litellm.acompletion(**call_kwargs)
            return response
        except litellm.exceptions.AuthenticationError as e:
            raise UpstreamError(f"LLM authentication failed: {e}") from e
        except litellm.exceptions.NotFoundError as e:
            raise ModelNotAllowedError(f"Model not found upstream: {e}") from e
        except Exception as e:
            raise UpstreamError(f"LLM request failed: {e}") from e

    async def stream(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
        trace_metadata: dict | None = None,
        api_key: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[litellm.utils.StreamingChoices, None]:
        try:
            call_kwargs: dict[str, Any] = dict(
                model=model,
                messages=messages,
                max_tokens=self._max_tokens_for(model, max_tokens),
                temperature=temperature,
                stream=True,
                metadata=trace_metadata or {},
                # Streaming responses are not cached — cache=None skips cache lookup
                **kwargs,
            )
            if api_key:
                call_kwargs["api_key"] = api_key
            effective_fallbacks = fallback_models or self._settings.fallback_models
            if effective_fallbacks and not api_key:
                call_kwargs["fallbacks"] = list(effective_fallbacks)

            response = await litellm.acompletion(**call_kwargs)
            async for chunk in response:
                yield chunk
        except litellm.exceptions.AuthenticationError as e:
            raise UpstreamError(f"LLM authentication failed: {e}") from e
        except Exception as e:
            raise UpstreamError(f"LLM stream failed: {e}") from e

    async def respond(
        self,
        model: str,
        input_: str | list[dict],
        *,
        max_output_tokens: int | None = None,
        trace_metadata: dict | None = None,
        api_key: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call LiteLLM's native Responses API adapter."""
        try:
            call_kwargs: dict[str, Any] = {
                "model": model,
                "input": input_,
                "max_output_tokens": self._max_tokens_for(model, max_output_tokens),
                "metadata": trace_metadata or {},
                **kwargs,
            }
            if api_key:
                call_kwargs["api_key"] = api_key
            effective_fallbacks = fallback_models or self._settings.fallback_models
            if effective_fallbacks and not api_key:
                call_kwargs["fallbacks"] = list(effective_fallbacks)
            return await litellm.aresponses(**call_kwargs)
        except litellm.exceptions.AuthenticationError as exc:
            raise UpstreamError(f"Responses API authentication failed: {exc}") from exc
        except litellm.exceptions.NotFoundError as exc:
            raise ModelNotAllowedError(f"Model not found upstream: {exc}") from exc
        except Exception as exc:
            raise UpstreamError(f"Responses API request failed: {exc}") from exc

    async def response_stream(
        self,
        model: str,
        input_: str | list[dict],
        *,
        max_output_tokens: int | None = None,
        trace_metadata: dict | None = None,
        api_key: str | None = None,
        fallback_models: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        try:
            call_kwargs: dict[str, Any] = {
                "model": model,
                "input": input_,
                "max_output_tokens": self._max_tokens_for(model, max_output_tokens),
                "metadata": trace_metadata or {},
                "stream": True,
                **kwargs,
            }
            if api_key:
                call_kwargs["api_key"] = api_key
            effective_fallbacks = fallback_models or self._settings.fallback_models
            if effective_fallbacks and not api_key:
                call_kwargs["fallbacks"] = list(effective_fallbacks)
            stream = await litellm.aresponses(**call_kwargs)
            async for event in stream:
                yield event
        except litellm.exceptions.AuthenticationError as exc:
            raise UpstreamError(f"Responses API authentication failed: {exc}") from exc
        except Exception as exc:
            raise UpstreamError(f"Responses API stream failed: {exc}") from exc


_client: LLMClient | None = None


def init_llm_client(settings: Settings) -> LLMClient:
    global _client
    _client = LLMClient(settings)
    return _client


def get_llm_client() -> LLMClient:
    if _client is None:
        raise RuntimeError("LLMClient not initialized")
    return _client
