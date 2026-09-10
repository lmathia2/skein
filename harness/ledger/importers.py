"""Deterministic adapters from existing durable records."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from harness.approvals import ApprovalRequest
from harness.models.checkpoint import Checkpoint
from harness.state.events import HarnessEvent
from harness.state.receipts import ToolReceipt
from harness.state.steering import SteeringMessage
from harness.telemetry.metrics import ModelUsageSample, TaskOutcomeSample, ToolUsageSample
from harness.tracing.store import TraceSpan

from .base import LedgerStore
from .models import EffectStatus, EventStatus, LedgerEvent, canonical_json


def _time(value: str | float | int) -> datetime:
    if isinstance(value, (float, int)) or value.replace(".", "", 1).isdigit():
        return datetime.fromtimestamp(float(value), tz=UTC)
    return datetime.fromisoformat(value)


def import_harness_event(store: LedgerStore, event: HarnessEvent) -> LedgerEvent:
    statuses: dict[str, EventStatus] = {
        "repl.cell_submitted": "started",
        "repl.cell_completed": "completed",
        "repl.cell_failed": "failed",
        "repl.cell_timeout": "timeout",
        "capability.requested": "started",
        "capability.completed": "completed",
        "capability.failed": "failed",
        "capability.blocked": "blocked",
    }
    status = statuses.get(event.kind, "observed")
    effects: dict[str, EffectStatus] = {
        "changed": "applied", "unknown": "unknown", "native_untracked": "unknown"
    }
    effect = effects.get(str(event.payload.get("effect", "")), "none")
    if event.kind == "capability.requested":
        effect = "intended"
    return store.append(
        task_id=event.task_id,
        source="harness_event",
        source_id=event.event_id,
        kind=event.kind,
        status=status,
        effect=effect,
        payload=event.payload,
        observed_at=event.timestamp,
        idempotency_key=(
            f"harness:{event.idempotency_key}"
            if event.idempotency_key is not None
            else f"harness:{event.event_id}"
        ),
        recorded_at=event.timestamp,
    )


def import_trace_span(store: LedgerStore, span: TraceSpan) -> LedgerEvent:
    phases: dict[str, EventStatus] = {
        "requested": "requested",
        "started": "started",
        "completed": "completed",
        "result": "completed",
        "failed": "failed",
        "blocked": "blocked",
        "timeout": "timeout",
        "open": "open",
    }
    status = phases.get(span.phase, "observed")
    return store.append(
        task_id=span.task_id,
        source="trace_span",
        source_id=span.span_id,
        kind=f"trace.{span.category}.{span.name}",
        status=status,
        observed_at=_time(span.timestamp),
        correlation_id=span.correlation_id,
        parent_event_id=span.parent_span_id,
        payload={"content_hash": span.content_hash, "data": span.payload(), "omitted_bytes": span.omitted_bytes},
        idempotency_key=f"trace:{span.idempotency_key}",
        recorded_at=_time(span.timestamp),
    )


def import_tool_receipt(store: LedgerStore, receipt: ToolReceipt) -> LedgerEvent:
    effect = "applied" if receipt.status == "completed" and receipt.side_effect_key else "intended" if receipt.side_effect_key else "none"
    return store.append(
        task_id=receipt.task_id,
        source="tool_receipt",
        source_id=f"{receipt.tool_call_id}:{receipt.status}",
        kind=f"tool.{receipt.tool_name}",
        status=receipt.status,
        effect=effect,
        observed_at=_time(receipt.completed_at or receipt.started_at),
        correlation_id=receipt.invocation_id,
        payload=receipt.model_dump(mode="json"),
        idempotency_key=f"receipt:{receipt.tool_call_id}:{receipt.status}",
        recorded_at=_time(receipt.completed_at or receipt.started_at),
    )


def import_checkpoint(store: LedgerStore, checkpoint: Checkpoint) -> LedgerEvent:
    return store.append(
        task_id=checkpoint.task_id,
        source="checkpoint",
        source_id=checkpoint.checkpoint_id,
        kind="checkpoint.created",
        status="completed",
        observed_at=checkpoint.created_at,
        payload=checkpoint.model_dump(mode="json"),
        idempotency_key=f"checkpoint:{checkpoint.checkpoint_id}",
        recorded_at=checkpoint.created_at,
    )


def import_approval(store: LedgerStore, approval: ApprovalRequest) -> LedgerEvent:
    statuses: dict[str, EventStatus] = {
        "pending": "requested",
        "approved": "completed",
        "denied": "blocked",
        "expired": "timeout",
    }
    timestamp = _time(approval.decided_at or approval.requested_at)
    return store.append(
        task_id=approval.task_id,
        source="approval",
        source_id=f"{approval.request_id}:{approval.status}",
        kind=f"approval.{approval.status}",
        status=statuses[approval.status],
        observed_at=timestamp,
        payload=approval.model_dump(mode="json"),
        idempotency_key=f"approval:{approval.request_id}:{approval.status}",
        recorded_at=timestamp,
    )


def import_steering(store: LedgerStore, message: SteeringMessage) -> LedgerEvent:
    statuses: dict[str, EventStatus] = {
        "queued": "requested",
        "leased": "started",
        "acked": "completed",
    }
    timestamp = _time(message.created_at)
    return store.append(
        task_id=message.task_id,
        source="steering",
        source_id=f"{message.message_id}:{message.status}",
        kind=f"steering.{message.status}",
        status=statuses[message.status],
        observed_at=timestamp,
        payload=message.model_dump(mode="json"),
        idempotency_key=f"steering:{message.message_id}:{message.status}",
        recorded_at=timestamp,
    )


def import_metric(
    store: LedgerStore,
    sample: ModelUsageSample | ToolUsageSample | TaskOutcomeSample,
) -> LedgerEvent:
    if isinstance(sample, ModelUsageSample):
        source_id, kind, timestamp = sample.sample_id, "metric.model", sample.created_at
    elif isinstance(sample, ToolUsageSample):
        source_id, kind, timestamp = sample.sample_id, "metric.tool", sample.created_at
    else:
        source_id = f"{sample.task_id}:{sample.status}:{sample.completed_at}"
        kind, timestamp = "metric.outcome", sample.completed_at
    observed_at = _time(timestamp)
    return store.append(
        task_id=sample.task_id,
        source="metric",
        source_id=source_id,
        kind=kind,
        status="completed",
        observed_at=observed_at,
        payload=sample.model_dump(mode="json"),
        idempotency_key=f"metric:{source_id}",
        recorded_at=observed_at,
    )


def import_run(store: LedgerStore, run: Any) -> LedgerEvent:
    statuses: dict[str, EventStatus] = {
        "queued": "requested",
        "running": "started",
        "completed": "completed",
        "cancelled": "blocked",
        "failed": "failed",
    }
    timestamp = _time(run.updated_at)
    return store.append(
        task_id=run.run_id,
        source="run_registry",
        source_id=f"{run.run_id}:{run.status}",
        kind=f"run.{run.status}",
        status=statuses[run.status],
        observed_at=timestamp,
        correlation_id=run.invocation_id,
        payload=run.model_dump(mode="json"),
        idempotency_key=f"run:{run.run_id}:{run.status}",
        recorded_at=timestamp,
    )


def import_public_event(
    store: LedgerStore,
    envelope: Any,
    *,
    recorded_at: str | float | int | datetime | None = None,
) -> LedgerEvent:
    timestamp = (
        _time(recorded_at) if isinstance(recorded_at, (str, float, int)) else recorded_at
    ) or datetime.now().astimezone()
    return store.append(
        task_id=envelope.run_id,
        source="public_event",
        source_id=f"{envelope.run_id}:{envelope.sequence}",
        kind=f"public.{envelope.event.type}",
        observed_at=timestamp,
        correlation_id=envelope.invocation_id,
        payload=envelope.model_dump(mode="json"),
        idempotency_key=f"public:{envelope.run_id}:{envelope.sequence}",
        recorded_at=timestamp,
    )


def import_session_record(
    store: LedgerStore,
    session_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    recorded_at: str | float | int | datetime | None = None,
) -> LedgerEvent:
    event_payload = payload.get("event")
    event_id = event_payload.get("id") if isinstance(event_payload, dict) else None
    source_id = str(event_id or hashlib.sha256(canonical_json(payload).encode()).hexdigest())
    timestamp = (
        _time(recorded_at) if isinstance(recorded_at, (str, float, int)) else recorded_at
    ) or datetime.now().astimezone()
    return store.append(
        task_id=session_id,
        source="adk_session",
        source_id=f"{kind}:{source_id}",
        kind=f"adk.{kind}",
        status="completed" if kind != "session.deleted" else "blocked",
        observed_at=timestamp,
        payload=payload,
        idempotency_key=f"adk-session:{session_id}:{kind}:{source_id}",
        recorded_at=timestamp,
    )
