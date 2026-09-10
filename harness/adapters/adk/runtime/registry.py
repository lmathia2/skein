"""Durable public run/event registry and bounded live fan-out."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from .protocol import AgUiEvent, ServerEnvelope

RunStatus = Literal["queued", "running", "completed", "cancelled", "failed"]

_LEGACY_INPUT = "[legacy run input unavailable]"
_LEGACY_INPUT_ERROR = "run cannot resume because its input predates durable input storage"

_ALLOWED_STATUS_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"completed", "cancelled", "failed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    invocation_id: str = Field(min_length=1, max_length=256)
    input: str = Field(min_length=1, max_length=50_000)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, str] = Field(default_factory=dict)
    status: RunStatus
    error: str | None = Field(default=None, max_length=4_096)
    created_at: str
    updated_at: str


class EventAppendResult(BaseModel):
    """Outcome of a durable append, including whether this call inserted it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: ServerEnvelope
    created: bool


class ReplayPage(BaseModel):
    """A page from one durable high-water snapshot of a run's event stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    events: tuple[ServerEnvelope, ...]
    high_water_sequence: int = Field(ge=0)
    has_more: bool


class SqliteRunEventStore:
    """Transactional run metadata and replayable normalized protocol events."""

    def __init__(
        self,
        database: Path,
        *,
        run_sink: Callable[[RunRecord], object] | None = None,
        event_sink: Callable[[ServerEnvelope], object] | None = None,
    ) -> None:
        self.database = database.expanduser().resolve()
        self._run_sink = run_sink
        self._event_sink = event_sink
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    input TEXT NOT NULL,
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
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(agent_runs)")
            }
            if "input" not in columns:
                # Input cannot be reconstructed from the hash used by the legacy schema.
                # Preserve terminal history, but fail active rows closed instead of ever
                # resuming them with invented user input.
                connection.execute(
                    "ALTER TABLE agent_runs ADD COLUMN input TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "UPDATE agent_runs SET input=? WHERE input=''", (_LEGACY_INPUT,)
                )
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET status='failed', error=?, updated_at=?
                    WHERE status IN ('queued', 'running')
                    """,
                    (_LEGACY_INPUT_ERROR, datetime.now(UTC).isoformat()),
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    source_key TEXT,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    UNIQUE(run_id, source_key),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                )
                """
            )

    @staticmethod
    def _run_from_row(row: sqlite3.Row | None) -> RunRecord | None:
        if row is None:
            return None
        payload = dict(row)
        payload["metadata"] = json.loads(payload.pop("metadata_json"))
        return RunRecord.model_validate(payload)

    @staticmethod
    def _run_id(user_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"run\0{user_id}\0{idempotency_key}".encode()
        ).hexdigest()
        return digest[:32]

    def create_run(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        thread_id: str,
        user_id: str,
        input: str,
        metadata: dict[str, str] | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
    ) -> tuple[RunRecord, bool]:
        """Create a run or return its exact idempotent predecessor."""

        run_id = self._run_id(user_id, idempotency_key)
        resolved_session_id = session_id or thread_id
        resolved_invocation_id = invocation_id or run_id
        input_sha256 = hashlib.sha256(input.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        candidate = RunRecord(
            run_id=run_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            thread_id=thread_id,
            user_id=user_id,
            session_id=resolved_session_id,
            invocation_id=resolved_invocation_id,
            input=input,
            input_sha256=input_sha256,
            metadata=metadata or {},
            status="queued",
            created_at=now,
            updated_at=now,
        )
        metadata_json = json.dumps(
            candidate.metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if row is not None:
                existing = self._run_from_row(row)
                assert existing is not None
                if (
                    existing.thread_id != candidate.thread_id
                    or existing.session_id != candidate.session_id
                    or existing.invocation_id != candidate.invocation_id
                    or existing.input != candidate.input
                    or existing.input_sha256 != candidate.input_sha256
                    or existing.metadata != candidate.metadata
                ):
                    raise ValueError(
                        "run idempotency key was reused with different parameters"
                    )
                connection.execute("COMMIT")
                return existing, False
            active = connection.execute(
                "SELECT run_id FROM agent_runs WHERE user_id=? AND session_id=? "
                "AND status IN ('queued', 'running') LIMIT 1",
                (user_id, resolved_session_id),
            ).fetchone()
            if active is not None:
                raise ValueError("conversation already has active work; steer it or wait")
            prior = connection.execute(
                "SELECT metadata_json FROM agent_runs WHERE user_id=? AND session_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, resolved_session_id),
            ).fetchone()
            if prior is not None:
                previous = json.loads(prior["metadata_json"])
                for binding in ("coding.workspace_identity", "coding.harness_implementation"):
                    if previous.get(binding) != candidate.metadata.get(binding):
                        raise ValueError("conversation belongs to a different workspace or harness")
            connection.execute(
                """
                INSERT INTO agent_runs(
                    run_id, request_id, idempotency_key, thread_id, user_id,
                    session_id, invocation_id, input, input_sha256, metadata_json,
                    status, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, ?, ?)
                """,
                (
                    run_id,
                    request_id,
                    idempotency_key,
                    thread_id,
                    user_id,
                    resolved_session_id,
                    resolved_invocation_id,
                    input,
                    input_sha256,
                    metadata_json,
                    now,
                    now,
                ),
            )
            connection.execute("COMMIT")
        created = self.get_run(run_id)
        assert created is not None
        if self._run_sink is not None:
            self._run_sink(created)
        return created, True

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._run_from_row(row)

    def active_run_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            return tuple(row["run_id"] for row in connection.execute(
                "SELECT run_id FROM agent_runs WHERE status IN ('queued', 'running')"
            ))

    def update_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
        expected_status: RunStatus | None = None,
    ) -> RunRecord:
        """Conditionally advance a run while preserving immutable terminal state.

        Repeating an exact update is idempotent. A different error, a backward
        transition, or an unmet ``expected_status`` is a conflict.
        """

        if error is not None:
            error = error[:4_096]
        if status in {"queued", "running", "completed"} and error is not None:
            raise ValueError(f"run status {status} cannot carry an error")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KeyError(f"unknown run: {run_id}")
            raw_previous = str(row["status"])
            if raw_previous not in _ALLOWED_STATUS_TRANSITIONS:
                connection.execute("ROLLBACK")
                raise ValueError(f"stored run has invalid status: {raw_previous}")
            previous = cast(RunStatus, raw_previous)
            previous_error = row["error"]
            if expected_status is not None and previous != expected_status:
                connection.execute("ROLLBACK")
                raise ValueError(
                    "run status compare-and-set conflict: "
                    f"expected {expected_status}, found {previous}"
                )
            if status == previous:
                if error != previous_error:
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "idempotent run status update cannot change its error"
                    )
                connection.execute("COMMIT")
                existing = self._run_from_row(row)
                assert existing is not None
                return existing
            if status not in _ALLOWED_STATUS_TRANSITIONS[previous]:
                connection.execute("ROLLBACK")
                raise ValueError(f"invalid run status transition: {previous} -> {status}")
            cursor = connection.execute(
                """
                UPDATE agent_runs SET status=?, error=?, updated_at=?
                WHERE run_id=? AND status=?
                """,
                (status, error, datetime.now(UTC).isoformat(), run_id, previous),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValueError("run status compare-and-set conflict")
            updated_row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            connection.execute("COMMIT")
        updated = self._run_from_row(updated_row)
        assert updated is not None
        if self._run_sink is not None:
            self._run_sink(updated)
        return updated

    def append_event(
        self,
        run_id: str,
        event: AgUiEvent,
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_key: str | None = None,
    ) -> ServerEnvelope:
        """Append with a transactional sequence; source keys make replay idempotent."""

        return self.append_event_with_outcome(
            run_id,
            event,
            durable=durable,
            session_id=session_id,
            invocation_id=invocation_id,
            source_key=source_key,
        ).envelope

    def append_event_with_outcome(
        self,
        run_id: str,
        event: AgUiEvent,
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_key: str | None = None,
    ) -> EventAppendResult:
        """Append one event and report whether it was newly committed."""

        return self.append_events_with_outcomes(
            run_id,
            (event,),
            durable=durable,
            session_id=session_id,
            invocation_id=invocation_id,
            source_keys=(source_key,),
        )[0]

    def append_events(
        self,
        run_id: str,
        events: Sequence[AgUiEvent],
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_keys: Sequence[str | None] | None = None,
    ) -> tuple[ServerEnvelope, ...]:
        """Atomically append a batch of normalized events."""

        return tuple(
            result.envelope
            for result in self.append_events_with_outcomes(
                run_id,
                events,
                durable=durable,
                session_id=session_id,
                invocation_id=invocation_id,
                source_keys=source_keys,
            )
        )

    def append_events_with_outcomes(
        self,
        run_id: str,
        events: Sequence[AgUiEvent],
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_keys: Sequence[str | None] | None = None,
    ) -> tuple[EventAppendResult, ...]:
        """Atomically append events and identify idempotent predecessors."""

        if not durable:
            raise ValueError("persisted server events must be durable")
        if not events:
            return ()
        resolved_source_keys = (
            tuple(source_keys) if source_keys is not None else (None,) * len(events)
        )
        if len(resolved_source_keys) != len(events):
            raise ValueError("source_keys must align one-to-one with events")
        concrete_source_keys = [key for key in resolved_source_keys if key is not None]
        if any(not key or len(key) > 512 for key in concrete_source_keys):
            raise ValueError("source keys must contain 1 to 512 characters")
        if len(set(concrete_source_keys)) != len(concrete_source_keys):
            raise ValueError("source keys must be unique within an append batch")
        for event in events:
            if event.run_id is not None and event.run_id != run_id:
                raise ValueError("event run_id does not match append run_id")
        now = datetime.now(UTC).isoformat()
        outcomes: list[EventAppendResult] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT session_id, invocation_id FROM agent_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                connection.execute("ROLLBACK")
                raise KeyError(f"unknown run: {run_id}")
            resolved_session_id = session_id or str(run["session_id"])
            resolved_invocation_id = invocation_id or str(run["invocation_id"])
            next_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                    FROM public_run_events WHERE run_id=?
                    """,
                    (run_id,),
                ).fetchone()["next_sequence"]
            )
            for event, source_key in zip(events, resolved_source_keys, strict=True):
                existing: ServerEnvelope | None = None
                if source_key is not None:
                    row = connection.execute(
                        """
                        SELECT envelope_json FROM public_run_events
                        WHERE run_id=? AND source_key=?
                        """,
                        (run_id, source_key),
                    ).fetchone()
                    if row is not None:
                        existing = ServerEnvelope.model_validate_json(
                            row["envelope_json"]
                        )
                if existing is not None:
                    if (
                        existing.event != event
                        or existing.durable != durable
                        or existing.session_id != resolved_session_id
                        or existing.invocation_id != resolved_invocation_id
                    ):
                        connection.execute("ROLLBACK")
                        raise ValueError(
                            "event source_key idempotency was reused with different content"
                        )
                    outcomes.append(
                        EventAppendResult(envelope=existing, created=False)
                    )
                    continue
                envelope = ServerEnvelope(
                    sequence=next_sequence,
                    run_id=run_id,
                    session_id=resolved_session_id,
                    invocation_id=resolved_invocation_id,
                    durable=durable,
                    event=event,
                )
                connection.execute(
                    """
                    INSERT INTO public_run_events(
                        run_id, sequence, source_key, envelope_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        next_sequence,
                        source_key,
                        envelope.model_dump_json(),
                        now,
                    ),
                )
                outcomes.append(EventAppendResult(envelope=envelope, created=True))
                next_sequence += 1
            connection.execute("COMMIT")
        if self._event_sink is not None:
            for outcome in outcomes:
                self._event_sink(outcome.envelope)
        return tuple(outcomes)

    def replay(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> tuple[ServerEnvelope, ...]:
        return self.replay_page(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        ).events

    def terminalize(
        self,
        run_id: str,
        *,
        status: Literal["completed", "cancelled", "failed"],
        event: AgUiEvent,
        source_key: str,
        expected_status: Literal["queued", "running"],
        error: str | None = None,
    ) -> EventAppendResult:
        """Atomically append the terminal event and freeze terminal run status."""

        if not source_key or len(source_key) > 512:
            raise ValueError("source_key must contain 1 to 512 characters")
        if event.run_id is not None and event.run_id != run_id:
            raise ValueError("event run_id does not match terminal run_id")
        if status == "completed" and error is not None:
            raise ValueError("completed runs cannot carry an error")
        if error is not None:
            error = error[:4_096]
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise KeyError(f"unknown run: {run_id}")
            previous = str(row["status"])
            prior_error = row["error"]
            existing_row = connection.execute(
                """
                SELECT envelope_json FROM public_run_events
                WHERE run_id=? AND source_key=?
                """,
                (run_id, source_key),
            ).fetchone()
            existing = (
                ServerEnvelope.model_validate_json(existing_row["envelope_json"])
                if existing_row is not None
                else None
            )
            if previous == status:
                if prior_error != error or existing is None or existing.event != event:
                    connection.execute("ROLLBACK")
                    raise ValueError("terminal run replay conflicts with durable state")
                connection.execute("COMMIT")
                if self._event_sink is not None:
                    self._event_sink(existing)
                return EventAppendResult(envelope=existing, created=False)
            if previous != expected_status:
                connection.execute("ROLLBACK")
                raise ValueError(
                    "run status compare-and-set conflict: "
                    f"expected {expected_status}, found {previous}"
                )
            if existing is not None:
                connection.execute("ROLLBACK")
                raise ValueError("terminal source key already exists for an active run")
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                    FROM public_run_events WHERE run_id=?
                    """,
                    (run_id,),
                ).fetchone()["next_sequence"]
            )
            envelope = ServerEnvelope(
                sequence=sequence,
                run_id=run_id,
                session_id=str(row["session_id"]),
                invocation_id=str(row["invocation_id"]),
                durable=True,
                event=event,
            )
            connection.execute(
                """
                INSERT INTO public_run_events(
                    run_id, sequence, source_key, envelope_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, sequence, source_key, envelope.model_dump_json(), now),
            )
            updated = connection.execute(
                """
                UPDATE agent_runs SET status=?, error=?, updated_at=?
                WHERE run_id=? AND status=?
                """,
                (status, error, now, run_id, expected_status),
            )
            if updated.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValueError("run status compare-and-set conflict")
            connection.execute("COMMIT")
        terminal_run = self.get_run(run_id)
        assert terminal_run is not None
        if self._run_sink is not None:
            self._run_sink(terminal_run)
        if self._event_sink is not None:
            self._event_sink(envelope)
        return EventAppendResult(envelope=envelope, created=True)

    def replay_page(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
        high_water_sequence: int | None = None,
    ) -> ReplayPage:
        """Read an exclusive-cursor page bounded by one durable snapshot."""

        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if high_water_sequence is not None and high_water_sequence < 0:
            raise ValueError("high_water_sequence must be non-negative")
        bounded_limit = max(1, min(limit, 10_000))
        with self._connect() as connection:
            connection.execute("BEGIN")
            run = connection.execute(
                "SELECT 1 FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                connection.execute("ROLLBACK")
                raise KeyError(f"unknown run: {run_id}")
            observed_high_water = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) AS high_water
                    FROM public_run_events WHERE run_id=?
                    """,
                    (run_id,),
                ).fetchone()["high_water"]
            )
            effective_high_water = (
                observed_high_water
                if high_water_sequence is None
                else min(high_water_sequence, observed_high_water)
            )
            rows = connection.execute(
                """
                SELECT envelope_json FROM public_run_events
                WHERE run_id=? AND sequence>? AND sequence<=?
                ORDER BY sequence ASC LIMIT ?
                """,
                (run_id, after_sequence, effective_high_water, bounded_limit + 1),
            ).fetchall()
            connection.execute("COMMIT")
        has_more = len(rows) > bounded_limit
        events = tuple(
            ServerEnvelope.model_validate_json(row["envelope_json"]) for row in rows
        )
        if has_more:
            events = events[:bounded_limit]
        return ReplayPage(
            events=events,
            high_water_sequence=effective_high_water,
            has_more=has_more,
        )


_CLOSED = object()


class SubscriberBackpressureError(RuntimeError):
    """The live queue overflowed; clients must reconnect using durable replay."""


class RunEventSubscription:
    def __init__(self, queue_capacity: int) -> None:
        self._queue: asyncio.Queue[ServerEnvelope | object] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self._closed = False
        self._overflowed = False

    def __aiter__(self) -> AsyncIterator[ServerEnvelope]:
        return self

    async def __anext__(self) -> ServerEnvelope:
        item = await self._queue.get()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)
            if self._overflowed:
                raise SubscriberBackpressureError(
                    "subscriber backpressure overflow; reconnect and replay from the last ack"
                )
            raise StopAsyncIteration
        assert isinstance(item, ServerEnvelope)
        return item

    def receive_nowait(self) -> ServerEnvelope:
        item = self._queue.get_nowait()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)
            if self._overflowed:
                raise SubscriberBackpressureError(
                    "subscriber backpressure overflow; reconnect and replay from the last ack"
                )
            raise StopAsyncIteration
        assert isinstance(item, ServerEnvelope)
        return item

    def _close(self, *, overflowed: bool = False) -> None:
        if self._closed:
            self._overflowed = self._overflowed or overflowed
            return
        self._closed = True
        self._overflowed = overflowed
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSED)

    @property
    def closed(self) -> bool:
        return self._closed


class RunEventBroker:
    """Best-effort live fan-out; disconnected clients recover from durable replay."""

    def __init__(self, *, queue_capacity: int = 256) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self.queue_capacity = queue_capacity
        self._subscribers: dict[str, set[RunEventSubscription]] = {}

    def subscribe(self, run_id: str) -> RunEventSubscription:
        subscription = RunEventSubscription(self.queue_capacity)
        self._subscribers.setdefault(run_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, run_id: str, subscription: RunEventSubscription) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(subscription)
            if not subscribers:
                self._subscribers.pop(run_id, None)
        subscription._close()

    def publish(self, envelope: ServerEnvelope) -> int:
        delivered = 0
        subscribers = self._subscribers.get(envelope.run_id, set())
        stale: list[RunEventSubscription] = []
        for subscription in tuple(subscribers):
            try:
                subscription._queue.put_nowait(envelope)
                delivered += 1
            except asyncio.QueueFull:
                stale.append(subscription)
        for subscription in stale:
            subscribers.discard(subscription)
            subscription._close(overflowed=True)
        if not subscribers:
            self._subscribers.pop(envelope.run_id, None)
        return delivered


class DurableRunEventJournal:
    """Commit durable events before performing best-effort live publication.

    The SQLite stream remains authoritative. Idempotent source-key retries are not
    published twice; a client that misses live fan-out recovers with replay.
    """

    def __init__(self, store: SqliteRunEventStore, broker: RunEventBroker) -> None:
        self.store = store
        self.broker = broker

    def append_event(
        self,
        run_id: str,
        event: AgUiEvent,
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_key: str | None = None,
    ) -> ServerEnvelope:
        result = self.store.append_event_with_outcome(
            run_id,
            event,
            durable=durable,
            session_id=session_id,
            invocation_id=invocation_id,
            source_key=source_key,
        )
        if result.created:
            self.broker.publish(result.envelope)
        return result.envelope

    def append_events(
        self,
        run_id: str,
        events: Sequence[AgUiEvent],
        *,
        durable: bool = True,
        session_id: str | None = None,
        invocation_id: str | None = None,
        source_keys: Sequence[str | None] | None = None,
    ) -> tuple[ServerEnvelope, ...]:
        results = self.store.append_events_with_outcomes(
            run_id,
            events,
            durable=durable,
            session_id=session_id,
            invocation_id=invocation_id,
            source_keys=source_keys,
        )
        for result in results:
            if result.created:
                self.broker.publish(result.envelope)
        return tuple(result.envelope for result in results)

    def terminalize(
        self,
        run_id: str,
        *,
        status: Literal["completed", "cancelled", "failed"],
        event: AgUiEvent,
        source_key: str,
        expected_status: Literal["queued", "running"],
        error: str | None = None,
    ) -> ServerEnvelope:
        result = self.store.terminalize(
            run_id,
            status=status,
            event=event,
            source_key=source_key,
            expected_status=expected_status,
            error=error,
        )
        if result.created:
            self.broker.publish(result.envelope)
        return result.envelope


__all__ = [
    "DurableRunEventJournal",
    "EventAppendResult",
    "ReplayPage",
    "RunEventBroker",
    "RunEventSubscription",
    "RunRecord",
    "RunStatus",
    "SqliteRunEventStore",
    "SubscriberBackpressureError",
]
