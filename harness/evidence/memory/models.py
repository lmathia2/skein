from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.evidence.ledger.models import canonical_json


class ReadEvidence(BaseModel):
    """A historical range; its source hash does not assert current freshness."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    offset: int = Field(ge=1)
    returned_lines: int = Field(ge=0)

    def source_coverage(self, total_lines: int | None) -> dict[str, Any]:
        """Whole-file coverage at capture, independent of recovery pagination."""
        end = self.offset - 1 + self.returned_lines
        if total_lines is not None and (type(total_lines) is not int or total_lines < end):
            raise ValueError("source line count contradicts captured read range")
        return {
            "total_lines": total_lines,
            "whole_file": self.offset == 1 and end == total_lines if total_lines is not None else None,
            "next_unread_offset": (end + 1 if total_lines is not None and end < total_lines
                                   else 1 if self.offset > 1 else None),
        }


class MemoryFinding(BaseModel):
    """Public, advisory conclusions, not reasoning traces or verified task state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    kind: Literal["observation", "hypothesis", "decision", "rejected_approach", "open_question", "next_action"]
    text: str = Field(min_length=1, max_length=1000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    task_links: tuple[str, ...] = Field(default=(), max_length=16)
    related_paths: tuple[str, ...] = Field(default=(), max_length=16)
    status: Literal["active", "disputed", "superseded"] = "active"
    supersedes: tuple[str, ...] = Field(default=(), max_length=16)
    conflicts_with: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def bounded_links(self) -> MemoryFinding:
        if not self.text.strip() or len(self.text.encode()) > 2000:
            raise ValueError("finding text requires bounded nonblank public content")
        if self.kind == "observation" and not self.evidence_refs:
            raise ValueError("observations require evidence; use hypothesis for unsupported claims")
        for links in (self.evidence_refs, self.task_links, self.related_paths, self.supersedes, self.conflicts_with):
            if any(not item or len(item.encode()) > 4096 for item in links) or len(set(links)) != len(links):
                raise ValueError("finding links must be unique bounded nonempty strings")
        if self.id in (*self.supersedes, *self.conflicts_with):
            raise ValueError("finding cannot supersede or conflict with itself")
        return self


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
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    focus: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def aware_times(self) -> ViewRequest:
        if any(not item or len(item.encode()) > 4096 for item in self.focus):
            raise ValueError("focus requires bounded nonempty task links or paths")
        if self.program == "working_set" and (self.query or self.kinds or self.statuses or self.observed_after
                                               or self.cursor or self.retrieval != "keyword"):
            raise ValueError("working_set selects latest notes before ranking; use focus, not history filters")
        for value in (self.as_of, self.recorded_before, self.observed_after):
            if value is not None and value.utcoffset() is None:
                raise ValueError("memory time boundaries require an explicit timezone")
        if (self.path is not None or self.source_sha256 is not None) and self.program not in {
            "reads.lookup", "read.recover"
        }:
            raise ValueError("source filters require a read evidence program")
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
