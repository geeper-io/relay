from __future__ import annotations

import re

from app.config import Settings
from app.rag import embedder, vector_store

# Matches ```lang\n...\n``` or ```\n...\n```
_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n?(.*?)```", re.DOTALL)
# Matches a filepath-like comment on the first line of a code block
_FILEPATH_RE = re.compile(r"^(?:#|//|--|/\*)\s*([\w./\-]+\.\w+)")


def _extract_code_blocks(text: str) -> list[dict]:
    blocks = []
    for m in _CODE_BLOCK_RE.finditer(text):
        lang = m.group(1) or ""
        code = m.group(2) or ""
        filepath = ""
        first_line = code.lstrip().split("\n")[0]
        fm = _FILEPATH_RE.match(first_line)
        if fm:
            filepath = fm.group(1)
        blocks.append({"language": lang, "code": code, "filepath": filepath})
    return blocks


def _build_code_query(blocks: list[dict], original: str) -> str:
    """Construct a richer query from code block signals."""
    parts = [original]
    for b in blocks:
        if b["filepath"]:
            parts.append(b["filepath"])
        # First 300 chars of code carries most of the semantic signal
        parts.append(b["code"][:300])
    return "\n".join(parts)


class RAGRetriever:
    def __init__(self, settings: Settings):
        self._settings = settings

    async def retrieve_context(
        self,
        query: str,
        filters: dict | None = None,
    ) -> tuple[str, int]:
        """
        Returns (context_string, num_chunks_found).

        If the query contains code blocks, uses multi-signal retrieval:
        separate queries against code chunks and doc chunks so both
        signal types are represented even when one dominates by volume.
        """
        if not query.strip():
            return "", 0

        code_blocks = _extract_code_blocks(query)
        if code_blocks:
            return await self._retrieve_multi_signal(query, code_blocks, filters)
        return await self._retrieve_single(query, filters)

    async def _retrieve_single(self, query: str, filters: dict | None) -> tuple[str, int]:
        embedding = embedder.embed_one(query)
        results = vector_store.query(
            query_embedding=embedding,
            n_results=self._settings.rag_top_k,
            where=filters,
        )
        threshold = self._settings.rag_score_threshold
        relevant = [r for r in results if r.distance <= threshold]
        return self._format(relevant), len(relevant)

    async def _retrieve_multi_signal(
        self,
        original_query: str,
        code_blocks: list[dict],
        filters: dict | None,
    ) -> tuple[str, int]:
        rich_query = _build_code_query(code_blocks, original_query)
        embedding = embedder.embed_one(rich_query)
        threshold = self._settings.rag_score_threshold
        top_k = self._settings.rag_top_k

        # Fetch more than top_k so we can split across types
        all_results = vector_store.query(
            query_embedding=embedding,
            n_results=top_k * 3,
            where=filters,
        )

        # Separate by doc_type, take top (top_k // 2) from each so both
        # code patterns and policy/ADR docs appear in the final context.
        # Falls back gracefully for chunks that predate the doc_type field.
        half = max(1, top_k // 2)
        code_chunks, doc_chunks, untyped = [], [], []
        for r in all_results:
            if r.distance > threshold:
                continue
            dt = r.metadata.get("doc_type", "")
            if dt == "code":
                code_chunks.append(r)
            elif dt == "doc":
                doc_chunks.append(r)
            else:
                untyped.append(r)

        relevant = code_chunks[:half] + doc_chunks[:half] + untyped[:half]
        # Deduplicate by id, preserve order
        seen: set[str] = set()
        deduped = []
        for r in relevant:
            if r.doc_id not in seen:
                seen.add(r.doc_id)
                deduped.append(r)

        return self._format(deduped), len(deduped)

    def _format(self, results: list) -> str:
        if not results:
            return ""
        chunks = []
        for r in results:
            source = r.metadata.get("source", "unknown")
            symbol = r.metadata.get("symbol", "")
            title = r.metadata.get("title", source)
            label = f"{title}:{symbol}" if symbol and symbol != "__module__" else title
            chunks.append(f"[{label}]\n{r.text}")
        return self._settings.rag_context_prefix + self._settings.rag_context_separator.join(chunks)


_retriever: RAGRetriever | None = None


def init_retriever(settings: Settings) -> RAGRetriever:
    global _retriever
    _retriever = RAGRetriever(settings)
    return _retriever


def get_retriever() -> RAGRetriever:
    if _retriever is None:
        raise RuntimeError("RAGRetriever not initialized")
    return _retriever
