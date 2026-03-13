from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.content_policy import init_content_policy
from app.core.exceptions import ProxyError, proxy_exception_handler
from app.core.rate_limiter import init_rate_limiter
from app.db.engine import create_all_tables
from app.db.analytics import ensure_analytics_view, refresh_analytics_view
from app.analytics.langfuse import init_langfuse
from app.llm.client import init_cache, init_llm_client
from app.metrics.prometheus import metrics_response
from app.pii.restorer import init_restorer
from app.pii.scrubber import init_scrubber
from app.rag.embedder import init_embedder
from app.rag.retriever import init_retriever
from app.rag.vector_store import init_vector_store

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logging.basicConfig(level=settings.log_level.upper())
    log.info("Starting LLM Proxy", log_level=settings.log_level)

    # Database
    await create_all_tables()
    log.info("Database tables ready")

    # Analytics materialized view (Postgres only — no-op on SQLite)
    try:
        await ensure_analytics_view()
    except Exception as exc:
        log.warning("Could not create analytics view", error=str(exc))

    # PII
    if settings.pii_enabled:
        log.info("Initializing Presidio PII scrubber...")
        init_scrubber(settings)
        init_restorer()
        log.info("PII scrubber ready")
    else:
        # Initialize with disabled state so dependencies resolve
        init_scrubber(settings)
        init_restorer()

    # RAG
    if settings.rag_enabled:
        log.info("Initializing embedding model and vector store...")
        init_embedder(settings)
        init_vector_store(settings)
        init_retriever(settings)
        log.info("RAG pipeline ready")
    else:
        init_retriever(settings)

    # Analytics (optional)
    if init_langfuse(settings):
        log.info("Langfuse analytics active")
    elif settings.analytics_enabled:
        log.warning("Analytics enabled but failed to initialize — check credentials")

    # LLM cache (must be set on litellm globals before first request)
    init_cache(settings)

    # LLM
    init_llm_client(settings)
    log.info(
        "LLM client ready",
        default_model=settings.default_model,
        fallbacks=settings.fallback_models or "none",
        cache=settings.cache_type if settings.cache_enabled else "disabled",
    )

    # Rate limiter
    init_rate_limiter(settings)

    # Content policy
    init_content_policy(settings)

    log.info("LLM Proxy ready", host=settings.host, port=settings.port)

    # Hourly analytics refresh (Postgres only — no-op on SQLite)
    async def _refresh_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                await refresh_analytics_view()
            except Exception as exc:
                log.warning("Analytics view refresh failed", error=str(exc))

    refresh_task = asyncio.create_task(_refresh_loop())

    yield

    refresh_task.cancel()
    log.info("Shutting down LLM Proxy")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="LLM Proxy",
        description="In-house AI gateway with PII scrubbing, RAG, usage tracking, and Prometheus metrics",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — lock this down per your internal network requirements
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    # Exception handlers
    app.add_exception_handler(ProxyError, proxy_exception_handler)

    # Routers
    from app.api.v1.chat import router as chat_router
    from app.api.v1.health import router as health_router
    from app.api.v1.messages import router as messages_router
    from app.api.v1.models import router as models_router
    from app.api.internal.admin import router as admin_router
    from app.api.internal.kb import router as kb_router
    from app.api.auth import router as auth_router

    app.include_router(health_router)
    app.include_router(chat_router, prefix="/v1")
    app.include_router(messages_router, prefix="/v1")
    app.include_router(models_router, prefix="/v1")
    app.include_router(admin_router, prefix="/internal")
    app.include_router(kb_router, prefix="/internal")
    app.include_router(auth_router)

    # Prometheus metrics endpoint
    app.get("/metrics", include_in_schema=False)(metrics_response)

    return app


app = create_app()
