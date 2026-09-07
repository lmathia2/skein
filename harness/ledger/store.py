"""DuckDB-backed append-only canonical ledger."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .models import EffectStatus, EventStatus, LedgerEvent, canonical_json, extend_event_hash

_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}
# ponytail: locks are process-local; use a multi-process database when more than one
# server owns the same state root.


class DuckDbLedgerStore:
    """One-process writer with idempotent append and deterministic task ordering."""

    def __init__(self, database: Path) -> None:
        self.database = database.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK_REGISTRY_GUARD:
            self._lock = _LOCKS.setdefault(self.database, threading.RLock())
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_events (
                    event_id VARCHAR PRIMARY KEY,
                    task_id VARCHAR NOT NULL,
                    sequence BIGINT NOT NULL,
                    source VARCHAR NOT NULL,
                    source_id VARCHAR NOT NULL,
                    kind VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    effect VARCHAR NOT NULL,
                    observed_at VARCHAR NOT NULL,
                    recorded_at VARCHAR NOT NULL,
                    correlation_id VARCHAR,
                    parent_event_id VARCHAR,
                    payload_json VARCHAR NOT NULL,
                    payload_hash VARCHAR NOT NULL,
                    idempotency_key VARCHAR NOT NULL,
                    UNIQUE(task_id, sequence),
                    UNIQUE(task_id, idempotency_key),
                    UNIQUE(source, source_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ledger_task_kind ON ledger_events(task_id, kind, sequence)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ledger_event_counts (
                    task_id VARCHAR NOT NULL, source VARCHAR NOT NULL, kind VARCHAR NOT NULL,
                    status VARCHAR NOT NULL, count BIGINT NOT NULL,
                    PRIMARY KEY(task_id, source, kind, status)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ledger_stream_heads (
                    task_id VARCHAR PRIMARY KEY, watermark BIGINT NOT NULL, stream_hash VARCHAR NOT NULL
                )"""
            )
            count = connection.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
            projected = connection.execute("SELECT COALESCE(SUM(count), 0) FROM ledger_event_counts").fetchone()[0]
            if count != projected:
                self._rebuild_aggregates(connection)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database))

    @staticmethod
    def _rebuild_aggregates(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("DELETE FROM ledger_event_counts")
        connection.execute("DELETE FROM ledger_stream_heads")
        rows = connection.execute(
            "SELECT task_id, sequence, event_id, payload_hash FROM ledger_events ORDER BY task_id, sequence"
        ).fetchall()
        heads: dict[str, tuple[int, str]] = {}
        for task_id, sequence, event_id, payload_hash in rows:
            heads[task_id] = (sequence, extend_event_hash(heads.get(task_id, (0, ""))[1], event_id, payload_hash))
        connection.execute(
            """INSERT INTO ledger_event_counts
               SELECT task_id, source, kind, status, COUNT(*) FROM ledger_events
               GROUP BY task_id, source, kind, status"""
        )
        if heads:
            connection.executemany("INSERT INTO ledger_stream_heads VALUES (?, ?, ?)",
                                   [(task, *head) for task, head in heads.items()])

    def event_counts(self, task_id: str) -> tuple[int, str, list[tuple[str, str, str, int]]]:
        """Return the incrementally maintained complete aggregate at its watermark."""
        with self._connect() as connection:
            head = connection.execute(
                "SELECT watermark, stream_hash FROM ledger_stream_heads WHERE task_id=?", [task_id]
            ).fetchone()
            rows = connection.execute(
                """SELECT source, kind, status, count FROM ledger_event_counts
                   WHERE task_id=? ORDER BY source, kind, status""", [task_id]
            ).fetchall()
        return (int(head[0]), str(head[1]), [(str(a), str(b), str(c), int(d)) for a, b, c, d in rows]) if head else (0, "", [])

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> LedgerEvent:
        return LedgerEvent.model_validate(
            {
                "event_id": row[0],
                "task_id": row[1],
                "sequence": row[2],
                "source": row[3],
                "source_id": row[4],
                "kind": row[5],
                "status": row[6],
                "effect": row[7],
                "observed_at": row[8],
                "recorded_at": row[9],
                "correlation_id": row[10],
                "parent_event_id": row[11],
                "payload": __import__("json").loads(row[12]),
                "payload_hash": row[13],
                "idempotency_key": row[14],
            }
        )

    def append(
        self,
        *,
        task_id: str,
        source: str,
        source_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        status: EventStatus = "observed",
        effect: EffectStatus = "none",
        observed_at: datetime | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
        idempotency_key: str | None = None,
        recorded_at: datetime | None = None,
    ) -> LedgerEvent:
        body = payload or {}
        key = idempotency_key or f"{source}:{source_id}"
        event_id = hashlib.sha256(f"{task_id}\0{key}".encode()).hexdigest()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                previous = connection.execute(
                    "SELECT * FROM ledger_events WHERE task_id=? AND idempotency_key=?",
                    [task_id, key],
                ).fetchone()
                if previous is not None:
                    stored = self._from_row(previous)
                    if (
                        stored.source != source
                        or stored.source_id != source_id
                        or stored.kind != kind
                        or stored.status != status
                        or stored.effect != effect
                        or stored.payload != body
                    ):
                        raise ValueError("ledger idempotency key reused for different content")
                    connection.execute("COMMIT")
                    return stored
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM ledger_events WHERE task_id=?",
                    [task_id],
                ).fetchone()
                assert row is not None
                sequence = int(row[0])
                event = LedgerEvent(
                    event_id=event_id,
                    task_id=task_id,
                    sequence=sequence,
                    source=source,
                    source_id=source_id,
                    kind=kind,
                    status=status,
                    effect=effect,
                    observed_at=observed_at or datetime.now(UTC),
                    recorded_at=recorded_at or datetime.now(UTC),
                    correlation_id=correlation_id,
                    parent_event_id=parent_event_id,
                    payload=body,
                    idempotency_key=key,
                )
                connection.execute(
                    "INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        event.event_id,
                        event.task_id,
                        event.sequence,
                        event.source,
                        event.source_id,
                        event.kind,
                        event.status,
                        event.effect,
                        event.observed_at.isoformat(),
                        event.recorded_at.isoformat(),
                        event.correlation_id,
                        event.parent_event_id,
                        canonical_json(event.payload),
                        event.payload_hash,
                        event.idempotency_key,
                    ],
                )
                connection.execute(
                    """INSERT INTO ledger_event_counts VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT (task_id, source, kind, status)
                       DO UPDATE SET count=ledger_event_counts.count + 1""",
                    [task_id, source, kind, status],
                )
                previous_head = connection.execute(
                    "SELECT stream_hash FROM ledger_stream_heads WHERE task_id=?", [task_id]
                ).fetchone()
                stream_hash = extend_event_hash(str(previous_head[0]) if previous_head else "",
                                                event.event_id, event.payload_hash)
                connection.execute(
                    """INSERT INTO ledger_stream_heads VALUES (?, ?, ?)
                       ON CONFLICT (task_id) DO UPDATE SET
                       watermark=excluded.watermark, stream_hash=excluded.stream_hash""",
                    [task_id, sequence, stream_hash],
                )
                connection.execute("COMMIT")
                return event
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def read(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        as_of: datetime | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 100_000,
    ) -> list[LedgerEvent]:
        clauses = ["task_id=?", "sequence>?"]
        parameters: list[Any] = [task_id, max(after_sequence, 0)]
        if as_of is not None:
            clauses.append("observed_at<=?")
            parameters.append(as_of.isoformat())
        values = sorted(set(kinds or ()))
        if values:
            clauses.append("kind IN (" + ",".join("?" for _ in values) + ")")
            parameters.extend(values)
        parameters.append(max(limit, 0))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ledger_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?",
                parameters,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def task_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT task_id FROM ledger_events ORDER BY task_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def source_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source, COUNT(*) FROM ledger_events GROUP BY source ORDER BY source"
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def content_hash(self, task_id: str) -> str:
        records = [event.model_dump(mode="json") for event in self.read(task_id)]
        return hashlib.sha256(canonical_json(records).encode()).hexdigest()

    def erase_task(self, task_id: str) -> int:
        """Physically remove one task when retention policy overrides audit history."""

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM ledger_events WHERE task_id=?", [task_id]
                ).fetchone()
                assert row is not None
                connection.execute("DELETE FROM ledger_events WHERE task_id=?", [task_id])
                connection.execute("DELETE FROM ledger_event_counts WHERE task_id=?", [task_id])
                connection.execute("DELETE FROM ledger_stream_heads WHERE task_id=?", [task_id])
                connection.execute("COMMIT")
                connection.execute("CHECKPOINT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return int(row[0])
