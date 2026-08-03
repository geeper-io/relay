from unittest.mock import MagicMock, patch

import chromadb

from app.rag.vector_store import lexical_query, lexical_tokens


def test_lexical_tokens_normalize_and_drop_common_words():
    assert lexical_tokens("How does validate_API_key work with tokens?") == [
        "does",
        "validate_api_key",
        "work",
        "tokens",
    ]


def test_lexical_query_bm25_ranks_exact_terms_and_preserves_filter():
    collection = MagicMock()
    collection.get.return_value = {
        "ids": ["generic", "exact"],
        "documents": [
            "Authentication overview and general policy.",
            "validate_api_key validates API keys. Call validate_api_key before routing.",
        ],
        "metadatas": [{"source": "overview.md"}, {"source": "auth.py"}],
    }

    with patch("app.rag.vector_store.get_collection", return_value=collection):
        results = lexical_query("validate_api_key authentication", n_results=2, where={"repo": "org/repo"})

    assert results[0].doc_id == "exact"
    assert results[0].lexical_score > results[1].lexical_score
    assert collection.get.call_args.kwargs["where"] == {"repo": "org/repo"}


def test_lexical_query_uses_supported_chroma_full_text_filter():
    collection = chromadb.EphemeralClient().create_collection("lexical-filter-test")
    collection.add(
        ids=["auth", "billing"],
        documents=["validate_api_key checks authentication", "invoice processing and billing"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{"repo": "org/repo"}, {"repo": "org/repo"}],
    )

    with patch("app.rag.vector_store.get_collection", return_value=collection):
        results = lexical_query("validate_api_key authentication", n_results=2, where={"repo": "org/repo"})

    assert [result.doc_id for result in results] == ["auth"]
