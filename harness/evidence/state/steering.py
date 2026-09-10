"""Durable user-steering queue with lease/ack semantics."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_STEERING_MESSAGE_BYTES = 4_096
STEERING_BATCH_LIMIT = 4
SteeringStatus = Literal["queued", "leased", "acked"]


class SteeringMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    priority: int = Field(ge=-1_000, le=1_000)
    created_at: str
    status: SteeringStatus
    lease_owner: str | None = None
    lease_until: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("steering content must not be blank")
        if len(normalized.encode("utf-8")) > MAX_STEERING_MESSAGE_BYTES:
            raise ValueError(
                f"steering content exceeds {MAX_STEERING_MESSAGE_BYTES} UTF-8 bytes"
            )
        return normalized


class SteeringQueue:
    def __init__(
        self,
        database: Path,
        *,
        on_change: Callable[[SteeringMessage], object] | None = None,
    ) -> None:
        self.database = database
        self._on_change = on_change
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS steering_messages (
                    message_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TEXT,
                    idempotency_key TEXT,
                    UNIQUE(task_id, idempotency_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SteeringMessage:
        data = dict(row)
        data.pop("idempotency_key", None)
        return SteeringMessage.model_validate(data)

    def enqueue(
        self,
        task_id: str,
        content: str,
        *,
        priority: int = 0,
        idempotency_key: str | None = None,
    ) -> SteeringMessage:
        validated = SteeringMessage(
            message_id="pending",
            task_id=task_id,
            content=content,
            priority=priority,
            created_at=datetime.now(UTC).isoformat(),
            status="queued",
        )
        now = datetime.now(UTC).isoformat()
        message_id = uuid4().hex
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO steering_messages(
                        message_id, task_id, content, priority, created_at,
                        status, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        message_id,
                        validated.task_id,
                        validated.content,
                        validated.priority,
                        now,
                        idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError as error:
                row = connection.execute(
                    "SELECT * FROM steering_messages WHERE task_id=? AND idempotency_key=?",
                    (task_id, idempotency_key),
                ).fetchone()
                assert row is not None
                existing = self._from_row(row)
                if (
                    existing.content != validated.content
                    or existing.priority != validated.priority
                ):
                    raise ValueError(
                        "steering idempotency key reused with different content"
                    ) from error
                return existing
            row = connection.execute(
                "SELECT * FROM steering_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        assert row is not None
        message = self._from_row(row)
        if self._on_change is not None:
            self._on_change(message)
        return message

    def lease(
        self,
        task_id: str,
        owner: str,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[SteeringMessage]:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE steering_messages SET status='queued', lease_owner=NULL, lease_until=NULL
                WHERE task_id=? AND status='leased' AND lease_until < ?
                """,
                (task_id, now.isoformat()),
            )
            rows = connection.execute(
                """
                SELECT * FROM steering_messages
                WHERE task_id=? AND status='queued'
                ORDER BY priority DESC, created_at ASC LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
            ids = [row["message_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE steering_messages
                    SET status='leased', lease_owner=?, lease_until=?
                    WHERE message_id IN ({placeholders})
                    """,
                    (owner, lease_until.isoformat(), *ids),
                )
            connection.execute("COMMIT")
        leased = [
            message.model_copy(
                update={
                    "status": "leased",
                    "lease_owner": owner,
                    "lease_until": lease_until.isoformat(),
                }
            )
            for message in map(self._from_row, rows)
        ]
        if self._on_change is not None:
            for message in leased:
                self._on_change(message)
        return leased

    def leased_by(self, task_id: str, owner: str) -> list[SteeringMessage]:
        """Return the owner's live leases in deterministic delivery order."""

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM steering_messages
                WHERE task_id=? AND status='leased' AND lease_owner=? AND lease_until>=?
                ORDER BY priority DESC, created_at ASC, message_id ASC
                """,
                (task_id, owner, now),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def has_pending(self, task_id: str) -> bool:
        """Report whether new or expired leased steering awaits delivery."""

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM steering_messages
                WHERE task_id=? AND (
                    status='queued' OR (status='leased' AND lease_until<?)
                )
                LIMIT 1
                """,
                (task_id, now),
            ).fetchone()
        return row is not None

    def list_messages(
        self,
        task_id: str,
        *,
        statuses: tuple[SteeringStatus, ...] = ("queued", "leased", "acked"),
        limit: int = 100,
    ) -> list[SteeringMessage]:
        """List bounded task steering for operator status views."""

        if not statuses:
            return []
        bounded_limit = max(1, min(limit, 1_000))
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM steering_messages
                WHERE task_id=? AND status IN ({placeholders})
                ORDER BY created_at ASC, message_id ASC
                LIMIT ?
                """,
                (task_id, *statuses, bounded_limit),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def ack(self, message_ids: list[str], owner: str) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE steering_messages
                SET status='acked', lease_owner=NULL, lease_until=NULL
                WHERE message_id IN ({placeholders}) AND status='leased' AND lease_owner=?
                """,
                (*message_ids, owner),
            )
            rows = connection.execute(
                f"SELECT * FROM steering_messages WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        count = cursor.rowcount
        if self._on_change is not None:
            for row in rows:
                self._on_change(self._from_row(row))
        return count

    def release(self, message_ids: list[str], owner: str) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE steering_messages
                SET status='queued', lease_owner=NULL, lease_until=NULL
                WHERE message_id IN ({placeholders}) AND status='leased' AND lease_owner=?
                """,
                (*message_ids, owner),
            )
            rows = connection.execute(
                f"SELECT * FROM steering_messages WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        if self._on_change is not None:
            for row in rows:
                self._on_change(self._from_row(row))
        return cursor.rowcount
