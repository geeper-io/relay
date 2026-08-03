"""Create Relay's original core schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tpm_limit", sa.Integer(), nullable=False),
        sa.Column("daily_token_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("tpm_limit", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=False)
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("was_rag_used", sa.Boolean(), nullable=False),
        sa.Column("pii_entities_scrubbed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_created_at", "usage_records", ["created_at"], unique=False)
    op.create_index("ix_usage_model_created", "usage_records", ["model", "created_at"], unique=False)
    op.create_index("ix_usage_request_id", "usage_records", ["request_id"], unique=False)
    op.create_index("ix_usage_team_created", "usage_records", ["team_id", "created_at"], unique=False)
    op.create_index("ix_usage_user_created", "usage_records", ["user_id", "created_at"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_index("ix_usage_user_created", table_name="usage_records")
    op.drop_index("ix_usage_team_created", table_name="usage_records")
    op.drop_index("ix_usage_request_id", table_name="usage_records")
    op.drop_index("ix_usage_model_created", table_name="usage_records")
    op.drop_index("ix_usage_created_at", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("teams")
