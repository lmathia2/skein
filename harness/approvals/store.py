"""Durable human approval requests for exact command fingerprints."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .contracts import ApprovalDecision, ApprovalRequest, ApprovalSubmission

Clock = Callable[[], datetime]


class ApprovalStore:
    """SQLite approval transport suitable for CLI, API, or managed workers."""

    def __init__(
        self,
        database: Path,
        *,
        clock: Clock | None = None,
        on_change: Callable[[ApprovalRequest], object] | None = None,
    ) -> None:
        self.database = database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._on_change = on_change
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision_note TEXT,
                    expires_at TEXT,
                    UNIQUE(task_id, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS ix_approval_pending
                ON approval_requests(task_id, status, requested_at);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(approval_requests)"
                ).fetchall()
            }
            if "expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE approval_requests ADD COLUMN expires_at TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("approval clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ApprovalRequest | None:
        return ApprovalRequest.model_validate(dict(row)) if row else None

    def request(
        self,
        *,
        task_id: str,
        fingerprint: str,
        operation: str,
        risk: str,
        reason: str,
        expires_at: str | None = None,
    ) -> ApprovalRequest:
        self.expire_due()
        candidate = ApprovalRequest(
            task_id=task_id,
            fingerprint=fingerprint,
            operation=operation,
            risk=risk,
            reason=reason,
            expires_at=expires_at,
            requested_at=self._now().isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET request_id=:request_id, operation=:operation, risk=:risk,
                    reason=:reason, status='pending', requested_at=:requested_at,
                    decided_at=NULL, decided_by=NULL, decision_note=NULL,
                    expires_at=:expires_at
                WHERE task_id=:task_id AND fingerprint=:fingerprint
                    AND status='expired'
                """,
                candidate.model_dump(mode="python"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO approval_requests (
                    request_id, task_id, fingerprint, operation, risk, reason,
                    status, requested_at, decided_at, decided_by, decision_note,
                    expires_at
                ) VALUES (
                    :request_id, :task_id, :fingerprint, :operation, :risk,
                    :reason, :status, :requested_at, :decided_at, :decided_by,
                    :decision_note, :expires_at
                )
                """,
                candidate.model_dump(mode="python"),
            )
            persisted = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE task_id=? AND fingerprint=?
                """,
                (task_id, fingerprint),
            ).fetchone()
        request = self._from_row(persisted)
        assert request is not None
        expected = (operation, risk, reason, candidate.expires_at)
        actual = (request.operation, request.risk, request.reason, request.expires_at)
        if actual != expected:
            raise ValueError(
                "approval fingerprint reused with different request content"
            )
        if self._on_change is not None:
            self._on_change(request)
        return request

    def submit(self, submission: ApprovalSubmission) -> ApprovalRequest:
        return self.request(**submission.model_dump(mode="python"))

    def expire_due(self) -> int:
        now = self._now().isoformat()
        with self._connect() as connection:
            due = connection.execute(
                """
                SELECT request_id FROM approval_requests
                WHERE status IN ('pending', 'approved')
                    AND expires_at IS NOT NULL AND expires_at<=?
                """,
                (now,),
            ).fetchall()
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status='expired',
                    decided_at=COALESCE(decided_at, ?),
                    decided_by=COALESCE(decided_by, 'system'),
                    decision_note=COALESCE(
                        decision_note,
                        'approval request expired'
                    )
                WHERE status IN ('pending', 'approved')
                    AND expires_at IS NOT NULL
                    AND expires_at<=?
                """,
                (now, now),
            )
            rows = [
                connection.execute(
                    "SELECT * FROM approval_requests WHERE request_id=?",
                    (row["request_id"],),
                ).fetchone()
                for row in due
            ]
        if self._on_change is not None:
            for row in rows:
                request = self._from_row(row)
                if request is not None:
                    self._on_change(request)
        return max(cursor.rowcount, 0)

    def get(self, request_id: str) -> ApprovalRequest | None:
        self.expire_due()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return self._from_row(row)

    def expire(self, request_id: str, *, task_id: str) -> None:
        """Close an abandoned wait without overwriting a concurrent human decision."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE approval_requests SET status='expired', decided_at=?, "
                "decided_by='system', decision_note='approval wait ended' "
                "WHERE request_id=? AND task_id=? AND status='pending'",
                (self._now().isoformat(), request_id, task_id),
            )
        changed = self.get(request_id)
        if changed is not None and self._on_change is not None:
            self._on_change(changed)

    def for_fingerprint(
        self,
        task_id: str,
        fingerprint: str,
    ) -> ApprovalRequest | None:
        self.expire_due()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE task_id=? AND fingerprint=?
                """,
                (task_id, fingerprint),
            ).fetchone()
        return self._from_row(row)

    def list(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        self.expire_due()
        conditions: list[str] = []
        values: list[object] = []
        if task_id is not None:
            conditions.append("task_id=?")
            values.append(task_id)
        if status is not None:
            conditions.append("status=?")
            values.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT * FROM approval_requests"
            + where
            + " ORDER BY requested_at DESC LIMIT ?"
        )
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [request for row in rows if (request := self._from_row(row))]

    def decide(
        self,
        request_id: str,
        *,
        decision: Literal["approved", "denied"],
        actor: str,
        note: str | None = None,
    ) -> ApprovalRequest:
        return self.submit_decision(
            ApprovalDecision(
                request_id=request_id,
                decision=decision,
                actor=actor,
                note=note,
            )
        )

    def submit_decision(self, decision: ApprovalDecision) -> ApprovalRequest:
        self.expire_due()
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (decision.request_id,),
            ).fetchone()
            request = self._from_row(row)
            if request is None:
                raise KeyError(decision.request_id)
            if request.status in {"approved", "denied"}:
                if request.status != decision.decision:
                    raise ValueError(
                        f"approval request already decided as {request.status}"
                    )
                return request
            if request.status != "pending":
                raise ValueError(
                    f"cannot decide approval request in status {request.status}"
                )
            connection.execute(
                """
                UPDATE approval_requests
                SET status=?, decided_at=?, decided_by=?, decision_note=?
                WHERE request_id=?
                """,
                (
                    decision.decision,
                    now,
                    decision.actor,
                    decision.note,
                    decision.request_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM approval_requests WHERE request_id=?",
                (decision.request_id,),
            ).fetchone()
        result = self._from_row(updated)
        assert result is not None
        if self._on_change is not None:
            self._on_change(result)
        return result

    def is_approved(self, task_id: str, fingerprint: str) -> bool:
        request = self.for_fingerprint(task_id, fingerprint)
        return request is not None and request.status == "approved"
