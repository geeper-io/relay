from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db.engine import Base, get_db
from app.db.models import ApiKey, Team, UsageRecord, User
from app.portal.router import router
from app.portal.session import COOKIE_NAME, issue_portal_session


@pytest.fixture
async def portal_app():
    from fastapi import FastAPI

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        proxy_master_key="portal-test-master-key",
        portal__enabled=True,
        portal__secure_cookies=False,
        portal__max_active_keys=3,
        portal__max_key_ttl_days=90,
        oidc__default_key_scopes=["chat", "responses", "mcp:code:*"],
        auth_base_url="https://relay.example.com",
    )
    async with factory() as db:
        team = Team(id="team-1", name="Engineering", tpm_limit=300_000, daily_token_limit=3_000_000)
        user = User(id="user-1", external_id="oidc:issuer:alice", team_id=team.id, rpm_limit=40)
        other_user = User(id="user-2", external_id="oidc:issuer:bob", team_id=team.id)
        key = ApiKey(
            id="key-1",
            key_hash="hash",
            key_prefix="gr-existing",
            user_id=user.id,
            name="existing",
            scopes=["chat"],
        )
        other_key = ApiKey(
            id="key-other",
            key_hash="other-hash",
            key_prefix="gr-other",
            user_id=other_user.id,
            name="other-user-key",
            scopes=["chat"],
        )
        usage = UsageRecord(
            id="usage-1",
            user_id=user.id,
            team_id=team.id,
            model="gpt-4o",
            prompt_tokens=700,
            completion_tokens=300,
            total_tokens=1000,
            latency_ms=120,
            request_id="request-1",
            cost_usd=0.02,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add_all([team, user, other_user, key, other_key, usage])
        await db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    yield app, settings, factory
    await engine.dispose()


def _session_cookie(settings: Settings) -> tuple[str, str]:
    token, session = issue_portal_session(
        settings,
        user_id="user-1",
        email="alice@example.com",
        display_name="Alice",
    )
    return token, session.csrf_token


@pytest.mark.asyncio
async def test_portal_overview_is_user_scoped_and_contains_limits_guides_and_safe_keys(portal_app):
    app, settings, _factory = portal_app
    token, _csrf = _session_cookie(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(COOKIE_NAME, token)
        page = await client.get("/portal")
        overview = await client.get("/portal/api/overview?days=30")
        keys = await client.get("/portal/api/keys")

    assert page.status_code == 200
    assert "Usage &amp; limits" in page.text or "Usage & limits" in page.text
    assert overview.status_code == 200
    body = overview.json()
    assert body["usage"]["total_tokens"] == 1000
    assert body["limits"]["rpm"] == 40
    assert body["limits"]["tokens_today"] == 1000
    assert body["team_name"] == "Engineering"
    assert body["api_keys"][0]["key_prefix"] == "gr-existing"
    assert "key_hash" not in body["api_keys"][0]
    assert body["base_url"] == "https://relay.example.com"
    assert keys.status_code == 200
    assert keys.json()["items"][0]["id"] == "key-1"
    assert all(item["id"] != "key-other" for item in keys.json()["items"])


@pytest.mark.asyncio
async def test_user_can_create_rotate_and_revoke_only_owned_keys(portal_app):
    app, settings, factory = portal_app
    token, csrf = _session_cookie(settings)
    headers = {"X-Relay-CSRF": csrf}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(COOKIE_NAME, token)
        forbidden_scope = await client.post(
            "/portal/api/keys",
            headers=headers,
            json={"name": "too-powerful", "scopes": ["*"], "expires_in_days": 30},
        )
        too_long = await client.post(
            "/portal/api/keys",
            headers=headers,
            json={"name": "too-long", "scopes": ["chat"], "expires_in_days": 365},
        )
        created = await client.post(
            "/portal/api/keys",
            headers=headers,
            json={"name": "laptop", "scopes": ["chat", "responses"], "expires_in_days": 30},
        )
        key_id = created.json()["id"]
        rotated = await client.post(f"/portal/api/keys/{key_id}/rotate", headers=headers)
        replacement_id = rotated.json()["replacement"]["id"]
        revoked = await client.delete(f"/portal/api/keys/{replacement_id}", headers=headers)
        missing = await client.delete("/portal/api/keys/key-other", headers=headers)

    assert forbidden_scope.status_code == 403
    assert too_long.status_code == 400
    assert created.status_code == 201
    assert created.json()["key"].startswith("gr-")
    assert created.json()["scopes"] == ["chat", "responses"]
    assert rotated.status_code == 200
    assert rotated.json()["key"].startswith("gr-")
    assert rotated.json()["revoked"]["status"] == "revoked"
    assert revoked.status_code == 204
    assert missing.status_code == 404
    async with factory() as db:
        replacement = await db.get(ApiKey, replacement_id)
        assert replacement is not None and not replacement.is_active


@pytest.mark.asyncio
async def test_portal_key_mutations_require_csrf(portal_app):
    app, settings, _factory = portal_app
    token, _csrf = _session_cookie(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(COOKIE_NAME, token)
        response = await client.post(
            "/portal/api/keys",
            json={"name": "laptop", "scopes": ["chat"], "expires_in_days": 30},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_portal_enforces_active_key_ceiling(portal_app):
    app, settings, _factory = portal_app
    settings.portal__max_active_keys = 1
    token, csrf = _session_cookie(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(COOKIE_NAME, token)
        response = await client.post(
            "/portal/api/keys",
            headers={"X-Relay-CSRF": csrf},
            json={"name": "second", "scopes": ["chat"], "expires_in_days": 30},
        )
    assert response.status_code == 409
    assert "limit" in response.json()["detail"].lower()
