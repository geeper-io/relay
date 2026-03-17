"""Anthropic Messages API request/response schemas + conversion helpers."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

# ── Content blocks ────────────────────────────────────────────────────────────


class AnthropicTextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class AnthropicImageSource(BaseModel):
    type: str  # "base64" | "url"
    media_type: str | None = None
    data: str | None = None
    url: str | None = None


class AnthropicImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: AnthropicImageSource


class AnthropicToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class AnthropicToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[AnthropicTextBlock] | None = None
    is_error: bool | None = None


AnthropicContentBlock = AnthropicTextBlock | AnthropicImageBlock | AnthropicToolUseBlock | AnthropicToolResultBlock


# ── Tool definitions ──────────────────────────────────────────────────────────


class AnthropicToolInputSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: dict[str, Any] | None = None
    required: list[str] | None = None


class AnthropicTool(BaseModel):
    name: str
    description: str | None = None
    input_schema: AnthropicToolInputSchema


class AnthropicToolChoiceAuto(BaseModel):
    type: Literal["auto"] = "auto"


class AnthropicToolChoiceAny(BaseModel):
    type: Literal["any"] = "any"


class AnthropicToolChoiceTool(BaseModel):
    type: Literal["tool"] = "tool"
    name: str


AnthropicToolChoice = AnthropicToolChoiceAuto | AnthropicToolChoiceAny | AnthropicToolChoiceTool


# ── Request ───────────────────────────────────────────────────────────────────


class AnthropicMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[AnthropicContentBlock]


class AnthropicRequest(BaseModel):
    model: str
    messages: list[AnthropicMessage]
    system: str | list[AnthropicTextBlock] | None = None
    max_tokens: int = 1024
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stream: bool = False
    stop_sequences: list[str] | None = None
    metadata: dict[str, Any] | None = None
    tools: list[AnthropicTool] | None = None
    tool_choice: AnthropicToolChoice | None = None


# ── Response ──────────────────────────────────────────────────────────────────


class AnthropicUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicResponse(BaseModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[AnthropicTextBlock | AnthropicToolUseBlock]
    model: str
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: AnthropicUsage


# ── Conversion helpers ────────────────────────────────────────────────────────


def _finish_reason_to_stop_reason(finish_reason: str | None) -> str | None:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish_reason or "", "end_turn")


def anthropic_to_openai_messages(request: AnthropicRequest) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI-compatible message dicts."""
    messages: list[dict] = []
    if request.system:
        system_text = (
            "\n".join(b.text for b in request.system if isinstance(b, AnthropicTextBlock))
            if isinstance(request.system, list)
            else request.system
        )
        messages.append({"role": "system", "content": system_text})

    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            messages.append({"role": msg.role, "content": content})
            continue

        # List of typed content blocks
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []

        for block in content:
            if isinstance(block, AnthropicTextBlock):
                text_parts.append(block.text)
            elif isinstance(block, AnthropicToolUseBlock):
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input),
                        },
                    }
                )
            elif isinstance(block, AnthropicToolResultBlock):
                if isinstance(block.content, str):
                    result_content = block.content
                elif isinstance(block.content, list):
                    result_content = "\n".join(b.text for b in block.content if isinstance(b, AnthropicTextBlock))
                else:
                    result_content = ""
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": result_content,
                    }
                )

        if tool_results:
            messages.extend(tool_results)
        else:
            d: dict = {"role": msg.role}
            if text_parts:
                d["content"] = "\n".join(text_parts)
            if tool_calls:
                d["tool_calls"] = tool_calls
            messages.append(d)

    return messages


def anthropic_tools_to_openai(tools: list[AnthropicTool]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema.model_dump(exclude_none=True),
            },
        }
        for t in tools
    ]


def anthropic_tool_choice_to_openai(
    tool_choice: AnthropicToolChoice | None,
) -> str | dict | None:
    if isinstance(tool_choice, AnthropicToolChoiceAuto):
        return "auto"
    if isinstance(tool_choice, AnthropicToolChoiceAny):
        return "required"
    if isinstance(tool_choice, AnthropicToolChoiceTool):
        return {"type": "function", "function": {"name": tool_choice.name}}
    return None


def openai_response_to_anthropic(response: Any, model: str) -> AnthropicResponse:
    """Convert a LiteLLM (OpenAI-style) response to Anthropic Messages format."""
    choice = response.choices[0]
    msg = choice.message
    content: list[AnthropicTextBlock | AnthropicToolUseBlock] = []

    if msg.content:
        content.append(AnthropicTextBlock(type="text", text=msg.content))

    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (ValueError, AttributeError):
                args = {}
            content.append(
                AnthropicToolUseBlock(
                    type="tool_use",
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                )
            )

    usage = getattr(response, "usage", None)
    return AnthropicResponse(
        id=response.id,
        type="message",
        role="assistant",
        content=content,
        model=model,
        stop_reason=_finish_reason_to_stop_reason(choice.finish_reason),
        usage=AnthropicUsage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
    )
