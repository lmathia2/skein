"""Atomic sealed Parquet export for measured hot/cold tiering."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict, Field


class SealedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    through_sequence: int = Field(ge=0)
    row_count: int = Field(ge=0)
    path: Path
    content_sha256: str


def seal_task_events(
    database: Path,
    *,
    task_id: str,
    destination: Path,
    through_sequence: int | None = None,
) -> SealedSegment:
    """Write one immutable task segment; publish only after DuckDB closes it."""

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with duckdb.connect(str(database.resolve()), read_only=True) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM ledger_events WHERE task_id=?",
            [task_id],
        ).fetchone()
        assert row is not None
        watermark = min(int(row[0]), through_sequence) if through_sequence is not None else int(row[0])
        count_row = connection.execute(
            "SELECT COUNT(*) FROM ledger_events WHERE task_id=? AND sequence<=?",
            [task_id, watermark],
        ).fetchone()
        assert count_row is not None
        task_literal = task_id.replace("'", "''")
        path_literal = temporary.as_posix().replace("'", "''")
        connection.execute(
            f"""
            COPY (
                SELECT * FROM ledger_events
                WHERE task_id='{task_literal}' AND sequence<={watermark}
                ORDER BY sequence
            ) TO '{path_literal}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    temporary.replace(destination)
    segment = SealedSegment(
        task_id=task_id,
        through_sequence=watermark,
        row_count=int(count_row[0]),
        path=destination,
        content_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
    )
    manifest = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(segment.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return segment
