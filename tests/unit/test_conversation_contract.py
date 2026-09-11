from __future__ import annotations

import pytest

from harness.core.models.agent_step import AgentStep, CompletionClaim
from harness.core.models.task import TaskRequest
from harness.core.orchestration.runtime import can_answer_directly, parse_task_request


def test_plain_conversation_does_not_invent_coding_acceptance_criteria() -> None:
    request = parse_task_request("hello")
    assert request.mode == "auto"
    assert request.acceptance_criteria == []
    assert TaskRequest(goal="Implement parser").mode == "coding"


@pytest.mark.parametrize("override", [
    {"mode": "coding"}, {"acceptance_criteria": ["Tests pass"]},
    {"verification_requirements": ["pytest -q"]},
    {"verification_level": "behavioral"},
])
def test_explicit_work_obligations_cannot_be_answered_away(override) -> None:
    request = TaskRequest.model_validate({"goal": "request", "mode": "auto", **override})
    assert not can_answer_directly(
        request, AgentStep(status="answer", message="claimed done"),
        verification_required=False, workspace_unchanged=True,
    )


@pytest.mark.parametrize("required,unchanged", [(True, True), (False, False)])
def test_tool_effects_and_workspace_changes_force_verification(required, unchanged) -> None:
    assert not can_answer_directly(
        parse_task_request("request"), AgentStep(status="answer", message="claimed done"),
        verification_required=required, workspace_unchanged=unchanged,
    )


def test_empty_reply_and_completion_claims_are_not_direct_answers() -> None:
    for step in [AgentStep(status="answer"), AgentStep(status="answer", message="done",
            completion_claims=[CompletionClaim(criterion_id="criterion-implemented")])]:
        assert not can_answer_directly(
            parse_task_request("request"), step,
            verification_required=False, workspace_unchanged=True,
        )


def test_answer_is_allowed_only_without_work_obligations_or_effects() -> None:
    assert can_answer_directly(
        parse_task_request("hello"), AgentStep(status="answer", message="Hello!"),
        verification_required=False, workspace_unchanged=True,
    )
