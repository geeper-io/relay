"""Knowledge base management endpoints."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.core.auth import require_admin
from app.rag import vector_store
from app.rag.ingestion import SUPPORTED_EXTENSIONS, ingest_file
from app.rag.ingestors.github import GitHubIngestor
from app.rag.ingestors.gitlab import GitLabIngestor
from app.rag.sync_engine import sync_ingestor

router = APIRouter(tags=["knowledge-base"], dependencies=[Depends(require_admin)])


class RepoSyncRequest(BaseModel):
    provider: Literal["github", "gitlab"]
    repo: str               # GitHub: "owner/name" | GitLab: numeric project ID or "namespace%2Fproject"
    token: str = ""         # PAT with read access; optional for public repos
    ref: str = "main"
    host: str = "https://gitlab.com"   # GitLab only; ignored for GitHub
    force: bool = False     # ignore stored SHA and re-index from scratch


@router.post("/kb/sync-repo")
async def sync_repo(body: RepoSyncRequest):
    """
    Fetch and index an entire GitHub or GitLab repository.
    Runs in the background — returns immediately with a job acknowledgement.
    Re-ingesting is idempotent: stale chunks are deleted before re-indexing.
    """
    if body.provider == "github":
        ingestor = GitHubIngestor(repo=body.repo, token=body.token, ref=body.ref)
    else:
        ingestor = GitLabIngestor(project_id=body.repo, token=body.token, host=body.host, ref=body.ref)

    coro = sync_ingestor(ingestor, force=body.force)
    asyncio.create_task(coro)
    return {"status": "started", "provider": body.provider, "repo": body.repo, "ref": body.ref}


@router.post("/kb/upload")
async def upload_document(
    file: UploadFile,
    _: None = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Upload and immediately ingest a document or code file."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    kb_dir = Path("knowledge_base")
    kb_dir.mkdir(exist_ok=True)
    dest = kb_dir / (file.filename or "upload.txt")
    content = await file.read()
    dest.write_bytes(content)

    # Delete existing chunks for this source before re-ingesting
    vector_store.delete_by_source(str(dest))
    chunks = ingest_file(dest)
    return {"filename": file.filename, "chunks_ingested": chunks}


@router.delete("/kb/source")
async def delete_source(path: str):
    """Delete all chunks for a given source path."""
    deleted = vector_store.delete_by_source(path)
    return {"deleted_chunks": deleted, "source": path}


@router.get("/kb/search")
async def kb_search(
    q: str,
    n: int = 10,
    repo: str | None = None,
    settings: Settings = Depends(get_settings),
):
    """Debug: run a raw vector search and return chunks with scores."""
    from app.rag.embedder import embed_one
    embedding = embed_one(q)
    where = {"repo": repo} if repo else None
    results = vector_store.query(query_embedding=embedding, n_results=n, where=where)
    threshold = settings.rag_score_threshold
    return {
        "query": q,
        "threshold": threshold,
        "results": [
            {
                "distance": round(r.distance, 4),
                "above_threshold": r.distance > threshold,
                "source": r.metadata.get("source"),
                "symbol": r.metadata.get("symbol"),
                "doc_type": r.metadata.get("doc_type"),
                "text_preview": r.text[:200],
            }
            for r in results
        ],
    }


@router.get("/kb/stats")
async def kb_stats():
    collection = vector_store.get_collection()
    return {"total_documents": collection.count()}


@router.delete("/kb/reset")
async def reset_kb(settings: Settings = Depends(get_settings)):
    """Delete all documents from the knowledge base."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    client = chromadb.PersistentClient(
        path=settings.chroma_persist_dir,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    client.delete_collection(settings.chroma_collection_name)
    vector_store.init_vector_store(settings)
    return {"status": "reset", "collection": settings.chroma_collection_name}
