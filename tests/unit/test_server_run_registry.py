from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.adapters.adk.runtime import AgUiEvent, AgUiEventType, ServerEnvelope
from harness.adapters.adk.runtime.registry import (
    DurableRunEventJournal,
    RunEventBroker,
    RunStatus,
    SqliteRunEventStore,
    SubscriberBackpressureError,
)


def _event(index: int, *, run_id: str = "run-1") -> AgUiEvent:
    return AgUiEvent(
        type=AgUiEventType.CUSTOM,
        run_id=run_id,
        name="coding.test.event",
        value={"index": index},
    )


def _envelope(sequence: int, *, run_id: str = "run-1") -> ServerEnvelope:
    return ServerEnvelope(
        sequence=sequence,
        run_id=run_id,
        session_id="session-1",
        invocation_id="invocation-1",
        durable=True,
        event=_event(sequence, run_id=run_id),
    )


def _create_run(
    store: SqliteRunEventStore,
    *,
    idempotency_key: str = "start-1",
    thread_id: str = "thread-1",
    user_id: str = "user-1",
):
    record, created = store.create_run(
        request_id=f"request-{idempotency_key}",
        idempotency_key=idempotency_key,
        thread_id=thread_id,
        user_id=user_id,
        input="Fix the parser",
    )
    assert created is True
    return record


def test_conversation_reuses_session_but_refuses_overlapping_runs(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "runs.db")
    first = _create_run(store)
    with pytest.raises(ValueError, match="active work"):
        _create_run(store, idempotency_key="second")
    store.update_status(first.run_id, "running")
    store.update_status(first.run_id, "completed")
    second = _create_run(store, idempotency_key="second")
    assert first.session_id == second.session_id
    assert first.run_id != second.run_id


def test_conversation_cannot_be_rebound_to_another_workspace(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "runs.db")
    first, _ = store.create_run(request_id="one", idempotency_key="one", thread_id="thread",
        user_id="user", input="hello", metadata={"coding.workspace_identity": "workspace-a"})
    store.update_status(first.run_id, "running")
    store.update_status(first.run_id, "completed")
    with pytest.raises(ValueError, match="different workspace"):
        store.create_run(request_id="two", idempotency_key="two", thread_id="thread",
            user_id="user", input="hello", metadata={"coding.workspace_identity": "workspace-b"})


def test_durable_events_replay_after_store_reopen_from_exclusive_cursor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "server-runs.db"
    store = SqliteRunEventStore(database)
    created = _create_run(store)
    appended = [
        store.append_event(
            created.run_id,
            _event(index, run_id=created.run_id),
            source_key=f"adk:event-{index}",
            session_id="session-1",
            invocation_id="invocation-1",
            durable=True,
        )
        for index in range(1, 4)
    ]

    reopened = SqliteRunEventStore(database)
    restored = reopened.get_run(created.run_id)
    assert restored is not None
    replay = reopened.replay(created.run_id, after_sequence=1, limit=10)

    assert created.run_id == restored.run_id
    assert created.thread_id == restored.thread_id == "thread-1"
    assert replay == tuple(appended[1:])
    assert [envelope.sequence for envelope in replay] == [2, 3]
    assert all(envelope.durable for envelope in replay)


def test_create_run_is_exactly_idempotent_and_rejects_key_reuse(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    created = _create_run(store)

    repeated, was_created = store.create_run(
        request_id="request-retry",
        idempotency_key="start-1",
        thread_id="thread-1",
        user_id="user-1",
        input="Fix the parser",
    )

    assert was_created is False
    assert repeated == created
    with pytest.raises(ValueError, match="idempotency key"):
        store.create_run(
            request_id="request-retry",
            idempotency_key="start-1",
            thread_id="thread-1",
            user_id="user-1",
            input="Change the operation",
        )


def test_append_allocates_monotonic_sequences_under_concurrency_and_deduplicates(
    tmp_path: Path,
) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)

    def append(index: int) -> ServerEnvelope:
        return store.append_event(
            record.run_id,
            _event(index, run_id=record.run_id),
            source_key=f"adk:event-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        concurrent = list(executor.map(append, range(1, 17)))

    assert sorted(envelope.sequence for envelope in concurrent) == list(range(1, 17))
    assert [event.sequence for event in store.replay(record.run_id)] == list(
        range(1, 17)
    )

    original = next(
        envelope for envelope in concurrent if envelope.event.value == {"index": 7}
    )
    duplicate = store.append_event(
        record.run_id,
        _event(7, run_id=record.run_id),
        source_key="adk:event-7",
    )

    assert duplicate == original
    assert len(store.replay(record.run_id)) == 16

    with pytest.raises(ValueError, match=r"idempotency|source_key|different"):
        store.append_event(
            record.run_id,
            _event(700, run_id=record.run_id),
            source_key="adk:event-7",
        )


def test_replay_is_bounded_and_runs_are_isolated(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    first = _create_run(store)
    second = _create_run(
        store,
        idempotency_key="start-2",
        thread_id="thread-2",
        user_id="user-2",
    )
    for index in range(1, 6):
        store.append_event(
            first.run_id,
            _event(index, run_id=first.run_id),
            source_key=f"run-1:event-{index}",
        )
    store.append_event(
        second.run_id,
        _event(99, run_id=second.run_id),
        source_key="run-2:event-1",
    )

    page = store.replay(first.run_id, after_sequence=1, limit=2)

    assert [event.sequence for event in page] == [2, 3]
    assert all(event.run_id == first.run_id for event in page)
    assert [event.sequence for event in store.replay(second.run_id)] == [1]


def test_replay_page_pins_a_high_water_mark_across_pagination(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)
    store.append_events(
        record.run_id,
        tuple(_event(index, run_id=record.run_id) for index in range(1, 5)),
        source_keys=tuple(f"adk:event-{index}" for index in range(1, 5)),
    )

    first = store.replay_page(record.run_id, limit=2)
    assert [event.sequence for event in first.events] == [1, 2]
    assert first.high_water_sequence == 4
    assert first.has_more is True

    store.append_event(
        record.run_id,
        _event(5, run_id=record.run_id),
        source_key="adk:event-5",
    )
    second = store.replay_page(
        record.run_id,
        after_sequence=2,
        limit=10,
        high_water_sequence=first.high_water_sequence,
    )

    assert [event.sequence for event in second.events] == [3, 4]
    assert second.high_water_sequence == 4
    assert second.has_more is False
    assert [event.sequence for event in store.replay(record.run_id)] == [1, 2, 3, 4, 5]

    with pytest.raises(KeyError, match="unknown run"):
        store.replay_page("missing-run")


def test_batch_append_is_atomic_and_source_key_retries_report_creation(
    tmp_path: Path,
) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)
    original = store.append_event_with_outcome(
        record.run_id,
        _event(1, run_id=record.run_id),
        source_key="adk:event-1",
    )
    retry = store.append_event_with_outcome(
        record.run_id,
        _event(1, run_id=record.run_id),
        source_key="adk:event-1",
    )

    assert original.created is True
    assert retry.created is False
    assert retry.envelope == original.envelope

    with pytest.raises(ValueError, match="different content"):
        store.append_events(
            record.run_id,
            (
                _event(2, run_id=record.run_id),
                _event(100, run_id=record.run_id),
            ),
            source_keys=("adk:event-2", "adk:event-1"),
        )

    assert [event.sequence for event in store.replay(record.run_id)] == [1]


def test_status_transitions_are_conditional_idempotent_and_terminally_immutable(
    tmp_path: Path,
) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)

    running = store.update_status(
        record.run_id, "running", expected_status="queued"
    )
    repeated = store.update_status(record.run_id, "running")
    assert repeated == running

    with pytest.raises(ValueError, match="compare-and-set conflict"):
        store.update_status(record.run_id, "failed", expected_status="queued")
    with pytest.raises(ValueError, match="cannot carry an error"):
        store.update_status(record.run_id, "completed", error="not allowed")

    completed = store.update_status(
        record.run_id, "completed", expected_status="running"
    )
    assert store.update_status(record.run_id, "completed") == completed
    with pytest.raises(ValueError, match="invalid run status transition"):
        store.update_status(record.run_id, "failed", error="late failure")
    assert store.get_run(record.run_id) == completed


def test_terminalize_commits_event_and_status_atomically_and_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)
    store.update_status(record.run_id, "running", expected_status="queued")
    event = AgUiEvent(
        type=AgUiEventType.RUN_FINISHED,
        run_id=record.run_id,
        thread_id=record.thread_id,
        result={"status": "completed"},
    )

    committed = store.terminalize(
        record.run_id,
        status="completed",
        event=event,
        source_key="server:run-finished",
        expected_status="running",
    )
    repeated = store.terminalize(
        record.run_id,
        status="completed",
        event=event,
        source_key="server:run-finished",
        expected_status="running",
    )

    assert committed.created is True
    assert repeated.created is False
    assert repeated.envelope == committed.envelope
    assert store.get_run(record.run_id).status == "completed"  # type: ignore[union-attr]
    assert store.replay(record.run_id) == (committed.envelope,)


def test_terminalize_conflict_rolls_back_event_and_status(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)
    event = AgUiEvent(
        type=AgUiEventType.RUN_ERROR,
        run_id=record.run_id,
        thread_id=record.thread_id,
        message="failed",
    )

    with pytest.raises(ValueError, match="compare-and-set conflict"):
        store.terminalize(
            record.run_id,
            status="failed",
            event=event,
            source_key="server:run-error",
            expected_status="running",
            error="failed",
        )

    current = store.get_run(record.run_id)
    assert current is not None
    assert current.status == "queued"
    assert store.replay(record.run_id) == ()


def test_concurrent_status_compare_and_set_has_exactly_one_winner(tmp_path: Path) -> None:
    store = SqliteRunEventStore(tmp_path / "server-runs.db")
    record = _create_run(store)

    def transition(status: RunStatus) -> str:
        try:
            store.update_status(
                record.run_id,
                status,
                expected_status="queued",
                error="cancelled" if status == "cancelled" else None,
            )
        except ValueError:
            return "conflict"
        return status

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(transition, ("running", "cancelled")))

    assert outcomes.count("conflict") == 1
    final = store.get_run(record.run_id)
    assert final is not None
    assert final.status in {"running", "cancelled"}


def test_initialization_migrates_legacy_schema_and_fails_active_runs_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-runs.db"
    now = datetime.now(UTC).isoformat()
    input_sha256 = hashlib.sha256(b"original prompt").hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "request-1",
                "key-1",
                "thread-1",
                "user-1",
                "session-1",
                "invocation-1",
                input_sha256,
                json.dumps({}),
                "running",
                None,
                now,
                now,
            ),
        )

    store = SqliteRunEventStore(database)
    migrated = store.get_run("legacy-run")

    assert migrated is not None
    assert migrated.input == "[legacy run input unavailable]"
    assert migrated.status == "failed"
    assert migrated.error is not None and "cannot resume" in migrated.error


def test_journal_publishes_only_new_events_after_durable_commit(tmp_path: Path) -> None:
    database = tmp_path / "server-runs.db"
    store = SqliteRunEventStore(database)
    record = _create_run(store)
    broker = RunEventBroker(queue_capacity=2)
    journal = DurableRunEventJournal(store, broker)
    subscription = broker.subscribe(record.run_id)

    committed = journal.append_event(
        record.run_id,
        _event(1, run_id=record.run_id),
        source_key="adk:event-1",
    )

    assert SqliteRunEventStore(database).replay(record.run_id) == (committed,)
    assert subscription.receive_nowait() == committed

    assert (
        journal.append_event(
            record.run_id,
            _event(1, run_id=record.run_id),
            source_key="adk:event-1",
        )
        == committed
    )
    with pytest.raises(asyncio.QueueEmpty):
        subscription.receive_nowait()


def test_backpressure_evicts_only_the_slow_subscriber_without_blocking_publish() -> None:
    broker = RunEventBroker(queue_capacity=2)
    slow = broker.subscribe("run-1")
    fast = broker.subscribe("run-1")

    broker.publish(_envelope(1))
    assert fast.receive_nowait().sequence == 1
    broker.publish(_envelope(2))
    assert fast.receive_nowait().sequence == 2
    broker.publish(_envelope(3))

    assert slow.closed is True
    assert fast.closed is False
    assert fast.receive_nowait().sequence == 3
    with pytest.raises(SubscriberBackpressureError, match=r"replay|backpressure"):
        slow.receive_nowait()


def test_broker_delivers_only_matching_runs_and_unsubscribe_is_idempotent() -> None:
    broker = RunEventBroker(queue_capacity=2)
    first = broker.subscribe("run-1")
    second = broker.subscribe("run-2")

    broker.publish(_envelope(1, run_id="run-1"))

    assert first.receive_nowait().run_id == "run-1"
    with pytest.raises(asyncio.QueueEmpty):
        second.receive_nowait()
    broker.unsubscribe("run-1", first)
    broker.unsubscribe("run-1", first)
    assert first.closed is True


@pytest.mark.asyncio
async def test_close_and_overflow_wake_waiting_subscribers() -> None:
    broker = RunEventBroker(queue_capacity=1)
    closed = broker.subscribe("run-1")
    closed_waiter = asyncio.create_task(anext(closed))
    await asyncio.sleep(0)

    broker.unsubscribe("run-1", closed)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(closed_waiter, timeout=1)
    with pytest.raises(StopAsyncIteration):
        await anext(closed)

    overflowed = broker.subscribe("run-2")
    broker.publish(_envelope(1, run_id="run-2"))
    broker.publish(_envelope(2, run_id="run-2"))
    with pytest.raises(SubscriberBackpressureError, match=r"replay|backpressure"):
        await asyncio.wait_for(anext(overflowed), timeout=1)
    broker.unsubscribe("run-2", overflowed)
    with pytest.raises(SubscriberBackpressureError, match=r"replay|backpressure"):
        await anext(overflowed)
