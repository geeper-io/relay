import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.internal.mcp import router
from app.config import Settings, get_settings
from app.core.exceptions import ProxyError, proxy_exception_handler
from app.db.engine import Base, get_db
from app.db.models import User


@pytest.fixture
async def grant_api_app():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        proxy_master_key="grant-api-master-key",
        mcp__enabled=True,
        mcp__servers={"code": {"url": "https://tools.example/mcp"}},
        mcp__policies={"default": {"default_action": "require_approval"}},
    )
    app = FastAPI()
    app.include_router(router, prefix="/internal")
    app.add_exception_handler(ProxyError, proxy_exception_handler)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with factory() as db:
        db.add(User(id="user-1", external_id="alice@example.com"))
        await db.commit()
    try:
        yield app, settings
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_master_key_can_create_list_and_revoke_grant(grant_api_app):
    app, settings = grant_api_app
    headers = {"Authorization": f"Bearer {settings.proxy_master_key}"}
    body = {
        "subject_type": "user",
        "subject_id": "user-1",
        "server": "code",
        "tool": "test_*",
        "constraints": {},
        "ttl_seconds": 3600,
        "max_calls": 10,
        "reason": "Routine CI",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/internal/mcp/grants")
        assert denied.status_code == 403

        policies = await client.get("/internal/mcp/policies", headers=headers)
        assert policies.status_code == 200
        assert policies.json()["active"]["version"] == "default"
        draft = await client.post(
            "/internal/mcp/policies/drafts",
            headers=headers,
            json={
                "version": "api-v2",
                "base_version": "default",
                "reason": "Allow test tools",
                "document": {
                    "default_action": "deny",
                    "rules": [{"name": "tests", "server": "code", "tool": "test_*", "action": "allow"}],
                },
            },
        )
        assert draft.status_code == 201
        activated_policy = await client.post(
            "/internal/mcp/policies/api-v2/activate",
            headers=headers,
            json={"reason": "Validated by automation"},
        )
        assert activated_policy.status_code == 200
        simulation = await client.post(
            "/internal/mcp/policies/simulate",
            headers=headers,
            json={
                "user_id": "user-1",
                "scopes": ["mcp:code:*"],
                "server": "code",
                "tool": "test_unit",
                "arguments": {},
            },
        )
        assert simulation.status_code == 200
        assert simulation.json()["action"] == "allow"

        missing_subject = await client.post(
            "/internal/mcp/grants",
            headers=headers,
            json={**body, "subject_id": "missing"},
        )
        assert missing_subject.status_code == 404

        created = await client.post("/internal/mcp/grants", headers=headers, json=body)
        assert created.status_code == 201
        grant_id = created.json()["id"]
        assert created.json()["status"] == "active"

        inventory = await client.get("/internal/mcp/grants", headers=headers)
        assert inventory.status_code == 200
        assert inventory.json()["items"][0]["id"] == grant_id

        revoked = await client.delete(f"/internal/mcp/grants/{grant_id}", headers=headers)
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        active = await client.get("/internal/mcp/grants?include_inactive=false", headers=headers)
        assert active.json()["items"] == []
