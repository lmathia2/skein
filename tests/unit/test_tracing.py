from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.sessions.state import State
from google.genai import types

from harness.tracing import (
    HarnessTracePlugin,
    TraceContentMode,
    TraceSpan,
    TraceStore,
)


@dataclass
class _Context:
    invocation_id: str
    state: object
    session: object
    node_path: str = "root/worker@1"
    run_id: str = "1"
    attempt_count: int = 1
    function_call_id: str | None = None


def _context(invocation_id: str = "inv-1", *, tool: bool = False) -> _Context:
    return _Context(
        invocation_id=invocation_id,
        state=State({"task_id": "task-1"}, {}),
        session=SimpleNamespace(id="task-1", state={}),
        function_call_id="call-1" if tool else None,
    )


def _clock() -> datetime:
    return datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_store_orders_replays_queries_and_exports(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    base = TraceSpan(
        span_id="span-1",
        task_id="task",
        sequence=1,
        correlation_id="invocation",
        category="run",
        phase="start",
        name="run",
        timestamp=_clock().isoformat(),
        content_hash="a" * 64,
        payload_json="{}",
        idempotency_key="key-1",
    )
    first = store.append(base)
    replay = store.append(
        base.model_copy(
            update={
                "span_id": "different-generated-id",
                "timestamp": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
            }
        )
    )
    second = store.append(
        base.model_copy(
            update={
                "span_id": "span-2",
                "phase": "success",
                "parent_span_id": first.span_id,
                "idempotency_key": "key-2",
            }
        )
    )

    assert replay == first
    assert second.sequence == 2
    assert store.task_ids() == ["task"]
    assert store.query("task", phases=["success"]) == [second]
    exported = [json.loads(line) for line in store.export_jsonl("task").splitlines()]
    assert [item["sequence"] for item in exported] == [1, 2]


def test_metadata_only_is_default_and_never_persists_content(tmp_path: Path) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        known_secrets=[secret],
        clock=_clock,
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"prompt {secret}")],
    )

    asyncio.run(
        plugin.on_user_message_callback(
            invocation_context=_context(),
            user_message=message,
        )
    )

    span = plugin.store.query("task-1")[0]
    assert plugin.content_mode == TraceContentMode.METADATA_ONLY
    assert secret not in plugin.store.export_jsonl("task-1")
    assert "prompt" not in span.payload_json
    assert len(span.content_hash) == 64


def test_model_start_records_hashed_request_regions_without_prompt_content(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    request = LlmRequest(
        model="test-model",
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="work packet")]),
            types.Content(role="model", parts=[types.Part.from_text(text="tool call")]),
            types.Content(role="user", parts=[types.Part.from_text(text="tool result")]),
        ],
    )

    asyncio.run(
        plugin.before_model_callback(callback_context=_context(), llm_request=request)
    )

    payload = json.loads(plugin.store.query("task-1")[0].payload_json)
    profile = payload["request_profile"]
    assert profile["content_count"] == 3
    assert profile["total"]["bytes"] > profile["work_packet"]["bytes"]
    assert len(profile["latest_interaction"]["sha256"]) == 64
    assert "work packet" not in plugin.store.export_jsonl("task-1")


def test_metadata_only_classifies_virtual_search_without_query_bodies(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    tool = SimpleNamespace(name="bash")
    commands = (
        ("search grep --pattern 'private TODO phrase'", "search.grep"),
        ("search find --pattern 'private filename phrase'", "search.find"),
        ("search health", "search.health"),
        ("pytest -q", "bash"),
    )

    async def invoke() -> None:
        for index, (command, _expected_name) in enumerate(commands, start=1):
            context = _context(f"search-inv-{index}", tool=True)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"command": command},
                tool_context=context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"command": command},
                tool_context=context,
                result={"status": "ok"},
            )

    asyncio.run(invoke())

    spans = plugin.store.query("task-1", categories=["tool"])
    assert [span.name for span in spans] == [
        name
        for _command, name in commands
        for _phase in ("start", "success")
    ]
    exported = plugin.store.export_jsonl("task-1")
    assert "private TODO phrase" not in exported
    assert "private filename phrase" not in exported
    assert all(
        json.loads(span.payload_json)["keys"]
        == (["arguments"] if span.phase == "start" else ["arguments", "result"])
        for span in spans
    )


def test_virtual_search_trace_classification_is_fail_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    from harness.tools import search_command

    def fail_parser(_command: str):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(search_command, "parse_search_command", fail_parser)
    asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="bash"),
            tool_args={"command": "search health"},
            tool_context=_context(tool=True),
        )
    )

    span = plugin.store.query("task-1", categories=["tool"])[0]
    assert span.name == "bash"


def test_redacted_content_is_bounded_and_reports_omissions(tmp_path: Path) -> None:
    secret = "super-secret-token-value"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        max_payload_bytes=128,
        known_secrets=[secret],
        clock=_clock,
    )
    message = {"authorization": f"Bearer {secret}", "text": "x" * 2_000}

    asyncio.run(
        plugin.on_user_message_callback(
            invocation_context={
                "invocation_id": "inv-map",
                "state": {"task_id": "task-map"},
            },
            user_message=message,
        )
    )

    span = plugin.store.query("task-map")[0]
    assert secret not in span.payload_json
    assert len(span.payload_json.encode()) <= 128
    assert span.omitted_bytes > 0
    assert json.loads(span.payload_json)["truncated"] is True


def test_plugin_covers_every_adk_callback_and_preserves_provider_objects(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        clock=_clock,
    )
    context = _context(tool=True)
    agent = SimpleNamespace(name="worker")
    tool = SimpleNamespace(name="bash")
    request = LlmRequest(
        model="gemini-test",
        contents=[types.Content(role="user", parts=[types.Part.from_text(text="prompt")])],
    )
    response = LlmResponse(
        model_version="gemini-test-001",
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="response")],
        ),
    )
    tool_args = {"command": "pytest"}
    tool_result = {"exit_code": 0, "stdout": "ok"}
    request_before = request.model_dump(mode="python")
    response_before = response.model_dump(mode="python")
    args_before = deepcopy(tool_args)
    result_before = deepcopy(tool_result)

    async def invoke() -> None:
        await plugin.on_user_message_callback(
            invocation_context=context,
            user_message=request.contents[0],
        )
        await plugin.before_run_callback(invocation_context=context)
        await plugin.before_agent_callback(agent=agent, callback_context=context)
        await plugin.before_model_callback(
            callback_context=context,
            llm_request=request,
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response=response,
        )
        await plugin.before_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=context,
        )
        await plugin.after_tool_callback(
            tool=tool,
            tool_args=tool_args,
            tool_context=context,
            result=tool_result,
        )
        await plugin.after_agent_callback(agent=agent, callback_context=context)
        await plugin.on_event_callback(
            invocation_context=context,
            event={"author": "worker", "content": "event"},
        )
        await plugin.after_run_callback(invocation_context=context)
        # A resumed callback may replay after its in-memory parent stack is gone.
        await plugin.after_run_callback(invocation_context=context)

    asyncio.run(invoke())

    assert request.model_dump(mode="python") == request_before
    assert response.model_dump(mode="python") == response_before
    assert tool_args == args_before
    assert tool_result == result_before
    available_callbacks = {
        name
        for name, value in inspect.getmembers(BasePlugin, inspect.isfunction)
        if name.endswith("_callback")
    }
    assert available_callbacks <= set(HarnessTracePlugin.__dict__)
    spans = plugin.store.query("task-1")
    assert len(spans) == 10
    observed = {(span.category, span.phase) for span in spans}
    assert {
        ("user", "success"),
        ("run", "start"),
        ("run", "success"),
        ("agent", "start"),
        ("agent", "success"),
        ("model", "start"),
        ("model", "success"),
        ("tool", "start"),
        ("tool", "success"),
        ("event", "success"),
    } <= observed
    success_by_category = {
        span.category: span
        for span in spans
        if span.phase == "success" and span.category in {"run", "agent", "model", "tool"}
    }
    assert all(span.parent_span_id for span in success_by_category.values())
    assert {span.correlation_id for span in spans} == {"inv-1"}


def test_event_callback_omits_streaming_partials(tmp_path: Path) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)

    async def invoke() -> None:
        await plugin.on_event_callback(
            invocation_context=_context(),
            event={"author": "worker", "partial": True, "content": "delta"},
        )
        await plugin.on_event_callback(
            invocation_context=_context(),
            event={"author": "worker", "partial": False, "content": "complete"},
        )

    asyncio.run(invoke())

    spans = plugin.store.query("task-1", categories=["event"])
    assert len(spans) == 1
    assert spans[0].name == "worker"


def test_error_callbacks_are_redacted_and_parented(tmp_path: Path) -> None:
    secret = "password-that-must-not-leak"
    plugin = HarnessTracePlugin(
        database=tmp_path / "trace.db",
        content_mode=TraceContentMode.REDACTED_CONTENT,
        known_secrets=[secret],
        clock=_clock,
    )
    agent = SimpleNamespace(name="worker")
    tool = SimpleNamespace(name="write")

    async def invoke_errors() -> None:
        run_context = _context("run-error")
        await plugin.before_run_callback(invocation_context=run_context)
        await plugin.on_run_error_callback(
            invocation_context=run_context,
            error=RuntimeError(secret),
        )

        agent_context = _context("agent-error")
        await plugin.before_agent_callback(agent=agent, callback_context=agent_context)
        await plugin.on_agent_error_callback(
            agent=agent,
            callback_context=agent_context,
            error=RuntimeError(secret),
        )

        model_context = _context("model-error")
        request = {"modelName": "gemini-test", "contents": [secret]}
        await plugin.before_model_callback(
            callback_context=model_context,
            llm_request=request,
        )
        await plugin.on_model_error_callback(
            callback_context=model_context,
            llm_request=request,
            error=RuntimeError(secret),
        )

        tool_context = _context("tool-error", tool=True)
        arguments = {"password": secret}
        await plugin.before_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=tool_context,
        )
        await plugin.on_tool_error_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=tool_context,
            error=RuntimeError(secret),
        )

    asyncio.run(invoke_errors())

    errors = plugin.store.query("task-1", phases=["error"])
    assert {span.category for span in errors} == {"run", "agent", "model", "tool"}
    assert all(span.parent_span_id for span in errors)
    assert secret not in plugin.store.export_jsonl("task-1")
    assert all("<redacted>" in span.payload_json for span in errors)


def test_session_identity_keeps_early_and_late_callbacks_together(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context()
    context.session.id = "session-direct"

    async def invoke() -> None:
        await plugin.before_run_callback(invocation_context=context)
        context.state["task_id"] = "derived-task"
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
        await plugin.after_run_callback(invocation_context=context)

    asyncio.run(invoke())

    assert len(plugin.store.query("session-direct")) == 3
    assert plugin.store.query("derived-task") == []


def test_nested_callback_context_reuses_early_session_identity(tmp_path: Path) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    invocation_context = _context()
    invocation_context.session.id = "session-nested"
    callback_context = SimpleNamespace(
        _invocation_context=invocation_context,
        state=State({"task_id": "late-task"}, {}),
        node_path="root/worker@1",
        run_id="1",
    )

    async def invoke() -> None:
        await plugin.before_run_callback(invocation_context=invocation_context)
        await plugin.before_model_callback(
            callback_context=callback_context,
            llm_request={"model": "gemini-test"},
        )
        await plugin.after_model_callback(
            callback_context=callback_context,
            llm_response={"modelVersion": "gemini-test-001", "partial": False},
        )
        await plugin.after_run_callback(invocation_context=invocation_context)

    asyncio.run(invoke())

    assert len(plugin.store.query("session-nested")) == 4
    assert plugin.store.query("late-task") == []


def test_invocation_identity_is_stable_when_task_id_appears_late(tmp_path: Path) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context: dict[str, object] = {"invocation_id": "inv-early", "state": {}}

    asyncio.run(plugin.before_run_callback(invocation_context=context))
    context["state"] = {"task_id": "late-task"}
    asyncio.run(plugin.after_run_callback(invocation_context=context))

    spans = plugin.store.query("invocation:inv-early")
    assert [span.phase for span in spans] == ["start", "success"]
    assert plugin.store.query("late-task") == []


def test_partial_models_blocked_tools_and_storage_failures_are_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context(tool=True)

    async def invoke() -> None:
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"partial": True},
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"partial": False},
        )
        await plugin.before_tool_callback(
            tool=SimpleNamespace(name="bash"),
            tool_args={"command": "curl example.test"},
            tool_context=context,
        )
        await plugin.after_tool_callback(
            tool=SimpleNamespace(name="bash"),
            tool_args={"command": "curl example.test"},
            tool_context=context,
            result={"status": "blocked", "risk": "network"},
        )

    asyncio.run(invoke())
    spans = plugin.store.query("task-1")
    assert len([span for span in spans if span.category == "model"]) == 2
    assert any(span.category == "tool" and span.phase == "blocked" for span in spans)

    def fail_append(_span):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(plugin.store, "append", fail_append)
    asyncio.run(plugin.before_run_callback(invocation_context=context))


def test_streaming_model_records_exactly_one_terminal_span(tmp_path: Path) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context()

    async def invoke() -> None:
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
        for text in ("a", "ab"):
            await plugin.after_model_callback(
                callback_context=context,
                llm_response={"modelVersion": "gemini-test-001", "partial": True, "text": text},
            )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"modelVersion": "gemini-test-001", "partial": False, "text": "abc"},
        )
        # Some streaming adapters repeat a differently shaped final response.
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={
                "modelVersion": "gemini-test-001",
                "partial": False,
                "text": "abc",
                "finish": "stop",
            },
        )

    asyncio.run(invoke())

    spans = plugin.store.query("task-1", categories=["model"])
    assert [span.phase for span in spans] == ["start", "success"]
    assert spans[1].parent_span_id == spans[0].span_id


def test_distinct_runtime_occurrences_are_replay_idempotent(tmp_path: Path) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    agent = SimpleNamespace(name="worker")
    tool = SimpleNamespace(name="read")
    first_agent = _context()
    second_agent = _context()
    second_agent.node_path = "root/worker@2"
    second_agent.run_id = "2"
    first_tool = _context(tool=True)
    second_tool = _context(tool=True)
    second_tool.function_call_id = "call-2"

    async def invoke() -> None:
        for context in (first_agent, second_agent, first_agent):
            await plugin.before_agent_callback(agent=agent, callback_context=context)
            await plugin.after_agent_callback(agent=agent, callback_context=context)
        for context in (first_tool, second_tool, first_tool):
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=context,
                result={"status": "success"},
            )

    asyncio.run(invoke())

    agent_spans = plugin.store.query("task-1", categories=["agent"])
    tool_spans = plugin.store.query("task-1", categories=["tool"])
    assert len(agent_spans) == 4
    assert len(tool_spans) == 4
    assert len({span.span_id for span in agent_spans if span.phase == "start"}) == 2
    assert len({span.span_id for span in tool_spans if span.phase == "start"}) == 2


def test_model_retry_attempts_are_distinct_and_each_attempt_is_replay_safe(
    tmp_path: Path,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context()

    async def invoke_attempt() -> None:
        await plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test", "prompt": "same"},
        )
        await plugin.after_model_callback(
            callback_context=context,
            llm_response={"modelVersion": "gemini-test-001", "text": "same"},
        )

    asyncio.run(invoke_attempt())
    context.attempt_count = 2
    asyncio.run(invoke_attempt())
    context.attempt_count = 1
    asyncio.run(invoke_attempt())

    spans = plugin.store.query("task-1", categories=["model"])
    assert [span.phase for span in spans] == ["start", "success", "start", "success"]
    assert spans[1].parent_span_id == spans[0].span_id
    assert spans[3].parent_span_id == spans[2].span_id


def test_model_terminal_bookkeeping_is_fail_open_for_hostile_state_accessor(
    tmp_path: Path,
) -> None:
    class HostileState:
        def get(self, _name: str, _default=None):
            raise RuntimeError("provider state unavailable")

    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = SimpleNamespace(
        invocation_id="hostile-invocation",
        state=HostileState(),
        session=SimpleNamespace(id="hostile-session", state={}),
    )

    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response={"modelVersion": "gemini-test-001", "text": "ok"},
        )
    )

    spans = plugin.store.query("hostile-session", categories=["model"])
    assert len(spans) == 1
    assert spans[0].phase == "success"


def test_terminal_storage_failure_preserves_parent_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = HarnessTracePlugin(database=tmp_path / "trace.db", clock=_clock)
    context = _context()

    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request={"model": "gemini-test"},
        )
    )
    append = plugin.store.append

    def fail_append(_span):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(plugin.store, "append", fail_append)
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response={"modelVersion": "gemini-test-001", "partial": False},
        )
    )
    monkeypatch.setattr(plugin.store, "append", append)
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response={"modelVersion": "gemini-test-001", "partial": False},
        )
    )

    spans = plugin.store.query("task-1", categories=["model"])
    assert len(spans) == 2
    assert spans[1].parent_span_id == spans[0].span_id
