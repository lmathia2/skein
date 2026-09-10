"""Configured canonical-ledger construction with lazy optional dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .base import LedgerStore
from .jsonl import JsonlLedgerStore


def open_ledger(
    state_root: Path, backend: Literal["jsonl", "duckdb"]
) -> LedgerStore:
    if backend == "jsonl":
        return JsonlLedgerStore(state_root / "ledger.jsonl")
    try:
        from .store import DuckDbLedgerStore
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB memory is configured; install skein[memory-duckdb]"
        ) from exc
    return DuckDbLedgerStore(state_root / "ledger.duckdb")
