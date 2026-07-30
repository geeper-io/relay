import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.engine import Base
from app.db.models import ApiKey, AuditLog, Team, UsageRecord, User
from app.db.repositories import usage
from app.db.repositories.usage import record_usage
from app.db.repositories.users import get_user_by_key_hash


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_key(factory, *, expires_at):
    raw_key = "gr-test-key"
    async with factory() as db:
        team = Team(id="team-1", name="security", tpm_limit=321, daily_token_limit=654)
        user = User(id="user-1", external_id="test:user", team_id=team.id)
        key = ApiKey(
            id="key-1",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix="gr-test",
            user_id=user.id,
            scopes=["chat"],
            expires_at=expires_at,
        )
        db.add_all([team, user, key])
        await db.commit()
    return raw_key


@pytest.mark.asyncio
async def test_expired_api_key_is_rejected_by_repository(session_factory):
    raw_key = await _seed_key(
        session_factory,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    async with session_factory() as db:
        assert await get_user_by_key_hash(db, hashlib.sha256(raw_key.encode()).hexdigest()) is None


@pytest.mark.asyncio
async def test_valid_key_resolves_team_limits(session_factory):
    raw_key = await _seed_key(
        session_factory,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    async with session_factory() as db:
        row = await get_user_by_key_hash(db, hashlib.sha256(raw_key.encode()).hexdigest())
    assert row is not None
    _user, _key, team = row
    assert team is not None
    assert team.tpm_limit == 321
    assert team.daily_token_limit == 654


@pytest.mark.asyncio
async def test_usage_and_audit_are_committed_together(session_factory, monkeypatch):
    await _seed_key(session_factory, expires_at=None)
    monkeypatch.setattr(usage, "get_session_factory", lambda: session_factory)

    await record_usage(
        user_id="user-1",
        team_id="team-1",
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=20,
        request_id="request-1",
        audit_metadata={"endpoint": "test"},
    )

    async with session_factory() as db:
        usage_count = await db.scalar(select(func.count()).select_from(UsageRecord))
        audit_count = await db.scalar(select(func.count()).select_from(AuditLog))
        audit = await db.scalar(select(AuditLog))
    assert usage_count == 1
    assert audit_count == 1
    assert audit is not None
    assert audit.metadata_["endpoint"] == "test"
