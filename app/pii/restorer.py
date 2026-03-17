"""Restores PII placeholders in LLM responses back to original values."""

from __future__ import annotations

import re

_PLACEHOLDER_RE = re.compile(r"<<PII_[A-Z_]+_[a-f0-9]{8}>>")


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
        # Partial match prefix — longest possible partial placeholder start
        partial_prefix = "<<PII_"

        for chunk in chunks:
            buffer += chunk
            # Keep buffering if we have a possible partial placeholder at the end
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
                else:
                    # Check if buffer ends with a partial placeholder
                    partial_match = False
                    for i in range(1, len(partial_prefix) + 1):
                        if buffer.endswith(partial_prefix[:i]):
                            partial_match = True
                            break
                    if partial_match and len(buffer) < 40:
                        # Keep buffering
                        break
                    else:
                        yield buffer
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
