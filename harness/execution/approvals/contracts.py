"""Typed contracts shared by human approval transports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(UTC).isoformat()


class ApprovalSubmission(BaseModel):
    """One exact operation submitted for human authorization."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    risk: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_at: str | None = None

    _normalize_expiry = field_validator("expires_at")(_normalized_timestamp)


class ApprovalRequest(ApprovalSubmission):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    requested_at: str = Field(default_factory=utc_timestamp)
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None

    _normalize_requested_at = field_validator("requested_at")(
        _normalized_timestamp
    )
    _normalize_decided_at = field_validator("decided_at")(_normalized_timestamp)


class ApprovalDecision(BaseModel):
    """Idempotent terminal decision for one durable approval request."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    decision: Literal["approved", "denied"]
    actor: str = Field(min_length=1)
    note: str | None = None


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalSubmission",
    "utc_timestamp",
]
