from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cachetools import TTLCache
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.db.engine import get_db
from app.db.repositories.users import get_user_by_key_hash, update_key_last_used

_cache: TTLCache = TTLCache(maxsize=1024, ttl=60)


@dataclass
class ResolvedIdentity:
    user_id: str
    team_id: str | None
    key_id: str | None
    scopes: list[str]
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    passthrough_key: str | None = None  # set when client provides their own upstream key


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

    # Passthrough mode: any key that isn't a Relay-issued key goes straight to the upstream
    if not raw_key.startswith("gr-") and settings.allow_passthrough_keys:
        key_hash = _hash_key(raw_key)
        return ResolvedIdentity(
            user_id=f"passthrough:{key_hash[:16]}",
            team_id=None,
            key_id=None,
            scopes=[],
            passthrough_key=raw_key,
        )

    key_hash = _hash_key(raw_key)

    # Check in-process cache first
    cached = _cache.get(key_hash)
    if cached:
        return cached

    row = await get_user_by_key_hash(db, key_hash)
    if not row:
        raise AuthenticationError("Invalid or expired API key")

    user, api_key = row
    if not user.is_active:
        raise AuthenticationError("User account is deactivated")

    identity = ResolvedIdentity(
        user_id=user.id,
        team_id=user.team_id,
        key_id=api_key.id,
        scopes=api_key.scopes or [],
        rpm_limit=user.rpm_limit,
        tpm_limit=user.tpm_limit,
    )
    _cache[key_hash] = identity

    import asyncio

    asyncio.create_task(update_key_last_used(api_key.id))

    return identity


def require_scope(scope: str):
    """Dependency factory — ensures identity has a given scope."""

    async def check(identity: ResolvedIdentity = Depends(resolve_identity)) -> ResolvedIdentity:
        if scope not in identity.scopes:
            raise AuthorizationError(f"Scope '{scope}' required")
        return identity

    return check


async def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    header = request.headers.get("Authorization", "")
    key = header.replace("Bearer ", "").strip()
    if key != settings.proxy_master_key:
        raise AuthorizationError("Admin access required")
