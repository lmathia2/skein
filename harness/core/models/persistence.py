"""Event, receipt, steering, and checkpoint contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from .base import StrictModel


class Checkpoint(StrictModel):
    checkpoint_id: str
    task_id: str
    session_id: str
    invocation_id: str
    branch_id: str
    parent_checkpoint_id: str | None = None
    workspace_id: str
    base_revision: str
    git_tree_hash: str
    ledger_version: int = Field(ge=1)
    ledger_hash: str
    schema_version: int = Field(default=1, ge=1)
    reducer_version: str = "task-ledger-v1"
    event_stream_hash: str | None = None
    receipt_stream_hash: str | None = None
    context_epoch: str | None = None
    compaction_id: str | None = None
    label: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
