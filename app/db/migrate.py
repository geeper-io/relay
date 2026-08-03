"""Alembic migration runner with legacy-schema adoption and upgrade locking."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POSTGRES_LOCK_ID = 7_214_483_651

_SCHEMA_STAGES = (
    (
        "0001_core",
        {"teams", "users", "api_keys", "usage_records", "audit_logs"},
    ),
    (
        "0002_mcp",
        {"mcp_approvals", "mcp_response_approvals"},
    ),
    (
        "0003_admin",
        {"admin_identities", "admin_role_assignments"},
    ),
    (
        "0004_grants",
        {"mcp_approval_grant_offers", "mcp_approval_grants"},
    ),
    (
        "0005_policy",
        {"mcp_policy_versions", "mcp_policy_state", "mcp_policy_activations"},
    ),
)

_REQUIRED_COLUMNS = {
    "teams": {"id", "name", "tpm_limit", "daily_token_limit", "created_at"},
    "users": {"id", "external_id", "team_id", "rpm_limit", "tpm_limit", "is_active", "created_at"},
    "api_keys": {
        "id",
        "key_hash",
        "key_prefix",
        "user_id",
        "name",
        "scopes",
        "expires_at",
        "last_used_at",
        "is_active",
        "created_at",
    },
    "usage_records": {
        "id",
        "user_id",
        "team_id",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "request_id",
        "cost_usd",
        "cache_hit",
        "was_rag_used",
        "pii_entities_scrubbed",
        "status",
        "error_code",
        "created_at",
    },
    "audit_logs": {"id", "request_id", "user_id", "action", "resource", "metadata", "created_at"},
    "mcp_approvals": {
        "id",
        "user_id",
        "team_id",
        "server_name",
        "tool_name",
        "arguments_hash",
        "arguments",
        "purpose",
        "policy_version",
        "status",
        "requested_at",
        "expires_at",
        "decided_at",
        "decided_by",
        "decision_reason",
        "consumed_at",
    },
    "mcp_response_approvals": {
        "id",
        "provider_response_id",
        "provider_approval_request_id",
        "approval_id",
        "user_id",
        "team_id",
        "created_at",
    },
    "admin_identities": {"user_id", "email", "display_name", "last_seen_at"},
    "admin_role_assignments": {"user_id", "role", "assigned_by", "created_at", "updated_at"},
    "mcp_approval_grant_offers": {"approval_id", "template", "created_at"},
    "mcp_approval_grants": {
        "id",
        "subject_type",
        "subject_id",
        "server_name",
        "tool_pattern",
        "constraints",
        "policy_version",
        "max_calls",
        "calls_used",
        "expires_at",
        "revoked_at",
        "created_by",
        "reason",
        "source_approval_id",
        "workflow_id",
        "created_at",
        "updated_at",
    },
    "mcp_policy_versions": {
        "version",
        "document",
        "status",
        "base_version",
        "created_by",
        "reason",
        "created_at",
        "activated_by",
        "activated_at",
    },
    "mcp_policy_state": {"id", "active_version", "updated_by", "updated_at"},
    "mcp_policy_activations": {"id", "version", "previous_version", "actor", "reason", "created_at"},
}


def alembic_config(database_url: str) -> Config:
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    config.attributes["relay_database_url"] = database_url
    return config


def _schema_state(sync_connection) -> tuple[set[str], str | None, dict[str, set[str]]]:
    inspector = inspect(sync_connection)
    tables = set(inspector.get_table_names())
    revision = MigrationContext.configure(sync_connection).get_current_revision()
    columns = {
        table: {column["name"] for column in inspector.get_columns(table)} for table in tables & set(_REQUIRED_COLUMNS)
    }
    return tables, revision, columns


async def _state(connection: AsyncConnection) -> tuple[set[str], str | None, dict[str, set[str]]]:
    return await connection.run_sync(_schema_state)


def _legacy_revision(tables: set[str], columns: dict[str, set[str]]) -> str | None:
    application_tables = tables - {"alembic_version"}
    if not application_tables:
        return None

    known_tables = set().union(*(stage_tables for _, stage_tables in _SCHEMA_STAGES))
    present_known = application_tables & known_tables
    for table in present_known:
        missing_columns = _REQUIRED_COLUMNS[table] - columns.get(table, set())
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RuntimeError(f"Database has an incompatible legacy table '{table}'; missing columns: {missing}")
    highest_revision: str | None = None
    missing_stage = False
    for revision, stage_tables in _SCHEMA_STAGES:
        present = stage_tables & present_known
        if present and present != stage_tables:
            missing = ", ".join(sorted(stage_tables - present))
            raise RuntimeError(f"Database has a partial Relay schema at {revision}; missing tables: {missing}")
        if present == stage_tables:
            if missing_stage:
                raise RuntimeError(
                    f"Database schema includes {revision} tables but an earlier migration stage is absent"
                )
            highest_revision = revision
        else:
            missing_stage = True

    if highest_revision is None:
        raise RuntimeError(
            "Database is not empty and does not contain Relay's core schema; refusing to stamp it automatically"
        )
    return highest_revision


@asynccontextmanager
async def _migration_lock(database_url: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(database_url)
    parsed = make_url(database_url)
    lock_file = None
    try:
        if parsed.get_backend_name() == "sqlite" and parsed.database not in {None, "", ":memory:"}:
            database_path = Path(parsed.database)
            if not database_path.is_absolute():
                database_path = database_path.resolve()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(f"{database_path}.migrate.lock", "a+")  # noqa: SIM115
            await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX)

        async with engine.connect() as connection:
            if parsed.get_backend_name() == "postgresql":
                await connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _POSTGRES_LOCK_ID})
                await connection.commit()
            try:
                yield connection
            finally:
                if parsed.get_backend_name() == "postgresql":
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _POSTGRES_LOCK_ID},
                    )
                    await connection.commit()
    finally:
        if lock_file is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        await engine.dispose()


async def upgrade_database(database_url: str | None = None) -> str:
    """Upgrade to head, adopting schemas from pre-Alembic Relay releases safely."""
    url = database_url or get_settings().database_url
    config = alembic_config(url)
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None

    async with _migration_lock(url) as connection:
        tables, revision, columns = await _state(connection)
        await connection.rollback()
        if revision is None:
            legacy_revision = _legacy_revision(tables, columns)
            if legacy_revision is not None:
                await asyncio.to_thread(command.stamp, config, legacy_revision)
        await asyncio.to_thread(command.upgrade, config, "head")
        tables, revision, _columns = await _state(connection)
        await connection.rollback()
        if revision != head:
            raise RuntimeError(f"Database migration ended at {revision or 'base'}, expected {head}")
        expected_tables = set().union(*(stage_tables for _, stage_tables in _SCHEMA_STAGES))
        missing = expected_tables - tables
        if missing:
            raise RuntimeError("Database migration is missing tables: " + ", ".join(sorted(missing)))
    return head


async def current_revision(database_url: str | None = None) -> tuple[str | None, str]:
    url = database_url or get_settings().database_url
    config = alembic_config(url)
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            _tables, revision, _columns = await _state(connection)
    finally:
        await engine.dispose()
    return revision, head


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Relay's relational database schema")
    parser.add_argument("command", choices=["upgrade", "current"], nargs="?", default="upgrade")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "upgrade":
        revision = asyncio.run(upgrade_database())
        print(f"Relay database is at {revision}")
        return
    revision, head = asyncio.run(current_revision())
    print(f"Current: {revision or 'unversioned'}")
    print(f"Head:    {head}")
    if revision != head:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
