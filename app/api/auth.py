"""Google OAuth 2.0 login portal.

Flow:
  GET /auth/login     → redirect to Google consent screen
  GET /auth/callback  → exchange code, upsert user, issue API key, show HTML

Set these env vars (or config.yaml keys) to enable:
  GOOGLE_CLIENT_ID=...
  GOOGLE_CLIENT_SECRET=...
  AUTH_BASE_URL=https://your-proxy.internal   (for the redirect_uri)
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from html import escape
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.users import create_api_key, create_user, get_user_by_external_id

router = APIRouter(tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# ── State signing (HMAC-SHA256, no server-side storage needed) ────────────────

def _make_state(secret: str) -> str:
    nonce = secrets.token_urlsafe(32)
    sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{nonce}.{sig}"


def _verify_state(state: str, secret: str) -> bool:
    parts = state.split(".", 1)
    if len(parts) != 2:
        return False
    nonce, sig = parts
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(sig, expected)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/auth/login", include_in_schema=False)
async def login(settings: Settings = Depends(get_settings)):
    if not settings.oauth_enabled:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth is not configured on this proxy",
        )

    state = _make_state(settings.proxy_master_key)
    redirect_uri = f"{settings.auth_base_url.rstrip('/')}/auth/callback"

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{_GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/auth/callback", include_in_schema=False)
async def oauth_callback(
    code: str,
    state: str,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    if not settings.oauth_enabled:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")

    if not _verify_state(state, settings.proxy_master_key):
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again")

    redirect_uri = f"{settings.auth_base_url.rstrip('/')}/auth/callback"

    async with httpx.AsyncClient() as client:
        # Exchange authorization code for tokens
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Token exchange with Google failed")

        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="No access token returned by Google")

        # Fetch user profile
        userinfo_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch Google user info")

        userinfo = userinfo_resp.json()

    google_sub = userinfo.get("id") or userinfo.get("sub")
    email = userinfo.get("email", "")
    name = userinfo.get("name") or email

    if not google_sub:
        raise HTTPException(status_code=502, detail="Could not identify Google account")

    # Find or create user (keyed by stable Google account ID)
    external_id = f"google:{google_sub}"
    user = await get_user_by_external_id(db, external_id)
    is_new = user is None
    if user is None:
        user = await create_user(db, external_id=external_id)

    # Issue a fresh API key on every login
    raw_key, _api_key = await create_api_key(db, user_id=user.id, name="sso")

    return HTMLResponse(_key_page(name=name, email=email, raw_key=raw_key, is_new=is_new))


# ── HTML page ─────────────────────────────────────────────────────────────────

def _key_page(name: str, email: str, raw_key: str, is_new: bool) -> str:
    safe_name = escape(name)
    safe_email = escape(email)
    greeting = "Welcome! Your account has been created." if is_new else "Welcome back!"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Geeper Relay — API Key</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f2f5;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1rem;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,.08);
      max-width: 560px;
      width: 100%;
      padding: 2.5rem;
    }}
    h1 {{ font-size: 1.4rem; color: #111; margin-bottom: .3rem; }}
    .sub {{ color: #555; font-size: .9rem; margin-bottom: 1.5rem; }}
    .badge {{
      display: inline-block;
      background: #e8f5e9;
      color: #2e7d32;
      border-radius: 999px;
      padding: .2rem .8rem;
      font-size: .8rem;
      font-weight: 600;
      margin-bottom: 1.5rem;
    }}
    label {{
      display: block;
      font-size: .75rem;
      font-weight: 700;
      color: #444;
      letter-spacing: .06em;
      text-transform: uppercase;
      margin-bottom: .5rem;
    }}
    .key-row {{
      display: flex;
      align-items: center;
      gap: .5rem;
      background: #f7f7f7;
      border: 1.5px solid #e0e0e0;
      border-radius: 8px;
      padding: .7rem 1rem;
    }}
    .key-text {{
      flex: 1;
      font-family: 'Courier New', monospace;
      font-size: .9rem;
      color: #111;
      word-break: break-all;
    }}
    .copy-btn {{
      flex-shrink: 0;
      background: #111;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: .45rem .9rem;
      cursor: pointer;
      font-size: .8rem;
      transition: background .15s;
    }}
    .copy-btn:hover {{ background: #333; }}
    .warn {{
      margin-top: 1rem;
      background: #fff8e1;
      border-left: 4px solid #ffc107;
      border-radius: 4px;
      padding: .8rem 1rem;
      font-size: .85rem;
      color: #5d4037;
      line-height: 1.5;
    }}
    .steps {{ margin-top: 1.75rem; }}
    .steps h2 {{
      font-size: .75rem;
      font-weight: 700;
      color: #444;
      letter-spacing: .06em;
      text-transform: uppercase;
      margin-bottom: .75rem;
    }}
    .steps pre {{
      background: #1e1e1e;
      color: #d4d4d4;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      font-size: .82rem;
      overflow-x: auto;
      line-height: 1.6;
    }}
    .footer {{
      margin-top: 1.75rem;
      font-size: .8rem;
      color: #aaa;
      text-align: center;
    }}
  </style>
</head>
<body>
<div class="card">
  <h1>Your Geeper Relay API Key</h1>
  <p class="sub">{greeting} Signed in as <strong>{safe_name}</strong> ({safe_email})</p>
  <span class="badge">New key generated</span>

  <label>API Key &mdash; copy now, shown only once</label>
  <div class="key-row">
    <span class="key-text" id="key">{raw_key}</span>
    <button class="copy-btn" onclick="
      navigator.clipboard.writeText(document.getElementById('key').textContent)
        .then(() => {{ this.textContent = 'Copied!'; setTimeout(() => this.textContent = 'Copy', 2000); }})
    ">Copy</button>
  </div>
  <div class="warn">
    Save this key now &mdash; it cannot be retrieved again. If you lose it, log in again to generate a new one.
  </div>

  <div class="steps">
    <h2>Connect Claude Code</h2>
    <pre>export ANTHROPIC_BASE_URL=https://your-proxy.internal
export ANTHROPIC_AUTH_TOKEN={raw_key}</pre>
  </div>

  <div class="footer">Geeper Relay &mdash; Internal AI Gateway</div>
</div>
</body>
</html>"""
