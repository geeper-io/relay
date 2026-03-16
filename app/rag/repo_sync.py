"""
Backwards-compatible shims — thin wrappers around the ingestor + sync_engine.
New code should use sync_engine.sync_ingestor() directly.
"""
from __future__ import annotations

from app.rag.ingestors.github import GitHubIngestor
from app.rag.ingestors.gitlab import GitLabIngestor
from app.rag.sync_engine import sync_ingestor


async def sync_github_repo(
    repo: str,
    token: str = "",
    ref: str = "main",
    concurrency: int | None = None,
    force: bool = False,
) -> dict:
    return await sync_ingestor(
        GitHubIngestor(repo=repo, token=token, ref=ref),
        concurrency=concurrency,
        force=force,
    )


async def sync_gitlab_repo(
    project_id: str,
    token: str = "",
    host: str = "https://gitlab.com",
    ref: str = "main",
    concurrency: int | None = None,
    force: bool = False,
) -> dict:
    return await sync_ingestor(
        GitLabIngestor(project_id=project_id, token=token, host=host, ref=ref),
        concurrency=concurrency,
        force=force,
    )
