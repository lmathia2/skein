from pathlib import Path

import duckdb

from harness.evidence.ledger import DuckDbLedgerStore, seal_task_events


def test_sealed_parquet_matches_hot_rows_at_watermark_and_is_reproducible(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    for index in range(1, 4):
        ledger.append(
            task_id="task",
            source="test",
            source_id=str(index),
            kind=f"event.{index}",
            payload={"index": index},
        )
    first = seal_task_events(
        database,
        task_id="task",
        destination=tmp_path / "segments" / "task-2.parquet",
        through_sequence=2,
    )
    second = seal_task_events(
        database,
        task_id="task",
        destination=tmp_path / "segments" / "task-2-copy.parquet",
        through_sequence=2,
    )
    assert first.row_count == first.through_sequence == 2
    assert first.content_sha256 == second.content_sha256
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT sequence, kind FROM read_parquet(?) ORDER BY sequence",
            [str(first.path)],
        ).fetchall()
    assert rows == [(1, "event.1"), (2, "event.2")]
