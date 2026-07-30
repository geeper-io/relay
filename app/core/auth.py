from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.db.engine import get_db
from app.db.repositories.users import get_user_by_key_hash, update_key_last_used
from app.mcp.grants import verify_mcp_grant


@dataclass
class ResolvedIdentity:
    user_id: str
    team_id: str | None
    key_id: str | None
    scopes: list[str]
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    daily_token_limit: int | None = None
    team_tpm_limit: int | None = None
    team_daily_token_limit: int | None = None
    expires_at: datetime | None = None
    passthrough_key: str | None = None  # set when client provides their own upstream key
    mcp_grant_approval_id: str | None = None
    mcp_grant_server: str | None = None
    mcp_grant_tool: str | None = None
    mcp_grant_arguments_hash: str | None = None

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:]
    # Also accept raw key in header for convenience
    if header:
        return header
    raise AuthenticationError("Missing Authorization header")


async def resolve_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResolvedIdentity:
    raw_key = _extract_bearer(request)

    if raw_key.startswith("grmcp-"):
        claims = verify_mcp_grant(raw_key, settings)
        return ResolvedIdentity(
            user_id=claims["user_id"],
            team_id=claims.get("team_id"),
            key_id=None,
            scopes=list(claims.get("scopes", [])),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc),
            mcp_grant_approval_id=claims.get("approval_id"),
            mcp_grant_server=claims.get("server"),
            mcp_grant_tool=claims.get("tool"),
            mcp_grant_arguments_hash=claims.get("arguments_hash"),
        )

    # Passthrough mode: any key that isn't a Relay-issued key goes straight to the upstream
    if not raw_key.startswith("gr-") and settings.allow_passthrough_keys:
        key_hash = _hash_key(raw_key)
        return ResolvedIdentity(
            user_id=f"passthrough:{key_hash[:16]}",
            team_id=None,
            key_id=None,
            scopes=["chat", "responses", "embeddings"],
            passthrough_key=raw_key,
        )

    key_hash = _hash_key(raw_key)

    row = await get_user_by_key_hash(db, key_hash)
    if not row:
        raise AuthenticationError("Invalid or expired API key")

    user, api_key, team = row
    if not user.is_active:
        raise AuthenticationError("User account is deactivated")

    identity = ResolvedIdentity(
        user_id=user.id,
        team_id=user.team_id,
        key_id=api_key.id,
        scopes=api_key.scopes or [],
        rpm_limit=user.rpm_limit,
        tpm_limit=user.tpm_limit,
        daily_token_limit=None,
        team_tpm_limit=team.tpm_limit if team else None,
        team_daily_token_limit=team.daily_token_limit if team else None,
        expires_at=api_key.expires_at,
    )
    await update_key_last_used(api_key.id)

    return identity


def require_scope(scope: str):
    """Dependency factory — ensures identity has a given scope."""

    async def check(identity: ResolvedIdentity = Depends(resolve_identity)) -> ResolvedIdentity:
        if not identity.has_scope(scope):
            raise AuthorizationError(f"Scope '{scope}' required")
        return identity

    return check


async def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    header = request.headers.get("Authorization", "")
    key = header.replace("Bearer ", "").strip()
    if not settings.proxy_master_key or not hmac.compare_digest(key, settings.proxy_master_key):
        raise AuthorizationError("Admin access required")


def _is_future(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > datetime.now(timezone.utc)


def rag_filter_for_identity(
    identity: ResolvedIdentity,
    requested_repo: str | None,
    *,
    require_acl: bool = True,
) -> dict | None:
    """Build a Chroma filter from server-trusted scopes, never from a header alone."""
    if not require_acl:
        return {"repo": requested_repo} if requested_repo else None

    if identity.has_scope("rag:*"):
        return {"repo": requested_repo} if requested_repo else None

    prefix = "rag:repo:"
    allowed = sorted({scope[len(prefix) :] for scope in identity.scopes if scope.startswith(prefix)})
    if requested_repo:
        if requested_repo not in allowed:
            raise AuthorizationError(f"Repository '{requested_repo}' is not authorized for this API key")
        return {"repo": requested_repo}
    if not allowed:
        return {"repo": "__relay_no_access__"}
    if len(allowed) == 1:
        return {"repo": allowed[0]}
    return {"repo": {"$in": allowed}}
