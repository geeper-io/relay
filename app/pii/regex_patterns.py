"""Custom regex-based PII recognizers to supplement Presidio's built-in NLP models."""

from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer

CUSTOM_RECOGNIZERS: list[PatternRecognizer] = [
    PatternRecognizer(
        supported_entity="EMPLOYEE_ID",
        patterns=[Pattern("EMPLOYEE_ID", r"\bEMP-\d{6}\b", 0.9)],
        context=["employee", "emp", "staff", "id"],
    ),
    PatternRecognizer(
        supported_entity="INTERNAL_PROJECT",
        patterns=[Pattern("INTERNAL_PROJECT", r"\bPROJ-[A-Z]{2,5}-\d{3,6}\b", 0.85)],
        context=["project", "proj", "initiative"],
    ),
    PatternRecognizer(
        supported_entity="SLACK_CHANNEL",
        patterns=[Pattern("SLACK_CHANNEL", r"#[a-z0-9_-]{2,80}", 0.6)],
        context=["slack", "channel", "message"],
    ),
    # Internal API key / secret patterns
    PatternRecognizer(
        supported_entity="INTERNAL_SECRET",
        patterns=[
            Pattern("INTERNAL_SECRET_LONG", r"\b[A-Za-z0-9+/]{40,}={0,2}\b", 0.5),
        ],
        context=["token", "secret", "key", "api", "auth", "bearer"],
    ),
]
