from app.api.internal.kb import kb_search
from app.rag.vector_store import QueryResult


class _Settings:
    rag_score_threshold = 0.75


class _Retriever:
    async def retrieve_ranked(self, query, filters=None, limit=None):
        assert query == "authentication"
        assert filters == {"repo": "org/repo"}
        assert limit == 5
        return [
            QueryResult(
                doc_id="auth-chunk",
                text="validate_api_key checks credentials",
                metadata={"source": "auth.py", "symbol": "validate_api_key", "doc_type": "code"},
                distance=0.3,
                lexical_score=1.2,
                fused_score=0.03,
                rerank_score=4.5,
            )
        ]


async def test_kb_search_exposes_production_ranking_diagnostics():
    payload = await kb_search(
        q="authentication",
        n=5,
        repo="org/repo",
        settings=_Settings(),
        retriever=_Retriever(),
    )

    result = payload["results"][0]
    assert result["doc_id"] == "auth-chunk"
    assert result["lexical_score"] == 1.2
    assert result["fused_score"] == 0.03
    assert result["rerank_score"] == 4.5
