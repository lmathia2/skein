"""Verification evidence contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import StrictModel
from .task import ValidationResult


class EvidenceReference(StrictModel):
    """A deterministic reference to evidence produced by the environment."""

    kind: Literal["command_result", "artifact"]
    reference: str
    command_sha256: str
    validation_index: int = Field(ge=0)
    category: str
    strength: Literal["syntax", "static", "behavioral"]
    artifact_uri: str | None = None


class CriterionEvidence(StrictModel):
    criterion: str
    satisfied: bool
    claimed_evidence: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    notes: str | None = None


class VerificationReport(StrictModel):
    passed: bool
    criteria: list[CriterionEvidence] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    tests_passed: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)
    scope_violations: list[str] = Field(default_factory=list)
    unresolved_diagnostics: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    new_failures: list[str] = Field(default_factory=list)
    fixed_failures: list[str] = Field(default_factory=list)
    baseline_relative_commands: list[str] = Field(default_factory=list)
    required_strength: Literal["syntax", "static", "behavioral"] = "static"
    achieved_strength: Literal["none", "syntax", "static", "behavioral"] = "none"
    recommended_next_action: str | None = None
