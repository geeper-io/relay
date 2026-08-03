import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.models  # noqa: F401,E402
from app.db.engine import Base
from app.db.migrate import alembic_config, current_revision, upgrade_database


def _url(path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _database_shape(sync_connection):
    inspector = inspect(sync_connection)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    columns = {table: {column["name"] for column in inspector.get_columns(table)} for table in tables}
    indexes = {table: {index["name"] for index in inspector.get_indexes(table)} for table in tables}
    return tables, columns, indexes


@pytest.mark.asyncio
async def test_fresh_upgrade_matches_declared_metadata(tmp_path):
    database_url = _url(tmp_path / "fresh.db")
    head = await upgrade_database(database_url)
    revision, expected_head = await current_revision(database_url)
    assert revision == expected_head == head == "0005_policy"

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            tables, columns, indexes = await connection.run_sync(_database_shape)
    finally:
        await engine.dispose()

    assert tables == set(Base.metadata.tables)
    for table_name, table in Base.metadata.tables.items():
        assert columns[table_name] == set(table.columns.keys())
        assert indexes[table_name] == {index.name for index in table.indexes}


@pytest.mark.asyncio
async def test_upgrade_adopts_unversioned_pre_grant_database_without_data_loss(tmp_path):
    database_url = _url(tmp_path / "legacy.db")
    config = alembic_config(database_url)
    await asyncio.to_thread(command.upgrade, config, "0003_admin")

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO teams (id, name, tpm_limit, daily_token_limit) "
                    "VALUES ('team-1', 'Platform', 500000, 5000000)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, external_id, team_id, is_active) "
                    "VALUES ('user-1', 'alice@example.com', 'team-1', true)"
                )
            )
            await connection.execute(text("DROP TABLE alembic_version"))
    finally:
        await engine.dispose()

    assert await upgrade_database(database_url) == "0005_policy"
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            user = await connection.scalar(text("SELECT external_id FROM users WHERE id = 'user-1'"))
            tables, _columns, _indexes = await connection.run_sync(_database_shape)
    finally:
        await engine.dispose()
    assert user == "alice@example.com"
    assert {"mcp_approval_grants", "mcp_approval_grant_offers"} <= tables


@pytest.mark.asyncio
async def test_upgrade_refuses_partially_created_legacy_stage(tmp_path):
    database_url = _url(tmp_path / "partial.db")
    config = alembic_config(database_url)
    await asyncio.to_thread(command.upgrade, config, "0003_admin")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE admin_identities"))
            await connection.execute(text("DROP TABLE alembic_version"))
    finally:
        await engine.dispose()

    with pytest.raises(RuntimeError, match="partial Relay schema"):
        await upgrade_database(database_url)


@pytest.mark.asyncio
async def test_concurrent_upgrade_callers_are_serialized(tmp_path):
    database_url = _url(tmp_path / "concurrent.db")
    revisions = await asyncio.gather(
        upgrade_database(database_url),
        upgrade_database(database_url),
    )
    assert revisions == ["0005_policy", "0005_policy"]


@pytest.mark.asyncio
async def test_upgrade_refuses_incompatible_legacy_table_shape(tmp_path):
    database_url = _url(tmp_path / "incompatible.db")
    config = alembic_config(database_url)
    await asyncio.to_thread(command.upgrade, config, "0001_core")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE users DROP COLUMN tpm_limit"))
            await connection.execute(text("DROP TABLE alembic_version"))
    finally:
        await engine.dispose()

    with pytest.raises(RuntimeError, match="incompatible legacy table 'users'.*tpm_limit"):
        await upgrade_database(database_url)
