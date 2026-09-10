"""Append-only SQLite storage for bounded, privacy-safe execution traces."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class TraceSpan(BaseModel):
    """One immutable lifecycle observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    span_id: str
    task_id: str
    sequence: int = Field(ge=1)
    correlation_id: str
    parent_span_id: str | None = None
    category: str
    phase: str
    name: str
    timestamp: str
    content_hash: str
    payload_json: str
    omitted_bytes: int = Field(default=0, ge=0)
    idempotency_key: str

    def payload(self) -> object:
        return json.loads(self.payload_json)


class TraceStore:
    """Persist per-task ordered spans without update or delete operations."""

    def __init__(
        self,
        database: Path,
        *,
        on_append: Callable[[TraceSpan], object] | None = None,
    ) -> None:
        self.database = database
        self.on_append = on_append
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_spans (
                    span_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    correlation_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    category TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    name TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    omitted_bytes INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    UNIQUE(task_id, sequence),
                    UNIQUE(task_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS ix_trace_spans_correlation
                ON trace_spans(task_id, correlation_id, sequence);
                CREATE INDEX IF NOT EXISTS ix_trace_spans_category
                ON trace_spans(task_id, category, phase, sequence);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> TraceSpan:
        return TraceSpan.model_validate(dict(row))

    def append(self, span: TraceSpan) -> TraceSpan:
        """Append once, returning an identical prior span on replay."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM trace_spans
                WHERE task_id=? AND idempotency_key=?
                """,
                (span.task_id, span.idempotency_key),
            ).fetchone()
            if existing is not None:
                previous = self._from_row(existing)
                comparable = span.model_copy(
                    update={
                        "span_id": previous.span_id,
                        "sequence": previous.sequence,
                        "timestamp": previous.timestamp,
                        "parent_span_id": previous.parent_span_id,
                    }
                )
                if comparable != previous:
                    raise ValueError(
                        "trace idempotency key reused for different span content"
                    )
                if self.on_append is not None:
                    self.on_append(previous)
                return previous

            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM trace_spans WHERE task_id=?",
                (span.task_id,),
            ).fetchone()
            assert row is not None
            stored = span.model_copy(update={"sequence": int(row[0])})
            connection.execute(
                """
                INSERT INTO trace_spans VALUES (
                    :span_id, :task_id, :sequence, :correlation_id,
                    :parent_span_id, :category, :phase, :name, :timestamp,
                    :content_hash, :payload_json, :omitted_bytes,
                    :idempotency_key
                )
                """,
                stored.model_dump(mode="python"),
            )
            if self.on_append is not None:
                self.on_append(stored)
            return stored

    def query(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        categories: Iterable[str] | None = None,
        phases: Iterable[str] | None = None,
        correlation_id: str | None = None,
        limit: int = 10_000,
    ) -> list[TraceSpan]:
        clauses = ["task_id=?", "sequence>?"]
        parameters: list[object] = [task_id, max(after_sequence, 0)]
        category_values = sorted(set(categories or ()))
        phase_values = sorted(set(phases or ()))
        if category_values:
            clauses.append(
                "category IN (" + ",".join("?" for _ in category_values) + ")"
            )
            parameters.extend(category_values)
        if phase_values:
            clauses.append("phase IN (" + ",".join("?" for _ in phase_values) + ")")
            parameters.extend(phase_values)
        if correlation_id is not None:
            clauses.append("correlation_id=?")
            parameters.append(correlation_id)
        parameters.append(max(limit, 0))
        query = (
            "SELECT * FROM trace_spans WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._from_row(row) for row in rows]

    def task_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT task_id FROM trace_spans ORDER BY task_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def export_jsonl(self, task_id: str) -> str:
        """Export stable sequence-ordered records for offline learning."""

        return "\n".join(
            json.dumps(span.model_dump(mode="json"), sort_keys=True)
            for span in self.query(task_id)
        )


__all__ = ["TraceSpan", "TraceStore"]
