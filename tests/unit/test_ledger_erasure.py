import hashlib
from pathlib import Path

from harness.ledger import DuckDbLedgerStore, erase_task_state, seal_task_events
from harness.state import JsonlEventStore
from harness.telemetry.metrics import MetricsStore, ModelUsageSample


def test_erasure_removes_task_ledger_operational_notebook_artifact_and_segment(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    ledger = DuckDbLedgerStore(state / "ledger.duckdb")
    artifact = state / "artifacts" / "sha256" / ("a" * 64)
    artifact.parent.mkdir(parents=True)
    artifact.write_text("secret", encoding="utf-8")
    ledger.append(
        task_id="task",
        source="test",
        source_id="1",
        kind="artifact",
        payload={"artifact_uri": f"artifact://sha256/{artifact.name}"},
    )
    ledger.append(task_id="other", source="test", source_id="2", kind="keep")
    JsonlEventStore(state / "events").append("task", "event")
    notebook_id = hashlib.sha256(b"task").hexdigest()[:32]
    notebook = state / "notebooks" / f"{notebook_id}.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_text("secret", encoding="utf-8")
    MetricsStore(state / "metrics.db").record_model_usage(
        ModelUsageSample(
            task_id="task",
            invocation_id="i",
            model="m",
            static_prefix_hash="h",
            static_prefix_tokens=0,
            dynamic_suffix_tokens=0,
            input_tokens=0,
            output_tokens=0,
        )
    )
    segment = seal_task_events(
        state / "ledger.duckdb",
        task_id="task",
        destination=state / "ledger-segments" / "task.parquet",
    )
    search_projection = state / "memory-search" / hashlib.sha256(b"task").hexdigest()
    search_projection.mkdir(parents=True)
    (search_projection / "data.lance").write_text("derived secret", encoding="utf-8")

    result = erase_task_state(state, task_id="task", ledger=ledger)

    assert result.ledger_rows == 1
    assert result.sqlite_rows == 1
    assert ledger.read("task") == []
    assert len(ledger.read("other")) == 1
    assert not artifact.exists()
    assert not notebook.exists()
    assert not segment.path.exists()
    assert not segment.path.with_suffix(".parquet.manifest.json").exists()
    assert not search_projection.exists()
