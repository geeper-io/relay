from __future__ import annotations

from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None
_sync_collection: chromadb.Collection | None = None


@dataclass
class QueryResult:
    doc_id: str
    text: str
    metadata: dict
    distance: float


def init_vector_store(settings: Settings) -> chromadb.Collection:
    global _client, _collection
    if settings.chroma_host:
        _client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    else:
        _client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    _collection = _client.get_or_create_collection(
        name=settings.chroma_collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    global _sync_collection
    _sync_collection = _client.get_or_create_collection(
        name=f"{settings.chroma_collection_name}__sync",
    )
    return _collection


def get_collection() -> chromadb.Collection:
    if _collection is None:
        raise RuntimeError("Vector store not initialized")
    return _collection


def get_synced_sha(repo: str) -> str | None:
    """Return the last successfully synced commit SHA for a repo, or None."""
    if _sync_collection is None:
        return None
    result = _sync_collection.get(ids=[repo], include=["metadatas"])
    if result["ids"]:
        return result["metadatas"][0].get("sha")
    return None


def set_synced_sha(repo: str, sha: str) -> None:
    """Persist the synced commit SHA for a repo."""
    if _sync_collection is None:
        return
    _sync_collection.upsert(ids=[repo], documents=[sha], metadatas=[{"sha": sha}])


def delete_by_source(source: str) -> int:
    """Delete all chunks whose 'source' metadata matches. Returns deleted count."""
    collection = get_collection()
    existing = collection.get(where={"source": source}, include=[])
    if not existing["ids"]:
        return 0
    collection.delete(ids=existing["ids"])
    return len(existing["ids"])


def upsert_documents(
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    get_collection().upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )


def query(
    query_embedding: list[float],
    n_results: int = 5,
    where: dict | None = None,
) -> list[QueryResult]:
    collection = get_collection()
    kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, collection.count() or 1),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    output: list[QueryResult] = []
    for doc_id, doc, meta, dist in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append(QueryResult(doc_id=doc_id, text=doc, metadata=meta, distance=dist))
    return output
