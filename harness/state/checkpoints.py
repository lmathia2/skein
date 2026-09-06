"""Persistence for session/workspace checkpoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from harness.models.checkpoint import Checkpoint


class CheckpointStore:
    def __init__(
        self,
        database: Path,
        *,
        on_save: Callable[[Checkpoint], object] | None = None,
    ) -> None:
        self.database = database
        self.on_save = on_save
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_checkpoint_task ON checkpoints(task_id, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, checkpoint: Checkpoint) -> None:
        # Publish canonical capture before the operational checkpoint marker. A
        # failed capture must never make an incomplete checkpoint discoverable.
        if self.on_save is not None:
            self.on_save(checkpoint)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM checkpoints WHERE checkpoint_id=?",
                (checkpoint.checkpoint_id,),
            ).fetchone()
            if existing is not None:
                if Checkpoint.model_validate_json(existing["payload"]) != checkpoint:
                    raise ValueError("checkpoint identity reused with different content")
                return
            connection.execute(
                """
                INSERT INTO checkpoints(checkpoint_id, task_id, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.task_id,
                    checkpoint.created_at.isoformat(),
                    checkpoint.model_dump_json(),
                ),
            )

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
        return Checkpoint.model_validate_json(row["payload"]) if row else None

    def latest(self, task_id: str) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM checkpoints
                WHERE task_id=? ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return Checkpoint.model_validate_json(row["payload"]) if row else None
