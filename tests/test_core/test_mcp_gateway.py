import pytest

from app.api.mcp_gateway import _approval_aware_schema, gateway_tool_name, parse_gateway_tool_name


def test_gateway_namespaces_remote_tools():
    assert gateway_tool_name("github", "create_issue") == "github__create_issue"
    assert parse_gateway_tool_name("github__create_issue") == ("github", "create_issue")
    with pytest.raises(ValueError):
        parse_gateway_tool_name("not_namespaced")
    with pytest.raises(ValueError):
        gateway_tool_name("bad__server", "tool")


def test_gateway_schema_adds_relay_approval_fields():
    schema = _approval_aware_schema(
        {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
            "additionalProperties": False,
        }
    )
    assert schema["required"] == ["repo"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["_relay_approval_token"]["type"] == "string"
    assert schema["properties"]["_relay_purpose"]["maxLength"] == 1000
