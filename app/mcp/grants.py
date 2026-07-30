"""Short-lived signed credentials for provider-to-Relay MCP calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.config import Settings
from app.core.exceptions import AuthenticationError

_PREFIX = "grmcp-"


def issue_mcp_grant(
    settings: Settings,
    *,
    user_id: str,
    team_id: str | None,
    scopes: list[str],
    approval_id: str | None = None,
    server: str | None = None,
    tool: str | None = None,
    arguments_hash: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "typ": "relay_mcp_grant",
        "user_id": user_id,
        "team_id": team_id,
        "scopes": scopes,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.mcp__delegated_grant_ttl_seconds,
    }
    if approval_id:
        payload.update(
            approval_id=approval_id,
            server=server,
            tool=tool,
            arguments_hash=arguments_hash,
        )
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{_PREFIX}{encoded}.{_b64(signature)}"


def verify_mcp_grant(token: str, settings: Settings) -> dict[str, Any]:
    try:
        encoded, signature = token.removeprefix(_PREFIX).split(".", 1)
        expected = hmac.new(settings.proxy_master_key.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ValueError
        payload = json.loads(_unb64(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Invalid delegated MCP credential") from exc
    if not isinstance(payload, dict) or payload.get("typ") != "relay_mcp_grant":
        raise AuthenticationError("Invalid delegated MCP credential")
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise AuthenticationError("Delegated MCP credential has expired")
    if not payload.get("user_id") or not isinstance(payload.get("scopes"), list):
        raise AuthenticationError("Invalid delegated MCP credential")
    return payload


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
