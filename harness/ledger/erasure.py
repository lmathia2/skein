"""Explicit physical erasure across local trace-native authorities."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict

from .base import LedgerStore
from .factory import open_ledger


class ErasureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id_sha256: str
    ledger_rows: int
    sqlite_rows: int
    files: tuple[str, ...]


_SQLITE_TARGETS = {
    "managed-tools.db": (("tool_receipts", "task_id"),),
    "approvals.db": (("approval_requests", "task_id"),),
    "metrics.db": (
        ("model_usage", "task_id"),
        ("tool_usage", "task_id"),
        ("task_outcomes", "task_id"),
    ),
    "traces.db": (("trace_spans", "task_id"),),
    "state.db": (("checkpoints", "task_id"), ("steering_messages", "task_id")),
    "adk/sessions.db": (("events", "session_id"), ("sessions", "id")),
    "server/runs.db": (("public_run_events", "run_id"), ("agent_runs", "run_id")),
}


def _delete_sqlite(database: Path, targets: tuple[tuple[str, str], ...], value: str) -> int:
    if not database.exists():
        return 0
    deleted = 0
    with sqlite3.connect(database) as connection:
        for table, column in targets:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is not None:
                cursor = connection.execute(
                    f'DELETE FROM "{table}" WHERE "{column}"=?', (value,)
                )
                deleted += max(cursor.rowcount, 0)
        connection.commit()
        connection.execute("VACUUM")
    return deleted


def _artifact_uris(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_artifact_uris(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_artifact_uris(item) for item in value), set())
    if isinstance(value, str) and value.startswith(("artifact://", "file://")):
        return {value}
    return set()


def _artifact_path(root: Path, uri: str) -> Path | None:
    parsed = urlsplit(uri)
    if parsed.scheme == "artifact" and parsed.netloc == "sha256":
        candidate = root / "artifacts" / "sha256" / unquote(parsed.path).lstrip("/")
    elif parsed.scheme == "file" and not parsed.netloc:
        candidate = Path(unquote(parsed.path))
    else:
        return None
    resolved = candidate.resolve()
    return resolved if resolved.is_relative_to(root) else None


def erase_task_state(
    state_root: Path,
    *,
    task_id: str,
    ledger: LedgerStore | None = None,
    shared_state_root: Path | None = None,
) -> ErasureResult:
    """Erase one exact task; callers must separately authorize this destructive action."""

    root = state_root.resolve()
    shared = shared_state_root.resolve() if shared_state_root else root
    if root.parent.name == "runs" and shared_state_root is None:
        raise ValueError("server-run erasure requires the shared server state root")
    if shared != root and root.parent != shared / "runs":
        raise ValueError("run state is not a direct child of the shared server root")
    if ledger is None and not any((root / filename).exists() for filename in (
        "ledger.jsonl", "ledger.duckdb"
    )):
        raise ValueError("no canonical ledger exists at the requested state root")
    active_ledger = ledger or open_ledger(
        root, "jsonl" if (root / "ledger.jsonl").exists() else "duckdb"
    )
    task_events = active_ledger.read(task_id)
    referenced = set().union(*(_artifact_uris(event.payload) for event in task_events), set())
    retained: set[str] = set()
    for other_task in active_ledger.task_ids():
        if other_task != task_id:
            for event in active_ledger.read(other_task):
                retained.update(_artifact_uris(event.payload))
    ledger_rows = active_ledger.erase_task(task_id)
    sqlite_rows = sum(
        _delete_sqlite(root / relative, targets, task_id)
        for relative, targets in _SQLITE_TARGETS.items()
    )
    digest = hashlib.sha256(task_id.encode()).hexdigest()
    candidates = [
        root / "events" / f"{digest}.jsonl",
        root / "notebooks" / f"{digest[:32]}.ipynb",
        root / "prime" / digest,
        root / "memory-search" / digest,
        root / "memory" / "lance" / digest,
    ]
    candidates.extend(
        path
        for uri in sorted(referenced - retained)
        if (path := _artifact_path(root, uri)) is not None
    )
    for manifest in (root / "ledger-segments").glob("*.manifest.json"):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("task_id") == task_id:
            candidates.extend((manifest, manifest.with_suffix("").with_suffix("")))
    removed: list[str] = []
    for candidate in candidates:
        if not candidate.resolve().is_relative_to(root):
            continue
        if candidate.is_file():
            candidate.unlink()
        elif candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            continue
        removed.append(candidate.relative_to(root).as_posix())
    # Shared notebooks are disposable projections, including immutable snapshots.
    # Remove only files whose explicit source manifest names the erased task.
    shared_candidates = list((shared / "conversations").glob("*/notebooks/*.ipynb"))
    for run_root in sorted((shared / "runs").glob("*")):
        if not run_root.is_dir() or not run_root.resolve().is_relative_to(shared):
            continue
        shared_candidates.extend((run_root / "artifacts" / "sha256").glob("*"))
        shared_candidates.extend((run_root / "notebooks").glob("*.ipynb"))
        # The physical Lance projection embeds a single canonical task, so its
        # task-hash directory is an exact invalidation target across readers.
        for projection in (run_root / "memory-search" / digest,
                           run_root / "memory" / "lance" / digest):
            if projection.is_dir() and not projection.is_symlink():
                shutil.rmtree(projection)
                removed.append(projection.relative_to(shared).as_posix())
    for candidate in shared_candidates:
        if (not candidate.is_file() or candidate.is_symlink()
                or not candidate.resolve().is_relative_to(shared)):
            continue
        try:
            notebook = json.loads(candidate.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(notebook, dict):
            continue
        metadata = notebook.get("metadata")
        agent = metadata.get("agent") if isinstance(metadata, dict) else None
        if isinstance(agent, dict) and task_id in agent.get("source_watermarks", {}):
            candidate.unlink()
            removed.append(candidate.relative_to(shared).as_posix())
    return ErasureResult(
        task_id_sha256=digest,
        ledger_rows=ledger_rows,
        sqlite_rows=sqlite_rows,
        files=tuple(sorted(removed)),
    )
