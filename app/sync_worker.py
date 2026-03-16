"""
Standalone sync worker — runs repo discovery/sync then exits.
Used by the Helm CronJob so proxy pods are never burdened with sync work.

Usage:
    python -m app.sync_worker
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app.config import get_settings
from app.rag.embedder import init_embedder
from app.rag.repo_discovery import auto_sync_repos
from app.rag.vector_store import init_vector_store

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), stream=sys.stdout)

    if not settings.github_token and not settings.gitlab_token:
        log.info("No repo credentials configured, nothing to sync")
        return

    init_embedder(settings)
    init_vector_store(settings)
    await auto_sync_repos(settings)
    log.info("Sync worker finished")


if __name__ == "__main__":
    asyncio.run(main())
