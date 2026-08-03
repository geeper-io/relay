from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import ResolvedIdentity
from app.db.engine import Base
from app.db.models import AuditLog, MCPApprovalGrant
from app.mcp.approval_grants import (
    consume_matching_grant,
    create_approval_grant,
    grant_status,
    revoke_approval_grant,
)
from app.mcp.approvals import create_approval, decide_approval


@pytest.fixture
async def grant_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _identity(*, user_id="user-1", team_id="team-1"):
    return ResolvedIdentity(
        user_id=user_id,
        team_id=team_id,
        key_id="key-1",
        scopes=["mcp:code:*"],
    )


@pytest.mark.asyncio
async def test_scoped_grant_matches_constraints_and_exhausts_atomically(grant_factory):
    async with grant_factory() as db:
        grant = await create_approval_grant(
            db,
            subject_type="user",
            subject_id="user-1",
            server="code",
            tool_pattern="test_*",
            constraints={"allowed_values": {"runtime": ["python"]}},
            policy_version="v1",
            max_calls=2,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            actor="admin",
            reason="Test workflow",
            request_id="request-1",
        )

        assert (
            await consume_matching_grant(
                db,
                identity=_identity(),
                server="code",
                tool="test_repo",
                arguments={"runtime": "node"},
                policy_version="v1",
                request_id="request-2",
            )
            is None
        )
        for request_id in ("request-3", "request-4"):
            matched = await consume_matching_grant(
                db,
                identity=_identity(),
                server="code",
                tool="test_repo",
                arguments={"runtime": "python"},
                policy_version="v1",
                request_id=request_id,
            )
            assert matched is not None
        assert (
            await consume_matching_grant(
                db,
                identity=_identity(),
                server="code",
                tool="test_repo",
                arguments={"runtime": "python"},
                policy_version="v1",
                request_id="request-5",
            )
            is None
        )
        await db.refresh(grant)
        events = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "mcp.grant.consumed",
                        AuditLog.resource == f"mcp-grant/{grant.id}",
                    )
                )
            ).all()
        )

    assert grant.calls_used == 2
    assert grant_status(grant) == "exhausted"
    assert len(events) == 2


@pytest.mark.asyncio
async def test_policy_approval_creates_team_grant_and_revocation_is_immediate(grant_factory):
    async with grant_factory() as db:
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id="team-1",
            server="code",
            tool="execute",
            arguments={"runtime": "python", "command": "pytest"},
            purpose="Run tests",
            policy_version="v1",
            ttl_seconds=300,
            request_id="request-1",
            grant_template={
                "subject": "team",
                "ttl_seconds": 3600,
                "max_calls": 10,
                "constraints": {"allowed_values": {"runtime": ["python"]}},
            },
        )
        decided = await decide_approval(
            db,
            approval_id=approval.id,
            decision="approved",
            actor="admin",
            reason="Approved for the team",
            request_id="request-2",
        )
        assert decided is not None
        grant = await db.scalar(select(MCPApprovalGrant).where(MCPApprovalGrant.source_approval_id == approval.id))
        assert grant is not None
        assert grant.subject_type == "team"
        assert grant.subject_id == "team-1"
        assert grant.max_calls == 10

        matched = await consume_matching_grant(
            db,
            identity=_identity(user_id="user-2"),
            server="code",
            tool="execute",
            arguments={"runtime": "python", "command": "pytest tests"},
            policy_version="v1",
            request_id="request-3",
        )
        assert matched is not None

        revoked = await revoke_approval_grant(
            db,
            grant_id=grant.id,
            actor="admin",
            request_id="request-4",
        )
        assert revoked is not None
        assert grant_status(revoked) == "revoked"
        assert (
            await consume_matching_grant(
                db,
                identity=_identity(),
                server="code",
                tool="execute",
                arguments={"runtime": "python", "command": "pytest"},
                policy_version="v1",
                request_id="request-5",
            )
            is None
        )
