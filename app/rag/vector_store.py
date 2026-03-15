from __future__ import annotations

from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


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
    return _collection


def get_collection() -> chromadb.Collection:
    if _collection is None:
        raise RuntimeError("Vector store not initialized")
    return _collection


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
