"""GitLab repository ingestor."""
from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path

import httpx

from app.rag.ingestion import SUPPORTED_EXTENSIONS
from app.rag.ingestors.base import Document, Ingestor

log = logging.getLogger(__name__)

_ERROR_RETRIES = 3
_MAX_RATE_LIMIT_WAIT = 3700

_SKIP_PATTERNS = {
    "node_modules", "vendor", "dist", "build", ".git",
    "__pycache__", ".venv", "venv", "target",
}


def _should_skip(path: str) -> bool:
    for part in path.split("/"):
        if part in _SKIP_PATTERNS or part.startswith("."):
            return True
    return False


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with rate-limit back-off and server-error retries."""
    error_attempts = 0
    rate_limit_attempts = 0
    while True:
        r = await client.get(url, **kwargs)

        if r.status_code not in (403, 429) and r.status_code < 500:
            r.raise_for_status()
            return r

        if r.status_code in (403, 429):
            reset_ts = r.headers.get("RateLimit-Reset") or r.headers.get("X-RateLimit-Reset")
            wait = max(1, int(reset_ts) - int(time.time()) + 1) if reset_ts else 60 * (2 ** min(rate_limit_attempts, 4))
            if wait > _MAX_RATE_LIMIT_WAIT:
                r.raise_for_status()
            rate_limit_attempts += 1
            log.warning("GitLab rate limited — retrying in %ds (attempt %d)", wait, rate_limit_attempts)
            import asyncio
            await asyncio.sleep(wait)
        else:
            error_attempts += 1
            if error_attempts >= _ERROR_RETRIES:
                r.raise_for_status()
            wait = 2 ** error_attempts * 5
            log.warning("GitLab server error %d — retrying in %ds", r.status_code, wait)
            import asyncio
            await asyncio.sleep(wait)


class GitLabIngestor(Ingestor):
    def __init__(
        self,
        project_id: str,
        token: str = "",
        host: str = "https://gitlab.com",
        ref: str = "main",
    ):
        self._project_id = project_id
        self._token = token
        self._host = host.rstrip("/")
        self._ref = ref
        self._client: httpx.AsyncClient | None = None
        self._cursor: str | None = None

    @property
    def source_id(self) -> str:
        return self._project_id

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"PRIVATE-TOKEN": self._token} if self._token else {}
            self._client = httpx.AsyncClient(headers=headers, timeout=30)
        return self._client

    async def get_cursor(self) -> str:
        if self._cursor is None:
            r = await _get(
                self._http(),
                f"{self._host}/api/v4/projects/{self._project_id}/repository/commits",
                params={"ref_name": self._ref, "per_page": 1},
            )
            self._cursor = r.json()[0]["id"]
        return self._cursor

    async def list_changes(self, since: str | None) -> tuple[list[str], list[str]]:
        client = self._http()

        if since is None:
            # Full sync: paginate tree
            paths, page = [], 1
            while True:
                r = await _get(
                    client,
                    f"{self._host}/api/v4/projects/{self._project_id}/repository/tree",
                    params={"ref": self._ref, "recursive": True, "per_page": 100, "page": page},
                )
                items = r.json()
                if not items:
                    break
                for item in items:
                    if (
                        item["type"] == "blob"
                        and Path(item["path"]).suffix.lower() in SUPPORTED_EXTENSIONS
                        and not _should_skip(item["path"])
                    ):
                        paths.append(item["path"])
                page += 1
            return paths, []

        # Incremental: compare cursors
        new_sha = await self.get_cursor()
        r = await _get(
            client,
            f"{self._host}/api/v4/projects/{self._project_id}/repository/compare",
            params={"from": since, "to": new_sha},
        )
        to_index, to_delete = [], []
        for d in r.json().get("diffs", []):
            new_path, old_path = d["new_path"], d["old_path"]
            if d["deleted_file"]:
                to_delete.append(old_path)
            elif d["renamed_file"]:
                to_delete.append(old_path)
                if not _should_skip(new_path) and Path(new_path).suffix.lower() in SUPPORTED_EXTENSIONS:
                    to_index.append(new_path)
            elif not _should_skip(new_path) and Path(new_path).suffix.lower() in SUPPORTED_EXTENSIONS:
                to_index.append(new_path)
        return to_index, to_delete

    async def fetch_document(self, item_id: str) -> Document:
        encoded = urllib.parse.quote(item_id, safe="")
        r = await _get(
            self._http(),
            f"{self._host}/api/v4/projects/{self._project_id}/repository/files/{encoded}/raw",
            params={"ref": self._ref},
        )
        return Document(
            item_id=item_id,
            content=r.text,
            filename=Path(item_id).name,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
