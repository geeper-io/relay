"""Add MCP approvals and Responses continuation mappings."""

import sqlalchemy as sa
from alembic import op

revision = "0002_mcp"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("server_name", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(length=1000), nullable=True),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decision_reason", sa.String(length=1000), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_approvals_status_expires", "mcp_approvals", ["status", "expires_at"], unique=False)
    op.create_index(
        "ix_mcp_approvals_user_requested",
        "mcp_approvals",
        ["user_id", "requested_at"],
        unique=False,
    )
    op.create_table(
        "mcp_response_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_response_id", sa.String(length=255), nullable=False),
        sa.Column("provider_approval_request_id", sa.String(length=255), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["approval_id"], ["mcp_approvals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_approval_request_id"),
    )
    op.create_index(
        "ix_mcp_response_approvals_response",
        "mcp_response_approvals",
        ["provider_response_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_response_approvals_user",
        "mcp_response_approvals",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_response_approvals_user", table_name="mcp_response_approvals")
    op.drop_index("ix_mcp_response_approvals_response", table_name="mcp_response_approvals")
    op.drop_table("mcp_response_approvals")
    op.drop_index("ix_mcp_approvals_user_requested", table_name="mcp_approvals")
    op.drop_index("ix_mcp_approvals_status_expires", table_name="mcp_approvals")
    op.drop_table("mcp_approvals")
