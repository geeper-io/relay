"""MCP-compatible front door over Relay's authorized tool registry."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.mcp import _require_mcp_access, approval_status, invoke_mcp_tool, list_mcp_tools
from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, resolve_identity
from app.core.exceptions import AuthorizationError, ProxyError
from app.db.engine import get_db
from app.pii.scrubber import PIIScrubber, get_scrubber
from app.schemas.mcp import MCPInvokeRequest, MCPJSONRPCRequest

router = APIRouter(tags=["mcp-protocol"])
_APPROVAL_STATUS_TOOL = "relay_approval_status"
_APPROVAL_TOKEN_ARGUMENT = "_relay_approval_token"
_PURPOSE_ARGUMENT = "_relay_purpose"
_SUPPORTED_PROTOCOLS = {"2025-11-25", "2025-06-18", "2025-03-26"}


@router.post("/mcp")
async def mcp_gateway(
    message: MCPJSONRPCRequest,
    request: Request,
    identity: ResolvedIdentity = Depends(resolve_identity),
    settings: Settings = Depends(get_settings),
    scrubber: PIIScrubber = Depends(get_scrubber),
    db: AsyncSession = Depends(get_db),
):
    _validate_origin(request, settings)
    _require_mcp_access(identity, settings)
    if message.method != "initialize":
        version = request.headers.get("mcp-protocol-version", settings.mcp__protocol_version)
        if version not in _SUPPORTED_PROTOCOLS:
            return _jsonrpc_error(message.id, -32600, f"Unsupported MCP protocol version '{version}'")

    if message.method == "initialize":
        return _jsonrpc_result(
            message.id,
            {
                "protocolVersion": settings.mcp__protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "geeper-relay",
                    "title": "Geeper Relay MCP Gateway",
                    "version": "1.0.0",
                    "description": "Policy and approval gateway for enterprise MCP tools",
                },
            },
        )
    if message.method == "notifications/initialized":
        return Response(status_code=202)
    if message.method == "ping":
        return _jsonrpc_result(message.id, {})
    if message.method == "tools/list":
        try:
            tools = await _gateway_tools(identity, settings, db)
            return _jsonrpc_result(message.id, {"tools": tools})
        except ProxyError as exc:
            return _jsonrpc_error(message.id, -32000, exc.message)
    if message.method == "tools/call":
        return await _gateway_tool_call(message, request, identity, settings, scrubber, db)
    return _jsonrpc_error(message.id, -32601, f"Method not found: {message.method}")


async def _gateway_tools(
    identity: ResolvedIdentity,
    settings: Settings,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    tools = [_approval_status_definition()]
    for server_name, server in settings.mcp_servers.items():
        if server.get("enabled", True) is False:
            continue
        response = await list_mcp_tools(server_name, identity, settings, db)
        for tool in response["items"]:
            published = dict(tool)
            published["name"] = gateway_tool_name(server_name, str(tool["name"]))
            published["inputSchema"] = _approval_aware_schema(tool.get("inputSchema"))
            published["description"] = (
                f"[{server_name}] {tool.get('description', '')} Relay authorization: {tool['relay']['authorization']}."
            ).strip()
            tools.append(published)
    return tools


async def _gateway_tool_call(
    message: MCPJSONRPCRequest,
    request: Request,
    identity: ResolvedIdentity,
    settings: Settings,
    scrubber: PIIScrubber,
    db: AsyncSession,
) -> JSONResponse:
    name = str(message.params.get("name", ""))
    arguments = message.params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _jsonrpc_error(message.id, -32602, "Tool arguments must be an object")
    if name == _APPROVAL_STATUS_TOOL:
        approval_id = str(arguments.get("approval_id", ""))
        try:
            result = await approval_status(approval_id, identity, settings, db)
            return _jsonrpc_result(message.id, _tool_result(result))
        except ProxyError as exc:
            return _jsonrpc_result(message.id, _tool_error(exc.message))
        except HTTPException as exc:
            return _jsonrpc_result(message.id, _tool_error(str(exc.detail)))

    try:
        server_name, tool_name = parse_gateway_tool_name(name)
    except ValueError as exc:
        return _jsonrpc_result(message.id, _tool_error(str(exc)))

    forwarded = dict(arguments)
    approval_token = forwarded.pop(_APPROVAL_TOKEN_ARGUMENT, None)
    purpose = forwarded.pop(_PURPOSE_ARGUMENT, None)
    try:
        result = await invoke_mcp_tool(
            server_name,
            tool_name,
            MCPInvokeRequest(arguments=forwarded, purpose=purpose, approval_token=approval_token),
            request,
            identity,
            settings,
            scrubber,
            db,
        )
        if isinstance(result, JSONResponse):
            payload = json.loads(result.body)
            return _jsonrpc_result(
                message.id,
                _tool_error(
                    "Human approval is required before this tool can run.",
                    structured=payload,
                ),
            )
        return _jsonrpc_result(message.id, result["result"])
    except ProxyError as exc:
        return _jsonrpc_result(message.id, _tool_error(exc.message))


def gateway_tool_name(server: str, tool: str) -> str:
    if "__" in server:
        raise ValueError("MCP server names cannot contain '__'")
    return f"{server}__{tool}"


def parse_gateway_tool_name(name: str) -> tuple[str, str]:
    server, separator, tool = name.partition("__")
    if not separator or not server or not tool:
        raise ValueError("Relay MCP tool names must use '<server>__<tool>'")
    return server, tool


def _approval_aware_schema(schema: Any) -> dict[str, Any]:
    result = dict(schema) if isinstance(schema, dict) else {"type": "object"}
    properties = dict(result.get("properties", {}))
    properties[_APPROVAL_TOKEN_ARGUMENT] = {
        "type": "string",
        "description": "One-time Relay token returned by relay_approval_status after human approval.",
    }
    properties[_PURPOSE_ARGUMENT] = {
        "type": "string",
        "maxLength": 1000,
        "description": "Human-readable reason for invoking this tool, shown to approvers.",
    }
    result["type"] = "object"
    result["properties"] = properties
    return result


def _approval_status_definition() -> dict[str, Any]:
    return {
        "name": _APPROVAL_STATUS_TOOL,
        "title": "Check Relay approval",
        "description": "Check a pending MCP approval and obtain its one-time token after approval.",
        "inputSchema": {
            "type": "object",
            "properties": {"approval_id": {"type": "string"}},
            "required": ["approval_id"],
            "additionalProperties": False,
        },
    }


def _tool_result(value: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, default=str)}],
        "structuredContent": value,
        "isError": False,
    }


def _tool_error(message: str, *, structured: Any = None) -> dict[str, Any]:
    result = {"content": [{"type": "text", "text": message}], "isError": True}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _validate_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.mcp__allowed_origins:
        raise AuthorizationError("Origin is not allowed for the MCP endpoint")


def _jsonrpc_result(request_id: str | int | None, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: str | int | None, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
