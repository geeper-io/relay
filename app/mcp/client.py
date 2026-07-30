"""Minimal, security-focused MCP Streamable HTTP client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.config import Settings
from app.core.exceptions import MCPProtocolError

_SUPPORTED_PROTOCOLS = {"2025-11-25", "2025-06-18", "2025-03-26"}


@dataclass(frozen=True)
class MCPServer:
    name: str
    url: str
    description: str
    headers: dict[str, str]


class MCPStreamableHTTPClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def server(self, name: str) -> MCPServer:
        raw = self._settings.mcp_servers.get(name)
        if raw is None or raw.get("enabled", True) is False:
            raise MCPProtocolError(f"MCP server '{name}' is not registered or enabled")
        if str(raw.get("transport", "streamable_http")) != "streamable_http":
            raise MCPProtocolError("Relay only supports remote MCP Streamable HTTP servers")
        url = str(raw.get("url", ""))
        parsed = urlparse(url)
        if not parsed.hostname or parsed.fragment or parsed.username or parsed.password:
            raise MCPProtocolError(f"MCP server '{name}' has an invalid URL")
        if parsed.scheme != "https" and not self._settings.mcp__allow_insecure_http:
            raise MCPProtocolError("MCP server URLs must use HTTPS")
        if parsed.scheme not in {"http", "https"}:
            raise MCPProtocolError("MCP server URL must use HTTP or HTTPS")

        headers: dict[str, str] = {}
        for header, env_name in raw.get("headers_env", {}).items():
            value = os.environ.get(str(env_name), "")
            if not value:
                raise MCPProtocolError(f"Credential environment variable '{env_name}' is not set")
            headers[str(header)] = value
        return MCPServer(name, url, str(raw.get("description", "")), headers)

    async def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        server = self.server(server_name)
        async with self._http_client(server) as http:
            session = await self._initialize(http, server)
            try:
                return await self._list_tools(http, session)
            finally:
                await self._close_session(http, session)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self.server(server_name)
        async with self._http_client(server) as http:
            session = await self._initialize(http, server)
            try:
                tools = await self._list_tools(http, session)
                tool = next((item for item in tools if item.get("name") == tool_name), None)
                if tool is None:
                    raise MCPProtocolError(f"MCP server '{server_name}' does not expose tool '{tool_name}'")
                self._validate_arguments(tool, arguments)
                response = await self._request(
                    http,
                    session,
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                    request_id=1000,
                )
                result = response.get("result")
                if not isinstance(result, dict):
                    raise MCPProtocolError("MCP tools/call response has no object result")
                return result
            finally:
                await self._close_session(http, session)

    def _http_client(self, server: MCPServer) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=server.url,
            headers=server.headers,
            timeout=self._settings.mcp__request_timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        )

    async def _initialize(self, http: httpx.AsyncClient, server: MCPServer) -> dict[str, str]:
        requested = self._settings.mcp__protocol_version
        response = await self._post(
            http,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": requested,
                    "capabilities": {},
                    "clientInfo": {"name": "geeper-relay", "version": "1.0.0"},
                },
            },
            method="initialize",
        )
        payload = self._decode(response, request_id=1)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError(f"MCP server '{server.name}' returned an invalid initialize result")
        version = str(result.get("protocolVersion", ""))
        if version not in _SUPPORTED_PROTOCOLS:
            raise MCPProtocolError(f"MCP server negotiated unsupported protocol version '{version}'")
        if "tools" not in result.get("capabilities", {}):
            raise MCPProtocolError(f"MCP server '{server.name}' does not advertise tool capability")
        session = {"version": version}
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            session["id"] = session_id
        await self._notification(http, session, "notifications/initialized")
        return session

    async def _list_tools(self, http: httpx.AsyncClient, session: dict[str, str]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for request_id in range(2, 22):
            params = {"cursor": cursor} if cursor else {}
            payload = await self._request(http, session, "tools/list", params, request_id=request_id)
            result = payload.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("tools", []), list):
                raise MCPProtocolError("MCP tools/list response is invalid")
            tools.extend(item for item in result.get("tools", []) if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise MCPProtocolError("MCP tools/list exceeded the pagination limit")

    async def _request(
        self,
        http: httpx.AsyncClient,
        session: dict[str, str],
        method: str,
        params: dict[str, Any],
        *,
        request_id: int,
    ) -> dict[str, Any]:
        response = await self._post(
            http,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            method=method,
            name=str(params.get("name", "")) or None,
            session=session,
        )
        return self._decode(response, request_id=request_id)

    async def _notification(self, http: httpx.AsyncClient, session: dict[str, str], method: str) -> None:
        response = await self._post(
            http,
            {"jsonrpc": "2.0", "method": method},
            method=method,
            session=session,
        )
        if response.status_code not in {200, 202, 204}:
            self._raise_http(response)

    async def _post(
        self,
        http: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        method: str,
        name: str | None = None,
        session: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Mcp-Method": method,
        }
        if name:
            headers["Mcp-Name"] = name
        if session:
            headers["MCP-Protocol-Version"] = session["version"]
            if session.get("id"):
                headers["Mcp-Session-Id"] = session["id"]
        try:
            response = await http.post("", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise MCPProtocolError("MCP server request timed out") from exc
        except httpx.HTTPError as exc:
            raise MCPProtocolError(f"MCP server request failed: {exc}") from exc
        if response.is_redirect:
            raise MCPProtocolError("MCP server redirects are not followed")
        if response.status_code >= 400:
            self._raise_http(response)
        return response

    @staticmethod
    def _decode(response: httpx.Response, *, request_id: int) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        try:
            if "text/event-stream" in content_type:
                candidates = []
                for line in response.text.splitlines():
                    if line.startswith("data:") and line[5:].strip():
                        value = json.loads(line[5:].strip())
                        if isinstance(value, dict):
                            candidates.append(value)
                payload = next((item for item in candidates if item.get("id") == request_id), None)
            else:
                payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise MCPProtocolError("MCP server returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MCPProtocolError("MCP server response did not contain the requested JSON-RPC result")
        if payload.get("error") is not None:
            error = payload["error"]
            message = error.get("message", "unknown error") if isinstance(error, dict) else str(error)
            raise MCPProtocolError(f"MCP protocol error: {message}")
        return payload

    @staticmethod
    def _validate_arguments(tool: dict[str, Any], arguments: dict[str, Any]) -> None:
        schema = tool.get("inputSchema") or {"type": "object"}
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
        except SchemaError as exc:
            raise MCPProtocolError("MCP tool published an invalid input schema") from exc
        except ValidationError as exc:
            raise MCPProtocolError(f"MCP tool arguments failed schema validation: {exc.message}") from exc

    @staticmethod
    def _raise_http(response: httpx.Response) -> None:
        raise MCPProtocolError(f"MCP server returned HTTP {response.status_code}")

    @staticmethod
    async def _close_session(http: httpx.AsyncClient, session: dict[str, str]) -> None:
        if not session.get("id"):
            return
        try:
            await http.delete(
                "",
                headers={
                    "MCP-Protocol-Version": session["version"],
                    "Mcp-Session-Id": session["id"],
                },
            )
        except httpx.HTTPError:
            return
