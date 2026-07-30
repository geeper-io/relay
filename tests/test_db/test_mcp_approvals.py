from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.core.exceptions import AuthorizationError
from app.db.engine import Base
from app.mcp.approvals import (
    consume_approval,
    create_approval,
    decide_approval,
    issue_approval_token,
)


@pytest.fixture
async def approval_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_token_is_argument_bound_and_replay_safe(approval_factory):
    settings = Settings(proxy_master_key="test-master-key")
    arguments = {"runtime": "python", "command": "pytest"}
    async with approval_factory() as db:
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id="team-1",
            server="code",
            tool="execute",
            arguments=arguments,
            purpose="Run tests",
            policy_version="v1",
            ttl_seconds=300,
            request_id="request-1",
        )
        approval = await decide_approval(
            db,
            approval_id=approval.id,
            decision="approved",
            actor="admin",
            reason="safe test command",
            request_id="request-2",
        )
        assert approval is not None
        token = issue_approval_token(approval, settings)
        consumed = await consume_approval(
            db,
            token=token,
            settings=settings,
            user_id="user-1",
            server="code",
            tool="execute",
            arguments=arguments,
            policy_version="v1",
            request_id="request-3",
        )
        assert consumed.status == "consumed"
        assert consumed.consumed_at is not None

        with pytest.raises(AuthorizationError, match="consumed"):
            await consume_approval(
                db,
                token=token,
                settings=settings,
                user_id="user-1",
                server="code",
                tool="execute",
                arguments=arguments,
                policy_version="v1",
                request_id="request-4",
            )


@pytest.mark.asyncio
async def test_approval_token_rejects_changed_arguments(approval_factory):
    settings = Settings(proxy_master_key="test-master-key")
    async with approval_factory() as db:
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id=None,
            server="code",
            tool="execute",
            arguments={"command": "pytest"},
            purpose=None,
            policy_version="v1",
            ttl_seconds=300,
            request_id="request-1",
        )
        approval = await decide_approval(
            db,
            approval_id=approval.id,
            decision="approved",
            actor="admin",
            reason=None,
            request_id="request-2",
        )
        assert approval is not None
        token = issue_approval_token(approval, settings)
        with pytest.raises(AuthorizationError, match="bound"):
            await consume_approval(
                db,
                token=token,
                settings=settings,
                user_id="user-1",
                server="code",
                tool="execute",
                arguments={"command": "deploy production"},
                policy_version="v1",
                request_id="request-3",
            )
        assert approval.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
