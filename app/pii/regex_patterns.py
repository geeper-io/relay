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
            Pattern("OPENAI_STYLE_KEY", r"\bsk-[A-Za-z0-9_-]{20,}\b", 0.95),
            Pattern("GITHUB_TOKEN", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", 0.95),
            Pattern("BEARER_TOKEN", r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", 0.9),
        ],
        context=["token", "secret", "key", "api", "auth", "bearer"],
    ),
]
