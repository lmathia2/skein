from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from harness.evidence.state import EventKind, JsonlEventStore
from harness.evidence.tracing import CodingToolArtifactPlugin


def _invoke(
    plugin: CodingToolArtifactPlugin,
    *,
    result: dict[str, object],
    tool: str = "bash",
    task_id: str | None = "task-1",
    parent_task_id: str | None = None,
    function_call_id: str = "call-1",
) -> None:
    state = {"task_id": task_id} if task_id is not None else {}
    parent = (
        SimpleNamespace(state={"task_id": parent_task_id}, parent_ctx=None)
        if parent_task_id is not None
        else None
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=SimpleNamespace(name=tool),
            tool_args={"command": "pytest -q"},
            tool_context=SimpleNamespace(
                state=state,
                function_call_id=function_call_id,
                parent_ctx=parent,
            ),
            result=result,
        )
    )


def test_tool_artifact_plugin_appends_one_replay_safe_control_event(tmp_path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)
    artifact_uri = f"artifact://tool-output/{'a' * 64}.txt"
    result: dict[str, object] = {
        "status": "ok",
        "model_text": "bounded output",
        "artifact_uri": artifact_uri,
    }
    original = deepcopy(result)

    _invoke(plugin, result=result)
    _invoke(plugin, result=result)

    events = store.read("task-1")
    assert len(events) == 1
    assert events[0].kind == EventKind.TOOL_ARTIFACT_RECORDED
    assert events[0].payload == {
        "artifact_uri": artifact_uri,
        "kind": "coding_tool_output",
        "tool": "bash",
    }
    assert events[0].idempotency_key is not None
    assert artifact_uri not in events[0].idempotency_key
    assert result == original


def test_tool_artifact_plugin_filters_and_sorts_references(tmp_path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)
    artifacts = [
        f"artifact://tool-output/{'b' * 64}.txt",
        f"artifact://tool-output/{'a' * 64}.txt",
    ]

    _invoke(
        plugin,
        result={
            "artifact_uri": artifacts[0],
            "artifact_uris": [artifacts[1], artifacts[0], "https://user:secret@example.test"],
        },
    )

    assert [event.payload["artifact_uri"] for event in store.read("task-1")] == sorted(artifacts)


def test_tool_artifact_plugin_distinguishes_calls_but_dedupes_call_replay(tmp_path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)
    artifact_uri = f"artifact://tool-output/{'a' * 64}.txt"
    result = {"artifact_uri": artifact_uri}

    _invoke(plugin, result=result, function_call_id="call-1")
    _invoke(plugin, result=result, function_call_id="call-1")
    _invoke(plugin, result=result, function_call_id="call-2")

    events = store.read("task-1")
    assert len(events) == 2
    assert events[0].payload == events[1].payload
    assert events[0].idempotency_key != events[1].idempotency_key


def test_tool_artifact_plugin_ignores_untrusted_or_unscoped_results(tmp_path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)

    _invoke(plugin, result={"artifact_uri": "https://user:secret@example.test"})
    _invoke(
        plugin,
        result={"artifact_uri": f"artifact://tool-output/{'a' * 64}.txt"},
        tool="external_tool",
    )
    _invoke(
        plugin,
        result={"artifact_uri": f"artifact://tool-output/{'a' * 64}.txt"},
        task_id=None,
    )

    assert store.read("task-1") == []


def test_tool_artifact_plugin_recovers_task_scope_from_parent_context(tmp_path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)

    _invoke(
        plugin,
        result={"artifact_uri": f"artifact://tool-output/{'a' * 64}.txt"},
        task_id=None,
        parent_task_id="parent-task",
    )

    assert len(store.read("parent-task")) == 1


def test_tool_artifact_plugin_fails_open_when_control_store_is_unavailable(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    store = JsonlEventStore(tmp_path / "events")
    plugin = CodingToolArtifactPlugin(event_store=store)

    def fail_append(*_args, **_kwargs):
        raise OSError("control volume unavailable")

    monkeypatch.setattr(store, "append", fail_append)
    _invoke(
        plugin,
        result={"artifact_uri": f"artifact://tool-output/{'a' * 64}.txt"},
    )

    assert "continuing without bridge event" in caplog.text
