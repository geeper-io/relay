from __future__ import annotations

import asyncio
import html
import re

from app.config import Settings
from app.rag import embedder, reranker, vector_store
from app.rag.vector_store import QueryResult

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


def _reciprocal_rank_fusion(
    dense: list[QueryResult],
    lexical: list[QueryResult],
    *,
    rrf_k: int,
    dense_weight: float,
    lexical_weight: float,
) -> list[QueryResult]:
    candidates: dict[str, QueryResult] = {}
    for rank, result in enumerate(dense, start=1):
        result.fused_score = dense_weight / (rrf_k + rank)
        candidates[result.doc_id] = result
    for rank, result in enumerate(lexical, start=1):
        contribution = lexical_weight / (rrf_k + rank)
        if result.doc_id in candidates:
            candidates[result.doc_id].fused_score += contribution
            candidates[result.doc_id].lexical_score = result.lexical_score
        else:
            result.fused_score = contribution
            candidates[result.doc_id] = result
    return sorted(candidates.values(), key=lambda item: (-item.fused_score, item.doc_id))


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

        if getattr(self._settings, "rag_hybrid_enabled", False):
            relevant = await self.retrieve_ranked(query, filters=filters)
            return self._format(relevant), len(relevant)

        code_blocks = _extract_code_blocks(query)
        if code_blocks:
            return await self._retrieve_multi_signal(query, code_blocks, filters)
        return await self._retrieve_single(query, filters)

    async def _retrieve_single(self, query: str, filters: dict | None) -> tuple[str, int]:
        results = await asyncio.to_thread(self._dense_candidates, query, self._settings.rag_top_k, filters)
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
        threshold = self._settings.rag_score_threshold
        top_k = self._settings.rag_top_k

        # Fetch more than top_k so we can split across types
        all_results = await asyncio.to_thread(self._dense_candidates, rich_query, top_k * 3, filters)

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

    async def retrieve_ranked(
        self,
        query: str,
        filters: dict | None = None,
        limit: int | None = None,
    ) -> list[QueryResult]:
        """Run the same ranked retrieval path used by live prompt enrichment."""
        if not query.strip():
            return []
        top_k = max(1, limit or self._settings.rag_top_k)
        multiplier = max(1, getattr(self._settings, "rag_candidate_multiplier", 4))
        candidate_count = max(top_k, top_k * multiplier)
        code_blocks = _extract_code_blocks(query)
        search_query = _build_code_query(code_blocks, query) if code_blocks else query

        dense_task = asyncio.to_thread(self._dense_candidates, search_query, candidate_count, filters)
        lexical_task = asyncio.to_thread(vector_store.lexical_query, search_query, candidate_count, filters)
        dense, lexical = await asyncio.gather(dense_task, lexical_task)
        dense = [result for result in dense if result.distance <= self._settings.rag_score_threshold]
        fused = _reciprocal_rank_fusion(
            dense,
            lexical,
            rrf_k=max(1, getattr(self._settings, "rag_rrf_k", 60)),
            dense_weight=max(0.0, getattr(self._settings, "rag_dense_weight", 1.0)),
            lexical_weight=max(0.0, getattr(self._settings, "rag_lexical_weight", 1.0)),
        )
        if getattr(self._settings, "rag_reranker_model", ""):
            reranker_top_n = max(top_k, getattr(self._settings, "rag_reranker_top_n", 20))
            fused = await asyncio.to_thread(reranker.rerank, query, fused[:reranker_top_n])
        return fused[:top_k]

    @staticmethod
    def _dense_candidates(query: str, n_results: int, filters: dict | None) -> list[QueryResult]:
        embedding = embedder.embed_one(query)
        return vector_store.query(query_embedding=embedding, n_results=n_results, where=filters)

    def _format(self, results: list) -> str:
        if not results:
            return ""
        chunks = []
        token_budget = max(1, getattr(self._settings, "rag_context_max_tokens", 4_000))
        used_tokens = max(1, len(self._settings.rag_context_prefix) // 4)
        for rank, r in enumerate(results, start=1):
            source = r.metadata.get("source", "unknown")
            symbol = r.metadata.get("symbol", "")
            title = r.metadata.get("title", source)
            label = f"{title}:{symbol}" if symbol and symbol != "__module__" else title
            attributes = {
                "id": r.doc_id,
                "source": source,
                "title": title,
                "symbol": symbol if symbol != "__module__" else "",
                "rank": str(rank),
            }
            serialized = " ".join(
                f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attributes.items() if value
            )
            header = f"<relay_source {serialized}>\n[{label}]\n"
            footer = "\n</relay_source>"
            remaining_tokens = token_budget - used_tokens
            overhead_tokens = max(1, (len(header) + len(footer)) // 4)
            if remaining_tokens <= overhead_tokens:
                break
            body_chars = (remaining_tokens - overhead_tokens) * 4
            safe_text = r.text.replace("<relay_source", "&lt;relay_source")
            safe_text = safe_text.replace("</relay_source", "&lt;/relay_source")
            body = safe_text[:body_chars]
            chunks.append(header + body + footer)
            used_tokens += overhead_tokens + max(1, len(body) // 4)
            if len(body) < len(safe_text):
                break
        if not chunks:
            return ""
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
