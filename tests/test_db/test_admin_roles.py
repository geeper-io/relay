import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.roles import (
    list_admin_identities,
    list_admin_roles,
    record_admin_identity,
    remove_admin_role,
    resolve_admin_role,
    set_admin_role,
)
from app.config import Settings
from app.db.engine import Base
from app.db.models import AuditLog, User


@pytest.fixture
async def admin_role_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_durable_assignment_overrides_bootstrap_and_is_audited(admin_role_session_factory):
    settings = Settings(
        proxy_master_key="test-master-key",
        admin__bootstrap_emails=["Admin@Example.com"],
    )
    async with admin_role_session_factory() as db:
        db.add(User(id="user-1", external_id="oidc:user-1"))
        await db.commit()
        await record_admin_identity(
            db,
            user_id="user-1",
            email="admin@example.com",
            display_name="Relay Admin",
        )

        assert (
            await resolve_admin_role(
                db,
                user_id="user-1",
                email="admin@example.com",
                settings=settings,
            )
            == "admin"
        )

        assignment = await set_admin_role(
            db,
            user_id="user-1",
            role="viewer",
            actor="master-key",
            request_id="request-1",
        )
        assert assignment.role == "viewer"
        assert (
            await resolve_admin_role(
                db,
                user_id="user-1",
                email="admin@example.com",
                settings=settings,
            )
            == "viewer"
        )
        assert [item.user_id for item in await list_admin_roles(db, role="viewer")] == ["user-1"]
        identities = await list_admin_identities(db)
        assert identities[0][0].email == "admin@example.com"
        assert identities[0][1] is not None
        assert identities[0][1].role == "viewer"

        assert await remove_admin_role(
            db,
            user_id="user-1",
            actor="master-key",
            request_id="request-2",
        )
        assert not await remove_admin_role(
            db,
            user_id="user-1",
            actor="master-key",
            request_id="request-3",
        )
        actions = list(
            (
                await db.scalars(
                    select(AuditLog.action)
                    .where(AuditLog.resource == "admin-role/user-1")
                    .order_by(AuditLog.created_at)
                )
            ).all()
        )

    assert actions == ["admin.role.assigned", "admin.role.removed"]
