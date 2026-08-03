"""Same-origin user portal and strictly user-scoped APIs."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.db.engine import get_db
from app.db.models import ApiKey, User
from app.db.repositories.users import create_api_key, revoke_api_key, rotate_api_key
from app.portal.operations import get_owned_key, get_portal_overview, key_metadata
from app.portal.pages import login_page, portal_page
from app.portal.session import (
    COOKIE_NAME,
    PortalSession,
    clear_portal_session_cookie,
    verify_portal_session,
)

router = APIRouter(tags=["developer-portal"])


class PortalKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=50)
    expires_in_days: int = Field(ge=1)


def _require_enabled(settings: Settings) -> None:
    if not settings.portal__enabled:
        raise HTTPException(status_code=404, detail="Developer portal is disabled")


async def require_portal_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> PortalSession:
    _require_enabled(settings)
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise AuthenticationError("Portal sign-in required")
    session = verify_portal_session(token, settings)
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Relay account is disabled")
    return session


def require_portal_csrf(
    session: PortalSession = Depends(require_portal_session),
    csrf_token: str = Header(default="", alias="X-Relay-CSRF"),
) -> PortalSession:
    if not hmac.compare_digest(csrf_token, session.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return session


def _html_response(content: str) -> HTMLResponse:
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'self' https://cdn.jsdelivr.net; "
                "script-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.get("/portal/login", include_in_schema=False)
async def portal_login_page(settings: Settings = Depends(get_settings)):
    _require_enabled(settings)
    return _html_response(login_page(oidc_enabled=settings.oauth_enabled))


@router.get("/portal", include_in_schema=False)
async def developer_portal(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    try:
        session = await require_portal_session(request, settings, db)
    except AuthenticationError:
        return RedirectResponse("/portal/login", status_code=303)
    return _html_response(
        portal_page(
            csrf_token=session.csrf_token,
            session_expires_at=session.expires_at,
            display_name=session.display_name,
            email=session.email,
        )
    )


@router.post("/portal/logout", include_in_schema=False)
async def portal_logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
    settings: Settings = Depends(get_settings),
):
    _require_enabled(settings)
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        session = verify_portal_session(token, settings)
        if not hmac.compare_digest(csrf_token, session.csrf_token):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
    response = RedirectResponse("/portal/login", status_code=303)
    clear_portal_session_cookie(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/portal/api/overview")
async def portal_overview(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    session = await require_portal_session(request, settings, db)
    result = await get_portal_overview(db, settings, user_id=session.user_id, days=days)
    if result is None:
        raise AuthenticationError("Relay account is disabled")
    result.update(
        {
            "display_name": session.display_name,
            "email": session.email,
            "base_url": settings.auth_base_url.rstrip("/"),
            "default_model": settings.default_model,
            "available_models": settings.allowed_models,
            "mcp_enabled": settings.mcp_enabled,
        }
    )
    return result


@router.post("/portal/api/keys", status_code=201)
async def create_portal_key(
    body: PortalKeyCreateRequest,
    session: PortalSession = Depends(require_portal_csrf),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Key name cannot be blank")
    allowed_scopes = set(settings.oidc__default_key_scopes)
    requested_scopes = list(dict.fromkeys(body.scopes))
    if not set(requested_scopes).issubset(allowed_scopes):
        raise HTTPException(status_code=403, detail="One or more scopes are not available for self-service")
    if body.expires_in_days > settings.portal__max_key_ttl_days:
        raise HTTPException(
            status_code=400,
            detail=f"Self-service keys may live for at most {settings.portal__max_key_ttl_days} days",
        )
    # Serialize key creation per user on databases that support row locks so
    # concurrent requests cannot bypass the configured active-key ceiling.
    user = await db.scalar(select(User).where(User.id == session.user_id).with_for_update())
    if user is None or not user.is_active:
        raise AuthenticationError("Relay account is disabled")
    now = datetime.now(timezone.utc)
    active_count = await db.scalar(
        select(func.count(ApiKey.id)).where(
            ApiKey.user_id == session.user_id,
            ApiKey.is_active.is_(True),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
    )
    if int(active_count or 0) >= settings.portal__max_active_keys:
        raise HTTPException(status_code=409, detail="Active key limit reached; revoke an unused key first")
    raw_key, key = await create_api_key(
        db,
        user_id=session.user_id,
        name=name,
        scopes=requested_scopes,
        expires_at=now + timedelta(days=body.expires_in_days),
        actor=f"portal:{session.user_id}",
    )
    return {"key": raw_key, **key_metadata(key)}


@router.get("/portal/api/keys")
async def list_portal_keys(
    session: PortalSession = Depends(require_portal_session),
    db: AsyncSession = Depends(get_db),
):
    keys = (
        await db.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == session.user_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
            .limit(100)
        )
    ).all()
    return {"items": [key_metadata(key) for key in keys]}


@router.post("/portal/api/keys/{key_id}/rotate")
async def rotate_portal_key(
    key_id: str,
    session: PortalSession = Depends(require_portal_csrf),
    db: AsyncSession = Depends(get_db),
):
    key = await get_owned_key(db, user_id=session.user_id, key_id=key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if key_metadata(key)["status"] != "active":
        raise HTTPException(status_code=409, detail="Only active keys can be rotated")
    result = await rotate_api_key(db, key_id=key.id, actor=f"portal:{session.user_id}")
    if result is None:
        raise HTTPException(status_code=409, detail="API key is no longer active")
    raw_key, replacement, revoked = result
    return {"key": raw_key, "replacement": key_metadata(replacement), "revoked": key_metadata(revoked)}


@router.delete("/portal/api/keys/{key_id}", status_code=204)
async def revoke_portal_key(
    key_id: str,
    session: PortalSession = Depends(require_portal_csrf),
    db: AsyncSession = Depends(get_db),
):
    key = await get_owned_key(db, user_id=session.user_id, key_id=key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    await revoke_api_key(db, key_id=key.id, actor=f"portal:{session.user_id}")
