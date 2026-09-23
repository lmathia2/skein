"""Single host-facing terminal contract for every harness runner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .verification import VerificationReport

OutcomeStatus = Literal["complete", "answered", "blocked", "failed", "cancelled"]
_OUTCOME_STATUS = TypeAdapter(OutcomeStatus)


def parse_outcome_status(value: object) -> OutcomeStatus:
    return _OUTCOME_STATUS.validate_python(value)


class HarnessOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: OutcomeStatus
    message: str = ""
    task_id: str | None = None
    changed_paths: tuple[str, ...] = ()
    verification: VerificationReport | None = None
    questions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_terminal(self) -> HarnessOutcome:
        if self.status == "complete" and (
            self.verification is None or not self.verification.passed
        ):
            raise ValueError("complete requires passing deterministic verification")
        if self.status == "answered" and not self.message.strip():
            raise ValueError("answered requires a public message")
        if self.status == "blocked" and not (
            self.questions or self.blockers or self.reason
        ):
            raise ValueError("blocked requires a reason, question, or blocker")
        return self

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> HarnessOutcome:
        return cls.model_validate(dict(value))
