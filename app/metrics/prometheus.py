"""Prometheus metrics definitions."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Request counters ──────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "relay_requests_total",
    "Total number of Geeper Relay requests",
    ["model", "status"],  # status: success | error | rate_limited | policy_blocked
)

# ── Latency ───────────────────────────────────────────────────────────────────

REQUEST_LATENCY = Histogram(
    "relay_request_latency_seconds",
    "End-to-end request latency in seconds",
    ["model", "stream"],
    # Buckets tuned for LLM response times: 100ms → 2 minutes
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

# ── Token usage ───────────────────────────────────────────────────────────────

TOKENS_USED = Counter(
    "relay_tokens_total",
    "Total tokens consumed, split by type",
    ["model", "token_type"],  # token_type: prompt | completion
)

# ── PII ───────────────────────────────────────────────────────────────────────

PII_ENTITIES_SCRUBBED = Counter(
    "relay_pii_entities_scrubbed_total",
    "Number of PII entities scrubbed from requests",
)

PII_REQUESTS_AFFECTED = Counter(
    "relay_pii_requests_affected_total",
    "Number of requests that contained at least one PII entity",
)

# ── RAG ───────────────────────────────────────────────────────────────────────

RAG_RETRIEVALS = Counter(
    "relay_rag_retrievals_total",
    "RAG context retrieval attempts",
    ["status"],  # hit | miss
)

RAG_CHUNKS_RETRIEVED = Histogram(
    "relay_rag_chunks_retrieved",
    "Number of RAG chunks retrieved per request",
    buckets=[0, 1, 2, 3, 5, 10],
)

# ── Rate limiting ─────────────────────────────────────────────────────────────

RATE_LIMIT_HITS = Counter(
    "relay_rate_limit_hits_total",
    "Requests rejected by the rate limiter",
    ["limit_type"],  # rpm | tpm | team_tpm
)

# ── Active requests ───────────────────────────────────────────────────────────

ACTIVE_REQUESTS = Gauge(
    "relay_active_requests",
    "Number of requests currently being processed",
)

# ── Cost ─────────────────────────────────────────────────────────────────────

COST_USD = Counter(
    "relay_cost_usd_total",
    "Cumulative estimated cost in USD",
    ["model"],
)

# ── Cache ─────────────────────────────────────────────────────────────────────

CACHE_HITS = Counter(
    "relay_cache_hits_total",
    "Requests served from LiteLLM cache",
    ["model"],
)

# ── Content policy ────────────────────────────────────────────────────────────

POLICY_BLOCKS = Counter(
    "relay_content_policy_blocks_total",
    "Requests blocked by content policy",
)


def metrics_response():
    """Returns a FastAPI Response with the current Prometheus metrics."""
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
