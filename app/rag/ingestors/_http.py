"""Shared HTTP retry helper for ingestors."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

_ERROR_RETRIES = 3
_MAX_RATE_LIMIT_WAIT = 3700  # ~1 hour; bail if reset is further away


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with:
    - Unlimited retries on rate-limit responses (403/429), sleeping until the reset window.
    - Up to _ERROR_RETRIES retries on 5xx with exponential backoff.
    - Immediate raise on other 4xx errors.
    """
    error_attempts = 0
    rate_limit_attempts = 0
    while True:
        r = await client.get(url, **kwargs)

        if r.status_code not in (403, 429) and r.status_code < 500:
            r.raise_for_status()
            return r

        if r.status_code in (403, 429):
            reset_ts = r.headers.get("X-RateLimit-Reset") or r.headers.get("RateLimit-Reset")
            wait = max(1, int(reset_ts) - int(time.time()) + 1) if reset_ts else 60 * (2 ** min(rate_limit_attempts, 4))
            if wait > _MAX_RATE_LIMIT_WAIT:
                r.raise_for_status()
            rate_limit_attempts += 1
            log.warning("Rate limited by %s — retrying in %ds (attempt %d)", url, wait, rate_limit_attempts)
            await asyncio.sleep(wait)
        else:
            error_attempts += 1
            if error_attempts >= _ERROR_RETRIES:
                r.raise_for_status()
            wait = 2**error_attempts * 5
            log.warning(
                "Server error %d from %s — retrying in %ds (attempt %d/%d)",
                r.status_code,
                url,
                wait,
                error_attempts,
                _ERROR_RETRIES,
            )
            await asyncio.sleep(wait)
