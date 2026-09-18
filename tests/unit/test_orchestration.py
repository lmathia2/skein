from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.core.context import estimate_tokens
from harness.core.context.compiler import ContextBudgetExceeded
from harness.core.models.agent_step import AgentStep, CompletionClaim, CriterionProposal
from harness.core.models.task import TaskPhase, TaskRequest, criterion_id
from harness.core.orchestration import (
    HarnessRoute,
    build_work_packet,
    create_initial_ledger,
    decide_route,
    parse_agent_step,
    parse_task_request,
    reduce_agent_step,
    replan_ledger,
    resume_for_steering,
)
from harness.execution.tools.adk_adapter import create_adk_tools


def _ledger():
    return create_initial_ledger(
        TaskRequest(goal="Fix login", acceptance_criteria=["Login works"]),
        task_id="task",
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    )


def test_task_and_step_parsing() -> None:
    request = parse_task_request("Fix login")
    assert request.goal == "Fix login"
    step = parse_agent_step(
        '{"status":"verify","progress":["implemented"],"completion_claims":[]}'
    )
    assert step.status == "verify"


def test_reducer_and_routes() -> None:
    ledger = _ledger()
    step = AgentStep(
        status="continue",
        progress=["found service"],
        decisions=["Reuse the existing service"],
        next_action="edit it",
    )
    ledger = reduce_agent_step(ledger, step)
    assert ledger.next_action == "edit it"
    assert ledger.progress == ["found service"]
    assert [decision.summary for decision in ledger.decisions] == ["Reuse the existing service"]
    assert decide_route(ledger, step) == HarnessRoute.CONTINUE

    verify = AgentStep(
        status="done",
        completion_claims=[CompletionClaim(criterion_id="criterion-login", evidence=["pytest"])],
    )
    ledger = reduce_agent_step(ledger, verify)
    assert decide_route(ledger, verify) == HarnessRoute.VERIFY

    replanned = replan_ledger(ledger)
    assert replanned.phase == "plan"
    assert replanned.no_progress_count == ledger.no_progress_count


def test_reducer_adopts_stable_child_rows_and_complete_transition_matrix() -> None:
    ledger = create_initial_ledger(
        TaskRequest(goal="Handle trait aspects"),
        task_id="task",
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    )
    parent_id = ledger.criterion_rows[0].criterion_id
    ledger = ledger.model_copy(update={"phase": TaskPhase.REVIEW})
    proposals = [
        CriterionProposal(text="Added", parent_id=parent_id),
        CriterionProposal(text="Removed", parent_id=parent_id),
        CriterionProposal(text="onAdd fires", parent_id=parent_id, probe="positive"),
        CriterionProposal(text="onAdd does not fire", parent_id=parent_id, probe="negative"),
        CriterionProposal(
            text="onAdd stays quiet before its prerequisite",
            parent_id=parent_id,
            probe="precondition_unmet",
        ),
    ]

    updated = reduce_agent_step(ledger, AgentStep(status="continue", criterion_proposals=proposals))
    repeated = reduce_agent_step(ledger, AgentStep(status="continue", criterion_proposals=proposals))

    assert updated.acceptance_criteria[:3] == ["Handle trait aspects", "Added", "Removed"]
    assert repeated.criterion_rows == updated.criterion_rows
    assert updated.criterion_rows[1].criterion_id == criterion_id("Added", parent_id)


def test_reducer_rejects_incomplete_transition_matrix() -> None:
    ledger = create_initial_ledger(
        TaskRequest(goal="Handle transition"),
        task_id="task",
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    ).model_copy(update={"phase": TaskPhase.REVIEW})
    parent_id = ledger.criterion_rows[0].criterion_id
    with pytest.raises(ValueError, match="all three probe rows"):
        reduce_agent_step(
            ledger,
            AgentStep(
                status="continue",
                criterion_proposals=[
                    CriterionProposal(text="positive", parent_id=parent_id, probe="positive")
                ],
            ),
        )


def test_pending_steering_preempts_terminal_routes_at_a_safe_point() -> None:
    ledger = _ledger()
    verify = AgentStep(status="done", progress=["implemented"])
    verifying = reduce_agent_step(ledger, verify)

    assert (
        decide_route(verifying, verify, pending_steering=True)
        == HarnessRoute.CONTINUE
    )
    resumed = resume_for_steering(verifying)
    assert resumed.status == "active"
    assert resumed.phase == "implement"
    assert resumed.next_action == "Apply the newest user steering before continuing"


def test_progress_route_uses_configured_thresholds() -> None:
    step = AgentStep(status="continue")
    ledger = _ledger().model_copy(update={"no_progress_count": 3})

    assert (
        decide_route(
            ledger,
            step,
            replan_after_no_progress=3,
            block_after_no_progress=5,
        )
        == HarnessRoute.REPLAN
    )
    assert (
        decide_route(
            ledger,
            step,
            replan_after_no_progress=4,
            block_after_no_progress=5,
        )
        == HarnessRoute.CONTINUE
    )


def test_work_packet_is_deterministic_and_steering_is_last() -> None:
    ledger = _ledger()
    packet = build_work_packet(
        ledger,
        selected_skills="Be careful",
        repository_manifest="Python project",
        recent_events=["read auth.py"],
        steering_messages=["Do not change the API"],
    )
    assert packet.rfind("## USER STEERING") > packet.index("## RECENT EVENTS")


def test_work_packet_enforces_section_and_total_token_budgets() -> None:
    packet = build_work_packet(
        _ledger(),
        selected_skills="Required skill instruction stays whole.",
        repository_manifest="manifest " * 10_000,
        compaction_summary="Required continuation stays whole.",
        evidence_navigation="Completed evidence navigation stays whole.",
        recent_events=["event " * 10_000],
        steering_messages=["Do not change the API."],
        max_tokens=1_000,
        section_token_limits={
            "TASK": 100,
            "SELECTED SKILLS": 100,
            "REPOSITORY MANIFEST": 100,
            "COMPACTED HISTORY": 100,
            "RECENT EVENTS": 100,
            "USER STEERING": 100,
        },
    )

    assert estimate_tokens(packet) <= 1_000
    assert "truncated to configured budget" in packet
    assert "Required skill instruction stays whole." in packet
    assert "Required continuation stays whole." in packet
    assert "Completed evidence navigation stays whole." in packet
    assert packet.endswith("Do not change the API.")


def test_task_control_is_whole_when_optional_progress_and_section_target_do_not_fit():
    from app.agent.workflow import _criterion_review_action
    ledger = create_initial_ledger(TaskRequest(
        goal="Keep this complete requirement. " * 25,
        constraints=["No network"], permitted_paths=["answer.json"], forbidden_paths=["oracle/**"],
        verification_requirements=["independent-check"], verification_level="behavioral"),
        task_id="task", base_revision="abc", workspace_id="workspace", branch_id="main")
    ledger.phase = TaskPhase.REVIEW
    ledger.next_action = _criterion_review_action(ledger, AgentStep(status="verify"))
    ledger.progress = ["large old progress " * 10_000]
    original = ledger.model_dump_json()
    packet = build_work_packet(ledger, max_tokens=2000, section_token_limits={"TASK": 600})
    task = json.loads(packet.removeprefix("## TASK\n"))
    assert task["packet_version"] == "work_packet@3"
    assert task["next_action"] == ledger.next_action
    assert task["goal"] == ledger.goal and task["acceptance_criteria"] == ledger.acceptance_criteria
    assert task["criterion_rows"] == [r.model_dump(mode="json") for r in ledger.criterion_rows]
    for field in ("constraints", "permitted_paths", "forbidden_paths", "verification_requirements", "verification_level"):
        assert task[field] == getattr(ledger, field)
    assert "recent_progress" in task["omitted_fields"] and "recent_progress" not in task
    assert "section truncated" not in packet and estimate_tokens(packet) <= 2000
    assert build_work_packet(ledger, max_tokens=2000, section_token_limits={"TASK": 600}) == packet
    assert ledger.model_dump_json() == original


@pytest.mark.parametrize("oversized", ("task", "selected_skills", "compaction_summary", "evidence_navigation", "steering_messages"))
def test_required_packet_overflow_never_returns_partial_instructions(oversized):
    ledger = _ledger()
    kwargs = {}
    if oversized == "task":
        ledger.next_action = "required instruction " * 1000
    else:
        kwargs[oversized] = ["required steering " * 1000] if oversized == "steering_messages" else "required context " * 1000
    with pytest.raises(ContextBudgetExceeded) as caught:
        build_work_packet(ledger, max_tokens=1000, **kwargs)
    assert caught.value.required_tokens > caught.value.budget_tokens == 1000


def test_adk_tool_adapter_exposes_four_working_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    tools = create_adk_tools(tmp_path)
    tools.write("hello.py", "print('hello')\n", expected_absent=True)
    assert "hello" in tools.read("hello.py")["model_text"]
    tools.edit("hello.py", "hello", "world")
    assert "world" in tools.read("hello.py")["model_text"]
    assert tools.bash("python hello.py")["exit_code"] == 0
