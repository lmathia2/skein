"""SQLite tool-call receipts for at-least-once ADK resumability."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    invocation_id: str
    tool_call_id: str
    tool_name: str
    arguments_hash: str
    arguments_json: str | None = None
    status: Literal["started", "completed", "failed"]
    result_hash: str | None = None
    result_json: str | None = None
    workspace_before: str | None = None
    workspace_after: str | None = None
    artifact_uri: str | None = None
    side_effect_key: str | None = None
    error: str | None = None
    started_at: str
    completed_at: str | None = None


class ToolReceiptStore:
    def __init__(
        self,
        database: Path,
        *,
        on_save: Callable[[ToolReceipt], object] | None = None,
    ) -> None:
        self.database = database
        self.on_save = on_save
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_receipts (
                    task_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_hash TEXT,
                    artifact_uri TEXT,
                    side_effect_key TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (task_id, tool_call_id)
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_receipts)")}
            if "result_json" not in columns:
                connection.execute("ALTER TABLE tool_receipts ADD COLUMN result_json TEXT")
            for column in ("workspace_before", "workspace_after"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE tool_receipts ADD COLUMN {column} TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_side_effect
                ON tool_receipts(task_id, side_effect_key)
                WHERE side_effect_key IS NOT NULL
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ToolReceipt | None:
        return ToolReceipt.model_validate(dict(row)) if row else None

    def get(self, task_id: str, tool_call_id: str) -> ToolReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE task_id=? AND tool_call_id=?",
                (task_id, tool_call_id),
            ).fetchone()
        return self._from_row(row)

    def begin(
        self,
        *,
        task_id: str,
        invocation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_hash: str,
        side_effect_key: str | None = None,
        claim: bool = False,
        workspace_before: str | None = None,
    ) -> ToolReceipt:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO tool_receipts(
                        task_id, invocation_id, tool_call_id, tool_name,
                        arguments_hash, status, side_effect_key, started_at, workspace_before
                    ) VALUES (?, ?, ?, ?, ?, 'started', ?, ?, ?)
                    """,
                    (
                        task_id,
                        invocation_id,
                        tool_call_id,
                        tool_name,
                        arguments_hash,
                        side_effect_key,
                        now,
                        workspace_before,
                    ),
                )
            except sqlite3.IntegrityError as error:
                existing = self.get(task_id, tool_call_id)
                if existing is None and side_effect_key is not None:
                    row = connection.execute(
                        "SELECT * FROM tool_receipts WHERE task_id=? AND side_effect_key=?",
                        (task_id, side_effect_key),
                    ).fetchone()
                    existing = self._from_row(row)
                if existing is None:
                    raise
                if (
                    existing.tool_name != tool_name
                    or existing.arguments_hash != arguments_hash
                ):
                    raise ValueError(
                        "tool receipt key reused with different arguments"
                    ) from error
                if claim and existing.status != "completed":
                    raise RuntimeError("operation outcome requires reconciliation; automatic retry refused") from error
                return existing
        receipt = self.get(task_id, tool_call_id)
        assert receipt is not None
        if self.on_save is not None:
            self.on_save(receipt)
        return receipt

    def finish(
        self,
        *,
        task_id: str,
        tool_call_id: str,
        status: Literal["completed", "failed"],
        result_hash: str | None = None,
        artifact_uri: str | None = None,
        error: str | None = None,
        result_json: str | None = None,
        workspace_after: str | None = None,
    ) -> ToolReceipt:
        completed_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_receipts
                SET status=?, result_hash=?, artifact_uri=?, error=?, completed_at=?, result_json=?, workspace_after=?
                WHERE task_id=? AND tool_call_id=?
                """,
                (
                    status,
                    result_hash,
                    artifact_uri,
                    error,
                    completed_at,
                    result_json,
                    workspace_after,
                    task_id,
                    tool_call_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown tool receipt: {task_id}/{tool_call_id}")
        receipt = self.get(task_id, tool_call_id)
        assert receipt is not None
        if self.on_save is not None:
            self.on_save(receipt)
        return receipt

    def for_task(self, task_id: str) -> list[ToolReceipt]:
        """Include unfinished attempts when reconciling after any checkpoint."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_receipts WHERE task_id=? ORDER BY started_at, tool_call_id",
                (task_id,),
            ).fetchall()
        return [ToolReceipt.model_validate(dict(row)) for row in rows]
