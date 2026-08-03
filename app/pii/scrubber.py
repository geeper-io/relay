"""PII scrubbing using Microsoft Presidio + custom regex recognizers."""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.config import Settings
from app.pii.regex_patterns import CUSTOM_RECOGNIZERS

# Placeholder format: <<PII_<ENTITY_TYPE>_<REQUEST_LOCAL_128_BIT_ID>>>
_PLACEHOLDER_PREFIX = "<<PII_"
_PLACEHOLDER_SUFFIX = ">>"
_PROMPT_TEXT_KEYS = {"content", "text", "output"}
_IRREVERSIBLE_ENTITIES = {"INTERNAL_SECRET"}


def _make_placeholder(entity_type: str) -> str:
    return f"{_PLACEHOLDER_PREFIX}{entity_type}_{uuid.uuid4().hex}{_PLACEHOLDER_SUFFIX}"


@dataclass
class ScrubSession:
    """Request-local state used to keep reversible placeholders consistent."""

    restoration_map: dict[str, str] = field(default_factory=dict)
    original_to_placeholder: dict[tuple[str, str], str] = field(default_factory=dict)


class PIIScrubber:
    def __init__(self, settings: Settings):
        self._enabled = settings.pii_enabled
        self._threshold = settings.pii_score_threshold
        self._entities = settings.pii_entities
        self._allow_set = {s.lower() for s in settings.pii_allow_list}

        if self._enabled:
            nlp_engine = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": settings.pii_spacy_model}],
                }
            ).create_engine()

            registry = RecognizerRegistry()
            registry.load_predefined_recognizers()
            for recognizer in CUSTOM_RECOGNIZERS:
                registry.add_recognizer(recognizer)

            self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, registry=registry)
        else:
            self._analyzer = None

    def scrub_messages(
        self,
        messages: list[dict],
    ) -> tuple[list[dict], dict[str, str], int]:
        """
        Returns (scrubbed_messages, restoration_map, total_entities_count).

        restoration_map maps placeholder → original value.
        Placeholders are consistent per-request: same original value → same placeholder.
        """
        if not self._enabled:
            return messages, {}, 0

        session = ScrubSession()
        scrubbed_messages = deepcopy(messages)
        total = 0

        for message in scrubbed_messages:
            total += self._scrub_message(message, session)

        return scrubbed_messages, session.restoration_map, total

    def scrub_text_values(self, values: list[str]) -> tuple[list[str], dict[str, str], int]:
        """Scrub arbitrary text fields while sharing placeholders across the request."""
        if not self._enabled:
            return values, {}, 0

        session = ScrubSession()
        scrubbed: list[str] = []
        total = 0
        for value in values:
            if not value:
                scrubbed.append(value)
                continue
            scrubbed_value, count = self._scrub_text(value, session, reversible=True)
            scrubbed.append(scrubbed_value)
            total += count
        return scrubbed, session.restoration_map, total

    def scrub_untrusted_text(self, text: str) -> tuple[str, int]:
        """Irreversibly redact PII from retrieved or externally supplied context."""
        if not self._enabled or not text:
            return text, 0
        return self._scrub_text(text, ScrubSession(), reversible=False)

    def _scrub_message(self, message: dict[str, Any], session: ScrubSession) -> int:
        total = 0
        if "content" in message:
            message["content"], count = self._scrub_prompt_value(message["content"], session)
            total += count

        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict) or "arguments" not in function:
                continue
            arguments = function["arguments"]
            if isinstance(arguments, str):
                function["arguments"], count = self._scrub_text(arguments, session, reversible=True)
            else:
                function["arguments"], count = self._scrub_all_strings(arguments, session)
            total += count
        return total

    def _scrub_prompt_value(self, value: Any, session: ScrubSession) -> tuple[Any, int]:
        if isinstance(value, str):
            return self._scrub_text(value, session, reversible=True)
        if isinstance(value, list):
            total = 0
            for index, item in enumerate(value):
                value[index], count = self._scrub_prompt_value(item, session)
                total += count
            return value, total
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if key in _PROMPT_TEXT_KEYS:
                    value[key], count = self._scrub_prompt_value(item, session)
                    total += count
            return value, total
        return value, 0

    def _scrub_all_strings(self, value: Any, session: ScrubSession) -> tuple[Any, int]:
        if isinstance(value, str):
            return self._scrub_text(value, session, reversible=True)
        if isinstance(value, list):
            total = 0
            for index, item in enumerate(value):
                value[index], count = self._scrub_all_strings(item, session)
                total += count
            return value, total
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                value[key], count = self._scrub_all_strings(item, session)
                total += count
            return value, total
        return value, 0

    def _scrub_text(
        self,
        text: str,
        session: ScrubSession,
        *,
        reversible: bool,
    ) -> tuple[str, int]:
        results = self._analyzer.analyze(
            text=text,
            entities=self._entities,
            language="en",
            score_threshold=self._threshold,
        )

        # Filter out allow-listed terms
        if self._allow_set:
            results = [r for r in results if text[r.start : r.end].lower() not in self._allow_set]

        if not results:
            return text, 0

        # Presidio can return overlapping recognizers for the same span. Prefer the
        # earliest, longest, highest-confidence span so replacement is deterministic.
        selected = []
        last_end = -1
        for result in sorted(
            results,
            key=lambda item: (item.start, -(item.end - item.start), -item.score, item.entity_type),
        ):
            if result.start < last_end:
                continue
            selected.append(result)
            last_end = result.end

        replacements: list[tuple[int, int, str]] = []
        for result in selected:
            original_value = text[result.start : result.end]
            key = (result.entity_type, original_value)
            if not reversible or result.entity_type in _IRREVERSIBLE_ENTITIES:
                placeholder = f"<<REDACTED_{result.entity_type}>>"
            elif key in session.original_to_placeholder:
                placeholder = session.original_to_placeholder[key]
            else:
                placeholder = _make_placeholder(result.entity_type)
                session.original_to_placeholder[key] = placeholder
                session.restoration_map[placeholder] = original_value
            replacements.append((result.start, result.end, placeholder))

        scrubbed = text
        for start, end, placeholder in reversed(replacements):
            scrubbed = scrubbed[:start] + placeholder + scrubbed[end:]
        return scrubbed, len(selected)


_scrubber: PIIScrubber | None = None


def init_scrubber(settings: Settings) -> PIIScrubber:
    global _scrubber
    _scrubber = PIIScrubber(settings)
    return _scrubber


def get_scrubber() -> PIIScrubber:
    if _scrubber is None:
        raise RuntimeError("PIIScrubber not initialized")
    return _scrubber
