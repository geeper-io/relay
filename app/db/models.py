from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(Integer, default=500_000)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=5_000_000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list[User]] = relationship("User", back_populates="team")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("teams.id"), nullable=True)
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Team | None] = relationship("Team", back_populates="users")
    api_keys: Mapped[list[ApiKey]] = relationship("ApiKey", back_populates="user")


class AdminRoleAssignment(Base):
    __tablename__ = "admin_role_assignments"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (Index("ix_admin_role_assignments_role", "role"),)


class AdminIdentity(Base):
    __tablename__ = "admin_identities"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (Index("ix_admin_identities_email", "email"),)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship("User", back_populates="api_keys")

    __table_args__ = (Index("ix_api_keys_key_hash", "key_hash"),)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    was_rag_used: Mapped[bool] = mapped_column(Boolean, default=False)
    pii_entities_scrubbed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_usage_user_created", "user_id", "created_at"),
        Index("ix_usage_team_created", "team_id", "created_at"),
        Index("ix_usage_model_created", "model", "created_at"),
        Index("ix_usage_request_id", "request_id"),
        # date-range-first scans (leaderboards, time-series aggregations)
        Index("ix_usage_created_at", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MCPApproval(Base):
    __tablename__ = "mcp_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    server_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    purpose: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_mcp_approvals_status_expires", "status", "expires_at"),
        Index("ix_mcp_approvals_user_requested", "user_id", "requested_at"),
    )


class MCPApprovalGrantOffer(Base):
    """Policy-defined standing grant offered when an approval is accepted."""

    __tablename__ = "mcp_approval_grant_offers"

    approval_id: Mapped[str] = mapped_column(String(36), ForeignKey("mcp_approvals.id"), primary_key=True)
    template: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MCPApprovalGrant(Base):
    """Durable, scoped authorization for repeated MCP tool calls."""

    __tablename__ = "mcp_approval_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    server_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_approval_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("mcp_approvals.id"), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_mcp_grants_subject_policy_server_expiry",
            "subject_type",
            "subject_id",
            "policy_version",
            "server_name",
            "expires_at",
        ),
        Index("ix_mcp_grants_source_approval", "source_approval_id"),
        Index("ix_mcp_grants_workflow", "workflow_id"),
    )


class MCPPolicyVersion(Base):
    """Immutable database-managed MCP policy document."""

    __tablename__ = "mcp_policy_versions"

    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    base_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_mcp_policy_versions_status_created", "status", "created_at"),)


class MCPPolicyState(Base):
    """Singleton pointer to the database-managed active MCP policy."""

    __tablename__ = "mcp_policy_state"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    active_version: Mapped[str] = mapped_column(String(100), ForeignKey("mcp_policy_versions.version"), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MCPPolicyActivation(Base):
    """Append-only activation and rollback history."""

    __tablename__ = "mcp_policy_activations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(100), ForeignKey("mcp_policy_versions.version"), nullable=False)
    previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_mcp_policy_activations_created", "created_at"),)


class MCPResponseApproval(Base):
    """Links an OpenAI MCP approval item to Relay's durable approval."""

    __tablename__ = "mcp_response_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider_response_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_approval_request_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    approval_id: Mapped[str] = mapped_column(String(36), ForeignKey("mcp_approvals.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_mcp_response_approvals_response", "provider_response_id"),
        Index("ix_mcp_response_approvals_user", "user_id"),
    )
