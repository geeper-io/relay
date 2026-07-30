"""Sanitize and bound untrusted MCP tool results before returning them."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.core.exceptions import MCPProtocolError
from app.pii.scrubber import PIIScrubber


def sanitize_tool_result(result: dict[str, Any], scrubber: PIIScrubber, *, max_bytes: int) -> tuple[dict, int]:
    sanitized = deepcopy(result)
    paths: list[tuple[Any, Any]] = []
    values: list[str] = []
    _collect_strings(sanitized, paths, values)
    scrubbed, _restoration_map, pii_count = scrubber.scrub_text_values(values)
    for (container, key), value in zip(paths, scrubbed, strict=True):
        container[key] = value
    size = len(json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode())
    if size > max_bytes:
        raise MCPProtocolError(f"MCP tool result exceeds the {max_bytes}-byte limit")
    return sanitized, pii_count


def _collect_strings(value: Any, paths: list[tuple[Any, Any]], values: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                paths.append((value, key))
                values.append(item)
            else:
                _collect_strings(item, paths, values)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                paths.append((value, index))
                values.append(item)
            else:
                _collect_strings(item, paths, values)
