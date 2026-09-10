from __future__ import annotations

import importlib
from types import SimpleNamespace

from harness.core.models.agent_step import AgentStep
from harness.core.models.ledger import TaskLedger
from harness.core.models.task import TaskRequest
from harness.evidence.state import EventKind, JsonlEventStore


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
    store.append(task_id, EventKind.ACTION_RECORDED, {"kind": "useful"})
    deps = SimpleNamespace(
        event_store=store,
        settings=SimpleNamespace(recent_event_limit=12),
    )

    rendered = workflow._render_recent_events(deps, task_id)

    assert len(rendered) == 1
    assert "action.recorded" in rendered[0]
    assert "useful" in rendered[0]
