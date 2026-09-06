"""Killable optional semantic projection work over already-authorized evidence."""

from __future__ import annotations

import multiprocessing
import pickle
from multiprocessing.connection import Connection
from typing import Literal

from harness.ledger.models import LedgerEvent

from .lance import LanceMemorySearch


def _search(sender: Connection, search: LanceMemorySearch, events: list[LedgerEvent],
            query: str, mode: Literal["semantic", "hybrid"]) -> None:
    try:
        # Ordinal interleave, not a claim that independent task scores are calibrated.
        ranked = []
        for task in sorted({event.task_id for event in events}):
            ids = search.search([event for event in events if event.task_id == task], query,
                                limit=100, mode=mode)
            ranked.extend((rank, task, event_id) for rank, event_id in enumerate(ids))
        sender.send(("ok", [event_id for _, _, event_id in sorted(ranked)][:100]))
    except BaseException as exc:
        sender.send(("unavailable", type(exc).__name__))
    finally:
        sender.close()


def search_bounded(search: LanceMemorySearch, events: list[LedgerEvent], query: str,
                   mode: Literal["semantic", "hybrid"], timeout_seconds: float) -> list[str]:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_search, args=(sender, search, events, query, mode), daemon=True)
    try:
        process.start()
    except (TypeError, AttributeError, pickle.PickleError) as exc:
        receiver.close()
        sender.close()
        raise ValueError("semantic provider must be an importable, spawn-compatible callable") from exc
    sender.close()
    try:
        if not receiver.poll(max(0, timeout_seconds)):
            raise TimeoutError("semantic program deadline exceeded")
        status, value = receiver.recv()
        if status != "ok":
            raise ValueError(f"semantic provider unavailable: {value}")
        return value
    except EOFError as exc:
        raise ValueError("semantic worker exited without a result") from exc
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
