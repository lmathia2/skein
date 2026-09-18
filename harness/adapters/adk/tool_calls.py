"""Archive malformed provider arguments before ADK persistence; never execute them."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, cast

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from harness.adapters.providers.codex_responses import (
    INVALID_ARGUMENTS_KEY,
    InvalidArgumentsPart,
    _function_call_part,
)
from harness.evidence.state import EventKind, EventStore
from harness.evidence.tracing.artifact_plugin import _context_state_value
from harness.execution.safety.redaction import SecretRedactor
from harness.ptc.notebook.artifacts import put_artifact


class InvalidToolArgumentsPlugin(BasePlugin):
    """One bounded, replayable rejection per invocation/call, outside the PTC worker."""

    def __init__(self, *, event_store: EventStore, artifact_root: Path,
                 redactor: SecretRedactor, default_task_id: str | None = None) -> None:
        super().__init__(name="invalid_tool_arguments")
        self.event_store = event_store
        self.artifact_root = artifact_root
        self.redactor = redactor
        self.default_task_id = default_task_id
        self.source_hash = hashlib.sha256((inspect.getsource(type(self)) +
                                          inspect.getsource(_function_call_part) +
                                          inspect.getsource(InvalidArgumentsPart)).encode()).hexdigest()

    def _identity(self, context: Any, call_id: Any) -> tuple[str, str, str]:
        task_id = _context_state_value(context, "task_id") or self.default_task_id
        invocation = getattr(context, "invocation_id", None)
        if not all(isinstance(value, str) and 0 < len(value) <= 256
                   for value in (task_id, invocation, call_id)):
            raise ValueError("malformed-call evidence requires task/invocation/call identity")
        if self.default_task_id and task_id != self.default_task_id:
            raise ValueError("malformed-call evidence task mismatch")
        return cast(tuple[str, str, str], (task_id, invocation, call_id))

    async def after_model_callback(self, *, callback_context: Any,
                                   llm_response: LlmResponse) -> None:
        # ADK never executes partial calls. Their parser-owned placeholder is
        # already bounded; defer publication until final usage has been charged
        # by the preceding metrics hook, even if the artifact volume then fails.
        if llm_response.partial:
            return None
        for part in (llm_response.content.parts or []) if llm_response.content else []:
            if not isinstance(part, InvalidArgumentsPart):
                continue
            call = part.function_call
            if call is None:
                raise ValueError("malformed-call evidence lost function identity")
            task_id, invocation, call_id = self._identity(callback_context, call.id)
            original = part._raw_arguments.encode("utf-8", errors="surrogatepass")
            retained = self.redactor.redact_text(part._raw_arguments).encode("utf-8", errors="surrogatepass")
            uri = put_artifact(self.artifact_root, retained)
            # Existing content-addressed storage is idempotent, but verify a
            # pre-existing object before publishing an address into history.
            if (self.artifact_root / uri.rsplit("/", 1)[-1]).read_bytes() != retained:
                raise ValueError("malformed-call artifact integrity mismatch")
            view = {"reason": "expected_json_object", "artifact_uri": uri,
                    "original_bytes": len(original), "retained_bytes": len(retained),
                    "redacted": retained != original}
            payload = {
                "program": "invalid_tool_arguments@1", "source_hash": self.source_hash,
                "invocation_id": invocation, "call_id": call_id, "tool": call.name,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "artifact_uri": uri, "view": view, "execution_started": False, "effect": "none",
            }
            identity = hashlib.sha256(f"{invocation}\0{call_id}".encode()).hexdigest()
            event = self.event_store.append(task_id, EventKind.TOOL_CALL_REJECTED, payload,
                                           idempotency_key=f"invalid-tool-arguments:{identity}")
            if event.task_id != task_id or event.payload != payload:
                raise ValueError("malformed-call evidence identity/content mismatch")
            # Publish only after durable evidence. Returning None lets usage and
            # other ADK observers run; private raw bytes never enter serialized views.
            call.args = {INVALID_ARGUMENTS_KEY: view}
        return None

    async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any],
                                   tool_context: Any) -> dict[str, Any] | None:
        if INVALID_ARGUMENTS_KEY not in tool_args:
            return None
        task_id, invocation, call_id = self._identity(tool_context, tool_context.function_call_id)
        records = [event for event in self.event_store.read(task_id)
                   if event.kind == EventKind.TOOL_CALL_REJECTED
                   and event.payload.get("invocation_id") == invocation
                   and event.payload.get("call_id") == call_id]
        result: dict[str, Any] = {
            "status": "error", "error_code": "invalid_tool_arguments",
            "execution_started": False, "effect": "none",
            "model_text": "Tool not executed: arguments must be a valid JSON object matching its schema. "
                          "Submit a corrected call; no code was repaired or executed.",
        }
        if records:
            if (len(records) != 1 or records[0].task_id != task_id
                    or records[0].payload.get("tool") != tool.name
                    or tool_args != {INVALID_ARGUMENTS_KEY: records[0].payload.get("view")}):
                raise ValueError("malformed-call rejection identity/content mismatch")
            result["artifact_uri"] = records[0].payload["artifact_uri"]
        # A model-supplied reserved marker is also non-executable, but cannot
        # confer artifact access or manufacture provider evidence.
        return result
