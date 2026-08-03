from __future__ import annotations

import re

from app.config import Settings
from app.core.exceptions import ContentPolicyError
from app.schemas.openai import ChatMessage


class ContentPolicy:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._compiled = [re.compile(pattern, re.IGNORECASE) for pattern in settings.blocked_patterns]

    def check(self, messages: list[ChatMessage]) -> None:
        if not self._settings.content_policy_enabled:
            return

        combined = " ".join(m.text_content() for m in messages)

        # Token length guard (rough char-based estimate)
        if len(combined) // 4 > self._settings.max_input_tokens:
            raise ContentPolicyError(
                f"Input exceeds maximum allowed length (~{self._settings.max_input_tokens} tokens)"
            )

        for pattern in self._compiled:
            if pattern.search(combined):
                raise ContentPolicyError("Request blocked by content policy")

    def check_token_count(self, token_count: int) -> None:
        """Enforce the input ceiling after proxy-side context enrichment."""
        if self._settings.content_policy_enabled and token_count > self._settings.max_input_tokens:
            raise ContentPolicyError(
                f"Input exceeds maximum allowed length ({self._settings.max_input_tokens} tokens)"
            )

    def contains_blocked_pattern(self, text: str) -> bool:
        """Return whether untrusted enrichment text matches the active deny patterns."""
        return self._settings.content_policy_enabled and any(pattern.search(text) for pattern in self._compiled)


_policy: ContentPolicy | None = None


def init_content_policy(settings: Settings) -> ContentPolicy:
    global _policy
    _policy = ContentPolicy(settings)
    return _policy


def get_content_policy() -> ContentPolicy:
    if _policy is None:
        raise RuntimeError("Content policy not initialized")
    return _policy
