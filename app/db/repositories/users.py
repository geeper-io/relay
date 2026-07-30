from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session_factory
from app.db.models import ApiKey, AuditLog, Team, User


async def get_user_by_external_id(db: AsyncSession, external_id: str) -> User | None:
    result = await db.execute(select(User).where(User.external_id == external_id))
    return result.scalar_one_or_none()


async def get_user_by_key_hash(db: AsyncSession, key_hash: str) -> tuple[User, ApiKey, Team | None] | None:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(User, ApiKey, Team)
        .join(ApiKey, ApiKey.user_id == User.id)
        .outerjoin(Team, Team.id == User.team_id)
        .where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
            User.is_active.is_(True),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
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
    expires_at: datetime | None = None,
    actor: str = "system",
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
        expires_at=expires_at,
    )
    db.add(api_key)
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            user_id=user_id,
            action="api_key.created",
            resource=api_key.id,
            metadata_={"actor": actor, "key_prefix": key_prefix, "name": name, "scopes": api_key.scopes},
        )
    )
    await db.commit()
    await db.refresh(api_key)
    return raw_key, api_key


async def list_api_keys(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    include_inactive: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[ApiKey]:
    """List key metadata without ever returning key hashes or raw secrets."""
    query = select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id).limit(limit).offset(offset)
    if user_id:
        query = query.where(ApiKey.user_id == user_id)
    if not include_inactive:
        query = query.where(ApiKey.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars())


async def revoke_api_key(db: AsyncSession, *, key_id: str) -> ApiKey | None:
    """Revoke a key and record the lifecycle event in the same transaction."""
    key = await db.scalar(select(ApiKey).where(ApiKey.id == key_id))
    if key is None:
        return None
    if key.is_active:
        key.is_active = False
        db.add(
            AuditLog(
                id=str(uuid.uuid4()),
                request_id=str(uuid.uuid4()),
                user_id=key.user_id,
                action="admin.api_key.revoked",
                resource=key.id,
                metadata_={"actor": "admin", "key_prefix": key.key_prefix, "name": key.name},
            )
        )
        await db.commit()
        await db.refresh(key)
    return key


async def rotate_api_key(
    db: AsyncSession,
    *,
    key_id: str,
    expires_at: datetime | None = None,
    preserve_expiry: bool = True,
) -> tuple[str, ApiKey, ApiKey] | None:
    """Atomically revoke an active key and create a policy-equivalent replacement."""
    old_key = await db.scalar(select(ApiKey).where(ApiKey.id == key_id))
    if old_key is None or not old_key.is_active:
        return None

    raw_key = "gr-" + secrets.token_urlsafe(32)
    new_key = ApiKey(
        id=str(uuid.uuid4()),
        key_hash=_hash_key(raw_key),
        key_prefix=raw_key[:12],
        user_id=old_key.user_id,
        name=old_key.name,
        scopes=list(old_key.scopes or []),
        expires_at=old_key.expires_at if preserve_expiry else expires_at,
    )
    old_key.is_active = False
    db.add(new_key)
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            user_id=old_key.user_id,
            action="admin.api_key.rotated",
            resource=new_key.id,
            metadata_={
                "actor": "admin",
                "previous_key_id": old_key.id,
                "previous_key_prefix": old_key.key_prefix,
                "new_key_prefix": new_key.key_prefix,
            },
        )
    )
    await db.commit()
    await db.refresh(old_key)
    await db.refresh(new_key)
    return raw_key, new_key, old_key


async def update_key_last_used(key_id: str) -> None:
    async with get_session_factory()() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if key:
            key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
