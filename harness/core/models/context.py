"""Deterministic context-packet and compaction models."""

from __future__ import annotations

from pydantic import Field

from .base import StrictModel


class CompactionSnapshot(StrictModel):
    summary_markdown: str
    previous_summary_hash: str | None = None
    first_retained_event_id: str | None = None
    last_summarized_event_id: str | None = None
    tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    files_read: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    artifact_uris: list[str] = Field(default_factory=list)
