"""Restores PII placeholders in LLM responses back to original values."""

from __future__ import annotations

import re

# Accept legacy 8-character IDs while emitting request-local 128-bit IDs.
_PLACEHOLDER_RE = re.compile(r"<<PII_[A-Z_]+_[a-f0-9]{8,32}>>")
_PLACEHOLDER_PREFIX = "<<PII_"
_MAX_PLACEHOLDER_LENGTH = 128


class PIIRestorer:
    def restore(self, text: str, restoration_map: dict[str, str]) -> str:
        if not restoration_map:
            return text
        return _PLACEHOLDER_RE.sub(lambda m: restoration_map.get(m.group(0), m.group(0)), text)

    def restore_streaming(
        self,
        chunks: list[str],
        restoration_map: dict[str, str],
    ):
        """
        Generator that restores placeholders across streaming chunks.
        Buffers partial placeholder matches at chunk boundaries.
        """
        if not restoration_map:
            yield from chunks
            return

        buffer = ""
        for chunk in chunks:
            buffer += chunk
            while True:
                match = _PLACEHOLDER_RE.search(buffer)
                if match:
                    # Emit everything before the match (restored)
                    before = buffer[: match.start()]
                    if before:
                        yield self.restore(before, restoration_map)
                    # Restore the placeholder itself
                    yield restoration_map.get(match.group(0), match.group(0))
                    buffer = buffer[match.end() :]
                    continue

                # Retain a complete prefix and its unfinished placeholder body.
                placeholder_start = buffer.rfind(_PLACEHOLDER_PREFIX)
                if placeholder_start >= 0 and ">>" not in buffer[placeholder_start:]:
                    if placeholder_start:
                        yield self.restore(buffer[:placeholder_start], restoration_map)
                    buffer = buffer[placeholder_start:]
                    # Bound memory if an upstream emits a malformed placeholder.
                    if len(buffer) > _MAX_PLACEHOLDER_LENGTH:
                        yield buffer
                        buffer = ""
                    break

                # Retain a suffix which may become the placeholder prefix next chunk.
                keep = 0
                for length in range(1, len(_PLACEHOLDER_PREFIX)):
                    if buffer.endswith(_PLACEHOLDER_PREFIX[:length]):
                        keep = length
                if keep:
                    yield self.restore(buffer[:-keep], restoration_map)
                    buffer = buffer[-keep:]
                else:
                    yield self.restore(buffer, restoration_map)
                    buffer = ""
                break

        if buffer:
            yield self.restore(buffer, restoration_map)


_restorer: PIIRestorer | None = None


def init_restorer() -> PIIRestorer:
    global _restorer
    _restorer = PIIRestorer()
    return _restorer


def get_restorer() -> PIIRestorer:
    if _restorer is None:
        raise RuntimeError("PIIRestorer not initialized")
    return _restorer
