import pytest

from app.config import Settings
from app.core.auth import ResolvedIdentity
from app.core.exceptions import AuthorizationError
from app.mcp.policy import MCPPolicyEngine, validate_argument_constraints


def _settings(**overrides):
    values = {
        "proxy_master_key": "test-master-key",
        "mcp__enabled": True,
        "mcp__servers": {"code": {"url": "https://mcp.test/mcp"}},
        "mcp__active_policy_version": "v1",
        "mcp__policies": {
            "v1": {
                "default_action": "deny",
                "rules": [
                    {"name": "tests", "server": "code", "tool": "test_*", "action": "allow"},
                    {
                        "name": "execution",
                        "server": "code",
                        "tool": "execute",
                        "action": "require_approval",
                        "constraints": {
                            "allowed_values": {"runtime": ["python", "node"]},
                            "denied_patterns": {"command": ["rm\\s+-rf", "sudo"]},
                        },
                    },
                ],
            }
        },
        **overrides,
    }
    return Settings(**values)


def _identity(scopes=None):
    return ResolvedIdentity(user_id="user-1", team_id="team-1", key_id="key-1", scopes=scopes or ["mcp:code:*"])


def test_policy_allows_read_only_tool_and_requires_execution_approval():
    engine = MCPPolicyEngine(_settings())
    assert engine.authorize(_identity(), "code", "test_repo", {}).action == "allow"
    decision = engine.authorize(
        _identity(),
        "code",
        "execute",
        {"runtime": "python", "command": "python app.py"},
    )
    assert decision.action == "require_approval"
    assert decision.policy_version == "v1"


def test_policy_denies_missing_scope_and_dangerous_arguments():
    engine = MCPPolicyEngine(_settings())
    assert engine.authorize(_identity(["chat"]), "code", "test_repo", {}).action == "deny"
    decision = engine.authorize(
        _identity(),
        "code",
        "execute",
        {"runtime": "python", "command": "sudo rm -rf /"},
    )
    assert decision.action == "deny"
    assert "denied pattern" in decision.reason


def test_argument_constraints_support_nested_paths():
    validate_argument_constraints(
        {"target": {"environment": "staging"}},
        {
            "required_arguments": ["target.environment"],
            "allowed_values": {"target.environment": ["staging"]},
        },
    )
    with pytest.raises(AuthorizationError):
        validate_argument_constraints({}, {"required_arguments": ["target.environment"]})
