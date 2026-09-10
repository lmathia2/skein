"""Google ADK plugin that records provider model usage without prompt mutation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from .metrics import MetricsStore, ModelUsageSample, TaskOutcomeSample, ToolUsageSample

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per one million tokens for optional local cost accounting."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    reasoning: float = 0.0


@dataclass(frozen=True, slots=True)
class _PendingModelCall:
    started: float
    model: str
    task_id: str
    static_prefix_hash: str
    static_prefix_tokens: int
    dynamic_suffix_tokens: int


@dataclass(frozen=True, slots=True)
class _PendingToolCall:
    started: float
    task_id: str
    invocation_id: str
    tool_name: str
    arguments_hash: str


def _integer(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _attribute(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return default


def usage_counts(response: Any) -> dict[str, int]:
    """Extract usage from ADK or google-genai response variants."""

    usage = _attribute(
        response,
        "usage_metadata",
        "usageMetadata",
        "usage",
        default=response,
    )
    input_tokens = _integer(
        _attribute(
            usage,
            "prompt_token_count",
            "promptTokenCount",
            "input_tokens",
            "inputTokens",
        )
    )
    output_tokens = _integer(
        _attribute(
            usage,
            "candidates_token_count",
            "candidatesTokenCount",
            "output_tokens",
            "outputTokens",
        )
    )
    cache_read_tokens = _integer(
        _attribute(
            usage,
            "cached_content_token_count",
            "cachedContentTokenCount",
            "cache_read_tokens",
            "cacheReadTokens",
        )
    )
    cache_write_tokens = _integer(
        _attribute(
            usage,
            "cache_write_tokens",
            "cacheWriteTokens",
        )
    )
    reasoning_tokens = _integer(
        _attribute(
            usage,
            "thoughts_token_count",
            "thoughtsTokenCount",
            "reasoning_tokens",
            "reasoningTokens",
        )
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def estimate_cost(counts: Mapping[str, int], pricing: ModelPricing) -> float:
    input_tokens = max(counts.get("input_tokens", 0), 0)
    cache_read_tokens = max(counts.get("cache_read_tokens", 0), 0)
    uncached_input_tokens = max(input_tokens - cache_read_tokens, 0)
    total = (
        uncached_input_tokens * pricing.input
        + counts.get("output_tokens", 0) * pricing.output
        + cache_read_tokens * pricing.cache_read
        + counts.get("cache_write_tokens", 0) * pricing.cache_write
        + counts.get("reasoning_tokens", 0) * pricing.reasoning
    )
    return max(total / 1_000_000, 0.0)


def reported_cost(response: Any) -> float | None:
    """Return an exact provider-reported USD cost when an adapter supplies one."""

    metadata = _attribute(response, "custom_metadata", "customMetadata")
    value = _attribute(metadata, "provider_cost_usd")
    if isinstance(value, bool):
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if cost >= 0 else None


def pricing_from_env() -> dict[str, ModelPricing]:
    """Load a model-to-pricing map from JSON without embedding stale prices."""

    raw = os.getenv("SKEIN_MODEL_PRICING_JSON", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("SKEIN_MODEL_PRICING_JSON must be a JSON object")
    result: dict[str, ModelPricing] = {}
    for model, value in parsed.items():
        if not isinstance(value, dict):
            raise ValueError(f"pricing for {model!r} must be an object")
        result[str(model)] = ModelPricing(
            input=float(value.get("input", 0.0)),
            output=float(value.get("output", 0.0)),
            cache_read=float(value.get("cache_read", 0.0)),
            cache_write=float(value.get("cache_write", 0.0)),
            reasoning=float(value.get("reasoning", 0.0)),
        )
    return result


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    value = getattr(context, name, None)
    if value is not None:
        return value
    state = getattr(context, "state", None)
    state_get = getattr(state, "get", None)
    if callable(state_get):
        return state_get(name, default)
    return default


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class HarnessMetricsPlugin(BasePlugin):
    """Record one metrics row for every completed model call."""

    def __init__(
        self,
        *,
        database: Path,
        static_prefix_hash: str,
        static_prefix_tokens: int,
        default_model: str,
        default_task_id: str | None = None,
        pricing: Mapping[str, ModelPricing] | None = None,
        metric_sink: Callable[
            [ModelUsageSample | ToolUsageSample | TaskOutcomeSample], object
        ]
        | None = None,
    ) -> None:
        super().__init__(name="harness_metrics")
        self.store = MetricsStore(database, on_record=metric_sink)
        self.static_prefix_hash = static_prefix_hash
        self.static_prefix_tokens = max(static_prefix_tokens, 0)
        self.default_model = default_model
        self.default_task_id = default_task_id
        self.pricing = dict(pricing or {})
        self._starts: dict[str, deque[_PendingModelCall]] = defaultdict(deque)
        self._tool_starts: dict[str, deque[_PendingToolCall]] = defaultdict(deque)
        self._action_sequences: dict[str, int] = defaultdict(int)
        self._lock = threading.RLock()

    @staticmethod
    def _invocation_id(context: Any) -> str:
        value = _context_value(context, "invocation_id")
        if value:
            return str(value)
        session_id = _attribute(getattr(context, "session", None), "id")
        return str(session_id or "unknown")

    def _task_id(self, context: Any) -> str:
        value = _context_value(context, "task_id")
        if value:
            return str(value)
        if self.default_task_id:
            return self.default_task_id
        session_id = _context_value(context, "session_id")
        if not session_id:
            session_id = _attribute(getattr(context, "session", None), "id")
        return str(session_id or "unknown")

    def _pop_start(self, invocation_id: str) -> _PendingModelCall | None:
        with self._lock:
            pending = self._starts.get(invocation_id)
            if not pending:
                return None
            started = pending.popleft()
            if not pending:
                self._starts.pop(invocation_id, None)
            return started

    @staticmethod
    def _tool_name(tool: Any) -> str:
        return str(_attribute(tool, "name", default=type(tool).__name__))

    def _tool_key(self, context: Any, tool_name: str) -> str:
        invocation_id = self._invocation_id(context)
        call_id = _context_value(context, "function_call_id")
        return f"{invocation_id}\0{call_id or tool_name}"

    def _pop_tool_start(
        self,
        context: Any,
        tool_name: str,
    ) -> _PendingToolCall | None:
        key = self._tool_key(context, tool_name)
        with self._lock:
            pending = self._tool_starts.get(key)
            if not pending:
                return None
            started = pending.popleft()
            if not pending:
                self._tool_starts.pop(key, None)
            return started

    def _append_action_fingerprint(
        self,
        context: Any,
        *,
        task_id: str,
        fingerprint: str,
    ) -> None:
        state = getattr(context, "state", None)
        getter = getattr(state, "get", None)
        setter = getattr(state, "__setitem__", None)
        if not callable(getter) or not callable(setter):
            return
        with self._lock:
            self._action_sequences[task_id] += 1
            sequence = self._action_sequences[task_id]
        raw_history = getter("tool_action_fingerprints", [])
        history = list(raw_history) if isinstance(raw_history, list) else []
        existing_sequence = max(
            (
                _integer(item.get("sequence"))
                for item in history
                if isinstance(item, Mapping)
            ),
            default=0,
        )
        with self._lock:
            sequence = max(sequence, existing_sequence + 1)
            self._action_sequences[task_id] = sequence
        history.append({"sequence": sequence, "fingerprint": fingerprint})
        setter("tool_action_fingerprints", history[-200:])

    def _record_tool_result(
        self,
        *,
        context: Any,
        tool: Any,
        tool_args: dict[str, Any],
        result: Mapping[str, Any] | None,
        error: Exception | None = None,
    ) -> None:
        tool_name = self._tool_name(tool)
        pending = self._pop_tool_start(context, tool_name)
        invocation_id = self._invocation_id(context)
        task_id = self._task_id(context)
        arguments_hash = _canonical_hash(tool_args)
        started = time.monotonic()
        if pending is not None:
            invocation_id = pending.invocation_id
            task_id = pending.task_id
            arguments_hash = pending.arguments_hash
            started = pending.started
        payload = dict(result or {})
        raw_status = str(payload.get("status", "error" if error else "ok")).lower()
        status = (
            raw_status
            if raw_status in {"ok", "error", "blocked", "timeout"}
            else "error"
        )
        result_hash = str(payload.get("result_hash") or "") or _canonical_hash(
            payload if error is None else {"error_type": type(error).__name__}
        )
        canonical_result = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        self.store.record_tool_usage(
            ToolUsageSample(
                task_id=task_id,
                invocation_id=invocation_id,
                tool_name=tool_name,
                status=status,  # type: ignore[arg-type]
                arguments_hash=arguments_hash,
                result_hash=result_hash,
                duration_ms=max(int((time.monotonic() - started) * 1_000), 0),
                model_visible_bytes=len(canonical_result.encode()),
                omitted_bytes=_integer(payload.get("omitted_bytes")),
                replayed=bool(payload.get("replayed", False)),
            )
        )
        self._append_action_fingerprint(
            context,
            task_id=task_id,
            fingerprint=_canonical_hash(
                {
                    "tool": tool_name,
                    "arguments_hash": arguments_hash,
                    "result_hash": result_hash,
                    "status": status,
                }
            ),
        )

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        invocation_id = self._invocation_id(callback_context)
        model = str(
            _attribute(
                llm_request,
                "model",
                "model_name",
                "modelName",
                default=self.default_model,
            )
        )
        task_id = self._task_id(callback_context)
        input_limit = _integer(
            _context_value(callback_context, "task_input_token_limit", 0)
        )
        used_input = int(self.store.task_summary(task_id)["input_tokens"] or 0)
        if input_limit and used_input >= input_limit:
            raise RuntimeError(
                f"Task input-token budget exhausted ({used_input} used, {input_limit} allowed)"
            )
        prefix_hash = str(
            _context_value(
                callback_context,
                "stable_instruction_sha256",
                self.static_prefix_hash,
            )
        )
        prefix_tokens = _integer(
            _context_value(
                callback_context,
                "static_prefix_tokens_estimate",
                self.static_prefix_tokens,
            )
        )
        dynamic_tokens = _integer(
            _context_value(callback_context, "dynamic_context_tokens_estimate", 0)
        )
        with self._lock:
            self._starts[invocation_id].append(
                _PendingModelCall(
                    started=time.monotonic(),
                    model=model,
                    task_id=task_id,
                    static_prefix_hash=prefix_hash,
                    static_prefix_tokens=prefix_tokens,
                    dynamic_suffix_tokens=dynamic_tokens,
                )
            )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> None:
        if bool(_attribute(llm_response, "partial", default=False)):
            return None

        invocation_id = self._invocation_id(callback_context)
        metadata = _attribute(llm_response, "custom_metadata", "customMetadata", default={})
        if isinstance(metadata, Mapping) and metadata.get("skein_work_batch_yield") is True:
            self._pop_start(invocation_id)
            return None
        pending = self._pop_start(invocation_id)
        if pending is None:
            started = time.monotonic()
            model = self.default_model
            task_id = self._task_id(callback_context)
            prefix_hash = self.static_prefix_hash
            prefix_tokens = self.static_prefix_tokens
            dynamic_tokens = _integer(
                _context_value(
                    callback_context,
                    "dynamic_context_tokens_estimate",
                    0,
                )
            )
        else:
            started = pending.started
            model = pending.model
            task_id = pending.task_id
            prefix_hash = pending.static_prefix_hash
            prefix_tokens = pending.static_prefix_tokens
            dynamic_tokens = pending.dynamic_suffix_tokens
        response_model = _attribute(llm_response, "model_version", "modelVersion")
        if response_model:
            model = str(response_model)
        counts = usage_counts(llm_response)
        pricing = self.pricing.get(model, ModelPricing())
        exact_cost = reported_cost(llm_response)
        profile = metadata.get("provider_request_profile", {}) if isinstance(metadata, Mapping) else {}
        regions = profile.get("regions", {}) if isinstance(profile, Mapping) else {}
        self.store.record_model_usage(
            ModelUsageSample(
                task_id=task_id,
                invocation_id=invocation_id,
                model=model,
                static_prefix_hash=prefix_hash,
                static_prefix_tokens=prefix_tokens,
                dynamic_suffix_tokens=dynamic_tokens,
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cache_read_tokens=counts["cache_read_tokens"],
                cache_write_tokens=counts["cache_write_tokens"],
                reasoning_tokens=counts["reasoning_tokens"],
                cost_usd=(exact_cost if exact_cost is not None else estimate_cost(counts, pricing)),
                latency_ms=max(int((time.monotonic() - started) * 1_000), 0),
                provider_request_bytes=_integer(profile.get("bytes", 0)),
                provider_request_sha256=(
                    str(profile["sha256"])
                    if isinstance(profile, Mapping) and profile.get("sha256")
                    else None
                ),
                provider_request_regions_json=json.dumps(
                    regions if isinstance(regions, Mapping) else {},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        return None

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
        error: Exception,
    ) -> None:
        del llm_request, error
        self._pop_start(self._invocation_id(callback_context))
        return None

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> None:
        try:
            tool_name = self._tool_name(tool)
            pending = _PendingToolCall(
                started=time.monotonic(),
                task_id=self._task_id(tool_context),
                invocation_id=self._invocation_id(tool_context),
                tool_name=tool_name,
                arguments_hash=_canonical_hash(tool_args),
            )
            with self._lock:
                self._tool_starts[self._tool_key(tool_context, tool_name)].append(
                    pending
                )
        except Exception:
            LOGGER.exception("tool metrics start observation failed")
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
    ) -> None:
        try:
            self._record_tool_result(
                context=tool_context,
                tool=tool,
                tool_args=tool_args,
                result=result,
            )
        except Exception:
            LOGGER.exception("tool metrics completion observation failed")
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> None:
        try:
            self._record_tool_result(
                context=tool_context,
                tool=tool,
                tool_args=tool_args,
                result=None,
                error=error,
            )
        except Exception:
            LOGGER.exception("tool metrics error observation failed")
        return None


__all__ = [
    "HarnessMetricsPlugin",
    "ModelPricing",
    "estimate_cost",
    "pricing_from_env",
    "reported_cost",
    "usage_counts",
]
