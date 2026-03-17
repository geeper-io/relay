#!/usr/bin/env python3
"""CLI script to ingest documents into the knowledge base."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.rag.embedder import init_embedder
from app.rag.ingestion import ingest_directory, ingest_file
from app.rag.vector_store import init_vector_store


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ingest documents into the RAG knowledge base")
    parser.add_argument(
        "path",
        nargs="?",
        default="knowledge_base",
        help="File or directory to ingest (default: knowledge_base/)",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Initializing embedding model: {settings.rag_embedding_model}")
    init_embedder(settings)
    init_vector_store(settings)

    target = Path(args.path)
    if not target.exists():
        print(f"Error: '{target}' does not exist")
        sys.exit(1)

    if target.is_file():
        n = ingest_file(target)
        print(f"Ingested {target.name}: {n} chunks")
    else:
        results = ingest_directory(target)
        total = sum(results.values())
        print(f"\nDone. {len(results)} files, {total} total chunks ingested.")


if __name__ == "__main__":
    main()
