"""Durable JSONL event storage with idempotent appends."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from .events import HarnessEvent


class EventStore(Protocol):
    """Append-only event primitive consumed by the orchestration layer."""

    def read(self, task_id: str, *, after_sequence: int = 0) -> list[HarnessEvent]: ...

    def append(
        self, task_id: str, kind: str, payload: dict[str, Any] | None = None,
        *, idempotency_key: str | None = None,
    ) -> HarnessEvent: ...


class JsonlEventStore:
    """Store one append-only JSONL stream per task.

    The store is process-thread safe. Production deployments should put the same
    contract behind a transactional database when several processes can append to the
    same task concurrently.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, task_id: str) -> Path:
        digest = hashlib.sha256(task_id.encode()).hexdigest()
        return self.root / f"{digest}.jsonl"

    def read(self, task_id: str, *, after_sequence: int = 0) -> list[HarnessEvent]:
        path = self._path(task_id)
        if not path.exists():
            return []
        events: list[HarnessEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = HarnessEvent.model_validate_json(line)
            if event.task_id != task_id:
                raise ValueError("task stream contains a mismatched task_id")
            if event.sequence > after_sequence:
                events.append(event)
        return events

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent:
        with self._lock:
            existing = self.read(task_id)
            if idempotency_key is not None:
                for event in existing:
                    if event.idempotency_key == idempotency_key:
                        if event.kind != kind or event.payload != (payload or {}):
                            raise ValueError(
                                "idempotency key already used for different event content"
                            )
                        return event
            event = HarnessEvent(
                task_id=task_id,
                sequence=(existing[-1].sequence + 1 if existing else 1),
                kind=kind,
                payload=payload or {},
                idempotency_key=idempotency_key,
            )
            path = self._path(task_id)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(event.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return event
