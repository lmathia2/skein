from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from google.adk.models.llm_request import LlmRequest

from harness.adapters.adk import SteeringPlugin
from harness.evidence.state import EventKind, JsonlEventStore, SteeringQueue


def _context(*, agent_name: str = "coding_worker") -> SimpleNamespace:
    return SimpleNamespace(
        agent_name=agent_name,
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
    assert [event.kind for event in recorded] == [EventKind.STEERING_RECEIVED]
    assert recorded[0].payload["message_id"] == queued.message_id


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
