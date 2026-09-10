"""Structured output emitted by one bounded coding work batch."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_single_evidence_string(cls, value: object) -> object:
        """Accept a common local-model scalar while storing one canonical list."""

        if isinstance(value, str):
            return [value]
        return value


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answer", "continue", "verify", "blocked", "done"]
    message: str = Field(default="", max_length=16_000)
    progress: list[str] = Field(default_factory=list)
    next_action: str | None = None
    decisions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    discovered_constraints: list[str] = Field(default_factory=list)
    files_in_focus: list[str] = Field(default_factory=list)
    completion_claims: list[CompletionClaim] = Field(default_factory=list)


class StructuredCompletionClaim(BaseModel):
    """Strict-provider form: every property is required by OpenAI JSON schema."""

    model_config = ConfigDict(extra="forbid")

    criterion: str
    evidence: list[str]

    @model_validator(mode="before")
    @classmethod
    def fill_omitted_empty_evidence(cls, value: object) -> object:
        if isinstance(value, dict) and "evidence" not in value:
            return {**value, "evidence": []}
        return value


class StructuredAgentStep(BaseModel):
    """Provider-native terminal schema; optional values are required but nullable."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["answer", "verify", "blocked", "done"]
    message: str = Field(max_length=16_000)
    progress: list[str]
    next_action: str | None
    decisions: list[str]
    questions: list[str]
    discovered_constraints: list[str]
    files_in_focus: list[str]
    completion_claims: list[StructuredCompletionClaim]

    @model_validator(mode="before")
    @classmethod
    def fill_omitted_empty_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        completed = {**value}
        completed.setdefault("message", "")
        completed.setdefault("next_action", None)
        for field in (
            "progress",
            "decisions",
            "questions",
            "discovered_constraints",
            "files_in_focus",
            "completion_claims",
        ):
            completed.setdefault(field, [])
        return completed
