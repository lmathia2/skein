from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from harness.adapters.adk import SteeringPlugin
from harness.evidence.state import EventKind, JsonlEventStore, SteeringQueue


def _context(*, agent_name: str = "coding_worker") -> SimpleNamespace:
    return SimpleNamespace(
        agent_name=agent_name,
        invocation_id="invocation-1",
        state={
            "task_id": "task-1",
            "steering_owner": "worker-1",
            "steering_packet_message_ids": [],
        },
    )


def _plugin(tmp_path: Path) -> tuple[SteeringPlugin, SteeringQueue, JsonlEventStore]:
    queue = SteeringQueue(tmp_path / "state.db")
    events = JsonlEventStore(tmp_path / "events")
    return (
        SteeringPlugin(queue=queue, event_store=events, lease_seconds=60),
        queue,
        events,
    )


def test_plugin_injects_mid_batch_steering_on_every_model_boundary(
    tmp_path: Path,
) -> None:
    plugin, queue, events = _plugin(tmp_path)
    context = _context()
    initial = LlmRequest()
    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=initial,
        )
    )
    assert initial.contents == []

    queued = queue.enqueue("task-1", "Use the public API")
    fenced = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="edit"),
            tool_args={"path": "app.py"},
            tool_context=context,
        )
    )
    assert fenced is not None
    assert fenced["status"] == "steering_pending"

    first_turn = LlmRequest()
    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=first_turn,
        )
    )
    second_turn = LlmRequest()
    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=second_turn,
        )
    )

    assert "Use the public API" in first_turn.contents[-1].parts[0].text
    assert "Use the public API" in second_turn.contents[-1].parts[0].text
    assert [message.message_id for message in queue.leased_by("task-1", "worker-1")] == [
        queued.message_id
    ]
    recorded = events.read("task-1")
    assert [event.kind for event in recorded] == [EventKind.STEERING_RECEIVED, EventKind.STEERING_EXPOSED]
    assert recorded[0].payload["message_id"] == queued.message_id


def test_steering_keeps_its_position_after_acknowledgement_and_plugin_restart(tmp_path) -> None:
    from harness.adapters.providers.openrouter_responses import build_openrouter_request_body

    plugin, queue, events = _plugin(tmp_path)
    plugin.mark_context = True
    context = _context()
    initial = types.Content(role="user", parts=[types.Part(text="Original task")])
    response = types.Content(role="model", parts=[types.Part(text="Completed a bounded work batch")])
    native = [initial]
    message = queue.enqueue("task-1", "First instruction")
    first = LlmRequest(contents=list(native))
    asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=first))
    assert context.state["context_steering"]["protected_from"] == 1
    first_body = build_openrouter_request_body(first, model="fixture", reasoning_effort=None)
    queue.ack([message.message_id], "worker-1")
    native.append(response)
    restored = SteeringPlugin(queue=queue, event_store=events, lease_seconds=60, mark_context=True)
    second = LlmRequest(contents=list(native))
    asyncio.run(restored.before_model_callback(callback_context=context, llm_request=second))
    second_body = build_openrouter_request_body(second, model="fixture", reasoning_effort=None)
    assert second_body["input"][:len(first_body["input"])] == first_body["input"]
    assert second.contents[1] == first.contents[1]
    assert context.state["context_steering"] is None
    assert not queue.has_pending("task-1")
    queue.enqueue("task-1", "New correction")
    third = LlmRequest(contents=list(native))
    asyncio.run(restored.before_model_callback(callback_context=context, llm_request=third))
    assert third.contents[:len(second.contents)] == second.contents
    assert context.state["context_steering"]["protected_from"] == len(second.contents)
    assert len([e for e in events.read("task-1") if e.kind == EventKind.STEERING_EXPOSED]) == 2
    # Re-entry at an unchanged native boundary publishes no duplicate.
    replay = LlmRequest(contents=list(native))
    asyncio.run(restored.before_model_callback(callback_context=context, llm_request=replay))
    assert replay.contents == third.contents
    assert len([e for e in events.read("task-1") if e.kind == EventKind.STEERING_EXPOSED]) == 2


@pytest.mark.parametrize("fault", ("publication", "history", "hash", "anchor", "source", "cached_content"))
def test_steering_exposure_faults_fail_before_request_mutation(tmp_path, monkeypatch, fault) -> None:
    plugin, queue, events = _plugin(tmp_path)
    context = _context()
    original = [types.Content(role="user", parts=[types.Part(text="Task")]),
                types.Content(role="model", parts=[types.Part(text="Earlier response")])]
    queue.enqueue("task-1", "Required correction")
    if fault == "publication":
        append = events.append
        def fail(task_id, kind, payload, **kwargs):
            if kind == EventKind.STEERING_EXPOSED:
                raise OSError("publication failed")
            return append(task_id, kind, payload, **kwargs)
        monkeypatch.setattr(events, "append", fail)
    else:
        first = LlmRequest(contents=list(original))
        asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=first))
        if fault == "history":
            original[-1] = types.Content(role="model", parts=[types.Part(text="Rewritten response")])
        elif fault == "cached_content":
            next(iter(plugin._exposure_contents.values())).parts[0].text = "changed"
        else:
            recorded = events.read("task-1")
            damaged = []
            for event in recorded:
                payload = json.loads(json.dumps(event.payload))
                if event.kind == (EventKind.STEERING_EXPOSED if fault in {"hash", "anchor"} else EventKind.STEERING_RECEIVED):
                    payload[{"hash": "content_hash", "anchor": "anchor", "source": "content"}[fault]] = "changed"
                damaged.append(event.model_copy(update={"payload": payload}))
            monkeypatch.setattr(events, "read", lambda _: damaged)
    request = LlmRequest(contents=list(original))
    with pytest.raises((OSError, ValueError)):
        asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=request))
    assert request.contents == original
    if fault == "publication":
        monkeypatch.setattr(events, "append", append)
        asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=request))
        assert len(request.contents) == len(original) + 1


def test_plugin_ignores_non_coding_model_calls(tmp_path: Path) -> None:
    plugin, queue, _events = _plugin(tmp_path)
    queue.enqueue("task-1", "Change direction")
    request = LlmRequest()

    asyncio.run(
        plugin.before_model_callback(
            callback_context=_context(agent_name="final_diff_reviewer"),
            llm_request=request,
        )
    )

    assert request.contents == []
    assert queue.has_pending("task-1")


def test_plugin_respects_configured_safe_points_and_batch_limit(tmp_path: Path) -> None:
    queue = SteeringQueue(tmp_path / "state.db")
    events = JsonlEventStore(tmp_path / "events")
    plugin = SteeringPlugin(
        queue=queue,
        event_store=events,
        lease_seconds=60,
        batch_limit=1,
        before_model=False,
        before_tool=True,
    )
    queue.enqueue("task-1", "First", priority=2)
    queue.enqueue("task-1", "Second", priority=1)
    request = LlmRequest()

    asyncio.run(
        plugin.before_model_callback(
            callback_context=_context(),
            llm_request=request,
        )
    )
    fenced = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="edit"),
            tool_args={},
            tool_context=_context(),
        )
    )

    assert request.contents == []
    assert fenced is not None
    assert queue.list_messages("task-1", statuses=("queued",), limit=10)
