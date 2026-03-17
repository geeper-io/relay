from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ProxyError(Exception):
    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, **kwargs):
        self.message = message
        self.extra = kwargs
        super().__init__(message)


class AuthenticationError(ProxyError):
    status_code = 401
    error_code = "authentication_error"


class AuthorizationError(ProxyError):
    status_code = 403
    error_code = "authorization_error"


class RateLimitError(ProxyError):
    status_code = 429
    error_code = "rate_limit_exceeded"

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class ContentPolicyError(ProxyError):
    status_code = 400
    error_code = "content_policy_violation"


class ModelNotAllowedError(ProxyError):
    status_code = 400
    error_code = "model_not_allowed"


class UpstreamError(ProxyError):
    status_code = 502
    error_code = "upstream_error"


def _make_error_body(error_code: str, message: str) -> dict:
    return {"error": {"type": error_code, "message": message}}


async def proxy_exception_handler(request: Request, exc: ProxyError) -> JSONResponse:
    headers = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content=_make_error_body(exc.error_code, exc.message),
        headers=headers,
    )
