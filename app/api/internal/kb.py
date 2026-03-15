"""Knowledge base management endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.core.auth import require_admin
from app.rag import embedder, vector_store
from app.rag.ingestion import ingest_file

router = APIRouter(tags=["knowledge-base"], dependencies=[Depends(require_admin)])



@router.post("/kb/upload")
async def upload_document(
    file: UploadFile,
    settings: Settings = Depends(get_settings),
):
    """Upload and immediately ingest a document."""
    allowed_suffixes = {".txt", ".md", ".rst"}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {allowed_suffixes}",
        )

    # Save temporarily
    kb_dir = Path("knowledge_base")
    kb_dir.mkdir(exist_ok=True)
    dest = kb_dir / (file.filename or "upload.txt")
    content = await file.read()
    dest.write_bytes(content)

    chunks = ingest_file(dest)
    return {"filename": file.filename, "chunks_ingested": chunks}


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
