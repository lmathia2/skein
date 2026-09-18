"""Fail-closed admission for local process recovery; no effects are replayed here."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from harness.core.models.checkpoint import Checkpoint
from harness.evidence.ledger.models import LedgerEvent

from .events import EventKind, HarnessEvent, rebuild_ledger
from .receipts import ToolReceipt


def unresolved_execution(
    events: Sequence[HarnessEvent] | Sequence[LedgerEvent],
    receipts: Sequence[ToolReceipt] = (),
) -> list[dict[str, str]]:
    """Project unresolved intents/effects; later success is not reconciliation.

    No reconciliation event is currently admitted. In particular, a successful
    check or unchanged workspace cannot clear an unrelated unknown shell effect.
    """
    if len({event.task_id for event in events} | {receipt.task_id for receipt in receipts}) > 1:
        raise ValueError("execution evidence crosses task identity")
    if any(a.sequence >= b.sequence for a, b in pairwise(events)):
        raise ValueError("execution evidence is unordered or duplicated")
    starts = {EventKind.CAPABILITY_REQUESTED: ("capability", "operation_id"),
              EventKind.REPL_CELL_SUBMITTED: ("cell", "attempt_id"),
              "execution.validation_requested": ("validation", "operation_id")}
    ends: dict[str, tuple[str, str]] = {kind: ("capability", "operation_id") for kind in (
        EventKind.CAPABILITY_COMPLETED, EventKind.CAPABILITY_FAILED, EventKind.CAPABILITY_BLOCKED)}
    ends.update({kind: ("cell", "attempt_id") for kind in (
        EventKind.REPL_CELL_COMPLETED, EventKind.REPL_CELL_FAILED, EventKind.REPL_CELL_TIMEOUT)})
    ends["execution.validation_completed"] = ("validation", "operation_id")
    intents: dict[tuple[str, str], dict[str, Any]] = {}
    pending: dict[tuple[str, str], str] = {}
    uncertain: dict[tuple[str, str], str] = {}
    for event in events:
        payload = event.payload
        category = starts.get(event.kind) or ends.get(event.kind)
        if category is None:
            if payload.get("effect") in {"unknown", "native_untracked"}:
                uncertain[("event", event.event_id)] = "unknown"
            continue
        namespace, field = category
        identity = payload.get(field)
        if not isinstance(identity, str) or not identity:
            raise ValueError("execution evidence lacks operation identity")
        key = (namespace, identity)
        if event.kind in starts:
            if key in intents:
                raise ValueError("execution intent identity repeated")
            intents[key] = payload
            pending[key] = "started"
            continue
        prior = intents.get(key)
        if prior is None:
            raise ValueError("execution terminal lacks its intent")
        for field in ("operation", "arguments_sha256", "command_sha256", "cell_id", "attempt_id"):
            if field in prior and prior[field] != payload.get(field):
                raise ValueError("execution terminal identity mismatch")
        if namespace == "validation":
            result = payload.get("result", {})
            closed = result.get("status") == "blocked" or (
                result.get("status") in {"ok", "error"}
                and type(result.get("exit_code")) is int and result["exit_code"] >= 0)
        else:
            closed = payload.get("effect") in {"none", "observed", "changed"}
        if closed:
            pending.pop(key, None)
        else:
            uncertain[key] = "unknown"
    for receipt in receipts:
        result = json.loads(receipt.result_json) if receipt.result_json else {}
        if not isinstance(result, dict):
            raise ValueError("tool receipt result is not an object")
        safe_rejection = receipt.status == "failed" and (
            result.get("status") == "blocked" or result.get("effect") == "none")
        if ((receipt.status != "completed" and not safe_rejection)
                or result.get("reconciliation_required")
                or result.get("effect") in {"unknown", "native_untracked"}):
            uncertain[("tool", receipt.tool_call_id)] = receipt.status
    return [{"kind": kind, "id": identity, "status": status}
            for (kind, identity), status in sorted((pending | uncertain).items())]


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
    if unresolved_execution(events, receipts):
        raise ValueError("execution outcome requires reconciliation")
    expected = checkpoint.git_tree_hash
    for receipt in receipts:
        if receipt.completed_at is None or receipt.completed_at <= checkpoint.created_at.isoformat():
            continue
        if receipt.workspace_before != expected or receipt.workspace_after is None:
            raise ValueError("post-checkpoint workspace changes are not a receipted chain")
        expected = receipt.workspace_after
    if expected != workspace_fingerprint:
        raise ValueError("workspace diverged from receipted state")


__all__ = ["unresolved_execution", "validate_recovery_evidence"]
