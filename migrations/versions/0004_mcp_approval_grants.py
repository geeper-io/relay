"""Add bounded standing grants for repeated MCP calls."""

import sqlalchemy as sa
from alembic import op

revision = "0004_grants"
down_revision = "0003_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_approval_grant_offers",
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("template", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["approval_id"], ["mcp_approvals.id"]),
        sa.PrimaryKeyConstraint("approval_id"),
    )
    op.create_table(
        "mcp_approval_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("server_name", sa.String(length=100), nullable=False),
        sa.Column("tool_pattern", sa.String(length=255), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("calls_used", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("source_approval_id", sa.String(length=36), nullable=True),
        sa.Column("workflow_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_approval_id"], ["mcp_approvals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mcp_grants_subject_policy_server_expiry",
        "mcp_approval_grants",
        ["subject_type", "subject_id", "policy_version", "server_name", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_grants_source_approval",
        "mcp_approval_grants",
        ["source_approval_id"],
        unique=False,
    )
    op.create_index("ix_mcp_grants_workflow", "mcp_approval_grants", ["workflow_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mcp_grants_workflow", table_name="mcp_approval_grants")
    op.drop_index("ix_mcp_grants_source_approval", table_name="mcp_approval_grants")
    op.drop_index("ix_mcp_grants_subject_policy_server_expiry", table_name="mcp_approval_grants")
    op.drop_table("mcp_approval_grants")
    op.drop_table("mcp_approval_grant_offers")
