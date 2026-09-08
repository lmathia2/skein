"""Deterministic validation plan and command result contracts."""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

ValidationCategory: TypeAlias = Literal[
    "syntax",
    "format",
    "lint",
    "typecheck",
    "test",
    "build",
    "diff",
    "custom",
]
ValidationStatus: TypeAlias = Literal["ok", "error", "blocked", "timeout"]
VerificationStrength: TypeAlias = Literal["syntax", "static", "behavioral"]


_SYNTAX_ONLY_PATTERNS = (
    re.compile(r"(?:^|\s)(?:python\s+-m\s+)?py_compile(?:\s|$)"),
    re.compile(r"(?:^|\s)compileall(?:\s|$)"),
    re.compile(r"(?:^|\s)python\s+-m\s+json\.tool(?:\s|$)"),
)
_REUSABLE_COMMAND_PATTERN = re.compile(
    r"(?:^|[\s;&|])(?:python\s+-m\s+)?(?:pytest|unittest|vitest|jest)(?:\s|$)"
    r"|(?:^|[\s;&|])(?:go|cargo|npm|pnpm|yarn)\s+(?:test|check)(?:\s|$)"
    r"|(?:^|[\s;&|])(?:mvn|gradle|gradlew)(?:\s+[^;&|]+)?\s+test(?:\s|$)",
    re.IGNORECASE,
)


def is_reusable_validation_command(command: str) -> bool:
    """Return whether a successful complete shell result can serve as validation."""

    return _REUSABLE_COMMAND_PATTERN.search(command) is not None


def infer_verification_strength(
    category: ValidationCategory,
    command: str,
) -> VerificationStrength:
    """Classify what a command can prove without trusting its label alone."""

    normalized = " ".join(command.split()).lower()
    if category == "syntax" or any(
        pattern.search(normalized) for pattern in _SYNTAX_ONLY_PATTERNS
    ):
        return "syntax"
    if category in {"test", "custom"}:
        return "behavioral"
    return "static"


class ValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ValidationCategory
    command: str
    source: str
    required: bool = True
    targeted: bool = False
    minimum_test_count: int = Field(default=0, ge=0)
    strength: VerificationStrength | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)

    @property
    def effective_strength(self) -> VerificationStrength:
        inferred = infer_verification_strength(self.category, self.command)
        if self.strength is None:
            return inferred
        order = {"syntax": 0, "static": 1, "behavioral": 2}
        return min((self.strength, inferred), key=order.__getitem__)


class ValidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[ValidationCommand] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ValidationCategory
    command: str
    source: str = ""
    required: bool = True
    targeted: bool = False
    strength: VerificationStrength = "static"
    status: ValidationStatus = "ok"
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    omitted_bytes: int = Field(default=0, ge=0)
    artifact_uri: str | None = None
    approval_request_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "ok" and self.exit_code in {0, None}


__all__ = [
    "CommandResult",
    "ValidationCategory",
    "ValidationCommand",
    "ValidationPlan",
    "ValidationStatus",
    "VerificationStrength",
    "infer_verification_strength",
    "is_reusable_validation_command",
]
