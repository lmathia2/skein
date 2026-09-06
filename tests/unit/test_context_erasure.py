from __future__ import annotations

import json

import pytest

from harness.ledger import JsonlLedgerStore, LedgerBackedEventStore, erase_task_state
from harness.state import JsonlEventStore


def test_readonly_prior_replay_never_resurrects_erased_canonical_events(tmp_path):
    ledger = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    operational = JsonlEventStore(tmp_path / "events")
    store = LedgerBackedEventStore(operational, ledger)
    store.append("task", "message.recorded", {"role": "user", "content": "old fact"})
    ledger.erase_task("task")
    assert LedgerBackedEventStore(operational, ledger, repair=False).read("task") == []
    assert ledger.read("task") == []


def test_jsonl_erasure_invalidates_only_manifested_shared_notebooks(tmp_path):
    root = tmp_path / "runs" / "task-a"
    ledger = JsonlLedgerStore(root / "ledger.jsonl")
    ledger.append(task_id="task-a", source="test", source_id="one", kind="message.recorded")
    notebook = tmp_path / "conversations" / "owned" / "notebooks" / "one.ipynb"
    snapshot = tmp_path / "runs" / "task-b" / "artifacts" / "sha256" / ("a" * 64)
    keep = snapshot.with_name("b" * 64)
    for file, task in [(notebook, "task-a"), (snapshot, "task-a"), (keep, "task-c")]:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"metadata": {"agent": {"source_watermarks": {task: 1}}}}))
    with pytest.raises(ValueError, match="shared server state"):
        erase_task_state(root, task_id="task-a", ledger=ledger)
    assert ledger.read("task-a")
    result = erase_task_state(root, task_id="task-a", ledger=ledger, shared_state_root=tmp_path)
    assert result.ledger_rows == 1
    assert not notebook.exists() and not snapshot.exists()
    assert keep.exists()
    assert ledger.read("task-a") == []
