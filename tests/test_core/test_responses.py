from app.api.v1.responses import _has_partial_placeholder, _response_usage, _restore_value
from app.pii.restorer import PIIRestorer
from app.schemas.responses import (
    ResponsesRequest,
    inject_response_context,
    last_user_text,
    response_capabilities,
    response_policy_messages,
    scrub_response_input,
    scrub_response_payload,
)


class _FakeScrubber:
    def scrub_text_values(self, values):
        scrubbed = [value.replace("alice@example.com", "<<PII_EMAIL_12345678>>") for value in values]
        count = sum(value.count("alice@example.com") for value in values)
        return scrubbed, {"<<PII_EMAIL_12345678>>": "alice@example.com"}, count


def test_response_capabilities_cover_typed_features():
    request = ResponsesRequest(
        model="auto",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe this"},
                    {"type": "input_image", "image_url": "https://example.test/image.png"},
                ],
            }
        ],
        stream=True,
        store=True,
        tools=[{"type": "web_search"}],
        reasoning={"effort": "medium"},
        text={"format": {"type": "json_schema", "name": "answer", "schema": {}}},
    )
    assert response_capabilities(request) == {
        "responses",
        "streaming",
        "stateful",
        "tools",
        "tool:web_search",
        "reasoning",
        "structured_outputs",
        "vision",
    }


def test_policy_projection_and_last_user_text_preserve_roles():
    request = ResponsesRequest(
        input=[
            {"role": "developer", "content": "Follow company policy"},
            {"role": "user", "content": [{"type": "input_text", "text": "First"}]},
            {"role": "user", "content": "Last question"},
        ]
    )
    messages = response_policy_messages(request)
    assert [message.role for message in messages] == ["system", "user", "user"]
    assert last_user_text(request) == "Last question"


def test_structured_input_scrubbing_preserves_non_text_items():
    request = ResponsesRequest(
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Email alice@example.com"},
                    {"type": "input_image", "image_url": "https://example.test/alice@example.com.png"},
                ],
            }
        ]
    )
    scrubbed, restoration_map, count = scrub_response_input(request, _FakeScrubber())
    assert count == 1
    assert scrubbed[0]["content"][0]["text"] == "Email <<PII_EMAIL_12345678>>"
    assert scrubbed[0]["content"][1]["image_url"] == "https://example.test/alice@example.com.png"
    assert restoration_map["<<PII_EMAIL_12345678>>"] == "alice@example.com"


def test_instructions_and_input_share_response_scrubbing():
    request = ResponsesRequest(
        instructions="Write to alice@example.com",
        input="Confirm alice@example.com",
    )
    scrubbed_input, scrubbed_instructions, restoration_map, count = scrub_response_payload(request, _FakeScrubber())
    assert scrubbed_input == "Confirm <<PII_EMAIL_12345678>>"
    assert scrubbed_instructions == "Write to <<PII_EMAIL_12345678>>"
    assert restoration_map == {"<<PII_EMAIL_12345678>>": "alice@example.com"}
    assert count == 2


def test_context_injection_handles_string_input():
    assert inject_response_context("hello", "internal context") == [
        {"role": "system", "content": "internal context"},
        {"role": "user", "content": "hello"},
    ]


def test_response_output_restoration_and_usage_helpers():
    payload = {"output": [{"content": [{"text": "Hi <<PII_EMAIL_12345678>>"}]}]}
    _restore_value(payload, PIIRestorer(), {"<<PII_EMAIL_12345678>>": "alice@example.com"})
    assert payload["output"][0]["content"][0]["text"] == "Hi alice@example.com"
    assert _response_usage({"input_tokens": 4, "output_tokens": 3, "total_tokens": 7}) == (4, 3)
    assert _response_usage(None) == (0, 0)


def test_partial_placeholder_detection():
    assert _has_partial_placeholder("hello <<PII_EMAIL_123")
    assert not _has_partial_placeholder("hello <<PII_EMAIL_12345678>>")
