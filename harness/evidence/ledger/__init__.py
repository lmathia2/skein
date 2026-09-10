"""Canonical append-only evidence ledger."""

from typing import TYPE_CHECKING, Any

from .base import LedgerStore
from .factory import open_ledger
from .jsonl import JsonlLedgerStore
from .models import LedgerEvent
from .shadow import LedgerBackedEventStore

if TYPE_CHECKING:
    from .archive import SealedSegment, seal_task_events
    from .erasure import ErasureResult, erase_task_state
    from .store import DuckDbLedgerStore

__all__ = [
    "DuckDbLedgerStore",
    "ErasureResult",
    "JsonlLedgerStore",
    "LedgerBackedEventStore",
    "LedgerEvent",
    "LedgerStore",
    "SealedSegment",
    "erase_task_state",
    "open_ledger",
    "seal_task_events",
]


def __getattr__(name: str) -> Any:
    if name == "DuckDbLedgerStore":
        from .store import DuckDbLedgerStore

        return DuckDbLedgerStore
    if name in {"SealedSegment", "seal_task_events"}:
        from .archive import SealedSegment, seal_task_events

        return {"SealedSegment": SealedSegment, "seal_task_events": seal_task_events}[name]
    if name in {"ErasureResult", "erase_task_state"}:
        from .erasure import ErasureResult, erase_task_state

        return {"ErasureResult": ErasureResult, "erase_task_state": erase_task_state}[name]
    raise AttributeError(name)
