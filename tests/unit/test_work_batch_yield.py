from app.agent.workflow import _work_batch_yield_update
from harness.core.models.agent_step import AgentStep
from harness.core.models.task import TaskLedger, TaskRequest


def test_changed_max_cell_yield_stays_in_implementation() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"),
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    step = AgentStep(status="continue", next_action="Keep working")

    update = _work_batch_yield_update(
        ledger,
        step,
        {"reason": "max_cells", "workspace_changed": True},
    )

    assert update == {
        "phase": "implement",
        "status": "active",
        "next_action": "Continue implementation from the durable notebook.",
    }


def test_no_change_yield_enters_review() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"),
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    step = AgentStep(status="continue", next_action="Keep working")

    update = _work_batch_yield_update(
        ledger,
        step,
        {"reason": "no_workspace_change", "workspace_changed": False},
    )

    assert update["phase"] == "review"
    assert update["counterexample_review_completed"] is True
