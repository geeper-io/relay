from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session_factory
from app.db.models import ApiKey, Team, User


async def get_user_by_external_id(db: AsyncSession, external_id: str) -> User | None:
    result = await db.execute(select(User).where(User.external_id == external_id))
    return result.scalar_one_or_none()


async def get_user_by_key_hash(db: AsyncSession, key_hash: str) -> tuple[User, ApiKey] | None:
    result = await db.execute(
        select(User, ApiKey)
        .join(ApiKey, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == key_hash, ApiKey.is_active == True, User.is_active == True)
    )
    row = result.first()
    return row if row else None


async def create_team(db: AsyncSession, name: str, **kwargs) -> Team:
    team = Team(id=str(uuid.uuid4()), name=name, **kwargs)
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


async def create_user(db: AsyncSession, external_id: str, team_id: str | None = None) -> User:
    user = User(id=str(uuid.uuid4()), external_id=external_id, team_id=team_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(
    db: AsyncSession,
    user_id: str,
    name: str = "default",
    scopes: list[str] | None = None,
) -> tuple[str, ApiKey]:
    """Returns (raw_key, ApiKey). raw_key is shown once and not stored."""
    raw_key = "gr-" + secrets.token_urlsafe(32)
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:12]

    api_key = ApiKey(
        id=str(uuid.uuid4()),
        key_hash=key_hash,
        key_prefix=key_prefix,
        user_id=user_id,
        name=name,
        scopes=scopes or ["chat"],
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return raw_key, api_key


async def update_key_last_used(key_id: str) -> None:
    async with get_session_factory()() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if key:
            key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
