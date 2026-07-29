import litellm
from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.core.auth import ResolvedIdentity, require_scope

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(
    settings: Settings = Depends(get_settings),
    _identity: ResolvedIdentity = Depends(require_scope("chat")),
):
    if settings.allowed_models:
        models = settings.allowed_models
    else:
        try:
            resp = litellm.get_valid_models()
            models = resp or []
        except Exception:
            models = []

    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "owned_by": "proxy"} for m in models],
    }
