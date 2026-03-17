"""PII scrubbing using Microsoft Presidio + custom regex recognizers."""

from __future__ import annotations

import re
import uuid

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from app.config import Settings
from app.pii.regex_patterns import CUSTOM_RECOGNIZERS

# Placeholder format: <<PII_<ENTITY_TYPE>_<SHORT_UUID>>>
_PLACEHOLDER_PREFIX = "<<PII_"
_PLACEHOLDER_SUFFIX = ">>"
_PLACEHOLDER_RE = re.compile(r"<<PII_[A-Z_]+_[a-f0-9]{8}>>")

# Unified diff markers — unambiguously non-conversational; skip PII scrubbing entirely.
# Matches:
#   - git diff header:  "diff --git a/foo b/foo"
#   - unified hunk header: "@@ -1,5 +2,7 @@"  (git, svn diff, diff -u, patch files)
# Code blocks (```) are intentionally excluded: a docstring or comment inside a
# code block can still contain real emails or phone numbers.
_DIFF_RE = re.compile(
    r"^diff --git "  # git unified diff header
    r"|^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@",  # unified diff hunk header
    re.MULTILINE,
)


def _make_placeholder(entity_type: str) -> str:
    short_id = uuid.uuid4().hex[:8]
    return f"{_PLACEHOLDER_PREFIX}{entity_type}_{short_id}{_PLACEHOLDER_SUFFIX}"


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
            self._anonymizer = AnonymizerEngine()
        else:
            self._analyzer = None
            self._anonymizer = None

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

        restoration_map: dict[str, str] = {}
        # reverse map for dedup: original → placeholder
        original_to_placeholder: dict[str, str] = {}
        scrubbed_messages = []
        total = 0

        for msg in messages:
            if msg.get("role") == "system":
                # Optionally skip system messages (they're usually from the app, not users)
                scrubbed_messages.append(msg)
                continue

            content = msg.get("content")
            if not isinstance(content, str) or not content:
                scrubbed_messages.append(msg)
                continue

            # Skip scrubbing for git diffs — identifiers, class names, etc. would
            # produce too many false positives. Regular code blocks are still scrubbed
            # because docstrings/comments can contain real PII.
            if _DIFF_RE.search(content):
                scrubbed_messages.append(msg)
                continue

            scrubbed_content, n = self._scrub_text(content, restoration_map, original_to_placeholder)
            total += n
            scrubbed_messages.append({**msg, "content": scrubbed_content})

        return scrubbed_messages, restoration_map, total

    def _scrub_text(
        self,
        text: str,
        restoration_map: dict[str, str],
        original_to_placeholder: dict[str, str],
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

        # Build a custom operator that assigns our deterministic placeholders
        operators: dict[str, OperatorConfig] = {}
        for result in results:
            original_value = text[result.start : result.end]
            if original_value in original_to_placeholder:
                placeholder = original_to_placeholder[original_value]
            else:
                placeholder = _make_placeholder(result.entity_type)
                original_to_placeholder[original_value] = placeholder
                restoration_map[placeholder] = original_value

            operators[result.entity_type] = OperatorConfig("replace", {"new_value": placeholder})

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )
        return anonymized.text, len(results)


_scrubber: PIIScrubber | None = None


def init_scrubber(settings: Settings) -> PIIScrubber:
    global _scrubber
    _scrubber = PIIScrubber(settings)
    return _scrubber


def get_scrubber() -> PIIScrubber:
    if _scrubber is None:
        raise RuntimeError("PIIScrubber not initialized")
    return _scrubber
