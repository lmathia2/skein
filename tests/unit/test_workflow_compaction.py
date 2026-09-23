from __future__ import annotations

import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from harness.core.context.compiler import ContextBudgetExceeded
from harness.core.models.agent_step import AgentStep
from harness.core.models.ledger import TaskLedger
from harness.core.models.task import TaskRequest
from harness.core.orchestration import build_coding_packet
from harness.evidence.state import EventKind, JsonlEventStore, SteeringQueue


def _ledger() -> TaskLedger:
    return TaskLedger.from_request(
        TaskRequest(goal="Fix parser", acceptance_criteria=["Parser tests pass"]),
        task_id="task-1",
        workspace_id="workspace",
        base_revision="abc123",
    )


def test_workflow_consumes_monotonic_tool_actions_once() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    context = type(
        "Context",
        (),
        {
            "state": {
                "tool_action_fingerprints": [
                    {"sequence": 2, "fingerprint": "second"},
                    {"sequence": 1, "fingerprint": "first"},
                ]
            }
        },
    )()

    assert workflow._consume_tool_action_fingerprints(context) == ["first", "second"]
    assert workflow._consume_tool_action_fingerprints(context) == []


def test_model_progress_prose_cannot_reset_objective_stagnation() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    step = AgentStep(status="continue", progress=["claimed progress"])
    ledger = workflow._with_workspace_observations(
        _ledger(),
        step,
        [],
        [],
    )
    assert ledger.no_progress_count == 1

    ledger = workflow._with_workspace_observations(
        ledger,
        step,
        [],
        ["read-result"],
    )
    assert ledger.no_progress_count == 0
    ledger = workflow._with_workspace_observations(
        ledger,
        step,
        [],
        ["read-result"],
    )
    assert ledger.no_progress_count == 1


def test_task_input_budget_reservation_fails_closed_without_overcounting() -> None:
    workflow = importlib.import_module("app.agent.workflow")
    state: dict[str, object] = {}

    assert workflow._reserve_task_input_budget(
        state,
        projected_tokens=600,
        limit=1_000,
    ) == (True, 0)
    assert state["estimated_task_input_tokens"] == 600
    assert workflow._reserve_task_input_budget(
        state,
        projected_tokens=401,
        limit=1_000,
    ) == (False, 600)
    assert state["estimated_task_input_tokens"] == 600


def test_recent_context_omits_ledger_and_checkpoint_duplicates(tmp_path) -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    task_id = "task-context-dedupe"
    store.append(task_id, EventKind.TASK_CREATED, {"ledger": {"goal": "duplicate"}})
    store.append(task_id, EventKind.LEDGER_PATCHED, {"set_fields": {"phase": "plan"}})
    store.append(task_id, EventKind.CHECKPOINT_CREATED, {"checkpoint_id": "duplicate"})
    store.append(task_id, EventKind.STEERING_RECEIVED, {"message_id": "s", "content": "retained whole elsewhere"})
    store.append(task_id, EventKind.ACTION_RECORDED, {"kind": "useful"})
    deps = SimpleNamespace(
        event_store=store,
        settings=SimpleNamespace(recent_event_limit=12),
        delta_work_packets=False,
    )

    rendered = workflow._render_recent_events(deps, task_id)

    assert len(rendered) == 1
    assert "action.recorded" in rendered[0]
    assert "useful" in rendered[0]


def test_delivered_steering_survives_acknowledgement_and_recent_event_eviction(tmp_path) -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    queue = SteeringQueue(tmp_path / "steering.db")
    deps = SimpleNamespace(event_store=store)
    assert workflow._render_received_steering(deps, "task-1") == ""
    contents = ["Keep the public API unchanged.", "Prepare, then await a question.",
                "Preparation is complete. Implement the requested bounded parser fix. " + "Exact constraint. " * 80]
    for text in contents:
        message = queue.enqueue("task-1", text)
        queue.lease("task-1", "owner", limit=1, lease_seconds=60)
        store.append("task-1", EventKind.STEERING_RECEIVED, {"message_id": message.message_id, "content": text})
        queue.ack([message.message_id], "owner")
    for _ in range(20):
        store.append("task-1", EventKind.ACTION_RECORDED, {"kind": "later work"})
    store.append("another-task", EventKind.STEERING_RECEIVED, {"message_id": "foreign", "content": "do not import"})
    before = store.read("task-1")
    rendered = workflow._render_received_steering(deps, "task-1")
    assert workflow._render_received_steering(deps, "task-1") == rendered
    assert store.read("task-1") == before and not queue.has_pending("task-1")
    payload = json.loads(rendered.split("\n", 1)[1])
    assert [m["content"] for m in payload["messages"]] == contents
    assert [m["sequence"] for m in payload["messages"]] == [1, 2, 3]
    assert payload["source_clock"] == {"task_harness_event_sequence": 23}
    digest = payload.pop("content_hash")
    assert digest == hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    packet = build_coding_packet(_ledger(), steering_messages=[rendered], max_tokens=3000)
    assert rendered in packet and "do not import" not in packet
    with pytest.raises(ContextBudgetExceeded):
        build_coding_packet(_ledger(), steering_messages=[rendered], max_tokens=100)


@pytest.mark.parametrize("fault", ("foreign", "missing", "conflicting"))
def test_delivered_steering_corruption_fails_closed(tmp_path, fault) -> None:
    workflow = importlib.import_module("app.agent.workflow")
    store = JsonlEventStore(tmp_path / "events")
    event = store.append("task-1", EventKind.STEERING_RECEIVED, {"message_id": "m", "content": "first"})
    if fault == "foreign":
        deps = SimpleNamespace(event_store=SimpleNamespace(read=lambda _: [event.model_copy(update={"task_id": "foreign"})]))
    else:
        store.append("task-1", EventKind.STEERING_RECEIVED,
                     {} if fault == "missing" else {"message_id": "m", "content": "conflict"})
        deps = SimpleNamespace(event_store=store)
    with pytest.raises(ValueError):
        workflow._render_received_steering(deps, "task-1")
