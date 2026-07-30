import json

import httpx
import pytest

from app.config import Settings
from app.core.exceptions import MCPProtocolError
from app.mcp.client import MCPStreamableHTTPClient


def _settings():
    return Settings(
        proxy_master_key="test-master-key",
        mcp__enabled=True,
        mcp__servers={"demo": {"url": "https://mcp.test/mcp"}},
    )


def _response(request, payload, status=200, headers=None):
    return httpx.Response(
        status,
        json=payload,
        headers={"content-type": "application/json", **(headers or {})},
        request=request,
    )


@pytest.mark.asyncio
async def test_streamable_http_lifecycle_lists_and_calls_tool():
    seen = []

    async def handler(request):
        seen.append((request.method, request.headers, json.loads(request.content) if request.content else None))
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return _response(
                request,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "demo", "version": "1"},
                    },
                },
                headers={"mcp-session-id": "session-1"},
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202, request=request)
        if body["method"] == "tools/list":
            return _response(
                request,
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    },
                },
            )
        return _response(
            request,
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": body["params"]["arguments"]["text"]}]},
            },
        )

    client = MCPStreamableHTTPClient(_settings(), transport=httpx.MockTransport(handler))
    result = await client.call_tool("demo", "echo", {"text": "hello"})
    assert result["content"][0]["text"] == "hello"
    tool_call_headers = next(headers for method, headers, body in seen if body and body.get("method") == "tools/call")
    assert tool_call_headers["mcp-protocol-version"] == "2025-11-25"
    assert tool_call_headers["mcp-session-id"] == "session-1"
    assert tool_call_headers["mcp-method"] == "tools/call"
    assert seen[-1][0] == "DELETE"


@pytest.mark.asyncio
async def test_tool_arguments_are_validated_before_call():
    calls = []

    async def handler(request):
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "initialize":
            return _response(
                request,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}},
                },
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202, request=request)
        return _response(
            request,
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ]
                },
            },
        )

    client = MCPStreamableHTTPClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(MCPProtocolError, match="schema validation"):
        await client.call_tool("demo", "echo", {})
    assert "tools/call" not in calls
