"""Tests for app/rag/ingestion.py — chunking pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.rag.ingestion import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    _chunk_code,
    _chunk_text,
    ingest_file,
)

# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_basic():
    words = ["word"] * 20
    text = " ".join(words)
    chunks = _chunk_text(text, chunk_size=10, overlap=0)
    assert len(chunks) == 2
    assert all(len(c.split()) == 10 for c in chunks)


def test_chunk_text_overlap():
    words = list(map(str, range(10)))
    text = " ".join(words)
    chunks = _chunk_text(text, chunk_size=6, overlap=2)
    # step = 6 - 2 = 4; first chunk 0-5, second 4-9
    assert chunks[0].split()[:4] == ["0", "1", "2", "3"]
    assert chunks[1].split()[:2] == ["4", "5"]
    # overlapping words appear in consecutive chunks
    assert "4" in chunks[0]
    assert "4" in chunks[1]


def test_chunk_text_empty():
    assert _chunk_text("") == []
    assert _chunk_text("   \n  ") == []


def test_chunk_text_single_chunk():
    text = "hello world"
    chunks = _chunk_text(text, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP)
    assert chunks == ["hello world"]


def test_chunk_text_exact_size():
    words = ["w"] * DEFAULT_CHUNK_SIZE
    text = " ".join(words)
    chunks = _chunk_text(text)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# _chunk_code — tree-sitter path (Python)
# ---------------------------------------------------------------------------

_PYTHON_SOURCE = """\
\"\"\"Module docstring.\"\"\"


def add(a, b):
    return a + b


class Calculator:
    def multiply(self, x, y):
        return x * y
"""


def _tree_sitter_works() -> bool:
    try:
        from tree_sitter_languages import get_parser

        get_parser("python")
        return True
    except Exception:
        return False


_ts_available = pytest.mark.skipif(
    not _tree_sitter_works(),
    reason="tree-sitter-languages not functional in this environment",
)


@_ts_available
def test_chunk_code_python_symbols():
    chunks = _chunk_code(_PYTHON_SOURCE, "calc.py")
    kinds = [c["kind"] for c in chunks]
    # Should have module_doc + at least one function/class chunk
    assert "module_doc" in kinds
    # At least one top-level symbol detected
    symbol_kinds = {"function_definition", "function_declaration", "class_definition"}
    assert any(k in symbol_kinds for k in kinds)


@_ts_available
def test_chunk_code_python_symbol_names():
    chunks = _chunk_code(_PYTHON_SOURCE, "calc.py")
    symbols = {c["symbol"] for c in chunks}
    assert "add" in symbols or "Calculator" in symbols


@_ts_available
def test_chunk_code_python_module_doc_first_line():
    chunks = _chunk_code(_PYTHON_SOURCE, "calc.py")
    module_chunks = [c for c in chunks if c["kind"] == "module_doc"]
    assert module_chunks
    assert "Module docstring" in module_chunks[0]["text"]


# ---------------------------------------------------------------------------
# _chunk_code — fallback path (unknown extension)
# ---------------------------------------------------------------------------


def test_chunk_code_unknown_extension_falls_back():
    content = " ".join(["token"] * 30)
    chunks = _chunk_code(content, "file.xyz")
    assert chunks
    assert all(c["kind"] == "chunk" for c in chunks)
    assert all(c["symbol"] == "" for c in chunks)


def test_chunk_code_empty_content_fallback():
    chunks = _chunk_code("", "file.xyz")
    assert chunks == []


# ---------------------------------------------------------------------------
# ingest_file
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_embedder_and_store():
    fake_embedding = [0.1] * 384
    with (
        patch("app.rag.ingestion.embedder.embed", return_value=[fake_embedding]) as mock_embed,
        patch("app.rag.ingestion.vector_store.upsert_documents") as mock_upsert,
    ):
        yield mock_embed, mock_upsert


def test_ingest_file_markdown(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store
    mock_embed.side_effect = lambda texts: [[0.1] * 384] * len(texts)

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("# Hello\n\nThis is a test document with some content.")
        path = Path(f.name)

    try:
        count = ingest_file(path)
        assert count > 0
        mock_upsert.assert_called_once()
        _, kwargs = mock_upsert.call_args
        # or positional
        call_args = mock_upsert.call_args
        ids = call_args[1].get("ids") or call_args[0][0]
        assert len(ids) == count
    finally:
        path.unlink(missing_ok=True)


def test_ingest_file_python(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store
    mock_embed.side_effect = lambda texts: [[0.1] * 384] * len(texts)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_PYTHON_SOURCE)
        path = Path(f.name)

    try:
        count = ingest_file(path)
        assert count > 0
        mock_upsert.assert_called_once()
    finally:
        path.unlink(missing_ok=True)


def test_ingest_file_collection_filter_merged(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store
    mock_embed.side_effect = lambda texts: [[0.1] * 384] * len(texts)

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("Some content to index.")
        path = Path(f.name)

    try:
        ingest_file(path, collection_filter={"source": "custom/source", "repo": "my-repo"})
        call_args = mock_upsert.call_args
        metadatas = call_args[1].get("metadatas") or call_args[0][3]
        for meta in metadatas:
            assert meta["source"] == "custom/source"
            assert meta["repo"] == "my-repo"
    finally:
        path.unlink(missing_ok=True)


def test_ingest_file_empty_returns_zero(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("   \n\n   ")
        path = Path(f.name)

    try:
        count = ingest_file(path)
        assert count == 0
        mock_upsert.assert_not_called()
    finally:
        path.unlink(missing_ok=True)


def test_ingest_file_doc_type_metadata(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store
    mock_embed.side_effect = lambda texts: [[0.1] * 384] * len(texts)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def foo(): pass\n")
        path = Path(f.name)

    try:
        ingest_file(path)
        call_args = mock_upsert.call_args
        metadatas = call_args[1].get("metadatas") or call_args[0][3]
        assert all(m["doc_type"] == "code" for m in metadatas)
    finally:
        path.unlink(missing_ok=True)


def test_ingest_file_markdown_doc_type(mock_embedder_and_store):
    mock_embed, mock_upsert = mock_embedder_and_store
    mock_embed.side_effect = lambda texts: [[0.1] * 384] * len(texts)

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("Hello world content.")
        path = Path(f.name)

    try:
        ingest_file(path)
        call_args = mock_upsert.call_args
        metadatas = call_args[1].get("metadatas") or call_args[0][3]
        assert all(m["doc_type"] == "doc" for m in metadatas)
    finally:
        path.unlink(missing_ok=True)
