"""Add database-managed MCP policy versions and activation history."""

import sqlalchemy as sa
from alembic import op

revision = "0005_policy"
down_revision = "0004_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_policy_versions",
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("base_version", sa.String(length=100), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_by", sa.String(length=255), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_index(
        "ix_mcp_policy_versions_status_created",
        "mcp_policy_versions",
        ["status", "created_at"],
        unique=False,
    )
    op.create_table(
        "mcp_policy_state",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("active_version", sa.String(length=100), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["active_version"], ["mcp_policy_versions.version"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mcp_policy_activations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("previous_version", sa.String(length=100), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["version"], ["mcp_policy_versions.version"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mcp_policy_activations_created",
        "mcp_policy_activations",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_policy_activations_created", table_name="mcp_policy_activations")
    op.drop_table("mcp_policy_activations")
    op.drop_table("mcp_policy_state")
    op.drop_index("ix_mcp_policy_versions_status_created", table_name="mcp_policy_versions")
    op.drop_table("mcp_policy_versions")
