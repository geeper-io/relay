"""
Generic ingestor sync engine.

Handles cursor tracking, concurrency, delete-before-reingest, and SHA persistence.
Ingestors only need to implement list_changes() and fetch_document().
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from app.rag import vector_store
from app.rag.ingestion import ingest_file
from app.rag.ingestors.base import Ingestor
from app.rag.vector_store import get_synced_sha, set_synced_sha

log = logging.getLogger(__name__)


async def sync_ingestor(
    ingestor: Ingestor,
    concurrency: int | None = None,
    force: bool = False,
) -> dict:
    """
    Sync a data source into ChromaDB.

    - Checks cursor; skips if already up-to-date.
    - On first run (no cursor): full index.
    - On subsequent runs: incremental diff (changed + deleted only).
    - Persists cursor only on a fully clean run (zero failures).

    Returns a result dict with keys: skipped, total_chunks, files_processed,
    files_failed, failed, cursor.
    """
    old_cursor = "" if force else (get_synced_sha(ingestor.source_id) or "")
    new_cursor = await ingestor.get_cursor()

    if old_cursor == new_cursor:
        log.info("%s: up-to-date at %s, skipping", ingestor.source_id, new_cursor[:12])
        return {"skipped": True, "cursor": new_cursor, "total_chunks": 0}

    try:
        to_index, to_delete = await ingestor.list_changes(old_cursor or None)
    except Exception as e:
        log.error("%s: failed to list changes: %s", ingestor.source_id, e)
        raise

    if old_cursor:
        log.info(
            "%s: %d to index, %d to delete (%s → %s)",
            ingestor.source_id,
            len(to_index),
            len(to_delete),
            old_cursor[:12],
            new_cursor[:12],
        )
    else:
        log.info("%s: first sync @ %s — %d files", ingestor.source_id, new_cursor[:12], len(to_index))

    # Delete removed items
    for item_id in to_delete:
        vector_store.delete_by_source(f"{ingestor.source_id}/{item_id}")

    # Determine concurrency
    if concurrency is None:
        concurrency = getattr(ingestor, "concurrency", 5)

    sem = asyncio.Semaphore(concurrency)
    total_chunks = 0
    failed: list[str] = []

    async def _process(item_id: str) -> None:
        nonlocal total_chunks
        async with sem:
            try:
                doc = await ingestor.fetch_document(item_id)
                source_key = f"{ingestor.source_id}/{item_id}"

                with tempfile.TemporaryDirectory() as tmpdir:
                    dest = Path(tmpdir) / doc.filename.replace("/", "_")
                    dest.write_text(doc.content, encoding="utf-8", errors="replace")

                    vector_store.delete_by_source(source_key)
                    chunks = ingest_file(
                        dest,
                        collection_filter={
                            "source": source_key,
                            "repo": ingestor.source_id,
                            **doc.metadata,
                        },
                    )

                total_chunks += chunks
                log.debug("%s: ingested %s (%d chunks)", ingestor.source_id, item_id, chunks)
            except Exception as e:
                log.warning("%s: failed to ingest %s: %s", ingestor.source_id, item_id, e)
                failed.append(item_id)

    await asyncio.gather(*[_process(item_id) for item_id in to_index])

    result = {
        "skipped": False,
        "cursor": new_cursor,
        "files_processed": len(to_index) - len(failed),
        "files_failed": len(failed),
        "total_chunks": total_chunks,
        "failed": failed,
    }

    if not failed:
        set_synced_sha(ingestor.source_id, new_cursor)
    elif len(failed) == len(to_index):
        log.error(
            "%s: ALL %d files failed — is the source accessible? Cursor not saved.",
            ingestor.source_id,
            len(failed),
        )
    else:
        log.warning(
            "%s: %d/%d files failed — cursor not saved, will retry on next sync",
            ingestor.source_id,
            len(failed),
            len(to_index),
        )

    await ingestor.close()
    return result
