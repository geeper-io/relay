"""Tests for app/rag/sync_engine.py — cursor tracking, ingest, failure handling."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.rag.ingestors.base import Document, Ingestor
from app.rag.sync_engine import sync_ingestor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeIngestor(Ingestor):
    """Minimal concrete ingestor for testing."""

    def __init__(
        self,
        source_id: str = "test/repo",
        cursor: str = "abc123",
        to_index: list[str] | None = None,
        to_delete: list[str] | None = None,
        fetch_fail: set[str] | None = None,
    ):
        self._source_id = source_id
        self._cursor = cursor
        self._to_index = to_index or []
        self._to_delete = to_delete or []
        self._fetch_fail = fetch_fail or set()
        self.closed = False

    @property
    def source_id(self) -> str:
        return self._source_id

    async def get_cursor(self) -> str:
        return self._cursor

    async def list_changes(self, since: str | None) -> tuple[list[str], list[str]]:
        return self._to_index, self._to_delete

    async def fetch_document(self, item_id: str) -> Document:
        if item_id in self._fetch_fail:
            raise RuntimeError(f"fetch failed for {item_id}")
        return Document(
            item_id=item_id,
            content=f"content of {item_id}",
            filename=item_id.split("/")[-1] or "file.md",
        )

    async def close(self) -> None:
        self.closed = True


# sync_engine imports: `from app.rag.vector_store import get_synced_sha, set_synced_sha`
# and `from app.rag import vector_store` (for delete_by_source) and `from app.rag.ingestion import ingest_file`
_GET_SHA = "app.rag.sync_engine.get_synced_sha"
_SET_SHA = "app.rag.sync_engine.set_synced_sha"
_DEL_SRC = "app.rag.sync_engine.vector_store.delete_by_source"
_INGEST  = "app.rag.sync_engine.ingest_file"


# ---------------------------------------------------------------------------
# Skip when already up-to-date
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_when_cursor_matches():
    ingestor = _FakeIngestor(cursor="deadbeef")
    with (
        patch(_GET_SHA, return_value="deadbeef"),
        patch(_SET_SHA) as mock_set,
    ):
        result = await sync_ingestor(ingestor)

    assert result["skipped"] is True
    assert result["cursor"] == "deadbeef"
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_skip_does_not_call_close():
    """When skipping, close() is not called (no work was done)."""
    ingestor = _FakeIngestor(cursor="deadbeef")
    with patch(_GET_SHA, return_value="deadbeef"):
        await sync_ingestor(ingestor)

    assert not ingestor.closed


# ---------------------------------------------------------------------------
# Full sync (no stored cursor)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_sync_indexes_all_files():
    ingestor = _FakeIngestor(
        cursor="newsha",
        to_index=["a.md", "b.md", "c.md"],
    )
    with (
        patch(_GET_SHA, return_value=None),
        patch(_SET_SHA) as mock_set,
        patch(_DEL_SRC, return_value=0),
        patch(_INGEST, return_value=2),
    ):
        result = await sync_ingestor(ingestor)

    assert result["skipped"] is False
    assert result["files_processed"] == 3
    assert result["files_failed"] == 0
    assert result["total_chunks"] == 6  # 3 files × 2 chunks each
    mock_set.assert_called_once_with("test/repo", "newsha")
    assert ingestor.closed


@pytest.mark.asyncio
async def test_full_sync_saves_cursor_on_success():
    ingestor = _FakeIngestor(cursor="sha_new", to_index=["x.md"])
    with (
        patch(_GET_SHA, return_value=None),
        patch(_SET_SHA) as mock_set,
        patch(_DEL_SRC),
        patch(_INGEST, return_value=1),
    ):
        await sync_ingestor(ingestor)

    mock_set.assert_called_once_with("test/repo", "sha_new")


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incremental_sync_deletes_and_indexes():
    ingestor = _FakeIngestor(
        cursor="sha2",
        to_index=["added.py"],
        to_delete=["removed.py"],
    )
    with (
        patch(_GET_SHA, return_value="sha1"),
        patch(_SET_SHA),
        patch(_DEL_SRC) as mock_del,
        patch(_INGEST, return_value=1),
    ):
        result = await sync_ingestor(ingestor)

    # delete called for to_delete item AND for the re-indexed item (in _process)
    delete_calls = [str(c) for c in mock_del.call_args_list]
    assert any("removed.py" in c for c in delete_calls)
    assert result["files_processed"] == 1
    assert result["files_failed"] == 0


# ---------------------------------------------------------------------------
# Partial failure — cursor NOT saved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_failure_cursor_not_saved():
    ingestor = _FakeIngestor(
        cursor="sha_new",
        to_index=["ok.md", "fail.md"],
        fetch_fail={"fail.md"},
    )
    with (
        patch(_GET_SHA, return_value="sha_old"),
        patch(_SET_SHA) as mock_set,
        patch(_DEL_SRC),
        patch(_INGEST, return_value=1),
    ):
        result = await sync_ingestor(ingestor)

    assert result["files_failed"] == 1
    assert result["files_processed"] == 1
    assert "fail.md" in result["failed"]
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_all_failed_cursor_not_saved():
    ingestor = _FakeIngestor(
        cursor="sha_new",
        to_index=["a.md", "b.md"],
        fetch_fail={"a.md", "b.md"},
    )
    with (
        patch(_GET_SHA, return_value="sha_old"),
        patch(_SET_SHA) as mock_set,
        patch(_DEL_SRC),
        patch(_INGEST, return_value=1),
    ):
        result = await sync_ingestor(ingestor)

    assert result["files_failed"] == 2
    assert result["files_processed"] == 0
    mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# force=True ignores stored cursor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_ignores_stored_cursor():
    ingestor = _FakeIngestor(cursor="sha_current", to_index=["file.md"])
    with (
        patch(_GET_SHA, return_value="sha_current") as mock_get,
        patch(_SET_SHA),
        patch(_DEL_SRC),
        patch(_INGEST, return_value=1),
    ):
        result = await sync_ingestor(ingestor, force=True)

    # get_synced_sha not consulted when force=True
    mock_get.assert_not_called()
    assert result["skipped"] is False
    assert result["files_processed"] == 1


# ---------------------------------------------------------------------------
# Empty to_index (nothing to do except save cursor)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_changes_saves_cursor():
    ingestor = _FakeIngestor(cursor="sha2", to_index=[], to_delete=[])
    with (
        patch(_GET_SHA, return_value="sha1"),
        patch(_SET_SHA) as mock_set,
        patch(_DEL_SRC),
        patch(_INGEST, return_value=0),
    ):
        result = await sync_ingestor(ingestor)

    assert result["files_processed"] == 0
    assert result["files_failed"] == 0
    mock_set.assert_called_once_with("test/repo", "sha2")


# ---------------------------------------------------------------------------
# concurrency override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrency_override():
    """Passing concurrency= should not raise and should still process files."""
    ingestor = _FakeIngestor(cursor="sha2", to_index=["a.md", "b.md"])
    with (
        patch(_GET_SHA, return_value="sha1"),
        patch(_SET_SHA),
        patch(_DEL_SRC),
        patch(_INGEST, return_value=1),
    ):
        result = await sync_ingestor(ingestor, concurrency=1)

    assert result["files_processed"] == 2
