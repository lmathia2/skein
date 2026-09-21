from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.workflow import (
    _latest_kernel_epoch,
    _render_recent_events,
    _verification_transition,
)
from harness.core.context import estimate_tokens
from harness.core.context.compiler import ContextBudgetExceeded
from harness.core.models.agent_step import AgentStep, CompletionClaim, CriterionProposal
from harness.core.models.task import (
    TaskPhase,
    TaskRequest,
    TaskStatus,
    ValidationResult,
    criterion_id,
)
from harness.core.orchestration import (
    HarnessRoute,
    build_thin_packet,
    build_work_packet,
    build_work_packet_update,
    create_initial_ledger,
    decide_route,
    parse_agent_step,
    parse_task_request,
    reduce_agent_step,
    replan_ledger,
    resume_for_steering,
)
from harness.core.orchestration.core import work_packet_sections
from harness.evidence.state import EventKind, JsonlEventStore
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


def test_thin_packet_is_markdown_without_trace_envelopes() -> None:
    ledger = _ledger().model_copy(update={
        "validations": [ValidationResult(
            command="pytest -q", passed=False, exit_code=1,
            summary="one targeted test failed",
        )]
    })
    packet = build_thin_packet(
        ledger, selected_skills="Use the repository skill.",
        conversation="User clarified the login must preserve sessions.",
        compaction_summary="The service boundary was identified.",
    )

    assert "## Goal\n\nFix login" in packet
    assert "## Acceptance criteria\n\n- Login works" in packet
    assert "## Selected skills\n\nUse the repository skill." in packet
    assert '"packet_version"' not in packet
    assert '"command"' not in packet
    assert "**FAILED** — `pytest -q`" in packet


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


@pytest.mark.asyncio
async def test_thin_verification_failure_reenters_repair_then_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = JsonlEventStore(tmp_path / "events")
    reports = [
        {"passed": False, "recommended_next_action": "Fix the targeted failure", "tests_failed": 1},
        {"passed": False, "recommended_next_action": "Fix the targeted failure", "tests_failed": 1},
        {"passed": True, "tests_passed": 1, "tests_failed": 0},
    ]

    class Context:
        @staticmethod
        async def run_node(*_args, **_kwargs):
            report = reports.pop(0)
            return {
                "report": report,
                "changed_paths": ["src/login.py"],
                "workspace_fingerprint": "workspace-v2",
            }

        @staticmethod
        def get_invocation_context():
            return SimpleNamespace(invocation_id="invocation")

    deps = SimpleNamespace(
        event_store=events,
        max_verification_attempts=6,
        steering_enabled=False,
        steering_at_work_batch_boundary=False,
        thin_loop=True,
        metrics_store=SimpleNamespace(task_summary=lambda _task_id: {}),
    )
    monkeypatch.setattr("app.agent.workflow._save_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.agent.workflow._record_outcome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.agent.workflow._record_message", lambda *_args, **_kwargs: None)
    ledger = _ledger().model_copy(
        update={"phase": TaskPhase.VERIFY, "status": TaskStatus.VERIFYING}
    )
    step = AgentStep(status="verify", message="Ready")

    failed = await _verification_transition(
        deps, Context(), SimpleNamespace(), request=TaskRequest(goal="Fix login"),
        ledger=ledger, step=step, session_id=None, compaction_id=None, started=0.0,
    )
    assert failed.result is None
    assert failed.ledger.phase == TaskPhase.IMPLEMENT
    assert failed.ledger.next_action == "Fix the targeted failure"

    repair_ledger = failed.ledger.model_copy(update={"iteration": failed.ledger.iteration + 1})
    failed_again = await _verification_transition(
        deps, Context(), SimpleNamespace(), request=TaskRequest(goal="Fix login"),
        ledger=repair_ledger, step=step, session_id=None, compaction_id=None, started=0.0,
    )
    assert failed_again.result is None
    assert failed_again.ledger.phase == TaskPhase.IMPLEMENT

    second_repair = failed_again.ledger.model_copy(
        update={"iteration": failed_again.ledger.iteration + 1}
    )
    passed = await _verification_transition(
        deps, Context(), SimpleNamespace(), request=TaskRequest(goal="Fix login"),
        ledger=second_repair, step=step, session_id=None, compaction_id=None, started=0.0,
    )
    assert passed.result is not None
    assert passed.ledger.phase == TaskPhase.COMPLETE


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


def test_work_packet_updates_append_only_changed_control_and_new_events() -> None:
    ledger = _ledger()
    first = build_work_packet(ledger, selected_skills="Stable skill", repository_manifest="Stable manifest",
                              recent_events=["10. read: source", "11. check: passed"])
    initial, snapshot, watermark = build_work_packet_update(first, None)
    assert initial == first and watermark == 11
    ledger.next_action = "Verify the completed evidence"
    ledger.phase = TaskPhase.REVIEW
    second = build_work_packet(ledger, selected_skills="Stable skill", repository_manifest="Stable manifest",
                               recent_events=["11. check: passed", "12. edit: changed"])
    delta, current, watermark = build_work_packet_update(
        second, snapshot, previous_recent_sequence=watermark)
    assert "## TASK UPDATE\n" in delta and "Verify the completed evidence" in delta
    assert "Stable skill" not in delta and "Stable manifest" not in delta
    assert "12. edit: changed" in delta and "11. check: passed" not in delta
    assert watermark == 12
    unchanged, _, _ = build_work_packet_update(second, current, previous_recent_sequence=watermark)
    assert "No change to previously supplied work packet" in unchanged
    reset, _, _ = build_work_packet_update(second, None)
    assert "## TASK\n" in reset and "Stable skill" in reset


def test_work_packet_sections_keep_skill_subheadings_in_skill_body() -> None:
    packet = "## TASK\n{}\n\n## SELECTED SKILLS\nSkill text\n\n## Local guidance\nDetails"
    assert work_packet_sections(packet)["SELECTED SKILLS"] == (
        "Skill text\n\n## Local guidance\nDetails"
    )


def test_delta_recent_events_keep_receipt_but_not_duplicate_cell_payload(tmp_path: Path) -> None:
    events = JsonlEventStore(tmp_path / "events")
    events.append("task", EventKind.NOTEBOOK_CELL_ADDED, {"source": "large source" * 200})
    events.append("task", EventKind.REPL_CELL_SUBMITTED, {"source": "large source" * 200})
    events.append("task", EventKind.REPL_CELL_COMPLETED, {
        "effect": "none", "cell_id": "cell", "state": {"delta": ["read_catalog"],
                                                       "manifest": ["large manifest" * 200]},
    })
    deps = SimpleNamespace(event_store=events, settings=SimpleNamespace(recent_event_limit=12),
                           delta_work_packets=True)
    rendered = _render_recent_events(deps, "task")
    assert len(rendered) == 1
    assert "read_catalog" in rendered[0] and "cell" in rendered[0]
    assert "large source" not in rendered[0] and "large manifest" not in rendered[0]
    assert "event_id" in rendered[0]


def test_work_packet_epoch_tracks_restoration_after_failed_cell(tmp_path: Path) -> None:
    events = JsonlEventStore(tmp_path / "events")
    assert _latest_kernel_epoch(events.read("task")) is None
    events.append("task", EventKind.REPL_CELL_COMPLETED, {"kernel_epoch": "old"})
    events.append("task", EventKind.REPL_CELL_FAILED, {"kernel_epoch": "old", "effect": "none"})
    events.append("task", EventKind.REPL_STATE_RESTORED, {
        "kernel_epoch": "restored", "recovery_timing": "after_failed_cell",
    })
    events.append("task", EventKind.READ_OBSERVED, {"path": "src/a.py"})
    assert _latest_kernel_epoch(events.read("task")) == "restored"
    events.append("task", EventKind.REPL_CELL_COMPLETED, {"kernel_epoch": "restored"})
    assert _latest_kernel_epoch(events.read("task")) == "restored"


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
    assert "Do not reread unchanged source" in ledger.next_action
    assert "specifically identified missing, stale, or contradictory fact" in ledger.next_action
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
