"""Compatibility adapter while operational projections migrate to the ledger."""

from __future__ import annotations

import threading
from typing import Any

from harness.state.event_store import EventStore
from harness.state.events import HarnessEvent

from .base import LedgerStore
from .importers import import_harness_event


class LedgerBackedEventStore:
    """Serve task events from the ledger while retaining the JSONL compatibility write."""

    def __init__(self, operational: EventStore, ledger: LedgerStore, *, repair: bool = True) -> None:
        self.operational = operational
        self.ledger = ledger
        self.repair = repair
        self._lock = threading.RLock()
        self._repaired_tasks: set[str] = set()

    def read(self, task_id: str, *, after_sequence: int = 0) -> list[HarnessEvent]:
        # Read-repair makes existing state safe to open before the one-time backfill CLI
        # is run. Appends dual-write after the first repair, so rescanning an append-only
        # stream on every notebook projection only adds quadratic work.
        with self._lock:
            if self.repair and task_id not in self._repaired_tasks:
                for event in self.operational.read(task_id):
                    import_harness_event(self.ledger, event)
                self._repaired_tasks.add(task_id)
            events = [
                event
                for event in self.ledger.read(task_id)
                if event.source == "harness_event"
            ]
        return [
            HarnessEvent(
                event_id=event.source_id,
                task_id=event.task_id,
                sequence=sequence,
                kind=event.kind,
                payload=event.payload,
                timestamp=event.observed_at,
                idempotency_key=(
                    None
                    if event.idempotency_key == f"harness:{event.source_id}"
                    else event.idempotency_key.removeprefix("harness:")
                ),
            )
            for sequence, event in enumerate(events, start=1)
            if sequence > after_sequence
        ]

    def append(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HarnessEvent:
        with self._lock:
            event = self.operational.append(
                task_id, kind, payload, idempotency_key=idempotency_key
            )
            import_harness_event(self.ledger, event)
            return event
