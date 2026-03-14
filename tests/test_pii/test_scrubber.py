"""Tests for PII scrubbing. Requires presidio + spacy en_core_web_lg."""
import pytest

from app.pii.scrubber import PIIScrubber
from app.pii.restorer import PIIRestorer


class _FakeSettings:
    pii_enabled = True
    pii_score_threshold = 0.5
    pii_entities = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
        "CREDIT_CARD", "US_SSN", "IP_ADDRESS",
        "EMPLOYEE_ID",  # custom regex recognizer
    ]


@pytest.fixture
def scrubber():
    return PIIScrubber(_FakeSettings())


@pytest.fixture
def restorer():
    return PIIRestorer()


def test_email_scrubbed(scrubber, restorer):
    messages = [{"role": "user", "content": "Contact john@example.com for details."}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count > 0
    assert "john@example.com" not in scrubbed[0]["content"]
    assert "PII_EMAIL_ADDRESS" in scrubbed[0]["content"]
    # Restore
    restored = restorer.restore(scrubbed[0]["content"], rmap)
    assert "john@example.com" in restored

def test_name_scrubbed(scrubber, restorer):
    messages = [{"role": "user", "content": "Contact John Doe for details."}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count > 0
    assert "John Doe" not in scrubbed[0]["content"]
    assert "PII_PERSON" in scrubbed[0]["content"]
    # Restore
    restored = restorer.restore(scrubbed[0]["content"], rmap)
    assert "John Doe" in restored


def test_no_pii_unchanged(scrubber):
    messages = [{"role": "user", "content": "What is the capital of France?"}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count == 0
    assert scrubbed[0]["content"] == messages[0]["content"]


def test_system_message_not_scrubbed(scrubber):
    messages = [
        {"role": "system", "content": "You are a helpful assistant for john@example.com"},
        {"role": "user", "content": "Hello"},
    ]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    # System message preserved
    assert scrubbed[0]["content"] == messages[0]["content"]


def test_employee_id_scrubbed(scrubber, restorer):
    messages = [{"role": "user", "content": "Employee EMP-123456 submitted the request."}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count > 0
    assert "EMP-123456" not in scrubbed[0]["content"]
    assert "PII_EMPLOYEE_ID" in scrubbed[0]["content"]
    restored = restorer.restore(scrubbed[0]["content"], rmap)
    assert "EMP-123456" in restored


def test_disabled_pii_passthrough():
    class Disabled:
        pii_enabled = False
        pii_score_threshold = 0.5
        pii_entities = ["EMAIL_ADDRESS"]

    scrubber = PIIScrubber(Disabled())
    messages = [{"role": "user", "content": "Call me at test@test.com"}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count == 0
    assert scrubbed == messages
