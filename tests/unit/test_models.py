from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.core.models import (
    AgentStep,
    TaskLedger,
    TaskRequest,
)
from harness.core.models.agent_step import TerminalEnvelope


def test_task_request_adds_default_acceptance_criterion() -> None:
    request = TaskRequest(goal="Fix the parser")
    assert request.acceptance_criteria == ["Fix the parser"]


def test_task_control_contract_round_trips_into_ledger() -> None:
    request = TaskRequest(
        goal="Generate a parser",
        permitted_paths=["parser.py"],
        forbidden_paths=["tests/**"],
        verification_requirements=["python held_out_verify.py"],
        verification_level="behavioral",
        max_input_tokens=12_000,
    )

    ledger = TaskLedger.from_request(
        request,
        task_id="task-controls",
        workspace_id="workspace",
        base_revision="abc",
    )

    assert ledger.permitted_paths == ["parser.py"]
    assert ledger.forbidden_paths == ["tests/**"]
    assert ledger.verification_requirements == ["python held_out_verify.py"]
    assert ledger.verification_level == "behavioral"
    assert ledger.max_input_tokens == 12_000
    assert ledger.counterexample_review_completed is False


def test_strict_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(goal="Fix it", surprise=True)  # type: ignore[call-arg]


def test_canonical_hash_is_stable_across_input_order() -> None:
    first = TaskRequest(
        goal="Fix it",
        acceptance_criteria=["Tests pass"],
        constraints=["Keep API stable"],
    )
    second = TaskRequest.model_validate(
        {
            "constraints": ["Keep API stable"],
            "acceptance_criteria": ["Tests pass"],
            "goal": "Fix it",
        }
    )
    assert first.canonical_json() == second.canonical_json()
    assert first.content_hash() == second.content_hash()


def test_ledger_projection_is_bounded_and_model_facing() -> None:
    request = TaskRequest(goal="Fix it", acceptance_criteria=["Tests pass"])
    ledger = TaskLedger.from_request(
        request,
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    ledger.progress = [f"progress-{index}" for index in range(30)]
    ledger.files_read = [f"src/file_{index}.py" for index in range(30)]
    ledger.files_modified = ["src/changed.py"]
    projection = ledger.compact_projection()

    assert projection["goal"] == "Fix it"
    assert projection["recent_progress"] == [f"progress-{index}" for index in range(18, 30)]
    assert projection["phase_hint"] == "Choose the smallest coherent change and its check."
    assert "created_at" not in projection
    files_in_focus = projection["files_in_focus"]
    assert isinstance(files_in_focus, list)
    assert "src/changed.py" in files_in_focus


def test_agent_step_round_trip() -> None:
    step = AgentStep(
        status="verify",
        progress=["Implemented parser fix"],
        decisions=["Preserve public API for compatibility"],
        files_in_focus=["src/parser.py"],
    )
    assert AgentStep.model_validate_json(step.model_dump_json()) == step


def test_optional_pi_terminal_envelope_is_minimal() -> None:
    pi_step = TerminalEnvelope.model_json_schema()
    assert set(pi_step["properties"]) == {"status", "message", "question"}
    assert set(pi_step["required"]) == set(pi_step["properties"])
    assert TerminalEnvelope.model_validate({"status": "done"}).question is None
