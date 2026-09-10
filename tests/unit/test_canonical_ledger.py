from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.adapters.adk.runtime import AgUiEvent, AgUiEventType
from harness.adapters.adk.runtime.registry import SqliteRunEventStore
from harness.evidence.ledger import DuckDbLedgerStore, JsonlLedgerStore
from harness.evidence.ledger.importers import (
    import_approval,
    import_harness_event,
    import_public_event,
    import_run,
    import_steering,
)
from harness.evidence.ledger.shadow import LedgerBackedEventStore
from harness.evidence.state import JsonlEventStore, SteeringQueue
from harness.evidence.state.events import HarnessEvent
from harness.execution.approvals import ApprovalStore


def test_append_is_idempotent_gap_free_and_rejects_conflicts(tmp_path: Path) -> None:
    store = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    first = store.append(
        task_id="task",
        source="test",
        source_id="one",
        kind="operation.started",
        status="started",
        effect="intended",
        payload={"b": 2, "a": 1},
        idempotency_key="one",
    )
    assert store.append(
        task_id="task",
        source="test",
        source_id="one",
        kind="operation.started",
        status="started",
        effect="intended",
        payload={"a": 1, "b": 2},
        idempotency_key="one",
    ) == first
    with pytest.raises(ValueError, match="different content"):
        store.append(
            task_id="task",
            source="test",
            source_id="one",
            kind="operation.completed",
            idempotency_key="one",
        )
    second = store.append(
        task_id="task", source="test", source_id="two", kind="operation.open"
    )
    assert [event.sequence for event in store.read("task")] == [1, 2]
    assert second.status == "observed"


def test_jsonl_and_duckdb_emit_byte_equal_canonical_events(tmp_path: Path) -> None:
    observed = datetime(2026, 1, 2, tzinfo=UTC)
    recorded = observed + timedelta(seconds=1)
    stores = [
        JsonlLedgerStore(tmp_path / "ledger.jsonl"),
        DuckDbLedgerStore(tmp_path / "ledger.duckdb"),
    ]
    for store in stores:
        store.append(
            task_id="task",
            source="test",
            source_id="one",
            kind="operation.timeout",
            status="timeout",
            effect="unknown",
            payload={"missing": "completion"},
            observed_at=observed,
            recorded_at=recorded,
        )

    assert stores[0].read("task") == stores[1].read("task")
    assert stores[0].content_hash("task") == stores[1].content_hash("task")
    assert stores[0].source_counts() == stores[1].source_counts() == {"test": 1}
    assert stores[0].erase_task("task") == 1
    assert stores[0].read("task") == []


def test_import_is_deterministic_and_as_of_uses_observed_time(tmp_path: Path) -> None:
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    event = HarnessEvent(
        event_id="event-1",
        task_id="task",
        sequence=1,
        kind="task.created",
        payload={"value": 1},
        timestamp=timestamp,
    )
    left = DuckDbLedgerStore(tmp_path / "left.duckdb")
    right = DuckDbLedgerStore(tmp_path / "right.duckdb")
    assert import_harness_event(left, event).event_id == import_harness_event(right, event).event_id
    assert left.content_hash("task") == right.content_hash("task")
    assert left.read("task", as_of=timestamp - timedelta(microseconds=1)) == []
    assert len(left.read("task", as_of=timestamp)) == 1


def test_open_attempt_never_projects_as_success(tmp_path: Path) -> None:
    store = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    store.append(
        task_id="task",
        source="broker",
        source_id="call-1",
        kind="capability.requested",
        status="requested",
        effect="intended",
    )
    events = store.read("task")
    assert events[-1].status == "requested"
    assert events[-1].effect == "intended"


def test_compatibility_store_shadow_appends_without_changing_reducer_sequence(
    tmp_path: Path,
) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    store = LedgerBackedEventStore(JsonlEventStore(tmp_path / "events"), ledger)
    first = store.append("task", "one", {"value": 1}, idempotency_key="one")
    second = store.append("task", "two", idempotency_key="two")
    assert [event.sequence for event in store.read("task")] == [1, 2]
    assert [event.source_id for event in ledger.read("task")] == [
        first.event_id,
        second.event_id,
    ]


def test_compatibility_reader_repairs_and_serves_byte_equal_ledger_events(
    tmp_path: Path,
) -> None:
    operational = JsonlEventStore(tmp_path / "events")
    expected = [
        operational.append("task", "one", {"value": 1}),
        operational.append("task", "two", {"value": 2}, idempotency_key="two"),
    ]
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    ledger.append(
        task_id="task",
        source="metric",
        source_id="unrelated",
        kind="metric.model",
    )
    store = LedgerBackedEventStore(operational, ledger)

    with patch.object(operational, "read", wraps=operational.read) as read:
        actual = store.read("task")
        assert store.read("task", after_sequence=1) == actual[1:]

    assert [event.model_dump_json() for event in actual] == [
        event.model_dump_json() for event in expected
    ]
    assert read.call_count == 1


def test_source_namespaces_prevent_cross_store_idempotency_collisions(tmp_path: Path) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    event = HarnessEvent(
        event_id="event",
        task_id="task",
        sequence=1,
        kind="checkpoint.created",
        payload={"source": "event"},
        idempotency_key="checkpoint:same",
    )
    import_harness_event(ledger, event)
    ledger.append(
        task_id="task",
        source="checkpoint",
        source_id="same",
        kind="checkpoint.created",
        payload={"source": "projection"},
        idempotency_key="checkpoint:same",
    )
    assert len(ledger.read("task")) == 2


@pytest.mark.parametrize(
    ("kind", "payload", "status", "effect"),
    [
        ("repl.cell_completed", {"effect": "changed"}, "completed", "applied"),
        ("repl.cell_timeout", {"effect": "unknown"}, "timeout", "unknown"),
        ("capability.requested", {}, "started", "intended"),
        ("capability.blocked", {"effect": "none"}, "blocked", "none"),
    ],
)
def test_harness_ptc_lifecycle_imports_canonical_semantics(
    tmp_path: Path, kind: str, payload: dict, status: str, effect: str
) -> None:
    ledger = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    imported = import_harness_event(
        ledger,
        HarnessEvent(task_id="task", sequence=1, kind=kind, payload=payload),
    )

    assert imported.status == status
    assert imported.effect == effect


def test_approval_and_steering_transitions_are_replayable_evidence(tmp_path: Path) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    approvals = ApprovalStore(
        tmp_path / "approvals.db",
        on_change=lambda item: import_approval(ledger, item),
    )
    request = approvals.request(
        task_id="task",
        fingerprint="fingerprint",
        operation="shell",
        risk="external write",
        reason="test",
    )
    approvals.decide(request.request_id, decision="approved", actor="operator")
    steering = SteeringQueue(
        tmp_path / "state.db",
        on_change=lambda item: import_steering(ledger, item),
    )
    message = steering.enqueue("task", "continue", idempotency_key="steer")
    steering.lease("task", "worker")
    steering.ack([message.message_id], "worker")

    assert [event.kind for event in ledger.read("task")] == [
        "approval.pending",
        "approval.approved",
        "steering.queued",
        "steering.leased",
        "steering.acked",
    ]


def test_run_and_public_event_projections_are_captured(tmp_path: Path) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    runs = SqliteRunEventStore(
        tmp_path / "runs.db",
        run_sink=lambda item: import_run(ledger, item),
        event_sink=lambda item: import_public_event(ledger, item),
    )
    run, _ = runs.create_run(
        request_id="request",
        idempotency_key="start",
        thread_id="thread",
        user_id="user",
        input="hello",
    )
    runs.update_status(run.run_id, "running")
    runs.append_event(
        run.run_id,
        AgUiEvent(
            type=AgUiEventType.CUSTOM,
            run_id=run.run_id,
            name="coding.test",
            value={"ok": True},
        ),
        source_key="event",
    )
    assert [event.kind for event in ledger.read(run.run_id)] == [
        "run.queued",
        "run.running",
        "public.CUSTOM",
    ]
