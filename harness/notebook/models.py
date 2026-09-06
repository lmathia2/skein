"""Notebook projection contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from harness.models.base import StrictModel

CellStatus = Literal["started", "completed", "failed", "timeout", "effect_unknown"]
Effect = Literal["none", "observed", "changed", "unknown"]


class NotebookCell(StrictModel):
    task_id: str | None = None
    cell_type: Literal["code"] = "code"
    cell_id: str = Field(min_length=1)
    source: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cell_event_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    kernel_epoch: str = Field(min_length=1)
    ledger_start_seq: int = Field(ge=1)
    ledger_end_seq: int = Field(ge=1)
    status: CellStatus = "started"
    effect: Effect = "none"
    replay_policy: Literal["safe", "load_checkpoint", "requires_reconciliation", "never"]
    artifact_refs: list[str] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    program_version: int = Field(default=1, ge=1)
    observed_at: datetime
    completed_at: datetime | None = None


class NotebookMarkdownCell(StrictModel):
    task_id: str | None = None
    cell_type: Literal["markdown"] = "markdown"
    cell_id: str = Field(min_length=1)
    source: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_id: str = Field(min_length=1)
    event_kind: str = Field(min_length=1)
    ledger_seq: int = Field(ge=1)
    observed_at: datetime
    program_version: int = Field(default=1, ge=1)


class NotebookState(StrictModel):
    notebook_id: str = Field(min_length=1)
    cells: list[NotebookCell | NotebookMarkdownCell] = Field(default_factory=list)
    source_watermark: int = Field(ge=0)
    source_watermarks: dict[str, int] = Field(default_factory=dict)
