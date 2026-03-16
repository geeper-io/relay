"""GitHub repository ingestor."""
from __future__ import annotations

import base64
import time
import logging
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
            reset_ts = r.headers.get("X-RateLimit-Reset") or r.headers.get("RateLimit-Reset")
            wait = max(1, int(reset_ts) - int(time.time()) + 1) if reset_ts else 60 * (2 ** min(rate_limit_attempts, 4))
            if wait > _MAX_RATE_LIMIT_WAIT:
                r.raise_for_status()
            rate_limit_attempts += 1
            log.warning("GitHub rate limited — retrying in %ds (attempt %d)", wait, rate_limit_attempts)
            import asyncio
            await asyncio.sleep(wait)
        else:
            error_attempts += 1
            if error_attempts >= _ERROR_RETRIES:
                r.raise_for_status()
            wait = 2 ** error_attempts * 5
            log.warning("GitHub server error %d — retrying in %ds", r.status_code, wait)
            import asyncio
            await asyncio.sleep(wait)


class GitHubIngestor(Ingestor):
    def __init__(self, repo: str, token: str = "", ref: str = "main"):
        self._repo = repo
        self._token = token
        self._ref = ref
        self._client: httpx.AsyncClient | None = None
        self._cursor: str | None = None  # cached HEAD SHA

    @property
    def source_id(self) -> str:
        return self._repo

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/vnd.github+json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            # Unauthenticated: 60 req/hour — serialize fetches
            self._client = httpx.AsyncClient(headers=headers, timeout=30)
        return self._client

    async def get_cursor(self) -> str:
        if self._cursor is None:
            r = await _get(self._http(), f"https://api.github.com/repos/{self._repo}/commits/{self._ref}")
            self._cursor = r.json()["sha"]
        return self._cursor

    async def list_changes(self, since: str | None) -> tuple[list[str], list[str]]:
        client = self._http()
        new_sha = await self.get_cursor()

        if since is None:
            # Full sync: list all indexable files
            r = await _get(client, f"https://api.github.com/repos/{self._repo}/git/trees/{new_sha}?recursive=1")
            paths = [
                item["path"] for item in r.json().get("tree", [])
                if item["type"] == "blob"
                and Path(item["path"]).suffix.lower() in SUPPORTED_EXTENSIONS
                and not _should_skip(item["path"])
            ]
            return paths, []

        # Incremental: diff between cursors
        r = await _get(client, f"https://api.github.com/repos/{self._repo}/compare/{since}...{new_sha}")
        to_index, to_delete = [], []
        for f in r.json().get("files", []):
            path = f["filename"]
            if f["status"] == "removed":
                to_delete.append(path)
            elif f["status"] == "renamed":
                to_delete.append(f["previous_filename"])
                if not _should_skip(path) and Path(path).suffix.lower() in SUPPORTED_EXTENSIONS:
                    to_index.append(path)
            elif not _should_skip(path) and Path(path).suffix.lower() in SUPPORTED_EXTENSIONS:
                to_index.append(path)
        return to_index, to_delete

    async def fetch_document(self, item_id: str) -> Document:
        r = await _get(
            self._http(),
            f"https://api.github.com/repos/{self._repo}/contents/{item_id}?ref={self._ref}",
        )
        content = base64.b64decode(r.json()["content"]).decode("utf-8", errors="replace")
        return Document(
            item_id=item_id,
            content=content,
            filename=Path(item_id).name,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def concurrency(self) -> int:
        """Reduce concurrency for unauthenticated requests to stay within rate limits."""
        return 1 if not self._token else 5
