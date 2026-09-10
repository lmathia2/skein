"""A complete control header followed by plain Markdown, with no UI parsing."""

from __future__ import annotations

import json
from typing import Any

from harness.core.models.agent_step import AgentStep

MAX_REPLY_CHARS = 16_000
MAX_HEADER_CHARS = 16_000


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate reply control field")
        result[key] = value
    return result


def reply_header(line: str) -> AgentStep:
    """Validate all control fields before any human text can become public."""
    if len(line) > MAX_HEADER_CHARS:
        raise ValueError("Reply header exceeds its limit")
    payload = json.loads(line, object_pairs_hook=_unique_object)
    if not isinstance(payload, dict) or "message" in payload:
        raise ValueError("Reply header must not contain message")
    return AgentStep.model_validate(payload)


def parse_reply(text: str) -> AgentStep | None:
    """Recognize the new wire format; legacy JSON remains a buffered fallback."""
    line, separator, message = text.partition("\n")
    if not separator:
        return None
    try:
        header = reply_header(line)
    except ValueError:
        return None
    return AgentStep.model_validate({**header.model_dump(), "message": message})
