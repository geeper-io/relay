import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.internal.admin import _key_metadata
from app.db.engine import Base
from app.db.models import AuditLog, User
from app.db.repositories.users import (
    create_api_key,
    get_user_by_key_hash,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
)


@pytest.fixture
async def key_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_user_and_key(factory, *, expires_at=None):
    async with factory() as db:
        db.add(User(id="user-1", external_id="alice@example.com"))
        await db.commit()
        return await create_api_key(
            db,
            user_id="user-1",
            name="laptop",
            scopes=["chat", "rag:repo:org/backend"],
            expires_at=expires_at,
            actor="admin",
        )


@pytest.mark.asyncio
async def test_key_listing_returns_metadata_and_can_include_revoked(key_session_factory):
    _raw_key, api_key = await _create_user_and_key(key_session_factory)
    async with key_session_factory() as db:
        active = await list_api_keys(db)
        assert [key.id for key in active] == [api_key.id]
        await revoke_api_key(db, key_id=api_key.id)
        assert await list_api_keys(db) == []
        all_keys = await list_api_keys(db, include_inactive=True)

    metadata = _key_metadata(all_keys[0])
    assert metadata["status"] == "revoked"
    assert "key_hash" not in metadata
    assert "key" not in metadata


@pytest.mark.asyncio
async def test_revoke_rejects_key_and_is_idempotent(key_session_factory):
    raw_key, api_key = await _create_user_and_key(key_session_factory)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with key_session_factory() as db:
        assert await get_user_by_key_hash(db, key_hash) is not None
        revoked = await revoke_api_key(db, key_id=api_key.id)
        assert revoked is not None
        assert not revoked.is_active
        assert await get_user_by_key_hash(db, key_hash) is None
        await revoke_api_key(db, key_id=api_key.id)
        revoke_events = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "admin.api_key.revoked")
        )
    assert revoke_events == 1


@pytest.mark.asyncio
async def test_rotate_atomically_replaces_key_and_preserves_policy(key_session_factory):
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    old_raw, old_key = await _create_user_and_key(key_session_factory, expires_at=expiry)

    async with key_session_factory() as db:
        result = await rotate_api_key(db, key_id=old_key.id)
        assert result is not None
        new_raw, new_key, rotated_key = result

        assert not rotated_key.is_active
        assert new_key.is_active
        assert new_key.id != old_key.id
        assert new_key.name == "laptop"
        assert new_key.scopes == ["chat", "rag:repo:org/backend"]
        assert new_key.expires_at.replace(tzinfo=timezone.utc) == expiry
        assert await get_user_by_key_hash(db, hashlib.sha256(old_raw.encode()).hexdigest()) is None
        assert await get_user_by_key_hash(db, hashlib.sha256(new_raw.encode()).hexdigest()) is not None

        rotate_event = await db.scalar(select(AuditLog).where(AuditLog.action == "admin.api_key.rotated"))
    assert rotate_event is not None
    assert rotate_event.metadata_["previous_key_id"] == old_key.id


@pytest.mark.asyncio
async def test_expired_key_metadata_is_reported_without_exposing_secret(key_session_factory):
    _raw_key, api_key = await _create_user_and_key(
        key_session_factory,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    metadata = _key_metadata(api_key)
    assert metadata["status"] == "expired"
    assert set(metadata) == {
        "id",
        "key_prefix",
        "user_id",
        "name",
        "scopes",
        "status",
        "is_active",
        "expires_at",
        "last_used_at",
        "created_at",
    }
