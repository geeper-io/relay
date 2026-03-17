"""FastAPI dependency wrappers for singleton services."""

from app.config import get_settings
from app.core.content_policy import get_content_policy
from app.core.rate_limiter import get_rate_limiter
from app.llm.client import get_llm_client
from app.pii.restorer import get_restorer
from app.pii.scrubber import get_scrubber
from app.rag.retriever import get_retriever

# Re-export so chat.py can import from a single place
__all__ = [
    "get_settings",
    "get_scrubber",
    "get_restorer",
    "get_retriever",
    "get_llm_client",
    "get_rate_limiter",
    "get_content_policy",
]
