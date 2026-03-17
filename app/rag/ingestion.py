"""Document ingestion pipeline: chunk → embed → upsert into ChromaDB."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.rag import embedder, vector_store

# Doc types
DOC_EXTENSIONS = {".txt", ".md", ".rst"}
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rb",
    ".java",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
}
SUPPORTED_EXTENSIONS = DOC_EXTENSIONS | CODE_EXTENSIONS

# Tree-sitter language map (extension → language name)
_TS_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
}

# Tree-sitter node types that represent top-level symbols
_SYMBOL_NODES = {
    "function_definition",
    "function_declaration",
    "method_definition",
    "method_declaration",
    "class_definition",
    "class_declaration",
    "impl_item",  # Rust
    "function_item",  # Rust
}

DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 50


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Word-based chunking for prose docs."""
    words = text.split()
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunks.append(" ".join(words[i : i + chunk_size]))
        if i + chunk_size >= len(words):
            break
    return [c for c in chunks if c.strip()]


def _extract_symbol_name(node, source: str) -> str:
    """Extract the name identifier from a tree-sitter symbol node."""
    for child in node.children:
        if child.type == "identifier":
            return source[child.start_byte : child.end_byte]
    return ""


def _chunk_code(content: str, filepath: str) -> list[dict]:
    """
    AST-aware chunking using tree-sitter. Each top-level function/class
    becomes its own chunk. Falls back to word-based for unknown languages.
    Returns list of dicts with 'text', 'symbol', 'kind', 'start_line'.
    """
    ext = Path(filepath).suffix.lower()
    lang_name = _TS_LANGUAGES.get(ext)

    if lang_name:
        try:
            from tree_sitter_languages import get_parser

            parser = get_parser(lang_name)
            tree = parser.parse(content.encode())
            chunks = []

            # Module-level docstring / file header (first 20 lines)
            header_lines = content.split("\n")[:20]
            header = "\n".join(header_lines).strip()
            if header:
                chunks.append(
                    {
                        "text": header,
                        "symbol": "__module__",
                        "kind": "module_doc",
                        "start_line": 0,
                    }
                )

            for node in tree.root_node.children:
                if node.type in _SYMBOL_NODES:
                    text = content[node.start_byte : node.end_byte]
                    symbol = _extract_symbol_name(node, content)
                    chunks.append(
                        {
                            "text": text,
                            "symbol": symbol,
                            "kind": node.type,
                            "start_line": node.start_point[0],
                        }
                    )

            if chunks:
                return chunks
        except Exception:
            pass  # fall through to word-based

    # Fallback: treat code as prose
    return [{"text": t, "symbol": "", "kind": "chunk", "start_line": 0} for t in _chunk_text(content)]


def ingest_file(path: Path, collection_filter: dict | None = None) -> int:
    """Ingest a single file. Returns number of chunks upserted."""
    suffix = path.suffix.lower()
    content = _read_file(path)
    ingested_at = datetime.now(timezone.utc).isoformat()
    is_code = suffix in CODE_EXTENSIONS

    if is_code:
        raw_chunks = _chunk_code(content, str(path))
    else:
        raw_chunks = [{"text": t, "symbol": "", "kind": "chunk", "start_line": 0} for t in _chunk_text(content)]

    if not raw_chunks:
        return 0

    texts = [c["text"] for c in raw_chunks]
    embeddings = embedder.embed(texts)

    ids, metadatas = [], []
    for i, (chunk, raw) in enumerate(zip(texts, raw_chunks)):
        chunk_id = hashlib.sha256(f"{path}:{i}:{chunk[:50]}".encode()).hexdigest()[:16]
        ids.append(chunk_id)
        metadatas.append(
            {
                "source": str(path),
                "title": path.stem.replace("_", " ").replace("-", " ").title(),
                "doc_type": "code" if is_code else "doc",
                "language": _TS_LANGUAGES.get(suffix, ""),
                "symbol": raw["symbol"],
                "kind": raw["kind"],
                "start_line": raw["start_line"],
                "chunk_index": i,
                "total_chunks": len(raw_chunks),
                "ingested_at": ingested_at,
                **(collection_filter or {}),
            }
        )

    vector_store.upsert_documents(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    return len(raw_chunks)
