"""Privacy-safe Google ADK lifecycle tracing plugin."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk.plugins.base_plugin import BasePlugin
from pydantic import BaseModel

from harness.execution.safety.redaction import SecretRedactor

from .store import TraceSpan, TraceStore

LOGGER = logging.getLogger(__name__)


class TraceContentMode(StrEnum):
    """Controls whether traces contain structural metadata or redacted content."""

    METADATA_ONLY = "metadata_only"
    REDACTED_CONTENT = "redacted_content"


def _attribute(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        try:
            result = getattr(value, name, None)
        except Exception:  # pragma: no cover - defensive provider boundary
            continue
        if result is not None:
            return result
    return default


def _state_value(context: Any, name: str) -> Any:
    for candidate in (
        _attribute(context, "state"),
        _attribute(_attribute(context, "session"), "state"),
        _attribute(_attribute(context, "invocation_context", "_invocation_context"), "state"),
    ):
        if candidate is None:
            continue
        getter = getattr(candidate, "get", None)
        if callable(getter):
            try:
                value = getter(name, None)
            except Exception:  # pragma: no cover - defensive provider boundary
                continue
            if value is not None:
                return value
        if isinstance(candidate, Mapping) and name in candidate:
            return candidate[name]
    return None


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    value = _attribute(context, name)
    if value is not None:
        return value
    invocation = _attribute(context, "invocation_context", "_invocation_context")
    value = _attribute(invocation, name)
    if value is not None:
        return value
    value = _state_value(context, name)
    return default if value is None else value


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth >= 20:
        return {"type": type(value).__name__, "depth_limited": True}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="python"), depth=depth + 1)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item, depth=depth + 1) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict(), depth=depth + 1)
        except Exception:  # pragma: no cover - defensive provider boundary
            pass
    try:
        attributes = vars(value)
    except TypeError:
        attributes = {}
    public = {key: item for key, item in attributes.items() if not key.startswith("_")}
    if public:
        return {
            "type": type(value).__name__,
            "attributes": _jsonable(public, depth=depth + 1),
        }
    return {"type": type(value).__name__, "repr": repr(value)}


def _metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {
            "type": "object",
            "keys": sorted(str(key) for key in value),
            "field_count": len(value),
        }
        profile = value.get("request_profile")
        if isinstance(profile, Mapping):
            result["request_profile"] = dict(profile)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"type": "array", "length": len(value)}
    if isinstance(value, str):
        return {"type": "string", "length": len(value.encode())}
    return {"type": type(value).__name__}


def _bounded_json(value: Any, max_bytes: int) -> tuple[str, int]:
    full = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    full_size = len(full.encode())
    if full_size <= max_bytes:
        return full, 0
    low, high = 0, len(full)
    best = json.dumps({"preview": "", "truncated": True}, separators=(",", ":"))
    while low <= high:
        middle = (low + high) // 2
        candidate = json.dumps(
            {"preview": full[:middle], "truncated": True},
            separators=(",", ":"),
        )
        if len(candidate.encode()) <= max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    preview = json.loads(best)["preview"]
    return best, full_size - len(preview.encode())


def _request_profile(llm_request: Any) -> dict[str, Any]:
    """Hash and size canonical request regions without retaining prompt content."""

    request = _jsonable(llm_request)
    if not isinstance(request, Mapping):
        request = {"request": request}
    contents = request.get("contents", [])
    if not isinstance(contents, list):
        contents = []

    def region(value: Any) -> dict[str, Any]:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        return {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}

    stable = {
        "model": request.get("model"),
        "config": request.get("config"),
        "tools_dict": request.get("tools_dict"),
    }
    return {
        "serialization": "canonical_adk_json_v1",
        "content_count": len(contents),
        "total": region(request),
        "stable": region(stable),
        "work_packet": region(contents[:1]),
        "retained_history": region(contents[1:-2] if len(contents) > 2 else []),
        "latest_interaction": region(contents[-2:] if len(contents) > 1 else contents),
    }


def _tool_trace_name(tool: Any, tool_args: Mapping[str, Any]) -> str:
    """Classify reserved search calls without retaining their query arguments."""

    name = str(_attribute(tool, "name", default=type(tool).__name__))
    if name != "bash":
        return name
    command = tool_args.get("command")
    if not isinstance(command, str):
        return name
    try:
        # Keep the native-search dependency outside tracing's import path so
        # optional telemetry remains usable and fail-open on every platform.
        from harness.execution.tools.search_command import parse_search_command

        search_command = parse_search_command(command)
    except Exception:
        # Tracing is observational. Malformed commands and parser failures keep
        # the ordinary bash label and must never affect tool execution.
        return name
    if search_command is None:
        return name
    return f"search.{search_command.operation}"


class HarnessTracePlugin(BasePlugin):
    """Record all ADK 2.7 lifecycle callbacks without mutating provider data."""

    def __init__(
        self,
        *,
        database: Path,
        content_mode: TraceContentMode = TraceContentMode.METADATA_ONLY,
        max_payload_bytes: int = 4_096,
        known_secrets: Sequence[str] = (),
        default_task_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        span_sink: Callable[[TraceSpan], object] | None = None,
    ) -> None:
        super().__init__(name="harness_trace")
        if max_payload_bytes < 64:
            raise ValueError("max_payload_bytes must be at least 64")
        self.store = TraceStore(database, on_append=span_sink)
        self.content_mode = content_mode
        self.max_payload_bytes = max_payload_bytes
        self.default_task_id = default_task_id
        self.redactor = SecretRedactor(
            known_secrets=known_secrets,
            redact_high_entropy_values=True,
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self._active: dict[tuple[str, str, str, str], deque[str]] = defaultdict(deque)
        self._closed: set[tuple[str, str, str, str]] = set()
        self._task_by_correlation: dict[str, str] = {}
        self._lock = threading.RLock()

    def _task_id(self, context: Any) -> str:
        if self.default_task_id:
            return self.default_task_id
        correlation_id = self._correlation_id(context)
        with self._lock:
            cached = self._task_by_correlation.get(correlation_id)
        if cached is not None:
            return cached
        invocation = _attribute(context, "invocation_context", "_invocation_context")
        session = _attribute(context, "session") or _attribute(invocation, "session")
        session_id = _attribute(session, "id")
        value = str(
            session_id
            or _context_value(context, "task_id")
            or (f"invocation:{correlation_id}" if correlation_id != "unknown" else "unknown")
        )
        with self._lock:
            self._task_by_correlation.setdefault(correlation_id, value)
            return self._task_by_correlation[correlation_id]

    @staticmethod
    def _correlation_id(context: Any) -> str:
        value = _context_value(context, "invocation_id")
        if value:
            return str(value)
        invocation = _attribute(context, "invocation_context", "_invocation_context")
        session = _attribute(context, "session") or _attribute(invocation, "session")
        return str(_attribute(session, "id", default="unknown"))

    @staticmethod
    def _operation_id(context: Any, category: str, name: str) -> str:
        components: list[str] = []
        if category == "tool":
            value = _context_value(context, "function_call_id")
            if value:
                components.append(f"function_call_id:{value}")
        for field in ("node_path", "run_id", "branch", "attempt_count"):
            value = _context_value(context, field)
            if value:
                components.append(f"{field}:{value}")
        # Model providers commonly return a versioned model name that differs
        # from the requested name. Keep both callbacks in the same operation
        # when ADK does not provide workflow execution identifiers.
        return "|".join(components) if components else ("model" if category == "model" else name)

    def _active_key(self, context: Any, category: str, name: str) -> tuple[str, str, str, str]:
        return (
            self._task_id(context),
            self._correlation_id(context),
            category,
            self._operation_id(context, category, name),
        )

    def _parent_for(self, context: Any, category: str, name: str, phase: str) -> str | None:
        key = self._active_key(context, category, name)
        with self._lock:
            if phase in {"success", "error", "blocked"} and self._active.get(key):
                return self._active[key][0]
            task_id, correlation_id, _, _ = key
            parent_categories = {
                "agent": ("run",),
                "model": ("agent", "run"),
                "tool": ("agent", "run"),
                "event": ("run",),
                "user": ("run",),
            }.get(category, ())
            for parent_category in parent_categories:
                candidates = [
                    queue[-1]
                    for active_key, queue in self._active.items()
                    if active_key[0] == task_id
                    and active_key[1] == correlation_id
                    and active_key[2] == parent_category
                    and queue
                ]
                if candidates:
                    return candidates[-1]
        return None

    def _mark_recorded(
        self,
        *,
        key: tuple[str, str, str, str],
        phase: str,
        parent_span_id: str | None,
        candidate_span_id: str,
        stored_span_id: str,
    ) -> None:
        """Advance in-memory pairing only after durable storage succeeds."""

        with self._lock:
            if phase == "start":
                if stored_span_id == candidate_span_id:
                    self._closed.discard(key)
                    self._active[key].append(stored_span_id)
                elif key not in self._closed and stored_span_id not in self._active[key]:
                    # A fresh plugin instance may be replaying a stored start.
                    self._active[key].append(stored_span_id)
                return
            if phase not in {"success", "error", "blocked"}:
                return
            queue = self._active.get(key)
            if queue and queue[0] == parent_span_id:
                queue.popleft()
                if not queue:
                    self._active.pop(key, None)
            self._closed.add(key)

    def _is_closed(self, context: Any, category: str, name: str) -> bool:
        with self._lock:
            return self._active_key(context, category, name) in self._closed

    def _record_unchecked(
        self,
        *,
        context: Any,
        category: str,
        phase: str,
        name: str,
        content: Any,
    ) -> TraceSpan:
        task_id = self._task_id(context)
        correlation_id = self._correlation_id(context)
        jsonable = _jsonable(content)
        canonical = json.dumps(jsonable, sort_keys=True, separators=(",", ":"), default=str)
        content_hash = hashlib.sha256(canonical.encode()).hexdigest()
        if self.content_mode == TraceContentMode.METADATA_ONLY:
            persisted = _metadata(jsonable)
        else:
            persisted = self.redactor.redact(jsonable)
        payload_json, omitted_bytes = _bounded_json(persisted, self.max_payload_bytes)
        operation_id = self._operation_id(context, category, name)
        idempotency_material = "\0".join(
            (correlation_id, category, phase, name, operation_id, content_hash)
        )
        idempotency_key = hashlib.sha256(idempotency_material.encode()).hexdigest()
        parent_span_id = self._parent_for(context, category, name, phase)
        active_key = self._active_key(context, category, name)
        candidate = TraceSpan(
            span_id=uuid4().hex,
            task_id=task_id,
            sequence=1,
            correlation_id=correlation_id,
            parent_span_id=parent_span_id,
            category=category,
            phase=phase,
            name=name,
            timestamp=self.clock().astimezone(UTC).isoformat(),
            content_hash=content_hash,
            payload_json=payload_json,
            omitted_bytes=omitted_bytes,
            idempotency_key=idempotency_key,
        )
        stored = self.store.append(candidate)
        self._mark_recorded(
            key=active_key,
            phase=phase,
            parent_span_id=parent_span_id,
            candidate_span_id=candidate.span_id,
            stored_span_id=stored.span_id,
        )
        return stored

    def _record(
        self,
        *,
        context: Any,
        category: str,
        phase: str,
        name: str,
        content: Any,
    ) -> TraceSpan | None:
        """Observe without allowing optional telemetry to fail the task."""

        try:
            return self._record_unchecked(
                context=context,
                category=category,
                phase=phase,
                name=name,
                content=content,
            )
        except Exception:
            LOGGER.exception(
                "trace observation failed for %s.%s",
                category,
                phase,
            )
            return None

    async def on_user_message_callback(self, *, invocation_context: Any, user_message: Any) -> None:
        self._record(
            context=invocation_context,
            category="user",
            phase="success",
            name="message",
            content={"message": user_message},
        )

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        self._record(
            context=invocation_context,
            category="run",
            phase="start",
            name="run",
            content={"context": invocation_context},
        )

    async def on_event_callback(self, *, invocation_context: Any, event: Any) -> None:
        if _attribute(event, "partial", default=False):
            return
        self._record(
            context=invocation_context,
            category="event",
            phase="success",
            name=str(_attribute(event, "author", default="event")),
            content={"event": event},
        )

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        self._record(
            context=invocation_context,
            category="run",
            phase="success",
            name="run",
            content={"context": invocation_context},
        )

    async def on_run_error_callback(self, *, invocation_context: Any, error: Exception) -> None:
        self._record(
            context=invocation_context,
            category="run",
            phase="error",
            name="run",
            content={"error_type": type(error).__name__, "error": str(error)},
        )

    async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        name = str(_attribute(agent, "name", default=type(agent).__name__))
        self._record(
            context=callback_context,
            category="agent",
            phase="start",
            name=name,
            content={"agent": agent},
        )

    async def after_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        name = str(_attribute(agent, "name", default=type(agent).__name__))
        self._record(
            context=callback_context,
            category="agent",
            phase="success",
            name=name,
            content={"agent": agent},
        )

    async def on_agent_error_callback(
        self, *, agent: Any, callback_context: Any, error: Exception
    ) -> None:
        name = str(_attribute(agent, "name", default=type(agent).__name__))
        self._record(
            context=callback_context,
            category="agent",
            phase="error",
            name=name,
            content={"agent": agent, "error_type": type(error).__name__, "error": str(error)},
        )

    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> None:
        name = str(_attribute(llm_request, "model", "model_name", "modelName", default="model"))
        self._record(
            context=callback_context,
            category="model",
            phase="start",
            name=name,
            content={"request": llm_request, "request_profile": _request_profile(llm_request)},
        )

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        try:
            if bool(_attribute(llm_response, "partial", default=False)):
                return None
            name = str(
                _attribute(
                    llm_response,
                    "model_version",
                    "modelVersion",
                    default="model",
                )
            )
            if self._is_closed(callback_context, "model", name):
                return None
            self._record(
                context=callback_context,
                category="model",
                phase="success",
                name=name,
                content={"response": llm_response},
            )
        except Exception:
            LOGGER.exception("trace observation failed for model.success")
            return None

    async def on_model_error_callback(
        self, *, callback_context: Any, llm_request: Any, error: Exception
    ) -> None:
        name = str(_attribute(llm_request, "model", "model_name", "modelName", default="model"))
        self._record(
            context=callback_context,
            category="model",
            phase="error",
            name=name,
            content={
                "request": llm_request,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    async def before_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
    ) -> None:
        name = _tool_trace_name(tool, tool_args)
        self._record(
            context=tool_context,
            category="tool",
            phase="start",
            name=name,
            content={"arguments": tool_args},
        )

    async def after_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, result: dict[str, Any]
    ) -> None:
        name = _tool_trace_name(tool, tool_args)
        status = str(_attribute(result, "status", default="")).lower()
        phase = (
            "blocked"
            if status == "blocked"
            else "error"
            if status in {"error", "timeout"}
            else "success"
        )
        self._record(
            context=tool_context,
            category="tool",
            phase=phase,
            name=name,
            content={"arguments": tool_args, "result": result},
        )

    async def on_tool_error_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, error: Exception
    ) -> None:
        name = _tool_trace_name(tool, tool_args)
        self._record(
            context=tool_context,
            category="tool",
            phase="error",
            name=name,
            content={
                "arguments": tool_args,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )


__all__ = ["HarnessTracePlugin", "TraceContentMode"]
