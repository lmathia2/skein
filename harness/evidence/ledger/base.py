"""Storage-neutral canonical ledger contract."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from .models import EffectStatus, EventStatus, LedgerEvent


class LedgerStore(Protocol):
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
    ) -> LedgerEvent: ...

    def read(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        as_of: datetime | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 100_000,
    ) -> list[LedgerEvent]: ...

    def task_ids(self) -> list[str]: ...

    def source_counts(self) -> dict[str, int]: ...

    def content_hash(self, task_id: str) -> str: ...

    def erase_task(self, task_id: str) -> int: ...
