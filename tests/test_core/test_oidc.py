import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.auth as auth_module
from app.admin.session import COOKIE_NAME, verify_admin_session
from app.api.auth import _identity_claims, _make_state, _provider_config, _state_flow, _verify_state
from app.config import Settings, get_settings
from app.db.engine import Base, get_db
from app.db.models import AdminIdentity, ApiKey
from app.portal.session import COOKIE_NAME as PORTAL_COOKIE_NAME
from app.portal.session import verify_portal_session


def _settings(**overrides) -> Settings:
    values = {
        "oidc__issuer_url": "https://id.example.com",
        "oidc__client_id": "relay",
        "oidc__client_secret": "secret",
        "oidc__allowed_email_domains": ["example.com"],
    }
    values.update(overrides)
    return Settings(**values)


def test_signed_oidc_state_preserves_admin_flow_and_rejects_tampering():
    state = _make_state("state-secret", flow="admin")
    assert _verify_state(state, "state-secret")
    assert _state_flow(state) == "admin"
    assert not _verify_state(f"{state}x", "state-secret")


def test_default_oidc_state_targets_portal_and_legacy_key_flow_is_explicit():
    assert _state_flow(_make_state("state-secret", flow="portal")) == "portal"
    assert _state_flow(_make_state("state-secret", flow="api_key")) == "api_key"


@pytest.mark.asyncio
async def test_admin_oidc_callback_records_identity_and_issues_role_session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = _settings(
        proxy_master_key="admin-oidc-master-key",
        admin__enabled=True,
        admin__oidc_enabled=True,
        admin__bootstrap_emails=["alice@example.com"],
        admin__secure_cookies=False,
        auth_base_url="http://test",
    )
    app = FastAPI()
    app.include_router(auth_module.router)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db

    def provider_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://id.example.com",
                    "authorization_endpoint": "https://id.example.com/authorize",
                    "token_endpoint": "https://id.example.com/token",
                    "userinfo_endpoint": "https://id.example.com/userinfo",
                },
            )
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path == "/userinfo":
            return httpx.Response(
                200,
                json={
                    "sub": "alice-subject",
                    "email": "alice@example.com",
                    "name": "Alice",
                    "email_verified": True,
                },
            )
        return httpx.Response(404)

    original_client = httpx.AsyncClient
    provider_transport = httpx.MockTransport(provider_handler)

    def provider_client(*_args, **_kwargs):
        return original_client(transport=provider_transport)

    state = _make_state(settings.proxy_master_key, flow="admin")
    async with original_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        monkeypatch.setattr(auth_module.httpx, "AsyncClient", provider_client)
        response = await client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    session = verify_admin_session(response.cookies[COOKIE_NAME], settings)
    assert session.role == "admin"
    assert session.email == "alice@example.com"
    async with factory() as db:
        identity = await db.get(AdminIdentity, session.user_id)
        assert identity is not None
        assert identity.display_name == "Alice"
    await engine.dispose()


@pytest.mark.asyncio
async def test_oidc_callback_issues_portal_session_without_implicitly_creating_key(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = _settings(
        proxy_master_key="portal-oidc-master-key",
        portal__enabled=True,
        portal__secure_cookies=False,
        auth_base_url="http://test",
    )
    app = FastAPI()
    app.include_router(auth_module.router)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db

    def provider_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://id.example.com",
                    "authorization_endpoint": "https://id.example.com/authorize",
                    "token_endpoint": "https://id.example.com/token",
                    "userinfo_endpoint": "https://id.example.com/userinfo",
                },
            )
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path == "/userinfo":
            return httpx.Response(
                200,
                json={
                    "sub": "alice-subject",
                    "email": "alice@example.com",
                    "name": "Alice",
                    "email_verified": True,
                },
            )
        return httpx.Response(404)

    original_client = httpx.AsyncClient
    provider_transport = httpx.MockTransport(provider_handler)

    def provider_client(*_args, **_kwargs):
        return original_client(transport=provider_transport)

    state = _make_state(settings.proxy_master_key, flow="portal")
    async with original_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        monkeypatch.setattr(auth_module.httpx, "AsyncClient", provider_client)
        response = await client.get(
            "/auth/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/portal"
    session = verify_portal_session(response.cookies[PORTAL_COOKIE_NAME], settings)
    assert session.user_id
    assert session.email == "alice@example.com"
    async with factory() as db:
        assert await db.get(ApiKey, "missing") is None
        assert not list((await db.execute(ApiKey.__table__.select())).all())
    await engine.dispose()


@pytest.mark.asyncio
async def test_oidc_discovery_requires_matching_issuer_and_endpoints():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://id.example.com/.well-known/openid-configuration"
        return httpx.Response(
            200,
            json={
                "issuer": "https://id.example.com",
                "authorization_endpoint": "https://id.example.com/authorize",
                "token_endpoint": "https://id.example.com/token",
                "userinfo_endpoint": "https://id.example.com/userinfo",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = await _provider_config(_settings(), client)
    assert config["token_endpoint"] == "https://id.example.com/token"


@pytest.mark.asyncio
async def test_oidc_discovery_rejects_issuer_mismatch():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "issuer": "https://attacker.example",
                "authorization_endpoint": "https://attacker.example/authorize",
                "token_endpoint": "https://attacker.example/token",
                "userinfo_endpoint": "https://attacker.example/userinfo",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException, match="issuer mismatch"):
            await _provider_config(_settings(), client)


def test_oidc_claim_mapping_and_domain_allowlist():
    subject, email, name = _identity_claims(
        _settings(),
        {"sub": "user-1", "email": "Alice@Example.com", "name": "Alice", "email_verified": True},
    )
    assert (subject, email, name) == ("user-1", "Alice@Example.com", "Alice")


def test_oidc_rejects_unverified_or_unapproved_email():
    with pytest.raises(HTTPException, match="verified email"):
        _identity_claims(_settings(), {"sub": "user-1", "email": "alice@example.com"})
    with pytest.raises(HTTPException, match="domain"):
        _identity_claims(
            _settings(),
            {"sub": "user-1", "email": "alice@outside.test", "email_verified": True},
        )
