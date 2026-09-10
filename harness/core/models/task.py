"""Task contract, agent-step, and durable Task Ledger models."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import StrictModel


class TaskPhase(StrEnum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"
    BLOCKED = "blocked"
    COMPLETE = "complete"


class TaskStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_INPUT = "needs_input"
    VERIFYING = "verifying"
    FAILED = "failed"
    COMPLETE = "complete"
    ANSWERED = "answered"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class TaskRequest(StrictModel):
    """Normalized contract accepted by the coding workflow."""

    goal: str = Field(min_length=1, max_length=50_000)
    mode: Literal["auto", "coding"] = "coding"
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    permitted_paths: list[str] | None = None
    forbidden_paths: list[str] = Field(default_factory=list)
    verification_requirements: list[str] = Field(default_factory=list)
    verification_level: Literal["auto", "syntax", "static", "behavioral"] = "auto"
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, ge=1_000)
    max_iterations: int = Field(default=24, ge=1, le=500)

    @model_validator(mode="after")
    def ensure_acceptance_criteria(self) -> TaskRequest:
        if not self.acceptance_criteria and self.mode == "coding":
            self.acceptance_criteria = [self.goal]
        return self


class PlanStep(StrictModel):
    step_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlanStepStatus = PlanStepStatus.PENDING
    evidence: list[str] = Field(default_factory=list)


class Decision(StrictModel):
    summary: str = Field(min_length=1)
    rationale: str = ""
    affected_paths: list[str] = Field(default_factory=list)


class ValidationResult(StrictModel):
    command: str
    exit_code: int | None = None
    passed: bool
    summary: str
    duration_ms: int = Field(default=0, ge=0)
    artifact_uri: str | None = None


class TaskLedger(StrictModel):
    """Durable projection used to keep a long task on goal."""

    task_id: str
    goal: str
    mode: Literal["auto", "coding"] = "coding"
    acceptance_criteria: list[str]
    constraints: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    permitted_paths: list[str] | None = None
    forbidden_paths: list[str] = Field(default_factory=list)
    verification_requirements: list[str] = Field(default_factory=list)
    verification_level: Literal["auto", "syntax", "static", "behavioral"] = "auto"
    max_input_tokens: int | None = Field(default=None, ge=1_000)

    base_revision: str
    workspace_id: str
    branch_id: str = "main"

    phase: TaskPhase = TaskPhase.UNDERSTAND
    plan: list[PlanStep] = Field(default_factory=list)
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)

    progress: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    files_read: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    counterexample_review_completed: bool = False

    next_action: str | None = None
    iteration: int = Field(default=0, ge=0)
    no_progress_count: int = Field(default=0, ge=0)
    recent_action_fingerprints: list[str] = Field(default_factory=list)

    status: TaskStatus = TaskStatus.ACTIVE
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    version: int = Field(default=1, ge=1)

    @classmethod
    def from_request(
        cls,
        request: TaskRequest,
        *,
        task_id: str,
        workspace_id: str,
        base_revision: str,
        branch_id: str = "main",
    ) -> TaskLedger:
        return cls(
            task_id=task_id,
            goal=request.goal,
            mode=request.mode,
            acceptance_criteria=request.acceptance_criteria,
            constraints=request.constraints,
            non_goals=request.non_goals,
            permitted_paths=request.permitted_paths,
            forbidden_paths=request.forbidden_paths,
            verification_requirements=request.verification_requirements,
            verification_level=request.verification_level,
            max_input_tokens=request.max_input_tokens,
            base_revision=base_revision,
            workspace_id=workspace_id,
            branch_id=branch_id,
            phase=TaskPhase.PLAN,
        )

    def compact_projection(self) -> dict[str, object]:
        """Return the bounded state that is appropriate for model context."""

        latest_validation = self.validations[-1].model_dump(mode="json") if self.validations else None
        active_step = next(
            (step.model_dump(mode="json") for step in self.plan if step.step_id == self.current_step_id),
            None,
        )
        return {
            "goal": self.goal,
            "mode": self.mode,
            "acceptance_criteria": self.acceptance_criteria,
            "constraints": self.constraints,
            "non_goals": self.non_goals,
            "phase": self.phase.value,
            "status": self.status.value,
            "active_step": active_step,
            "completed_step_ids": self.completed_step_ids[-20:],
            "recent_progress": self.progress[-12:],
            "blockers": self.blockers[-10:],
            "open_questions": self.open_questions[-10:],
            "files_in_focus": sorted(set(self.files_read[-12:] + self.files_modified[-12:])),
            "files_modified": sorted(set(self.files_modified)),
            "latest_validation": latest_validation,
            "next_action": self.next_action,
            "iteration": self.iteration,
            "no_progress_count": self.no_progress_count,
        }
