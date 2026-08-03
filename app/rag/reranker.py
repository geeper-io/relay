"""Optional cross-encoder reranking for fused retrieval candidates."""

from __future__ import annotations

from sentence_transformers import CrossEncoder

from app.config import Settings
from app.rag.vector_store import QueryResult

_model: CrossEncoder | None = None


def init_reranker(settings: Settings) -> CrossEncoder | None:
    global _model
    _model = CrossEncoder(settings.rag_reranker_model) if settings.rag_reranker_model else None
    return _model


def rerank(query: str, candidates: list[QueryResult]) -> list[QueryResult]:
    if _model is None or not candidates:
        return candidates
    scores = _model.predict([(query, candidate.text) for candidate in candidates])
    for candidate, score in zip(candidates, scores, strict=True):
        candidate.rerank_score = float(score)
    return sorted(candidates, key=lambda item: (-(item.rerank_score or 0.0), -item.fused_score, item.doc_id))
