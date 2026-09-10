"""Dependency-free canonical JSONL ledger for local single-process use."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import Counter
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import EffectStatus, EventStatus, LedgerEvent, canonical_json

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class JsonlLedgerStore:
    """One fsync-backed append-only event file using the canonical ledger schema."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.path, threading.RLock())
        # ponytail: scans are O(total events); select DuckDB when local history makes
        # append or retrieval latency exceed the configured operational budget.

    def _all(self) -> list[LedgerEvent]:
        if not self.path.exists():
            return []
        return [
            LedgerEvent.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

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
        with self._lock:
            events = self._all()
            for previous in events:
                if (
                    previous.source == source
                    and previous.source_id == source_id
                    and (previous.task_id != task_id or previous.idempotency_key != key)
                ):
                    raise ValueError("ledger source identity reused by another event")
                if previous.task_id == task_id and previous.idempotency_key == key:
                    if (
                        previous.source != source
                        or previous.source_id != source_id
                        or previous.kind != kind
                        or previous.status != status
                        or previous.effect != effect
                        or previous.payload != body
                    ):
                        raise ValueError("ledger idempotency key reused for different content")
                    return previous
            task_events = [event for event in events if event.task_id == task_id]
            event = LedgerEvent(
                event_id=hashlib.sha256(f"{task_id}\0{key}".encode()).hexdigest(),
                task_id=task_id,
                sequence=(task_events[-1].sequence + 1 if task_events else 1),
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
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(event.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return event

    def read(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        as_of: datetime | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 100_000,
    ) -> list[LedgerEvent]:
        selected_kinds = set(kinds or ())
        return [
            event
            for event in self._all()
            if event.task_id == task_id
            and event.sequence > max(after_sequence, 0)
            and (as_of is None or event.observed_at <= as_of)
            and (not selected_kinds or event.kind in selected_kinds)
        ][: max(limit, 0)]

    def task_ids(self) -> list[str]:
        return sorted({event.task_id for event in self._all()})

    def source_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(event.source for event in self._all()).items()))

    def content_hash(self, task_id: str) -> str:
        records = [event.model_dump(mode="json") for event in self.read(task_id)]
        return hashlib.sha256(canonical_json(records).encode()).hexdigest()

    def erase_task(self, task_id: str) -> int:
        with self._lock:
            events = self._all()
            kept = [event for event in events if event.task_id != task_id]
            removed = len(events) - len(kept)
            if not removed:
                return 0
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    for event in kept:
                        stream.write(event.model_dump_json() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except BaseException:
                with suppress(FileNotFoundError):
                    os.unlink(temporary)
                raise
            return removed
