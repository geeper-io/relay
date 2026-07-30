import pytest

from app.core.exceptions import MCPProtocolError
from app.mcp.results import sanitize_tool_result


class _Scrubber:
    def scrub_text_values(self, values):
        return [value.replace("alice@example.com", "<<PII_EMAIL_12345678>>") for value in values], {}, 1


def test_tool_results_are_scrubbed_recursively():
    result, count = sanitize_tool_result(
        {"content": [{"type": "text", "text": "Contact alice@example.com"}]},
        _Scrubber(),
        max_bytes=1000,
    )
    assert result["content"][0]["text"] == "Contact <<PII_EMAIL_12345678>>"
    assert count == 1


def test_oversized_tool_results_are_rejected():
    with pytest.raises(MCPProtocolError, match="byte limit"):
        sanitize_tool_result({"content": [{"text": "x" * 100}]}, _Scrubber(), max_bytes=10)
