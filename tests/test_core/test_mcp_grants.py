import time

import pytest

from app.config import Settings
from app.core.exceptions import AuthenticationError
from app.mcp.grants import issue_mcp_grant, verify_mcp_grant


def test_delegated_mcp_grant_is_signed_and_exact_call_bound():
    settings = Settings(proxy_master_key="test-master-key", mcp__delegated_grant_ttl_seconds=60)
    token = issue_mcp_grant(
        settings,
        user_id="user-1",
        team_id="team-1",
        scopes=["mcp:code:execute"],
        approval_id="approval-1",
        server="code",
        tool="execute",
        arguments_hash="abc123",
    )
    assert token.startswith("grmcp-")
    claims = verify_mcp_grant(token, settings)
    assert claims["approval_id"] == "approval-1"
    assert claims["server"] == "code"
    assert claims["tool"] == "execute"
    assert claims["arguments_hash"] == "abc123"
    assert claims["exp"] > int(time.time())


def test_delegated_mcp_grant_rejects_tampering():
    settings = Settings(proxy_master_key="test-master-key")
    token = issue_mcp_grant(settings, user_id="user-1", team_id=None, scopes=["mcp:code:*"])
    with pytest.raises(AuthenticationError, match="Invalid"):
        verify_mcp_grant(token[:-1] + ("A" if token[-1] != "A" else "B"), settings)
