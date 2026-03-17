"""GitLab repository ingestor."""
from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

import httpx

from app.rag.ingestion import SUPPORTED_EXTENSIONS
from app.rag.ingestors._http import get_with_retry
from app.rag.ingestors.base import Document, Ingestor

log = logging.getLogger(__name__)

_SKIP_PATTERNS = {
    "node_modules", "vendor", "dist", "build", ".git",
    "__pycache__", ".venv", "venv", "target",
}


def _should_skip(path: str) -> bool:
    for part in path.split("/"):
        if part in _SKIP_PATTERNS or part.startswith("."):
            return True
    return False


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
            r = await get_with_retry(
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
                r = await get_with_retry(
                    client,
                    f"{self._host}/api/v4/projects/{self._project_id}/repository/tree",
                    params={"ref": self._ref, "recursive": True, "per_page": 100, "page": page},
                )
                try:
                    items = r.json()
                except Exception as e:
                    raise RuntimeError(f"GitLab tree API returned non-JSON response (page {page}): {e}") from e
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
        r = await get_with_retry(
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
        r = await get_with_retry(
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
