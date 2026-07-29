from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.core.exceptions import RateLimitError


@dataclass
class TokenBucket:
    capacity: float
    refill_rate: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def refresh(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def can_consume(self, amount: float) -> bool:
        self.refresh()
        return self.tokens >= amount

    def consume(self, amount: float) -> None:
        self.tokens -= amount

    def seconds_until_available(self, amount: float) -> float:
        deficit = amount - self.tokens
        return 0.0 if deficit <= 0 else deficit / self.refill_rate


_REDIS_LIMIT_SCRIPT = """
for i, key in ipairs(KEYS) do
  local offset = (i - 1) * 3
  local amount = tonumber(ARGV[offset + 1])
  local limit = tonumber(ARGV[offset + 2])
  local current = tonumber(redis.call('GET', key) or '0')
  if current + amount > limit then
    return i
  end
end
for i, key in ipairs(KEYS) do
  local offset = (i - 1) * 3
  local amount = tonumber(ARGV[offset + 1])
  local ttl = tonumber(ARGV[offset + 3])
  local value = redis.call('INCRBY', key, amount)
  if value == amount then redis.call('EXPIRE', key, ttl) end
end
return 0
"""


class RateLimiter:
    """Hierarchical user/team limiter with an atomic Redis backend."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = asyncio.Lock()
        self._user_buckets: dict[str, dict[str, TokenBucket]] = {}
        self._team_buckets: dict[str, TokenBucket] = {}
        self._daily_usage: dict[tuple[str, str], tuple[int, int]] = {}
        self._redis = None
        redis_enabled = (
            getattr(settings, "rate_limit_enabled", False)
            and getattr(settings, "rate_limit_backend", "memory") == "redis"
        )
        if redis_enabled:
            if not getattr(settings, "redis_url", ""):
                raise ValueError("RATE_LIMITING__REDIS_URL is required when the Redis backend is enabled")
            import redis.asyncio as redis

            self._redis = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def ping(self) -> bool:
        return self._redis is None or bool(await self._redis.ping())

    def _get_user_buckets(self, user_id: str, rpm: int, tpm: int) -> dict[str, TokenBucket]:
        buckets = self._user_buckets.get(user_id)
        if buckets is None or buckets["rpm"].capacity != rpm or buckets["tpm"].capacity != tpm:
            buckets = {
                "rpm": TokenBucket(capacity=rpm, refill_rate=rpm / 60.0),
                "tpm": TokenBucket(capacity=tpm, refill_rate=tpm / 60.0),
            }
            self._user_buckets[user_id] = buckets
        return buckets

    def _get_team_bucket(self, team_id: str, tpm: int) -> TokenBucket:
        bucket = self._team_buckets.get(team_id)
        if bucket is None or bucket.capacity != tpm:
            bucket = TokenBucket(capacity=tpm, refill_rate=tpm / 60.0)
            self._team_buckets[team_id] = bucket
        return bucket

    @staticmethod
    def _daily_key(kind: str, identity: str) -> tuple[str, str]:
        return kind, identity

    def _daily_current(self, kind: str, identity: str, day: int) -> int:
        stored_day, used = self._daily_usage.get(self._daily_key(kind, identity), (day, 0))
        return used if stored_day == day else 0

    async def check_and_consume(
        self,
        user_id: str,
        team_id: str | None,
        estimated_tokens: int,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        daily_token_limit: int | None = None,
        team_tpm_limit: int | None = None,
        team_daily_token_limit: int | None = None,
    ) -> None:
        if not self._settings.rate_limit_enabled:
            return

        rpm = rpm_limit or self._settings.default_rpm
        tpm = tpm_limit or self._settings.default_tpm
        tpd = daily_token_limit or self._settings.default_tpd
        team_tpm = team_tpm_limit or self._settings.default_tpm * 5
        team_tpd = team_daily_token_limit or self._settings.default_tpd * 5

        if self._redis is not None:
            await self._check_redis(user_id, team_id, estimated_tokens, rpm, tpm, tpd, team_tpm, team_tpd)
            return
        await self._check_memory(user_id, team_id, estimated_tokens, rpm, tpm, tpd, team_tpm, team_tpd)

    async def _check_memory(
        self,
        user_id: str,
        team_id: str | None,
        tokens: int,
        rpm: int,
        tpm: int,
        tpd: int,
        team_tpm: int,
        team_tpd: int,
    ) -> None:
        day = int(time.time() // 86400)
        async with self._lock:
            buckets = self._get_user_buckets(user_id, rpm, tpm)
            team_bucket = self._get_team_bucket(team_id, team_tpm) if team_id else None
            checks = [
                (buckets["rpm"].can_consume(1), "requests per minute", buckets["rpm"], 1),
                (buckets["tpm"].can_consume(tokens), "user tokens per minute", buckets["tpm"], tokens),
                (self._daily_current("user", user_id, day) + tokens <= tpd, "user tokens per day", None, tokens),
            ]
            if team_id and team_bucket:
                checks.extend(
                    [
                        (team_bucket.can_consume(tokens), "team tokens per minute", team_bucket, tokens),
                        (
                            self._daily_current("team", team_id, day) + tokens <= team_tpd,
                            "team tokens per day",
                            None,
                            tokens,
                        ),
                    ]
                )
            for allowed, label, bucket, amount in checks:
                if not allowed:
                    retry = (
                        int(bucket.seconds_until_available(amount)) + 1 if bucket else 86400 - int(time.time()) % 86400
                    )
                    raise RateLimitError(f"Rate limit exceeded: {label}", retry_after=retry)

            buckets["rpm"].consume(1)
            buckets["tpm"].consume(tokens)
            self._daily_usage[self._daily_key("user", user_id)] = (
                day,
                self._daily_current("user", user_id, day) + tokens,
            )
            if team_id and team_bucket:
                team_bucket.consume(tokens)
                self._daily_usage[self._daily_key("team", team_id)] = (
                    day,
                    self._daily_current("team", team_id, day) + tokens,
                )

    @staticmethod
    def _redis_identity(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:24]

    async def _check_redis(
        self,
        user_id: str,
        team_id: str | None,
        tokens: int,
        rpm: int,
        tpm: int,
        tpd: int,
        team_tpm: int,
        team_tpd: int,
    ) -> None:
        now = int(time.time())
        minute = now // 60
        day = now // 86400
        minute_ttl = 120
        day_ttl = 172800
        user = self._redis_identity(user_id)
        keys = [
            f"relay:rl:user:{user}:rpm:{minute}",
            f"relay:rl:user:{user}:tpm:{minute}",
            f"relay:rl:user:{user}:tpd:{day}",
        ]
        specs = [(1, rpm, minute_ttl), (tokens, tpm, minute_ttl), (tokens, tpd, day_ttl)]
        labels = ["requests per minute", "user tokens per minute", "user tokens per day"]
        if team_id:
            team = self._redis_identity(team_id)
            keys.extend([f"relay:rl:team:{team}:tpm:{minute}", f"relay:rl:team:{team}:tpd:{day}"])
            specs.extend([(tokens, team_tpm, minute_ttl), (tokens, team_tpd, day_ttl)])
            labels.extend(["team tokens per minute", "team tokens per day"])
        args = [value for spec in specs for value in spec]
        failed = int(await self._redis.eval(_REDIS_LIMIT_SCRIPT, len(keys), *keys, *args))
        if failed:
            daily = failed in (3, 5)
            retry = (86400 - now % 86400) if daily else (60 - now % 60)
            raise RateLimitError(f"Rate limit exceeded: {labels[failed - 1]}", retry_after=retry)

    async def reconcile_tokens(
        self,
        user_id: str,
        team_id: str | None,
        *,
        reserved_tokens: int,
        actual_tokens: int,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        daily_token_limit: int | None = None,
        team_tpm_limit: int | None = None,
        team_daily_token_limit: int | None = None,
    ) -> None:
        """Charge completion tokens after a response; overages block subsequent requests."""
        additional = max(0, actual_tokens - reserved_tokens)
        if not self._settings.rate_limit_enabled or additional == 0:
            return
        tpm = tpm_limit or self._settings.default_tpm
        rpm = rpm_limit or self._settings.default_rpm
        team_tpm = team_tpm_limit or self._settings.default_tpm * 5
        if self._redis is not None:
            now = int(time.time())
            minute = now // 60
            day = now // 86400
            user = self._redis_identity(user_id)
            keys = [
                (f"relay:rl:user:{user}:tpm:{minute}", 120),
                (f"relay:rl:user:{user}:tpd:{day}", 172800),
            ]
            if team_id:
                team = self._redis_identity(team_id)
                keys.extend(
                    [
                        (f"relay:rl:team:{team}:tpm:{minute}", 120),
                        (f"relay:rl:team:{team}:tpd:{day}", 172800),
                    ]
                )
            async with self._redis.pipeline(transaction=True) as pipe:
                for key, ttl in keys:
                    pipe.incrby(key, additional)
                    pipe.expire(key, ttl)
                await pipe.execute()
            return

        day = int(time.time() // 86400)
        async with self._lock:
            buckets = self._get_user_buckets(user_id, rpm, tpm)
            buckets["tpm"].refresh()
            buckets["tpm"].consume(additional)
            self._daily_usage[self._daily_key("user", user_id)] = (
                day,
                self._daily_current("user", user_id, day) + additional,
            )
            if team_id:
                team_bucket = self._get_team_bucket(team_id, team_tpm)
                team_bucket.refresh()
                team_bucket.consume(additional)
                self._daily_usage[self._daily_key("team", team_id)] = (
                    day,
                    self._daily_current("team", team_id, day) + additional,
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
