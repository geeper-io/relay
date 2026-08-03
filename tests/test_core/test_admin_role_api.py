import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.roles import record_admin_identity
from app.api.internal.admin import router
from app.config import Settings, get_settings
from app.core.exceptions import ProxyError, proxy_exception_handler
from app.db.engine import Base, get_db
from app.db.models import User


@pytest.fixture
async def role_api_app():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(proxy_master_key="role-api-master-key")
    app = FastAPI()
    app.include_router(router, prefix="/internal")
    app.add_exception_handler(ProxyError, proxy_exception_handler)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with factory() as db:
        db.add(User(id="user-1", external_id="oidc:user-1"))
        await db.commit()
        await record_admin_identity(
            db,
            user_id="user-1",
            email="alice@example.com",
            display_name="Alice",
        )
    try:
        yield app, settings
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_master_key_can_discover_identity_and_manage_role(role_api_app):
    app, settings = role_api_app
    headers = {"Authorization": f"Bearer {settings.proxy_master_key}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/internal/admin-identities")
        assert denied.status_code == 403

        identities = await client.get("/internal/admin-identities", headers=headers)
        assert identities.status_code == 200
        assert identities.json()["items"][0]["email"] == "alice@example.com"
        assert identities.json()["items"][0]["role"] is None

        assigned = await client.put(
            "/internal/admin-roles/user-1",
            headers=headers,
            json={"role": "approver"},
        )
        assert assigned.status_code == 200
        assert assigned.json()["role"] == "approver"

        removed = await client.delete("/internal/admin-roles/user-1", headers=headers)
        assert removed.status_code == 204
