import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api.v1.mcp import invoke_mcp_tool
from app.config import Settings
from app.core.auth import ResolvedIdentity
from app.core.exceptions import ApprovalRequiredError, ContentPolicyError
from app.db.engine import Base
from app.mcp.approvals import arguments_hash, create_approval, decide_approval
from app.mcp.grants import verify_mcp_grant
from app.mcp.responses import persist_responses_mcp_approvals, prepare_responses_mcp_tools
from app.schemas.mcp import MCPInvokeRequest
from app.schemas.responses import ResponsesRequest


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _settings() -> Settings:
    return Settings(
        proxy_master_key="test-master-key",
        mcp__enabled=True,
        mcp__public_url="https://relay.example/mcp",
        mcp__servers={"code": {"url": "https://tools.example/mcp"}},
        mcp__policies={"default": {"default_action": "require_approval"}},
    )


def _identity() -> ResolvedIdentity:
    return ResolvedIdentity(
        user_id="user-1",
        team_id="team-1",
        key_id="key-1",
        scopes=["responses", "mcp:code:*"],
    )


@pytest.mark.asyncio
async def test_initial_response_injects_native_relay_mcp_tool(monkeypatch, db_factory):
    async def fake_list_tools(server, identity, settings):
        return {
            "items": [
                {
                    "name": "execute",
                    "inputSchema": {"type": "object"},
                    "relay": {"authorization": "require_approval", "policy_version": "default"},
                }
            ]
        }

    monkeypatch.setattr("app.mcp.responses.list_mcp_tools", fake_list_tools)
    request = ResponsesRequest(input="run tests", store=True, relay_mcp_servers=["code"])
    async with db_factory() as db:
        tools = await prepare_responses_mcp_tools(request, _identity(), _settings(), db)
    relay = tools[-1]
    assert relay["type"] == "mcp"
    assert relay["server_label"] == "relay"
    assert relay["allowed_tools"] == ["code__execute"]
    assert relay["require_approval"] == "always"
    claims = verify_mcp_grant(relay["authorization"], _settings())
    assert claims["scopes"] == ["mcp:code:*"]
    assert "approval_id" not in claims


@pytest.mark.asyncio
async def test_response_approval_requires_admin_then_issues_exact_grant(db_factory):
    settings = _settings()
    identity = _identity()
    request = ResponsesRequest(input="run tests", store=True, relay_mcp_servers=["code"])
    payload = {
        "id": "resp-1",
        "output": [
            {
                "id": "mcpr-1",
                "type": "mcp_approval_request",
                "server_label": "relay",
                "name": "code__execute",
                "arguments": '{"command":"pytest"}',
            }
        ],
    }
    async with db_factory() as db:
        await persist_responses_mcp_approvals(payload, request, identity, settings, db, "request-1")
        approval_id = payload["output"][0]["relay_approval"]["id"]
        continuation = ResponsesRequest(
            input=[{"type": "mcp_approval_response", "approval_request_id": "mcpr-1", "approve": True}],
            previous_response_id="resp-1",
            store=True,
        )
        with pytest.raises(ApprovalRequiredError, match=approval_id):
            await prepare_responses_mcp_tools(continuation, identity, settings, db)
        await decide_approval(
            db,
            approval_id=approval_id,
            decision="approved",
            actor="admin",
            reason="approved for test",
            request_id="request-2",
        )
        tools = await prepare_responses_mcp_tools(continuation, identity, settings, db)
    relay = tools[-1]
    claims = verify_mcp_grant(relay["authorization"], settings)
    assert relay["require_approval"] == "never"
    assert claims["approval_id"] == approval_id
    assert claims["server"] == "code"
    assert claims["tool"] == "execute"


@pytest.mark.asyncio
async def test_response_mcp_rejects_unsafe_continuation_mode(db_factory):
    request = ResponsesRequest(input="run tests", relay_mcp_servers=["code"])
    async with db_factory() as db:
        with pytest.raises(ContentPolicyError, match="store=true"):
            await prepare_responses_mcp_tools(request, _identity(), _settings(), db)


@pytest.mark.asyncio
async def test_exact_delegated_grant_consumes_approval_at_tool_call(monkeypatch, db_factory):
    class FakeClient:
        def __init__(self, settings):
            pass

        async def call_tool(self, server, tool, arguments):
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    class FakeScrubber:
        def scrub_text_values(self, values):
            return values, {}, 0

    monkeypatch.setattr("app.api.v1.mcp.MCPStreamableHTTPClient", FakeClient)
    settings = _settings()
    arguments = {"command": "pytest"}
    async with db_factory() as db:
        approval = await create_approval(
            db,
            user_id="user-1",
            team_id="team-1",
            server="code",
            tool="execute",
            arguments=arguments,
            purpose="Run tests",
            policy_version="default",
            ttl_seconds=300,
            request_id="request-1",
        )
        await decide_approval(
            db,
            approval_id=approval.id,
            decision="approved",
            actor="admin",
            reason=None,
            request_id="request-2",
        )
        identity = ResolvedIdentity(
            user_id="user-1",
            team_id="team-1",
            key_id=None,
            scopes=["mcp:code:execute"],
            mcp_grant_approval_id=approval.id,
            mcp_grant_server="code",
            mcp_grant_tool="execute",
            mcp_grant_arguments_hash=arguments_hash(arguments),
        )
        result = await invoke_mcp_tool(
            "code",
            "execute",
            MCPInvokeRequest(arguments=arguments),
            Request({"type": "http", "headers": []}),
            identity,
            settings,
            FakeScrubber(),
            db,
        )
        await db.refresh(approval)
    assert result["approval_id"] == approval.id
    assert approval.status == "consumed"
