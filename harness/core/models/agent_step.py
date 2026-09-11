"""Structured output emitted by one bounded coding work batch."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CriterionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)
    parent_id: str
    probe: Literal["general", "positive", "negative", "precondition_unmet"] = "general"


class CompletionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
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
    criterion_proposals: list[CriterionProposal] = Field(default_factory=list, max_length=16)


class StructuredCriterionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    parent_id: str
    probe: Literal["general", "positive", "negative", "precondition_unmet"]


class StructuredCompletionClaim(BaseModel):
    """Strict-provider form: every property is required by OpenAI JSON schema."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
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
    criterion_proposals: list[StructuredCriterionProposal]

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
            "criterion_proposals",
        ):
            completed.setdefault(field, [])
        return completed
