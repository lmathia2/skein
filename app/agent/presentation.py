"""Explicit publication of workflow prose; never interpret raw JSON in the UI."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from google.adk.events import Event
from google.genai import types

from harness.core.context import estimate_tokens, truncate_to_tokens


def conversation_history(
    events: Sequence[Event], *, invocation_id: str, max_tokens: int,
) -> str:
    """Budget prior human turns/public replies without changing ADK task isolation."""
    if max_tokens <= 0:
        return ""
    recent: list[str] = []
    remaining = max_tokens
    for event in reversed(events):
        if event.invocation_id == invocation_id or event.partial or event.content is None:
            continue
        public = (event.custom_metadata or {}).get("coding.public_message") is True
        user = event.author == "user" and event.isolation_scope is None
        if not (public or user):
            continue
        text = "".join(part.text or "" for part in event.content.parts or () if not part.thought)
        if not text:
            continue
        line = json.dumps({"role": "user" if user else "assistant", "text": text}, ensure_ascii=False)
        cost = estimate_tokens(line) + 1
        if cost > remaining:
            if not recent:
                bounded, _ = truncate_to_tokens(line, max(0, remaining - 8))
                recent.append(bounded + " [history truncated]")
            break
        recent.append(line)
        remaining -= cost
        if len(recent) >= 24:
            break
    bounded, _ = truncate_to_tokens("\n".join(reversed(recent)), max_tokens)
    return bounded


def message_event(message: str) -> Event:
    return Event(
        content=types.Content(role="model", parts=[types.Part(text=message)]),
        custom_metadata={"coding.public_message": True},
    )


def result_events(result: Mapping[str, Any]) -> tuple[Event, Event]:
    """Publish one reply and a small result, after the workflow decides its outcome."""
    status = str(result.get("status", "blocked"))
    if status == "answered":
        reply = result["message"]
    elif status == "complete":
        reply = result.get("message") or "Completed; deterministic verification passed."
    else:
        details = result.get("questions") or result.get("blockers") or [
            result.get("reason") or "Human input is required to continue."
        ]
        reply = "\n\n".join(str(item) for item in details)
    report = result.get("verification", {})
    public = {
        "status": status,
        "verified": report.get("passed") is True,
        "changed_paths": result.get("changed_paths", []),
    }
    return message_event(str(reply)), Event(
        output=public, custom_metadata={"coding.public_result": True}
    )
