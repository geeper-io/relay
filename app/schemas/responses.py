"""OpenAI Responses API request helpers.

The API's item union evolves frequently, so the request model deliberately
preserves unknown fields while validating Relay's routing-critical surface.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.pii.scrubber import PIIScrubber
from app.schemas.openai import ChatMessage


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    input: str | list[dict[str, Any]]
    instructions: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    stream: bool = False
    store: bool | None = None
    previous_response_id: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    background: bool | None = None
    include: list[str] | None = None
    max_tool_calls: int | None = Field(default=None, ge=1)
    prompt: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    text: dict[str, Any] | None = None
    truncation: str | None = None
    user: str | None = None
    metadata: dict[str, Any] | None = None


def response_capabilities(request: ResponsesRequest, *, default_store: bool = False) -> set[str]:
    capabilities = {"responses"}
    if request.stream:
        capabilities.add("streaming")
    if request.tools:
        capabilities.add("tools")
        for tool in request.tools:
            tool_type = tool.get("type")
            if tool_type and tool_type != "function":
                capabilities.add(f"tool:{tool_type}")
    if request.reasoning:
        capabilities.add("reasoning")
    if request.text and request.text.get("format", {}).get("type") not in {None, "text"}:
        capabilities.add("structured_outputs")
    if request.store is True or (request.store is None and default_store) or request.previous_response_id:
        capabilities.add("stateful")
    if request.background:
        capabilities.add("background")
    if _contains_input_type(request.input, "input_image"):
        capabilities.add("vision")
    return capabilities


def response_policy_messages(request: ResponsesRequest) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if request.instructions:
        messages.append(ChatMessage(role="system", content=request.instructions))
    if isinstance(request.input, str):
        messages.append(ChatMessage(role="user", content=request.input))
        return messages

    for item in request.input:
        item_type = item.get("type", "message")
        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "tool" if item_type == "function_call_output" else "user"
        text = " ".join(_text_values(item))
        if text:
            messages.append(ChatMessage(role=role, content=text))
    return messages


def last_user_text(request: ResponsesRequest) -> str:
    messages = response_policy_messages(request)
    for message in reversed(messages):
        if message.role == "user":
            return message.text_content()
    return ""


def scrub_response_input(
    request: ResponsesRequest,
    scrubber: PIIScrubber,
) -> tuple[str | list[dict[str, Any]], dict[str, str], int]:
    input_, _, restoration_map, count = scrub_response_payload(request, scrubber)
    return input_, restoration_map, count


def scrub_response_payload(
    request: ResponsesRequest,
    scrubber: PIIScrubber,
) -> tuple[str | list[dict[str, Any]], str | None, dict[str, str], int]:
    """Scrub all client-controlled prompt text with one restoration map."""
    holder: dict[str, Any] = {
        "input": deepcopy(request.input),
        "instructions": request.instructions,
    }
    paths: list[tuple[dict, str]] = []
    values: list[str] = []
    if request.instructions:
        paths.append((holder, "instructions"))
        values.append(request.instructions)

    if isinstance(holder["input"], str):
        paths.append((holder, "input"))
        values.append(holder["input"])
    else:
        _collect_scrubbable_fields(holder["input"], paths, values)

    scrubbed, restoration_map, count = scrubber.scrub_text_values(values)
    for (container, key), value in zip(paths, scrubbed, strict=True):
        container[key] = value
    return holder["input"], holder["instructions"], restoration_map, count


def inject_response_context(input_: str | list[dict[str, Any]], context: str) -> list[dict[str, Any]]:
    if isinstance(input_, str):
        return [
            {"role": "system", "content": context},
            {"role": "user", "content": input_},
        ]
    return [{"role": "system", "content": context}, *input_]


def _contains_input_type(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return value.get("type") == expected or any(_contains_input_type(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_input_type(item, expected) for item in value)
    return False


def _text_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "output"} and isinstance(item, str):
                found.append(item)
            elif key == "content" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_text_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_text_values(item))
    return found


def _collect_scrubbable_fields(value: Any, paths: list[tuple[dict, str]], values: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "output"} and isinstance(item, str):
                paths.append((value, key))
                values.append(item)
            elif key == "content" and isinstance(item, str):
                paths.append((value, key))
                values.append(item)
            else:
                _collect_scrubbable_fields(item, paths, values)
    elif isinstance(value, list):
        for item in value:
            _collect_scrubbable_fields(item, paths, values)
