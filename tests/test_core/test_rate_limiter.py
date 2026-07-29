import pytest
import pytest_asyncio

from app.core.exceptions import RateLimitError
from app.core.rate_limiter import RateLimiter


class _Settings:
    rate_limit_enabled = True
    rate_limit_backend = "memory"
    redis_url = ""
    default_rpm = 5
    default_tpm = 1000
    default_tpd = 100_000


@pytest_asyncio.fixture
async def limiter():
    return RateLimiter(_Settings())


@pytest.mark.asyncio
async def test_allows_within_limit(limiter):
    for _ in range(5):
        await limiter.check_and_consume("user1", None, 10)


@pytest.mark.asyncio
async def test_rpm_exceeded(limiter):
    for _ in range(5):
        await limiter.check_and_consume("user2", None, 1)
    with pytest.raises(RateLimitError):
        await limiter.check_and_consume("user2", None, 1)


@pytest.mark.asyncio
async def test_tpm_exceeded(limiter):
    with pytest.raises(RateLimitError):
        await limiter.check_and_consume("user3", None, 5000)


@pytest.mark.asyncio
async def test_disabled_no_limit():
    class Disabled:
        rate_limit_enabled = False
        default_rpm = 1
        default_tpm = 1

    lim = RateLimiter(Disabled())
    # Should never raise even beyond limits
    for _ in range(100):
        await lim.check_and_consume("userX", None, 999999)


@pytest.mark.asyncio
async def test_daily_user_budget_is_enforced(limiter):
    await limiter.check_and_consume("daily-user", None, 60, daily_token_limit=100)
    with pytest.raises(RateLimitError, match="per day"):
        await limiter.check_and_consume("daily-user", None, 41, daily_token_limit=100)


@pytest.mark.asyncio
async def test_team_limits_use_database_override(limiter):
    await limiter.check_and_consume(
        "team-user-1",
        "team-1",
        70,
        team_tpm_limit=100,
        team_daily_token_limit=1000,
    )
    with pytest.raises(RateLimitError, match="team tokens per minute"):
        await limiter.check_and_consume(
            "team-user-2",
            "team-1",
            31,
            team_tpm_limit=100,
            team_daily_token_limit=1000,
        )


@pytest.mark.asyncio
async def test_failed_token_check_does_not_consume_rpm(limiter):
    with pytest.raises(RateLimitError, match="user tokens per minute"):
        await limiter.check_and_consume("atomic-user", None, 1001)
    for _ in range(5):
        await limiter.check_and_consume("atomic-user", None, 1)


@pytest.mark.asyncio
async def test_completion_tokens_are_reconciled(limiter):
    await limiter.check_and_consume("reconcile-user", None, 900)
    await limiter.reconcile_tokens(
        "reconcile-user",
        None,
        reserved_tokens=900,
        actual_tokens=1100,
    )
    with pytest.raises(RateLimitError, match="user tokens per minute"):
        await limiter.check_and_consume("reconcile-user", None, 1)


def test_redis_backend_requires_url():
    class RedisWithoutURL(_Settings):
        rate_limit_backend = "redis"
        redis_url = ""

    with pytest.raises(ValueError, match="REDIS_URL"):
        RateLimiter(RedisWithoutURL())
