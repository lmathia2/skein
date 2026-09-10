from __future__ import annotations

from pathlib import Path

from harness.core.context import estimate_tokens
from harness.core.models.agent_step import AgentStep, CompletionClaim
from harness.core.models.task import TaskRequest
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
        completion_claims=[CompletionClaim(criterion="Login works", evidence=["pytest"])],
    )
    ledger = reduce_agent_step(ledger, verify)
    assert decide_route(ledger, verify) == HarnessRoute.VERIFY

    replanned = replan_ledger(ledger)
    assert replanned.phase == "plan"
    assert replanned.no_progress_count == ledger.no_progress_count


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
        selected_skills="instructions " * 10_000,
        repository_manifest="manifest " * 10_000,
        compaction_summary="history " * 10_000,
        recent_events=["event " * 10_000],
        steering_messages=["steer " * 10_000],
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
