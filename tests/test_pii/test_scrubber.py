"""Tests for PII scrubbing. Requires presidio + spacy en_core_web_lg."""

import pytest

from app.pii.restorer import PIIRestorer
from app.pii.scrubber import PIIScrubber


class _FakeSettings:
    pii_enabled = True
    pii_score_threshold = 0.5
    pii_entities = [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "US_SSN",
        "IP_ADDRESS",
        "EMPLOYEE_ID",  # custom regex recognizer
    ]
    pii_allow_list = []
    pii_spacy_model = "en_core_web_lg"


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
        pii_allow_list = []
        pii_spacy_model = "en_core_web_lg"

    scrubber = PIIScrubber(Disabled())
    messages = [{"role": "user", "content": "Call me at test@test.com"}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count == 0
    assert scrubbed == messages


def test_git_diff_not_scrubbed(scrubber):
    """git diff output passes through without PII scrubbing."""
    diff = (
        "diff --git a/app/config.py b/app/config.py\n"
        "--- a/app/config.py\n"
        "+++ b/app/config.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+EMAIL = 'john@example.com'\n"
    )
    messages = [{"role": "user", "content": diff}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert scrubbed[0]["content"] == diff
    assert count == 0


def test_unified_diff_hunk_header_not_scrubbed(scrubber):
    """diff -u / svn diff / patch output (no 'diff --git' line) passes through."""
    diff = (
        "--- a/main.py\t2024-01-01\n"
        "+++ b/main.py\t2024-01-02\n"
        "@@ -10,6 +10,7 @@\n"
        " def setup():\n"
        "+    # contact admin@corp.com\n"
        "     pass\n"
    )
    messages = [{"role": "user", "content": diff}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert scrubbed[0]["content"] == diff
    assert count == 0


def test_hunk_only_diff_not_scrubbed(scrubber):
    """A message that is just a hunk (no file headers) is also skipped."""
    diff = "@@ -1,3 +1,4 @@\n-old line\n+new line with john@example.com\n"
    messages = [{"role": "user", "content": diff}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert scrubbed[0]["content"] == diff
    assert count == 0


def test_regular_code_block_still_scrubbed(scrubber):
    """Code blocks that are NOT diffs should still be scrubbed."""
    content = "Here is some code:\n```python\n# contact john@example.com\n```"
    messages = [{"role": "user", "content": content}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count > 0
    assert "john@example.com" not in scrubbed[0]["content"]


def test_python_decorator_not_treated_as_diff(scrubber):
    """@@ in Python decorators should not trigger diff skip."""
    content = "Can you explain what @property does? Also email me at john@example.com"
    messages = [{"role": "user", "content": content}]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    assert count > 0
    assert "john@example.com" not in scrubbed[0]["content"]


def test_allow_list_case_insensitive(scrubber, restorer):
    """Terms in pii_allow_list (case-insensitive) should NOT be redacted."""

    class AllowListSettings(_FakeSettings):
        pii_allow_list = ["Settings"]  # stored mixed-case, should still match lowercase

    s = PIIScrubber(AllowListSettings())
    # "Settings" is commonly detected as a PERSON by Presidio; it should be whitelisted
    messages = [{"role": "user", "content": "The Settings class has a bug."}]
    scrubbed, rmap, count = s.scrub_messages(messages)
    assert "Settings" in scrubbed[0]["content"]


def test_allow_list_lowercase_entry_matches_uppercase_text(scrubber):
    """Allow list entry 'settings' should protect 'Settings' in text."""

    class LowerAllowSettings(_FakeSettings):
        pii_allow_list = ["settings"]

    s = PIIScrubber(LowerAllowSettings())
    messages = [{"role": "user", "content": "Call Settings for more info."}]
    scrubbed, rmap, count = s.scrub_messages(messages)
    assert "Settings" in scrubbed[0]["content"]


def test_same_value_gets_same_placeholder(scrubber):
    """The same PII value within one request must map to the same placeholder."""
    messages = [
        {"role": "user", "content": "Email john@example.com or john@example.com again."},
    ]
    scrubbed, rmap, count = scrubber.scrub_messages(messages)
    content = scrubbed[0]["content"]
    # Extract all placeholders that replaced the email
    import re

    placeholders = re.findall(r"<<PII_EMAIL_ADDRESS_[a-f0-9]{8}>>", content)
    assert len(placeholders) >= 1
    # All occurrences must be the same placeholder
    assert len(set(placeholders)) == 1
