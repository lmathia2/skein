"""Small, model-readable canonical event contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventStatus = Literal[
    "observed", "requested", "started", "completed", "failed", "blocked", "timeout", "open"
]
EffectStatus = Literal["none", "intended", "applied", "not_applied", "unknown"]


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def extend_event_hash(previous: str, event_id: str, payload_hash: str) -> str:
    """Append one event identity to a deterministic stream hash."""
    return hashlib.sha256(canonical_json([previous, event_id, payload_hash]).encode()).hexdigest()


class LedgerEvent(BaseModel):
    """One immutable fact; projections are always derived from these rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    task_id: str
    sequence: int = Field(ge=1)
    source: str
    source_id: str
    kind: str
    status: EventStatus = "observed"
    effect: EffectStatus = "none"
    observed_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
    parent_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = ""
    idempotency_key: str

    @model_validator(mode="after")
    def validate_hash(self) -> LedgerEvent:
        expected = hashlib.sha256(canonical_json(self.payload).encode()).hexdigest()
        if self.payload_hash and self.payload_hash != expected:
            raise ValueError("payload_hash does not match payload")
        if not self.payload_hash:
            object.__setattr__(self, "payload_hash", expected)
        return self
