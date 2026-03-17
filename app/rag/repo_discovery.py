"""
Discover all repos a token has access to and kick off sync.
Called automatically at startup when credentials are configured.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.rag.ingestors.github import GitHubIngestor
from app.rag.ingestors.gitlab import GitLabIngestor
from app.rag.sync_engine import sync_ingestor

log = logging.getLogger(__name__)


# ── GitHub ─────────────────────────────────────────────────────────────────────

async def _github_list_repos(
    client: httpx.AsyncClient,
    orgs: list[str],
    exclude: list[str],
) -> list[str]:
    repos: list[str] = []
    exclude_set = set(exclude)

    async def _paginate(url: str):
        while url:
            r = await client.get(url, params={"per_page": 100, "type": "all"})
            r.raise_for_status()
            for item in r.json():
                name = item["full_name"]
                if name not in exclude_set and not item.get("archived"):
                    repos.append(name)
            url = r.links.get("next", {}).get("url")

    if orgs:
        for org in orgs:
            await _paginate(f"https://api.github.com/orgs/{org}/repos")
    else:
        await _paginate("https://api.github.com/user/repos")

    return repos


# ── GitLab ─────────────────────────────────────────────────────────────────────

async def _gitlab_list_projects(
    client: httpx.AsyncClient,
    host: str,
    groups: list[str],
    exclude: list[str],
) -> list[str]:
    projects: list[str] = []
    exclude_set = set(exclude)

    async def _paginate(url: str, params: dict):
        page = 1
        while True:
            r = await client.get(url, params={**params, "per_page": 100, "page": page})
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            for item in items:
                pid = str(item["id"])
                path = item.get("path_with_namespace", "")
                if pid not in exclude_set and path not in exclude_set and not item.get("archived"):
                    projects.append(pid)
            page += 1

    if groups:
        for group in groups:
            await _paginate(f"{host}/api/v4/groups/{group}/projects", {"include_subgroups": True})
    else:
        await _paginate(f"{host}/api/v4/projects", {"membership": True})

    return projects


# ── Auto-sync entry point ──────────────────────────────────────────────────────

async def auto_sync_repos(settings) -> None:
    """
    Discover and sync all repos for configured providers.
    Runs as a background task at startup — errors are logged, not raised.
    """
    tasks = []

    if settings.github_token or settings.github_include:
        tasks.append(_sync_github(settings))

    if settings.gitlab_token or settings.gitlab_include:
        tasks.append(_sync_gitlab(settings))

    if not tasks:
        return

    await asyncio.gather(*tasks, return_exceptions=True)


async def _sync_github(settings) -> None:
    try:
        if settings.github_include:
            exclude_set = set(settings.github_exclude)
            repos = [r for r in settings.github_include if r not in exclude_set]
            log.info("GitHub auto-sync: using whitelist (%d repos)", len(repos))
        else:
            headers = {
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
            }
            async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                repos = await _github_list_repos(client, settings.github_orgs, settings.github_exclude)
            log.info("GitHub auto-sync: discovered %d repos", len(repos))
    except Exception as e:
        log.error("GitHub repo discovery failed: %s", e)
        return

    sem = asyncio.Semaphore(3)

    async def _sync_one(repo: str):
        async with sem:
            try:
                result = await sync_ingestor(
                    GitHubIngestor(repo=repo, token=settings.github_token, ref=settings.github_ref)
                )
                if result.get("skipped"):
                    log.info("GitHub %s: up-to-date, skipped", repo)
                else:
                    log.info("GitHub synced %s: %d chunks", repo, result["total_chunks"])
            except Exception as e:
                log.warning("GitHub sync failed for %s: %s", repo, e)

    await asyncio.gather(*[_sync_one(r) for r in repos])
    log.info("GitHub auto-sync complete")


async def _sync_gitlab(settings) -> None:
    try:
        if settings.gitlab_include:
            exclude_set = set(settings.gitlab_exclude)
            projects = [p for p in settings.gitlab_include if p not in exclude_set]
            log.info("GitLab auto-sync: using whitelist (%d projects)", len(projects))
        else:
            headers = {"PRIVATE-TOKEN": settings.gitlab_token}
            async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                projects = await _gitlab_list_projects(
                    client, settings.gitlab_host, settings.gitlab_groups, settings.gitlab_exclude
                )
            log.info("GitLab auto-sync: discovered %d projects", len(projects))
    except Exception as e:
        log.error("GitLab project discovery failed: %s", e)
        return

    sem = asyncio.Semaphore(3)

    async def _sync_one(project_id: str):
        async with sem:
            try:
                result = await sync_ingestor(
                    GitLabIngestor(
                        project_id=project_id,
                        token=settings.gitlab_token,
                        host=settings.gitlab_host,
                        ref=settings.gitlab_ref,
                    )
                )
                if result.get("skipped"):
                    log.info("GitLab %s: up-to-date, skipped", project_id)
                else:
                    log.info("GitLab synced %s: %d chunks", project_id, result["total_chunks"])
            except Exception as e:
                log.warning("GitLab sync failed for %s: %s", project_id, e)

    await asyncio.gather(*[_sync_one(p) for p in projects])
    log.info("GitLab auto-sync complete")
