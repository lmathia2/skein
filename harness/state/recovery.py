"""Fail-closed admission for local process recovery; no effects are replayed here."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from harness.models.checkpoint import Checkpoint

from .events import EventKind, HarnessEvent, rebuild_ledger
from .receipts import ToolReceipt


def validate_recovery_evidence(
    checkpoint: Checkpoint,
    events: Sequence[HarnessEvent],
    receipts: Sequence[ToolReceipt],
    *,
    invocation_id: str,
    session_id: str,
    workspace_fingerprint: str,
) -> None:
    """Reject incomplete/corrupt checkpoints and every unreconciled later effect.

    Own writes after a checkpoint are accepted only as a continuous chain of
    pre/post workspace fingerprints. A receipt is not arbitrary-shell exactly-once.
    """
    if checkpoint.invocation_id != invocation_id or checkpoint.session_id != session_id:
        raise ValueError("checkpoint invocation/session identity does not match")
    if checkpoint.schema_version != 1 or checkpoint.reducer_version != "task-ledger-v1":
        raise ValueError("checkpoint schema/reducer version is incompatible")
    if checkpoint.event_stream_hash is None:
        raise ValueError("legacy checkpoint lacks publication integrity")
    if checkpoint.receipt_stream_hash is not None:
        prior_receipts = [receipt for receipt in receipts if receipt.started_at <= checkpoint.created_at.isoformat()
                          and (receipt.completed_at is None or receipt.completed_at <= checkpoint.created_at.isoformat())]
        receipt_hash = hashlib.sha256("\n".join(receipt.model_dump_json() for receipt in prior_receipts).encode()).hexdigest()
        if receipt_hash != checkpoint.receipt_stream_hash:
            raise ValueError("checkpoint receipt stream is missing or corrupt")
    if [event.sequence for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("task event stream is incomplete")
    prefix = list(events[:checkpoint.ledger_version])
    digest = hashlib.sha256("\n".join(event.model_dump_json() for event in prefix).encode()).hexdigest()
    if len(prefix) != checkpoint.ledger_version or digest != checkpoint.event_stream_hash:
        raise ValueError("checkpoint references missing or corrupt events")
    if hashlib.sha256(rebuild_ledger(prefix).model_dump_json().encode()).hexdigest() != checkpoint.ledger_hash:
        raise ValueError("checkpoint task-state hash does not match reducer")
    if not any(event.kind == EventKind.CHECKPOINT_CREATED and
               event.payload.get("checkpoint_id") == checkpoint.checkpoint_id for event in events):
        raise ValueError("checkpoint publication marker is missing")
    # Reconstruct the full stream, not only the checkpoint prefix.
    ledger = rebuild_ledger(events)
    if ledger.status in {"needs_input", "complete", "answered"}:
        raise ValueError("task is blocked or terminal; explicit continuation required")
    open_capabilities: set[str] = set()
    open_validations: set[str] = set()
    for event in events:
        operation = str(event.payload.get("operation_id", ""))
        if event.kind == EventKind.CAPABILITY_REQUESTED:
            open_capabilities.add(operation)
        elif event.kind in {EventKind.CAPABILITY_COMPLETED, EventKind.CAPABILITY_BLOCKED}:
            open_capabilities.discard(operation)
        elif event.kind == "execution.validation_requested":
            open_validations.add(operation)
        elif event.kind == "execution.validation_completed":
            open_validations.discard(operation)
    if open_capabilities:
        raise ValueError("notebook capability outcome requires reconciliation")
    if open_validations:
        raise ValueError("verification command outcome requires reconciliation")
    expected = checkpoint.git_tree_hash
    for receipt in receipts:
        if receipt.status != "completed":
            raise ValueError("tool outcome requires reconciliation")
        if receipt.completed_at is None or receipt.completed_at <= checkpoint.created_at.isoformat():
            continue
        if receipt.workspace_before != expected or receipt.workspace_after is None:
            raise ValueError("post-checkpoint workspace changes are not a receipted chain")
        expected = receipt.workspace_after
    if expected != workspace_fingerprint:
        raise ValueError("workspace diverged from receipted state")


__all__ = ["validate_recovery_evidence"]
