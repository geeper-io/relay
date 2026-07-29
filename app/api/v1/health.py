from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.core.rate_limiter import RateLimiter, get_rate_limiter
from app.db.engine import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    checks: dict[str, str] = {}
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    if settings.rag_enabled:
        try:
            from app.rag.vector_store import get_collection

            get_collection().count()
            checks["vector_store"] = "ok"
        except Exception:
            checks["vector_store"] = "unavailable"

    try:
        checks["rate_limiter"] = "ok" if await rate_limiter.ping() else "unavailable"
    except Exception:
        checks["rate_limiter"] = "unavailable"

    ready = all(value == "ok" for value in checks.values())
    payload = {"status": "ready" if ready else "not_ready", "checks": checks}
    return payload if ready else JSONResponse(status_code=503, content=payload)
