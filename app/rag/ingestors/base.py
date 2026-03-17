"""Base interfaces for RAG data source ingestors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Document:
    """A single fetchable document from any data source."""

    item_id: str  # stable ID within the source: file path, issue key, page ID, etc.
    content: str  # raw text content
    filename: str  # filename with extension — controls which chunker is used
    # (e.g. "main.py" → tree-sitter, "PROJ-123.md" → word-based)
    metadata: dict = field(default_factory=dict)  # extra fields merged into chunk metadata


class Ingestor(ABC):
    """
    Abstract data source ingestor.

    Implement this to add a new source (Jira, Confluence, Slack, etc.).
    The sync engine handles all cursor tracking, deduplication, embedding,
    and storage — the ingestor only needs to know how to list and fetch.

    Example for a new source::

        class JiraIngestor(Ingestor):
            @property
            def source_id(self) -> str:
                return f"jira:{self._project}"

            async def get_cursor(self) -> str:
                # Return ISO timestamp of most recently updated issue
                ...

            async def list_changes(self, since: str | None):
                # JQL: project = X AND updated >= since
                ...

            async def fetch_document(self, item_id: str) -> Document:
                # Fetch issue, format as markdown, return with filename="PROJ-123.md"
                ...
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """
        Stable unique identifier for this source instance.
        Used as the 'repo' metadata field in ChromaDB and as the key for
        cursor storage. Should be human-readable, e.g. 'owner/repo', 'jira:PROJ'.
        """

    @abstractmethod
    async def get_cursor(self) -> str:
        """
        Return the current state cursor for this source.
        For git: HEAD commit SHA. For Jira: ISO timestamp of latest update.
        The sync engine compares this against the stored cursor to detect changes.
        Implementations should cache this value since it may be called multiple times.
        """

    @abstractmethod
    async def list_changes(self, since: str | None) -> tuple[list[str], list[str]]:
        """
        Return (to_index, to_delete) item ID lists.
        - since=None: full sync — return all item IDs, empty delete list.
        - since=<cursor>: incremental — return only items changed/added/removed
          between 'since' and the current cursor (from get_cursor()).
        """

    @abstractmethod
    async def fetch_document(self, item_id: str) -> Document:
        """Fetch and return a Document for the given item ID."""

    async def close(self) -> None:
        """Optional cleanup: close HTTP clients, DB connections, etc."""
