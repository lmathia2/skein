from pathlib import Path

import pytest

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from harness.core.config import RuntimeBindings, load_harness_composition
from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.ledger.shadow import LedgerBackedEventStore
from harness.evidence.memory import MemoryProgramRuntime, ViewRequest
from harness.evidence.state import JsonlEventStore
from harness.execution.tools.adk_adapter import _ArtifactResolver


@pytest.mark.asyncio
async def test_four_tool_read_retains_addressed_result_without_expanding_prompt(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.py").write_text("alpha\nbeta\n")
    state = tmp_path / "state"
    ledger = JsonlLedgerStore(state / "ledger.jsonl")
    events = LedgerBackedEventStore(JsonlEventStore(state / "events"), ledger)
    composition = load_harness_composition()
    settings = settings_from_composition(composition, RuntimeBindings(
        workspace=workspace, state_root=state, task_id="task"))
    worker = build_coding_worker(settings, "test-model", event_store=events, capture_read_evidence=True)
    response = await worker.read("source.py", offset=2, limit=1)
    assert "data" not in response
    assert "result_artifact_uri" not in response
    captured = next(event for event in ledger.read("task") if event.kind == "read.observed")
    assert captured.payload["read_evidence"]["offset"] == 2
    assert captured.payload["source_coverage"] == {"total_lines": 2, "whole_file": False, "next_unread_offset": 1}
    (workspace / "source.py").write_text("changed\n")
    runtime = MemoryProgramRuntime(ledger, authorized_tasks=("task",),
        read_result_reader=_ArtifactResolver(workspace=workspace, state_root=state).recover_read)
    lookup = runtime.compute(ViewRequest(task_id="task", program="reads.lookup", path="source.py"))
    assert lookup.evidence_event_ids == (captured.event_id,)
    recovered = runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=captured.event_id))
    assert recovered.data["text"] == "beta\n"
    assert recovered.data["freshness"] == "historical_snapshot"
