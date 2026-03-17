from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.core.exceptions import RateLimitError


@dataclass
class TokenBucket:
    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def consume(self, amount: float) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def seconds_until_available(self, amount: float) -> float:
        deficit = amount - self.tokens
        if deficit <= 0:
            return 0.0
        return deficit / self.refill_rate


class RateLimiter:
    """In-memory token-bucket rate limiter. Replace with Redis for multi-worker setups."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = asyncio.Lock()
        # {user_id: {bucket_type: TokenBucket}}
        self._user_buckets: dict[str, dict[str, TokenBucket]] = {}
        # {team_id: TokenBucket}
        self._team_tpm_buckets: dict[str, TokenBucket] = {}

    def _get_user_buckets(self, user_id: str, rpm: int, tpm: int) -> dict[str, TokenBucket]:
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = {
                "rpm": TokenBucket(capacity=rpm, refill_rate=rpm / 60.0),
                "tpm": TokenBucket(capacity=tpm, refill_rate=tpm / 60.0),
            }
        return self._user_buckets[user_id]

    def _get_team_bucket(self, team_id: str, tpm: int) -> TokenBucket:
        if team_id not in self._team_tpm_buckets:
            self._team_tpm_buckets[team_id] = TokenBucket(capacity=tpm, refill_rate=tpm / 60.0)
        return self._team_tpm_buckets[team_id]

    async def check_and_consume(
        self,
        user_id: str,
        team_id: str | None,
        estimated_tokens: int,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
    ) -> None:
        if not self._settings.rate_limit_enabled:
            return

        rpm = rpm_limit or self._settings.default_rpm
        tpm = tpm_limit or self._settings.default_tpm
        team_tpm = self._settings.default_tpm * 5  # team default = 5x user default

        async with self._lock:
            buckets = self._get_user_buckets(user_id, rpm, tpm)

            if not buckets["rpm"].consume(1):
                retry_after = int(buckets["rpm"].seconds_until_available(1)) + 1
                raise RateLimitError(
                    f"Rate limit exceeded: too many requests per minute (limit {rpm})",
                    retry_after=retry_after,
                )

            if not buckets["tpm"].consume(estimated_tokens):
                retry_after = int(buckets["tpm"].seconds_until_available(estimated_tokens)) + 1
                raise RateLimitError(
                    f"Rate limit exceeded: token budget exhausted (limit {tpm} TPM)",
                    retry_after=retry_after,
                )

            if team_id:
                team_bucket = self._get_team_bucket(team_id, team_tpm)
                if not team_bucket.consume(estimated_tokens):
                    retry_after = int(team_bucket.seconds_until_available(estimated_tokens)) + 1
                    raise RateLimitError(
                        "Team token budget exhausted",
                        retry_after=retry_after,
                    )


_rate_limiter: RateLimiter | None = None


def init_rate_limiter(settings: Settings) -> RateLimiter:
    global _rate_limiter
    _rate_limiter = RateLimiter(settings)
    return _rate_limiter


def get_rate_limiter() -> RateLimiter:
    if _rate_limiter is None:
        raise RuntimeError("Rate limiter not initialized")
    return _rate_limiter
