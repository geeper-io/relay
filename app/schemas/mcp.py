"""Relay MCP gateway API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MCPInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str | None = Field(default=None, max_length=1000)
    approval_token: str | None = None


class MCPApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "denied"]
    reason: str | None = Field(default=None, max_length=1000)


class MCPJSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
