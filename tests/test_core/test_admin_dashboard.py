import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.roles import record_admin_identity, set_admin_role
from app.admin.router import router
from app.admin.session import COOKIE_NAME, issue_admin_session, verify_admin_session
from app.config import Settings, get_settings
from app.core.exceptions import ProxyError, proxy_exception_handler
from app.db.engine import Base, get_db
from app.db.models import ApiKey, Team, UsageRecord, User
from app.mcp.approvals import create_approval


@pytest.fixture
async def dashboard_app():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        proxy_master_key="dashboard-test-master-key",
        admin__enabled=True,
        admin__secure_cookies=False,
        admin__session_ttl_seconds=300,
        mcp__enabled=True,
        mcp__servers={"code": {"url": "https://tools.example/mcp"}},
        mcp__policies={"default": {"default_action": "require_approval"}},
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ProxyError, proxy_exception_handler)
    app.dependency_overrides[get_settings] = lambda: settings

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield app, factory, settings
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_login_queue_and_approval_decision(dashboard_app, monkeypatch):
    class FakeMCPClient:
        def __init__(self, settings):
            pass

        async def list_tools(self, server):
            return [{"name": "execute", "description": "Run a sandbox command", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("app.admin.router.MCPStreamableHTTPClient", FakeMCPClient)
    app, factory, settings = dashboard_app
    async with factory() as db:
        db.add(User(id="operator-1", external_id="oidc:operator-1"))
        await db.commit()
        await record_admin_identity(
            db,
            user_id="operator-1",
            email="operator@example.com",
            display_name="Operator",
        )
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id="team-1",
            server="code",
            tool="execute",
            arguments={"command": "pytest"},
            purpose="Run the test suite",
            policy_version="v1",
            ttl_seconds=300,
            request_id="request-1",
            grant_template={"subject": "user", "ttl_seconds": 3600, "max_calls": 3},
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post("/admin/login", data={"master_key": "wrong"})
        assert rejected.status_code == 401
        assert COOKIE_NAME not in client.cookies

        login = await client.post(
            "/admin/login",
            data={"master_key": settings.proxy_master_key},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/admin"
        cookie = client.cookies[COOKIE_NAME]
        session = verify_admin_session(cookie, settings)
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]

        page = await client.get("/admin")
        assert page.status_code == 200
        assert "Approval inbox" in page.text
        assert "Relay at a glance" in page.text
        assert "User directory" in page.text
        assert "Approval grants" in page.text
        assert "Policies &amp; servers" in page.text
        assert session.csrf_token in page.text
        assert page.headers["x-frame-options"] == "DENY"

        servers = await client.get("/admin/api/mcp/servers")
        assert servers.status_code == 200
        assert servers.json()["items"][0]["status"] == "healthy"
        assert servers.json()["items"][0]["tool_count"] == 1

        policies = await client.get("/admin/api/mcp/policies")
        assert policies.status_code == 200
        assert policies.json()["active"] == {
            "version": "default",
            "source": "configuration",
            "document": {"default_action": "require_approval"},
        }
        validation = await client.post(
            "/admin/api/mcp/policies/validate",
            json={
                "document": {
                    "default_action": "deny",
                    "rules": [{"name": "allow-execute", "server": "code", "tool": "execute", "action": "allow"}],
                }
            },
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        draft_document = {
            "default_action": "deny",
            "rules": [{"name": "allow-execute", "server": "code", "tool": "execute", "action": "allow"}],
        }
        draft = await client.post(
            "/admin/api/mcp/policies/drafts",
            headers={"X-CSRF-Token": session.csrf_token},
            json={
                "version": "dashboard-test-v2",
                "base_version": "default",
                "reason": "Allow reviewed execution",
                "document": draft_document,
            },
        )
        assert draft.status_code == 201
        activated_policy = await client.post(
            "/admin/api/mcp/policies/dashboard-test-v2/activate",
            headers={"X-CSRF-Token": session.csrf_token},
            json={"reason": "Policy simulation passed"},
        )
        assert activated_policy.status_code == 200
        simulation = await client.post(
            "/admin/api/mcp/policies/simulate",
            json={
                "user_id": "operator-1",
                "scopes": ["mcp:code:*"],
                "server": "code",
                "tool": "execute",
                "arguments": {},
            },
        )
        assert simulation.status_code == 200
        assert simulation.json()["action"] == "allow"
        assert simulation.json()["policy_version"] == "dashboard-test-v2"

        created_grant = await client.post(
            "/admin/api/mcp/grants",
            headers={"X-CSRF-Token": session.csrf_token},
            json={
                "subject_type": "user",
                "subject_id": "operator-1",
                "server": "code",
                "tool": "execute*",
                "constraints": {"allowed_values": {"runtime": ["python"]}},
                "ttl_seconds": 3600,
                "max_calls": 5,
                "reason": "Repeated test execution",
                "workflow_id": "ci-demo",
            },
        )
        assert created_grant.status_code == 201
        grant_id = created_grant.json()["id"]
        grants = await client.get("/admin/api/mcp/grants")
        assert grants.status_code == 200
        policies = await client.get("/admin/api/mcp/policies")
        assert policies.status_code == 200
        assert grants.json()["items"][0]["status"] == "active"
        revoked = await client.delete(
            f"/admin/api/mcp/grants/{grant_id}",
            headers={"X-CSRF-Token": session.csrf_token},
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        queue = await client.get("/admin/api/mcp/approvals?status=")
        assert queue.status_code == 200
        assert queue.json()["items"][0]["id"] == approval.id
        assert queue.json()["items"][0]["grant_offer"]["max_calls"] == 3

        no_csrf = await client.post(
            f"/admin/api/mcp/approvals/{approval.id}/decision",
            json={"decision": "approved", "reason": "Safe"},
        )
        assert no_csrf.status_code == 403

        decision = await client.post(
            f"/admin/api/mcp/approvals/{approval.id}/decision",
            headers={"X-CSRF-Token": session.csrf_token},
            json={"decision": "approved", "reason": "Limited to tests"},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert decision.json()["decided_by"] == "master-key"
        post_approval_grants = await client.get("/admin/api/mcp/grants")
        offered_grant = next(
            item for item in post_approval_grants.json()["items"] if item["source_approval_id"] == approval.id
        )
        assert offered_grant["max_calls"] == 3

        role = await client.put(
            "/admin/api/admin-roles/operator-1",
            headers={"X-CSRF-Token": session.csrf_token},
            json={"role": "approver"},
        )
        assert role.status_code == 200
        assert role.json()["role"] == "approver"
        assignments = await client.get("/admin/api/admin-roles")
        assert assignments.status_code == 200
        assert assignments.json()["items"][0]["user_id"] == "operator-1"
        identities = await client.get("/admin/api/admin-identities")
        assert identities.status_code == 200
        assert identities.json()["items"][0]["email"] == "operator@example.com"


@pytest.mark.asyncio
async def test_dashboard_overview_user_list_and_detail_are_read_only(dashboard_app):
    app, factory, settings = dashboard_app
    async with factory() as db:
        db.add(Team(id="team-1", name="Platform", tpm_limit=750_000, daily_token_limit=7_500_000))
        db.add_all(
            [
                User(
                    id="user-1",
                    external_id="alice@example.com",
                    team_id="team-1",
                    rpm_limit=120,
                    tpm_limit=200_000,
                ),
                User(id="user-2", external_id="disabled@example.com", is_active=False),
            ]
        )
        db.add(
            ApiKey(
                id="key-1",
                key_hash="a" * 64,
                key_prefix="gr-alice",
                user_id="user-1",
                name="workstation",
                scopes=["chat", "responses"],
            )
        )
        db.add_all(
            [
                UsageRecord(
                    id="usage-1",
                    user_id="user-1",
                    team_id="team-1",
                    model="gpt-4o",
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    latency_ms=220,
                    request_id="request-usage-1",
                    cost_usd=0.12,
                    cache_hit=True,
                    status="success",
                ),
                UsageRecord(
                    id="usage-2",
                    user_id="user-1",
                    team_id="team-1",
                    model="gpt-4o-mini",
                    prompt_tokens=40,
                    completion_tokens=10,
                    total_tokens=50,
                    latency_ms=400,
                    request_id="request-usage-2",
                    cost_usd=0.03,
                    status="error",
                    error_code="upstream_error",
                ),
            ]
        )
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/admin/login", data={"master_key": settings.proxy_master_key})
        overview = await client.get("/admin/api/overview?days=30")
        assert overview.status_code == 200
        assert overview.json()["totals"] == {
            "requests": 2,
            "total_tokens": 200,
            "cost_usd": 0.15,
            "errors": 1,
            "error_rate": 0.5,
            "cache_hits": 1,
            "cache_hit_rate": 0.5,
            "avg_latency_ms": 310.0,
        }
        assert overview.json()["users"] == {"total": 2, "enabled": 1, "active_in_window": 1}
        assert overview.json()["top_models"][0]["model"] == "gpt-4o"

        users = await client.get("/admin/api/users?q=alice&days=30")
        assert users.status_code == 200
        assert users.json()["total"] == 1
        assert users.json()["items"][0]["limits"]["rpm"] == 120
        assert users.json()["items"][0]["team_name"] == "Platform"
        assert users.json()["items"][0]["keys"] == {
            "active": 1,
            "total": 1,
            "last_used_at": None,
        }

        detail = await client.get("/admin/api/users/user-1?days=30")
        assert detail.status_code == 200
        assert [item["model"] for item in detail.json()["models"]] == ["gpt-4o", "gpt-4o-mini"]
        assert detail.json()["api_keys"][0]["key_prefix"] == "gr-alice"
        assert "key_hash" not in detail.json()["api_keys"][0]
        assert "key" not in detail.json()["api_keys"][0]

        missing = await client.get("/admin/api/users/missing")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_viewer_can_read_but_cannot_decide_and_role_changes_invalidate_session(dashboard_app):
    app, factory, settings = dashboard_app
    async with factory() as db:
        db.add(User(id="viewer-1", external_id="oidc:viewer-1"))
        await db.commit()
        await set_admin_role(
            db,
            user_id="viewer-1",
            role="viewer",
            actor="master-key",
            request_id="role-request-1",
        )
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id="team-1",
            server="code",
            tool="execute",
            arguments={"command": "pytest"},
            purpose="Run tests",
            policy_version="v1",
            ttl_seconds=300,
            request_id="request-viewer",
        )

    token, session = issue_admin_session(
        settings,
        role="viewer",
        actor="oidc:viewer-1",
        user_id="viewer-1",
        email="viewer@example.com",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(COOKIE_NAME, token, path="/admin")
        queue = await client.get("/admin/api/mcp/approvals")
        assert queue.status_code == 200
        overview = await client.get("/admin/api/overview")
        assert overview.status_code == 200
        grants = await client.get("/admin/api/mcp/grants")
        assert grants.status_code == 200

        create_grant = await client.post(
            "/admin/api/mcp/grants",
            headers={"X-CSRF-Token": session.csrf_token},
            json={
                "subject_type": "user",
                "subject_id": "viewer-1",
                "server": "code",
                "tool": "execute",
                "constraints": {},
                "ttl_seconds": 3600,
                "max_calls": 1,
                "reason": "Should not be accepted",
            },
        )
        assert create_grant.status_code == 403
        create_policy = await client.post(
            "/admin/api/mcp/policies/drafts",
            headers={"X-CSRF-Token": session.csrf_token},
            json={
                "version": "viewer-policy",
                "document": {"default_action": "deny", "rules": []},
                "reason": "Should not be accepted",
            },
        )
        assert create_policy.status_code == 403

        decision = await client.post(
            f"/admin/api/mcp/approvals/{approval.id}/decision",
            headers={"X-CSRF-Token": session.csrf_token},
            json={"decision": "approved", "reason": "Looks safe"},
        )
        assert decision.status_code == 403
        assert decision.json()["detail"] == "Approver role required"
        roles = await client.get("/admin/api/admin-roles")
        assert roles.status_code == 403
        assert roles.json()["detail"] == "Admin role required"

        async with factory() as db:
            await set_admin_role(
                db,
                user_id="viewer-1",
                role="approver",
                actor="master-key",
                request_id="role-request-2",
            )
        stale = await client.get("/admin/api/mcp/approvals")
        assert stale.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_is_hidden_when_disabled(dashboard_app):
    app, _factory, settings = dashboard_app
    settings.admin__enabled = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/login")
    assert response.status_code == 404
