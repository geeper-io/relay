"""Signed, short-lived browser sessions for the opt-in admin dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from fastapi.responses import Response

from app.config import Settings
from app.core.exceptions import AuthenticationError

COOKIE_NAME = "relay_admin_session"


@dataclass(frozen=True)
class AdminSession:
    csrf_token: str
    issued_at: int
    expires_at: int
    role: Literal["viewer", "approver", "admin"]
    actor: str
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None


def issue_admin_session(
    settings: Settings,
    *,
    role: Literal["viewer", "approver", "admin"] = "admin",
    actor: str = "master-key",
    user_id: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
) -> tuple[str, AdminSession]:
    now = int(time.time())
    session = AdminSession(
        csrf_token=secrets.token_urlsafe(24),
        issued_at=now,
        expires_at=now + settings.admin__session_ttl_seconds,
        role=role,
        actor=actor,
        user_id=user_id,
        email=email,
        display_name=display_name,
    )
    payload = {
        "typ": "relay_admin_session",
        "csrf": session.csrf_token,
        "iat": session.issued_at,
        "exp": session.expires_at,
        "role": session.role,
        "actor": session.actor,
        "user_id": session.user_id,
        "email": session.email,
        "display_name": session.display_name,
    }
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}", session


def verify_admin_session(token: str, settings: Settings) -> AdminSession:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ValueError
        payload = json.loads(_unb64(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Invalid admin session") from exc
    if not isinstance(payload, dict) or payload.get("typ") != "relay_admin_session":
        raise AuthenticationError("Invalid admin session")
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise AuthenticationError("Admin session has expired")
    csrf = payload.get("csrf")
    if not isinstance(csrf, str) or not csrf:
        raise AuthenticationError("Invalid admin session")
    role = payload.get("role")
    if role not in {"viewer", "approver", "admin"}:
        raise AuthenticationError("Invalid admin session")
    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor:
        raise AuthenticationError("Invalid admin session")
    return AdminSession(
        csrf_token=csrf,
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload["exp"]),
        role=role,
        actor=actor,
        user_id=_optional_string(payload.get("user_id")),
        email=_optional_string(payload.get("email")),
        display_name=_optional_string(payload.get("display_name")),
    )


def set_admin_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.admin__session_ttl_seconds,
        httponly=True,
        secure=settings.admin__secure_cookies,
        samesite="strict",
        path="/admin",
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
