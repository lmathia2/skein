"""Append-only harness events and deterministic Task Ledger reduction."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from harness.core.models.ledger import TaskLedger


class EventKind(StrEnum):
    TASK_CREATED = "task.created"
    LEDGER_PATCHED = "ledger.patched"
    WORKSPACE_INITIALIZED = "workspace.initialized"
    ACTION_RECORDED = "action.recorded"
    MESSAGE_RECORDED = "message.recorded"
    TOOL_ARTIFACT_RECORDED = "tool.artifact_recorded"
    STEERING_RECEIVED = "steering.received"
    COMPACTION_CREATED = "compaction.created"
    CHECKPOINT_CREATED = "checkpoint.created"
    VERIFICATION_COMPLETED = "verification.completed"
    REVIEW_COMPLETED = "review.completed"
    TASK_BLOCKED = "task.blocked"
    TASK_FINISHED = "task.finished"
    NOTEBOOK_CREATED = "notebook.created"
    NOTEBOOK_CELL_ADDED = "notebook.cell_added"
    NOTEBOOK_CELL_EDITED = "notebook.cell_edited"
    NOTEBOOK_CELL_DELETED = "notebook.cell_deleted"
    NOTEBOOK_CELL_REORDERED = "notebook.cell_reordered"
    NOTEBOOK_MATERIALIZED = "notebook.materialized"
    NOTEBOOK_SNAPSHOTTED = "notebook.snapshotted"
    REPL_CELL_SUBMITTED = "repl.cell_submitted"
    REPL_CELL_COMPLETED = "repl.cell_completed"
    REPL_CELL_FAILED = "repl.cell_failed"
    REPL_CELL_TIMEOUT = "repl.cell_timeout"
    REPL_STATE_RESTORED = "repl.state_restored"
    WORK_BATCH_YIELDED = "work_batch.yielded"
    CAPABILITY_REQUESTED = "capability.requested"
    CAPABILITY_COMPLETED = "capability.completed"
    CAPABILITY_FAILED = "capability.failed"
    CAPABILITY_BLOCKED = "capability.blocked"


class LedgerPatch(BaseModel):
    """Validated, deterministic update applied by the reducer."""

    model_config = ConfigDict(extra="forbid")

    set_fields: dict[str, Any] = Field(default_factory=dict)
    append_fields: dict[str, list[Any]] = Field(default_factory=dict)
    remove_values: dict[str, list[Any]] = Field(default_factory=dict)


class HarnessEvent(BaseModel):
    """One immutable record in a task event stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    sequence: int = Field(ge=1)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key: str | None = None


def apply_patch(ledger: TaskLedger, patch: LedgerPatch) -> TaskLedger:
    """Apply a patch and revalidate the complete ledger."""

    data = ledger.model_dump(mode="python")
    known = set(TaskLedger.model_fields)

    for field_name, value in patch.set_fields.items():
        if field_name not in known:
            raise ValueError(f"unknown TaskLedger field: {field_name}")
        data[field_name] = value

    for field_name, values in patch.append_fields.items():
        if field_name not in known:
            raise ValueError(f"unknown TaskLedger field: {field_name}")
        current = data.get(field_name)
        if not isinstance(current, list):
            raise TypeError(f"TaskLedger.{field_name} is not a list")
        current.extend(values)

    for field_name, values in patch.remove_values.items():
        if field_name not in known:
            raise ValueError(f"unknown TaskLedger field: {field_name}")
        current = data.get(field_name)
        if not isinstance(current, list):
            raise TypeError(f"TaskLedger.{field_name} is not a list")
        data[field_name] = [item for item in current if item not in values]

    return TaskLedger.model_validate(data)


def reduce_event(ledger: TaskLedger | None, event: HarnessEvent) -> TaskLedger:
    """Reduce one event into current task state."""

    if event.kind == EventKind.TASK_CREATED:
        if ledger is not None:
            raise ValueError("task.created must be the first event")
        created = TaskLedger.model_validate(event.payload["ledger"])
        if created.task_id != event.task_id:
            raise ValueError("event task_id does not match ledger task_id")
        return created

    if ledger is None:
        raise ValueError("event stream must begin with task.created")
    if ledger.task_id != event.task_id:
        raise ValueError("event task_id does not match ledger task_id")

    if event.kind == EventKind.LEDGER_PATCHED:
        return apply_patch(ledger, LedgerPatch.model_validate(event.payload))
    if event.kind == EventKind.TASK_BLOCKED:
        return apply_patch(
            ledger,
            LedgerPatch(
                set_fields={"status": "needs_input", "phase": "blocked"},
                append_fields={"blockers": [event.payload["reason"]]},
            ),
        )
    if event.kind == EventKind.TASK_FINISHED:
        return apply_patch(
            ledger,
            LedgerPatch(set_fields={"status": "complete", "phase": "complete"}),
        )

    # Observational events remain in the audit stream. State-changing callers emit
    # an explicit ledger.patched event so replay never depends on implicit behavior.
    return ledger


def rebuild_ledger(events: Iterable[HarnessEvent]) -> TaskLedger:
    """Replay a task stream, rejecting gaps and conflicting duplicates."""

    ordered = sorted(events, key=lambda item: item.sequence)
    if not ordered:
        raise ValueError("cannot rebuild a ledger from an empty event stream")

    seen_ids: set[str] = set()
    expected_sequence = 1
    ledger: TaskLedger | None = None
    for event in ordered:
        if event.event_id in seen_ids:
            continue
        if event.sequence != expected_sequence:
            raise ValueError(
                f"event sequence gap: expected {expected_sequence}, got {event.sequence}"
            )
        seen_ids.add(event.event_id)
        ledger = reduce_event(ledger, event)
        expected_sequence += 1

    assert ledger is not None
    return ledger
