"""Durably bridge coding-tool artifact metadata into the control event stream."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from harness.context import safe_artifact_uri
from harness.state import EventKind, EventStore

LOGGER = logging.getLogger(__name__)

_CODING_TOOL_NAMES = frozenset({"read", "bash", "edit", "write", "execute_code"})


def _context_state_value(context: Any, name: str) -> Any:
    current = context
    for _ in range(8):
        try:
            state = current.state
        except Exception:  # pragma: no cover - defensive provider boundary
            state = None
        getter = getattr(state, "get", None)
        if callable(getter):
            try:
                value = getter(name, None)
            except Exception:  # pragma: no cover - defensive provider boundary
                value = None
            if value is not None:
                return value
        elif isinstance(state, Mapping) and name in state:
            return state[name]
        try:
            current = current.parent_ctx
        except Exception:  # pragma: no cover - defensive provider boundary
            break
        if current is None:
            break
    return None


def _tool_name(tool: Any) -> str | None:
    try:
        name = getattr(tool, "name", None)
    except Exception:  # pragma: no cover - defensive provider boundary
        return None
    value = str(name) if name else None
    return value if value in _CODING_TOOL_NAMES else None


def _result_artifacts(result: Any) -> list[str]:
    if not isinstance(result, Mapping):
        return []
    candidates: list[Any] = [result.get("artifact_uri")]
    multiple = result.get("artifact_uris")
    if isinstance(multiple, Sequence) and not isinstance(multiple, (str, bytes)):
        candidates.extend(multiple[:64])
    return sorted(
        {
            artifact
            for candidate in candidates
            if (artifact := safe_artifact_uri(candidate)) is not None
        }
    )


def _tool_call_identity(context: Any) -> str:
    """Return a replay-stable call identity without exposing it in event payloads."""

    current = context
    parts: list[str] = []
    for _ in range(8):
        for name in ("function_call_id", "node_path", "run_id"):
            try:
                value = getattr(current, name, None)
            except Exception:  # pragma: no cover - defensive provider boundary
                value = None
            if value is not None:
                normalized = str(value).strip()
                if normalized:
                    parts.append(f"{name}:{normalized[:512]}")
        try:
            current = current.parent_ctx
        except Exception:  # pragma: no cover - defensive provider boundary
            break
        if current is None:
            break
    return "\0".join(parts) or "unscoped-call"


class CodingToolArtifactPlugin(BasePlugin):
    """Persist content-addressed tool artifacts without copying tool output."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        default_task_id: str | None = None,
    ) -> None:
        super().__init__(name="coding_tool_artifacts")
        self.event_store = event_store
        self.default_task_id = default_task_id

    def _task_id(self, context: Any) -> str | None:
        candidate = self.default_task_id or _context_state_value(context, "task_id")
        if not isinstance(candidate, str):
            return None
        normalized = candidate.strip()
        return normalized if normalized and len(normalized) <= 256 else None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
    ) -> None:
        """Append one idempotent metadata event per validated artifact URI."""

        del tool_args
        task_id = self._task_id(tool_context)
        name = _tool_name(tool)
        if task_id is None or name is None:
            return None
        call_identity = _tool_call_identity(tool_context)
        for artifact_uri in _result_artifacts(result):
            payload = {
                "artifact_uri": artifact_uri,
                "kind": "coding_tool_output",
                "tool": name,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"call_identity": call_identity, "payload": payload},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            try:
                self.event_store.append(
                    task_id,
                    EventKind.TOOL_ARTIFACT_RECORDED,
                    payload,
                    idempotency_key=f"tool-artifact:{fingerprint}",
                )
            except Exception:
                LOGGER.exception(
                    "coding-tool artifact metadata append failed; continuing without bridge event"
                )
        return None


__all__ = ["CodingToolArtifactPlugin"]
