import pytest

from app.agent.workflow import (
    _admit_completion_claims,
    _bounded_review_step,
    _recover_unsupported_blocked_step,
    _reject_late_criterion_proposals,
    _work_batch_yield_update,
)
from harness.core.models.agent_step import AgentStep, CriterionProposal
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


def test_review_cell_yield_preserves_exact_resubmission_action() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"), task_id="task-1",
        workspace_id="workspace-1", base_revision="abc123")
    step = AgentStep(status="continue", next_action="Use criterion-exact scaffold")

    update = _work_batch_yield_update(
        ledger,
        step,
        {"reason": "review_cell_limit", "workspace_changed": False},
    )

    assert update == {
        "phase": "review",
        "status": "active",
        "next_action": "Use criterion-exact scaffold",
    }


def test_no_change_yield_enters_planning_without_claiming_review() -> None:
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

    assert update["phase"] == "plan"
    assert "counterexample_review_completed" not in update


def test_unsupported_block_routes_changed_work_to_verification() -> None:
    step = AgentStep(status="blocked", next_action="Finish remaining edits")
    recovered = _recover_unsupported_blocked_step(
        step, workspace_changed=True, unresolved_execution_count=0)
    assert recovered.status == "verify"


@pytest.mark.parametrize("step,unresolved", [
    (AgentStep(status="blocked", questions=["Need a credential"]), 0),
    (AgentStep(status="blocked"), 1),
])
def test_real_or_uncertain_blocks_remain_closed(step: AgentStep, unresolved: int) -> None:
    assert _recover_unsupported_blocked_step(
        step, workspace_changed=True, unresolved_execution_count=unresolved) is step


def test_late_advisory_criterion_proposals_do_not_crash_terminal_progress() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"), task_id="task-1",
        workspace_id="workspace-1", base_revision="abc123")
    proposal = CriterionProposal(text="Late detail", parent_id=ledger.criterion_rows[0].criterion_id)
    step = AgentStep(status="verify", criterion_proposals=[proposal])
    assert _reject_late_criterion_proposals(ledger, step).criterion_proposals == []


def test_invalid_completion_claims_are_rejected_without_discarding_valid_evidence() -> None:
    evidence, rejected = _admit_completion_claims(
        [
            {"criterion_id": "criterion-known", "evidence": ["validation:0"]},
            {"criterion_id": "question-invented", "evidence": ["self-check"]},
            {"criterion_id": "criterion-known", "evidence": ["duplicate"]},
        ],
        {"criterion-known"},
    )

    assert evidence == {"criterion-known": ["validation:0"]}
    assert rejected == [
        {"index": 1, "criterion_id": "question-invented", "reason": "unknown_or_stale"},
        {"index": 2, "criterion_id": "criterion-known", "reason": "duplicate"},
    ]


def test_bounded_review_scaffolds_exact_valid_claims_for_fresh_decision() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"), task_id="task-1",
        workspace_id="workspace-1", base_revision="abc123")
    criterion = ledger.criterion_rows[0].criterion_id

    step = _bounded_review_step(
        ledger,
        [{"criterion_id": criterion, "evidence": ["targeted check passed"]}],
    )

    assert step.status == "continue"
    assert criterion in (step.next_action or "")
    assert "targeted check passed" in (step.next_action or "")
    assert step.completion_claims == []


def test_bounded_review_rejects_near_match_and_emits_exact_scaffold() -> None:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Implement aspects"), task_id="task-1",
        workspace_id="workspace-1", base_revision="abc123")
    criterion = ledger.criterion_rows[0].criterion_id

    step = _bounded_review_step(
        ledger,
        [{"criterion_id": criterion[:-1], "evidence": ["targeted check passed"]}],
    )

    assert step.status == "continue"
    assert criterion in (step.next_action or "")
    assert step.completion_claims == []
