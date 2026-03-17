"""Tests for app/rag/retriever.py — code block extraction, query building, retrieval."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.rag.retriever import (
    RAGRetriever,
    _build_code_query,
    _extract_code_blocks,
)
from app.rag.vector_store import QueryResult


# ---------------------------------------------------------------------------
# _extract_code_blocks
# ---------------------------------------------------------------------------

def test_extract_no_blocks():
    assert _extract_code_blocks("Plain text, no code here.") == []


def test_extract_single_block():
    text = "Review this:\n```python\nprint('hello')\n```"
    blocks = _extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["language"] == "python"
    assert "print" in blocks[0]["code"]


def test_extract_multiple_blocks():
    text = "```js\nconst x = 1;\n```\n\n```python\npass\n```"
    blocks = _extract_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0]["language"] == "js"
    assert blocks[1]["language"] == "python"


def test_extract_block_without_language():
    text = "```\nsome code\n```"
    blocks = _extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["language"] == ""


def test_extract_filepath_from_comment_hash():
    text = "```python\n# app/models/user.py\nclass User: pass\n```"
    blocks = _extract_code_blocks(text)
    assert blocks[0]["filepath"] == "app/models/user.py"


def test_extract_filepath_from_comment_slash():
    text = "```js\n// src/index.js\nconsole.log(1)\n```"
    blocks = _extract_code_blocks(text)
    assert blocks[0]["filepath"] == "src/index.js"


def test_extract_no_filepath_when_no_comment():
    text = "```python\nclass Foo: pass\n```"
    blocks = _extract_code_blocks(text)
    assert blocks[0]["filepath"] == ""


# ---------------------------------------------------------------------------
# _build_code_query
# ---------------------------------------------------------------------------

def test_build_code_query_includes_original():
    blocks = [{"filepath": "", "code": "x = 1"}]
    query = _build_code_query(blocks, "What does this do?")
    assert "What does this do?" in query


def test_build_code_query_includes_filepath():
    blocks = [{"filepath": "app/foo.py", "code": "x = 1"}]
    query = _build_code_query(blocks, "question")
    assert "app/foo.py" in query


def test_build_code_query_includes_code_snippet():
    code = "def foo():\n    return 42\n"
    blocks = [{"filepath": "", "code": code}]
    query = _build_code_query(blocks, "question")
    assert "def foo" in query


def test_build_code_query_truncates_long_code():
    long_code = "x = 1\n" * 200  # > 300 chars
    blocks = [{"filepath": "", "code": long_code}]
    query = _build_code_query(blocks, "q")
    # Only first 300 chars of code should be included
    assert long_code[:300] in query
    assert long_code[300:] not in query


def test_build_code_query_no_filepath_skipped():
    blocks = [{"filepath": "", "code": "pass"}]
    query = _build_code_query(blocks, "original")
    parts = query.split("\n")
    # Should have: original + code (no extra blank filepath line)
    assert "" not in parts or parts.count("") <= 1


# ---------------------------------------------------------------------------
# RAGRetriever — settings fixture
# ---------------------------------------------------------------------------

class _FakeSettings:
    rag_top_k = 5
    rag_score_threshold = 0.75
    rag_context_prefix = "CONTEXT:\n"
    rag_context_separator = "\n---\n"


def _make_result(doc_id, text, distance, doc_type="doc"):
    return QueryResult(
        doc_id=doc_id,
        text=text,
        metadata={"source": f"src/{doc_id}", "symbol": "", "title": doc_id, "doc_type": doc_type},
        distance=distance,
    )


@pytest.fixture
def retriever():
    return RAGRetriever(_FakeSettings())


# ---------------------------------------------------------------------------
# Empty query
# ---------------------------------------------------------------------------
и
@pytest.mark.asyncio
async def test_empty_query_returns_empty(retriever):
    ctx, n = await retriever.retrieve_context("")
    assert ctx == ""
    assert n == 0


@pytest.mark.asyncio
async def test_whitespace_query_returns_empty(retriever):
    ctx, n = await retriever.retrieve_context("   \n  ")
    assert ctx == ""
    assert n == 0


# ---------------------------------------------------------------------------
# Single-signal retrieval (no code blocks)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_retrieval_below_threshold(retriever):
    results = [_make_result("doc1", "relevant content", 0.5)]
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=results),
    ):
        ctx, n = await retriever.retrieve_context("find something")

    assert n == 1
    assert "relevant content" in ctx
    assert ctx.startswith("CONTEXT:\n")


@pytest.mark.asyncio
async def test_single_retrieval_above_threshold_excluded(retriever):
    results = [_make_result("doc1", "irrelevant", 0.9)]
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=results),
    ):
        ctx, n = await retriever.retrieve_context("find something")

    assert n == 0
    assert ctx == ""


@pytest.mark.asyncio
async def test_single_retrieval_mixed_threshold(retriever):
    results = [
        _make_result("good", "good content", 0.5),
        _make_result("bad", "noisy content", 0.9),
    ]
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=results),
    ):
        ctx, n = await retriever.retrieve_context("query")

    assert n == 1
    assert "good content" in ctx
    assert "noisy content" not in ctx


# ---------------------------------------------------------------------------
# Multi-signal retrieval (code blocks present)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_signal_triggered_by_code_block(retriever):
    query = "What does this do?\n```python\ndef foo(): pass\n```"
    results = [
        _make_result("c1", "code chunk", 0.4, doc_type="code"),
        _make_result("d1", "doc chunk", 0.5, doc_type="doc"),
    ]
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=results),
    ):
        ctx, n = await retriever.retrieve_context(query)

    assert n == 2
    assert "code chunk" in ctx
    assert "doc chunk" in ctx


@pytest.mark.asyncio
async def test_multi_signal_splits_by_doc_type(retriever):
    """Each doc_type capped at top_k // 2 = 2."""
    code_results = [_make_result(f"c{i}", f"code {i}", 0.3, "code") for i in range(5)]
    doc_results = [_make_result(f"d{i}", f"doc {i}", 0.4, "doc") for i in range(5)]
    all_results = code_results + doc_results

    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=all_results),
    ):
        ctx, n = await retriever.retrieve_context("fix:\n```py\npass\n```")

    # top_k=5, half=2: max 2 code + 2 doc = 4
    assert n <= 4


@pytest.mark.asyncio
async def test_multi_signal_deduplication(retriever):
    """Duplicate doc_ids should appear only once."""
    r = _make_result("dup", "duplicate", 0.3, "code")
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=[r, r]),
    ):
        ctx, n = await retriever.retrieve_context("fix:\n```py\npass\n```")

    assert n == 1


@pytest.mark.asyncio
async def test_multi_signal_all_above_threshold_returns_empty(retriever):
    results = [_make_result("c1", "code", 0.9, "code")]
    with (
        patch("app.rag.retriever.embedder.embed_one", return_value=[0.1] * 384),
        patch("app.rag.retriever.vector_store.query", return_value=results),
    ):
        ctx, n = await retriever.retrieve_context("fix:\n```py\npass\n```")

    assert n == 0
    assert ctx == ""


# ---------------------------------------------------------------------------
# _format output structure
# ---------------------------------------------------------------------------

def test_format_includes_label_with_symbol(retriever):
    results = [QueryResult(
        doc_id="x",
        text="body text",
        metadata={"source": "src/x.py", "symbol": "MyClass", "title": "X", "doc_type": "code"},
        distance=0.3,
    )]
    formatted = retriever._format(results)
    assert "[X:MyClass]" in formatted
    assert "body text" in formatted


def test_format_module_doc_uses_title_only(retriever):
    results = [QueryResult(
        doc_id="x",
        text="module header",
        metadata={"source": "src/x.py", "symbol": "__module__", "title": "X", "doc_type": "code"},
        distance=0.2,
    )]
    formatted = retriever._format(results)
    assert "[X]" in formatted
    assert "__module__" not in formatted


def test_format_empty_returns_empty(retriever):
    assert retriever._format([]) == ""


def test_format_separator_between_chunks(retriever):
    results = [
        _make_result("a", "chunk one", 0.2),
        _make_result("b", "chunk two", 0.3),
    ]
    formatted = retriever._format(results)
    assert "---" in formatted
