import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.core.auth import ResolvedIdentity
from app.db.engine import Base
from app.db.models import AuditLog, MCPPolicyActivation
from app.mcp.policy_store import (
    activate_policy_version,
    active_policy_engine,
    create_policy_draft,
    load_active_policy,
    validate_policy_document,
)


@pytest.fixture
async def policy_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


def _settings() -> Settings:
    return Settings(
        proxy_master_key="policy-store-test-key",
        mcp__enabled=True,
        mcp__servers={"code": {"url": "https://tools.example/mcp"}},
        mcp__active_policy_version="default",
        mcp__policies={"default": {"default_action": "deny", "rules": []}},
    )


@pytest.mark.asyncio
async def test_draft_activation_changes_runtime_policy_and_can_roll_back(policy_db):
    settings = _settings()
    document = {
        "default_action": "deny",
        "rules": [{"name": "tests", "server": "code", "tool": "test_*", "action": "allow"}],
    }
    draft = await create_policy_draft(
        policy_db,
        version="2026-08-01.1",
        document=document,
        base_version="default",
        actor="admin-1",
        reason="Allow test tools",
        request_id="request-create",
        settings=settings,
    )
    assert draft.status == "draft"

    activated = await activate_policy_version(
        policy_db,
        version=draft.version,
        actor="admin-1",
        reason="Evaluation suite passed",
        request_id="request-activate",
        settings=settings,
    )
    assert activated.status == "active"
    snapshot = await load_active_policy(policy_db, settings)
    assert snapshot.version == draft.version
    assert snapshot.source == "database"
    engine, _snapshot = await active_policy_engine(policy_db, settings)
    identity = ResolvedIdentity(user_id="user-1", team_id=None, key_id=None, scopes=["mcp:code:*"])
    assert engine.authorize(identity, "code", "test_unit").action == "allow"
    assert engine.authorize(identity, "code", "execute").action == "deny"

    rolled_back = await activate_policy_version(
        policy_db,
        version="default",
        actor="admin-1",
        reason="Rollback after regression",
        request_id="request-rollback",
        settings=settings,
    )
    assert rolled_back.status == "active"
    assert (await load_active_policy(policy_db, settings)).version == "default"
    assert await policy_db.scalar(select(func.count(MCPPolicyActivation.id))) == 2
    assert await policy_db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "mcp.policy.activated")) == 2


def test_policy_validation_rejects_typos_unsafe_grants_and_bad_regex():
    validation = validate_policy_document(
        {
            "default_action": "allow",
            "rules": [
                {
                    "name": "broken",
                    "server": "missing",
                    "tool": "*",
                    "action": "allow",
                    "grant": {"subject": "organization"},
                    "constraints": {"denied_patterns": {"command": ["["]}},
                    "typo": True,
                }
            ],
        },
        _settings(),
    )
    assert validation.valid is False
    assert any("unknown fields" in error for error in validation.errors)
    assert any("only valid for require_approval" in error for error in validation.errors)
    assert any("invalid regular expression" in error for error in validation.errors)
    assert any("matches no configured" in warning for warning in validation.warnings)
