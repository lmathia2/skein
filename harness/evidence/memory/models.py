from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.evidence.ledger.models import canonical_json


class ViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    program: str
    version: int = Field(default=1, ge=1)
    as_of: datetime | None = None
    query: str | None = None
    retrieval: Literal["keyword", "semantic", "hybrid"] = "keyword"
    max_bytes: int = Field(default=16_000, ge=128, le=1_000_000)
    source_tasks: tuple[str, ...] = ()
    watermark: int | None = Field(default=None, ge=0)
    recorded_before: datetime | None = None
    observed_after: datetime | None = None
    kinds: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    event_id: str | None = None
    cursor: str | None = Field(default=None, max_length=16000)
    limit: int = Field(default=20, ge=1, le=100)
    max_scan_events: int = Field(default=10000, ge=1, le=100000)
    timeout_seconds: float = Field(default=2, gt=0, le=60)
    artifact_uri: str | None = None
    offset: int = Field(default=1, ge=1)
    byte_offset: int = Field(default=0, ge=0)
    byte_limit: int | None = Field(default=None, ge=1, le=16000)

    @model_validator(mode="after")
    def aware_times(self) -> ViewRequest:
        for value in (self.as_of, self.recorded_before, self.observed_after):
            if value is not None and value.utcoffset() is None:
                raise ValueError("memory time boundaries require an explicit timezone")
        return self


class ViewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    view_id: str
    task_id: str
    program: str
    version: int
    watermark: int = Field(ge=0)
    data: dict[str, Any]
    evidence_event_ids: tuple[str, ...]
    content_hash: str = ""
    truncated: bool = False
    status: Literal["ok", "partial", "denied", "unavailable", "timeout"] = "ok"
    next_cursor: str | None = None
    program_hash: str = ""
    execution_hash: str = ""
    source_manifest: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_hash(self) -> ViewResult:
        body = {
            "task_id": self.task_id,
            "program": self.program,
            "version": self.version,
            "watermark": self.watermark,
            "data": self.data,
            "evidence_event_ids": self.evidence_event_ids,
            "truncated": self.truncated,
            "status": self.status,
            "next_cursor": self.next_cursor,
            "program_hash": self.program_hash,
            "execution_hash": self.execution_hash,
            "source_manifest": self.source_manifest,
        }
        expected = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        if self.content_hash and self.content_hash != expected:
            raise ValueError("content_hash does not match view result")
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected)
        return self
