"""Conversation queries and follow-ups in the existing local run database.

No model loop lives here. Dispatch uses the same coordinator.start contract as a
normal turn; its idempotency key makes crash recovery safe after partial dispatch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from .protocol import SessionRequestMessage, StartTaskMessage
from .registry import RunRecord, SqliteRunEventStore

if TYPE_CHECKING:
    from .runtime import RunCoordinator


class ConversationStore:
    """Extension tables alongside agent_runs, not a second conversation history."""

    def __init__(self, runs: SqliteRunEventStore) -> None:
        self.runs = runs
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversation_followups (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    run_id TEXT REFERENCES agent_runs(run_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS followups_pending ON conversation_followups(user_id, thread_id, status, position)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversation_continuations (
                    user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL, item_id TEXT,
                    PRIMARY KEY(user_id, request_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.runs.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def latest(self, user_id: str, thread_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT run_id FROM agent_runs WHERE user_id=? AND thread_id=? ORDER BY created_at DESC, run_id DESC LIMIT 1", (user_id, thread_id)).fetchone()
        record = self.runs.get_run(row["run_id"]) if row is not None else None
        if record is None:
            raise KeyError("conversation not found")
        return record

    def threads(self, user_id: str) -> list[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT run_id FROM (
                    SELECT run_id, created_at, ROW_NUMBER() OVER (
                        PARTITION BY thread_id ORDER BY created_at DESC, run_id DESC
                    ) AS rank FROM agent_runs WHERE user_id=?
                ) WHERE rank=1 ORDER BY created_at DESC, run_id DESC LIMIT 100
            """, (user_id,)).fetchall()
        return [record for row in rows if (record := self.runs.get_run(row["run_id"])) is not None]

    def history(self, user_id: str, thread_id: str, before: str | None) -> list[RunRecord]:
        cursor = self.runs.get_run(before) if before is not None else None
        if before is not None and (cursor is None or cursor.user_id != user_id or cursor.thread_id != thread_id):
            raise KeyError("history cursor not found")
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT run_id FROM agent_runs WHERE user_id=? AND thread_id=?
                AND (? IS NULL OR (created_at, run_id) < (?, ?))
                ORDER BY created_at DESC, run_id DESC LIMIT 20
            """, (user_id, thread_id, before, cursor.created_at if cursor else None, before)).fetchall()
        return [record for row in rows if (record := self.runs.get_run(row["run_id"])) is not None]

    def memory_sources(self, user_id: str, thread_id: str) -> list[RunRecord]:
        """Freeze a bounded set of completed, owned runs; project names grant nothing."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM agent_runs WHERE user_id=? AND thread_id=? "
                "AND status IN ('completed', 'failed', 'cancelled') "
                "ORDER BY created_at DESC, run_id DESC LIMIT 100", (user_id, thread_id),
            ).fetchall()
        return [record for row in reversed(rows)
                if (record := self.runs.get_run(row["run_id"])) is not None]

    def enqueue(self, parent: RunRecord, request_id: str, content: str) -> str:
        item_id = hashlib.sha256(f"followup\0{parent.user_id}\0{request_id}".encode()).hexdigest()[:32]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT * FROM conversation_followups WHERE item_id=?", (item_id,)).fetchone()
            if prior is not None:
                if prior["thread_id"] != parent.thread_id or prior["content"] != content:
                    raise ValueError("follow-up retry key reused with different parameters")
                return item_id
            size = connection.execute("SELECT COUNT(*) FROM conversation_followups WHERE user_id=? AND thread_id=? AND status='pending'", (parent.user_id, parent.thread_id)).fetchone()[0]
            if size >= 20:
                raise ValueError("conversation already has 20 pending follow-ups")
            connection.execute("INSERT INTO conversation_followups(item_id, user_id, thread_id, parent_run_id, content) VALUES (?, ?, ?, ?, ?)", (item_id, parent.user_id, parent.thread_id, parent.run_id, content))
        return item_id

    def successor(self, user_id: str, thread_id: str, run_id: str) -> RunRecord | None:
        cursor = self.runs.get_run(run_id)
        if cursor is None or cursor.user_id != user_id or cursor.thread_id != thread_id:
            raise KeyError("run cursor not found")
        with self._connect() as connection:
            row = connection.execute("""
                SELECT run_id FROM agent_runs WHERE user_id=? AND thread_id=?
                AND (created_at, run_id) > (?, ?) ORDER BY created_at, run_id LIMIT 1
            """, (user_id, thread_id, cursor.created_at, run_id)).fetchone()
        return self.runs.get_run(row["run_id"]) if row is not None else None

    def pending(self, user_id: str, thread_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM conversation_followups WHERE user_id=? AND thread_id=? AND status='pending' ORDER BY position LIMIT 20", (user_id, thread_id))]

    def remove(self, user_id: str, thread_id: str, item_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM conversation_followups WHERE item_id=? AND user_id=? AND thread_id=?", (item_id, user_id, thread_id)).fetchone()
            if row is None:
                raise KeyError("follow-up not found")
            if row["status"] == "dispatched":
                raise ValueError("follow-up has started; cancel its run instead")
            connection.execute("UPDATE conversation_followups SET status='cancelled' WHERE item_id=?", (item_id,))

    def continuation_target(self, user_id: str, thread_id: str, request_id: str) -> str | None:
        """A retry authorizes the same pending turn, never the next one after it."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT thread_id, item_id FROM conversation_continuations WHERE user_id=? AND request_id=?", (user_id, request_id)).fetchone()
            if prior is not None:
                if prior["thread_id"] != thread_id:
                    raise ValueError("continuation retry key reused for a different conversation")
                return prior["item_id"]
            first = connection.execute("SELECT item_id FROM conversation_followups WHERE user_id=? AND thread_id=? AND status='pending' ORDER BY position LIMIT 1", (user_id, thread_id)).fetchone()
            target = first["item_id"] if first is not None else None
            connection.execute("INSERT INTO conversation_continuations VALUES (?, ?, ?, ?)", (user_id, request_id, thread_id, target))
            return target

    def dispatched(self, item_id: str, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE conversation_followups SET status='dispatched', run_id=? WHERE item_id=? AND status='pending'", (run_id, item_id))

    def run_for_key(self, user_id: str, key: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT run_id FROM agent_runs WHERE user_id=? AND idempotency_key=?", (user_id, key)).fetchone()
        return self.runs.get_run(row["run_id"]) if row is not None else None


class ConversationController:
    def __init__(self, coordinator: RunCoordinator) -> None:
        self.coordinator = coordinator
        self.store = ConversationStore(coordinator.store)
        self._lock = asyncio.Lock()
        self.closed = False

    def _same_binding(self, record: RunRecord) -> bool:
        current = self.coordinator.execution_factory.run_metadata
        return all(record.metadata.get(key) == current.get(key) for key in (
            "coding.workspace_identity", "coding.harness_implementation",
        ))

    def _owned(self, user_id: str, thread_id: str) -> RunRecord:
        record = self.store.latest(user_id, thread_id)
        if not self._same_binding(record):
            raise ValueError("conversation belongs to a different workspace or harness")
        return record

    def _summary(self, record: RunRecord, *, full_input: bool = False) -> dict[str, object]:
        limit = 50_000 if full_input else 1024
        return {"thread_id": record.thread_id, "run_id": record.run_id,
                "status": record.status, "input": self.coordinator.redactor.redact_text(record.input)[:limit],
                "input_truncated": len(record.input) > limit, "updated_at": record.updated_at}

    def snapshot(self, user_id: str, thread_id: str, before: str | None = None, *, history_requested: bool = True, after: str | None = None) -> dict[str, object]:
        latest = self._owned(user_id, thread_id)
        history = self.store.history(user_id, thread_id, before) if history_requested else []
        queue = [{"item_id": str(item["item_id"]), "preview": self.coordinator.redactor.redact_text(str(item["content"]))[:1024]} for item in self.store.pending(user_id, thread_id)]
        successor = self.store.successor(user_id, thread_id, after) if after is not None else None
        return {"thread_id": thread_id, "latest": self._summary(latest, full_input=True),
                "after_run_id": after, "next": self._summary(successor, full_input=True) if successor else None,
                "runs": [self._summary(record) for record in history], "queue": queue,
                "next_before_run_id": history[-1].run_id if len(history) == 20 else None}

    def transcript_page(self, message: SessionRequestMessage, user_id: str) -> dict[str, object]:
        """Read a bounded durable snapshot; never attach, acknowledge or rerun work."""
        assert message.thread_id is not None and message.run_id is not None
        self._owned(user_id, message.thread_id)
        record = self.coordinator.store.get_run(message.run_id)
        if record is None or record.user_id != user_id or record.thread_id != message.thread_id:
            raise KeyError("run not found in this conversation")
        if not self._same_binding(record):
            raise ValueError("run belongs to a different workspace or harness")
        cursor = message.after_sequence or 0
        page = self.coordinator.store.replay_page(record.run_id, after_sequence=cursor,
            high_water_sequence=message.high_water_sequence, limit=100)
        if cursor > page.high_water_sequence:
            raise ValueError("event cursor exceeds available history")
        events: list[object] = []
        size = 0
        for envelope in page.events:
            public = self.coordinator.redactor.redact(envelope.model_dump(mode="json"))
            event_size = len(json.dumps(public, ensure_ascii=False).encode("utf-8"))
            if event_size > 512_000:
                raise ValueError("history event exceeds the display page limit")
            if size + event_size > 512_000:
                break
            events.append(public)
            size += event_size
            cursor = envelope.sequence
        return {"thread_id": message.thread_id, "run": self._summary(record, full_input=True),
                "events": events, "high_water_sequence": page.high_water_sequence,
                "next_after_sequence": cursor if cursor < page.high_water_sequence else None}

    async def request(self, message: SessionRequestMessage, user_id: str) -> dict[str, object]:
        if message.operation == "list":
            records = [record for record in self.store.threads(user_id) if self._same_binding(record)]
            return {"conversations": [self._summary(record) for record in records], "limit": 100}
        assert message.thread_id is not None
        if message.operation == "events":
            return self.transcript_page(message, user_id)
        async with self._lock:
            parent = self._owned(user_id, message.thread_id)
            if message.operation == "follow_up":
                assert message.content is not None
                self.store.enqueue(parent, message.request_id, message.content)
            elif message.operation == "remove_follow_up":
                assert message.item_id is not None
                self.store.remove(user_id, message.thread_id, message.item_id)
            elif message.operation == "continue":
                target = self.store.continuation_target(user_id, message.thread_id, message.request_id)
                if target is not None:
                    await self._dispatch(user_id, message.thread_id, expected_item=target)
            # A request racing successful completion may arrive just after the
            # completion hook. Only advance automatically from a successful turn.
            if message.operation == "follow_up" and self._successful(parent):
                await self._dispatch(user_id, message.thread_id)
            return self.snapshot(user_id, message.thread_id, message.before_run_id,
                history_requested=message.operation == "get", after=message.after_run_id)

    def _successful(self, record: RunRecord) -> bool:
        if record.status != "completed":
            return False
        # The public terminal outcome distinguishes answered/complete from blocked.
        page = self.coordinator.store.replay_page(record.run_id, limit=1)
        tail = self.coordinator.store.replay(record.run_id, after_sequence=max(0, page.high_water_sequence - 1))
        result = tail[-1].event.result if tail else None
        return not isinstance(result, dict) or result.get("status") not in {"blocked", "failed", "cancelled"}

    async def after_turn(self, record: RunRecord) -> None:
        async with self._lock:
            current = self.coordinator.store.get_run(record.run_id)
            if current is not None and self._successful(current):
                await self._dispatch(record.user_id, record.thread_id)

    async def _dispatch(self, user_id: str, thread_id: str, *, expected_item: str | None = None) -> None:
        if self.closed:
            return
        latest = self._owned(user_id, thread_id)
        pending = self.store.pending(user_id, thread_id)
        if not pending:
            return
        item = pending[0]
        item_id = str(item["item_id"])
        if expected_item is not None and expected_item != item_id:
            return
        prior = self.store.run_for_key(user_id, f"followup:{item_id}")
        if prior is not None:
            # A crash between start and marking dispatched must not invoke again.
            self.store.dispatched(item_id, prior.run_id)
            return
        if latest.status in {"queued", "running"}:
            return
        message = StartTaskMessage(type="task.start", request_id=f"followup:{item_id}",
            idempotency_key=f"followup:{item_id}", thread_id=thread_id, input=str(item["content"]),
            metadata={"interactive_approvals": "true"} if latest.metadata.get("interactive_approvals") == "true" else {})
        try:
            record, _ = await self.coordinator.start(message, user_id=user_id, queued_follow_up=True)
        except Exception:
            # start persists its failure before raising. Reconcile on the next
            # explicit continuation; do not start later messages past a failure.
            return
        self.store.dispatched(item_id, record.run_id)
