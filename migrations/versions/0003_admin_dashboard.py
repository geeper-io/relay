"""Add durable admin identities and role assignments."""

import sqlalchemy as sa
from alembic import op

revision = "0003_admin"
down_revision = "0002_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_identities",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_admin_identities_email", "admin_identities", ["email"], unique=False)
    op.create_table(
        "admin_role_assignments",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_admin_role_assignments_role", "admin_role_assignments", ["role"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_role_assignments_role", table_name="admin_role_assignments")
    op.drop_table("admin_role_assignments")
    op.drop_index("ix_admin_identities_email", table_name="admin_identities")
    op.drop_table("admin_identities")
