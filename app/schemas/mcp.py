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


class MCPApprovalGrantCreateRequest(BaseModel):
    subject_type: Literal["user", "team"]
    subject_id: str = Field(min_length=1, max_length=255)
    server: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=255)
    constraints: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    max_calls: int = Field(default=20, ge=1, le=10_000)
    reason: str | None = Field(default=None, max_length=1000)
    workflow_id: str | None = Field(default=None, max_length=100)


class MCPPolicyDocumentRequest(BaseModel):
    document: dict[str, Any]


class MCPPolicyDraftCreateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    document: dict[str, Any]
    base_version: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class MCPPolicyActivationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class MCPPolicySimulationRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=255)
    team_id: str | None = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=lambda: ["mcp:*"])
    server: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=255)
    arguments: dict[str, Any] = Field(default_factory=dict)
    version: str | None = Field(default=None, max_length=100)
    document: dict[str, Any] | None = None


class MCPJSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
