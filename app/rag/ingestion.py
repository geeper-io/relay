"""Document ingestion pipeline: chunk → embed → upsert into ChromaDB."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from app.rag import embedder, vector_store

SUPPORTED_EXTENSIONS = {".txt", ".md", ".rst"}
DEFAULT_CHUNK_SIZE = 512   # approximate tokens (chars / 4)
DEFAULT_CHUNK_OVERLAP = 50


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks by approximate token count."""
    # Tokenize roughly by whitespace
    words = text.split()
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk_words = words[i: i + chunk_size]
        chunks.append(" ".join(chunk_words))
        if i + chunk_size >= len(words):
            break
    return [c for c in chunks if c.strip()]


def ingest_file(path: Path, collection_filter: dict | None = None) -> int:
    """Ingest a single file. Returns number of chunks upserted."""
    text = _read_file(path)
    chunks = _chunk_text(text)

    if not chunks:
        return 0

    embeddings = embedder.embed(chunks)
    ingested_at = datetime.now(timezone.utc).isoformat()

    ids = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        chunk_id = hashlib.sha256(f"{path}:{i}:{chunk[:50]}".encode()).hexdigest()[:16]
        ids.append(chunk_id)
        metadatas.append({
            "source": str(path),
            "title": path.stem.replace("_", " ").replace("-", " ").title(),
            "chunk_index": i,
            "total_chunks": len(chunks),
            "ingested_at": ingested_at,
            **(collection_filter or {}),
        })

    vector_store.upsert_documents(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(chunks)
