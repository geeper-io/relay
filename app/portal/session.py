"""Signed, short-lived sessions for the self-service developer portal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi.responses import Response

from app.config import Settings
from app.core.exceptions import AuthenticationError

COOKIE_NAME = "relay_portal_session"


@dataclass(frozen=True)
class PortalSession:
    csrf_token: str
    issued_at: int
    expires_at: int
    user_id: str
    email: str
    display_name: str


def issue_portal_session(
    settings: Settings,
    *,
    user_id: str,
    email: str,
    display_name: str,
) -> tuple[str, PortalSession]:
    now = int(time.time())
    session = PortalSession(
        csrf_token=secrets.token_urlsafe(24),
        issued_at=now,
        expires_at=now + settings.portal__session_ttl_seconds,
        user_id=user_id,
        email=email,
        display_name=display_name,
    )
    payload = {
        "typ": "relay_portal_session",
        "csrf": session.csrf_token,
        "iat": session.issued_at,
        "exp": session.expires_at,
        "user_id": session.user_id,
        "email": session.email,
        "display_name": session.display_name,
    }
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}", session


def verify_portal_session(token: str, settings: Settings) -> PortalSession:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ValueError
        payload = json.loads(_unb64(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Invalid portal session") from exc
    if not isinstance(payload, dict) or payload.get("typ") != "relay_portal_session":
        raise AuthenticationError("Invalid portal session")
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise AuthenticationError("Portal session has expired")
    required = ("csrf", "user_id", "display_name")
    if any(not isinstance(payload.get(field), str) or not payload[field] for field in required):
        raise AuthenticationError("Invalid portal session")
    if not isinstance(payload.get("email"), str):
        raise AuthenticationError("Invalid portal session")
    return PortalSession(
        csrf_token=payload["csrf"],
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload["exp"]),
        user_id=payload["user_id"],
        email=payload["email"],
        display_name=payload["display_name"],
    )


def set_portal_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.portal__session_ttl_seconds,
        httponly=True,
        secure=settings.portal__secure_cookies,
        samesite="strict",
        path="/portal",
    )


def clear_portal_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=settings.portal__secure_cookies,
        samesite="strict",
        path="/portal",
    )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
