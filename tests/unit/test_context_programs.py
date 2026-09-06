from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore, JsonlLedgerStore
from harness.memory import MemoryProgramRuntime, ViewRequest
from harness.memory.context import bounded_events
from harness.tools.memory import ContextProgramService


def seed(store, n=6, task="task"):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [store.append(task_id=task, source="context", source_id=f"{task}-{i}",
                         kind="context.history", observed_at=start + timedelta(seconds=i),
                         recorded_at=start + timedelta(seconds=i + 10),
                         payload={"role": "user", "parts": [{"text": f"fact {i}"}]})
            for i in range(n)]


def test_snapshot_paging_is_stable_and_erasure_invalidates(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    events = seed(store)
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    request = ViewRequest(task_id="task", program="history.page", limit=2)
    first = runtime.compute(request)
    assert first.status == "partial"
    assert first.evidence_event_ids == tuple(event.event_id for event in events[:2])
    seed(store, 1, "other")
    store.append(task_id="task", source="context", source_id="late", kind="context.history",
                 payload={"role": "user", "parts": [{"text": "late fact"}]})
    second = runtime.compute(request.model_copy(update={"cursor": first.next_cursor}))
    assert second.evidence_event_ids == tuple(event.event_id for event in events[2:4])
    assert second.source_manifest == first.source_manifest
    assert runtime.compute(request.model_copy(update={"cursor": first.next_cursor, "query": "fact"})).status == "unavailable"
    store.erase_task("task")
    assert runtime.compute(request.model_copy(update={"cursor": first.next_cursor})).status == "unavailable"


def test_complete_temporal_aggregate_and_private_exposure(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    seed(store, 30)
    private = store.append(task_id="task", source="adk_session", source_id="private",
                           kind="adk.event", payload={"thinking": "never expose"})
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    request = ViewRequest(task_id="task", program="events.count", limit=1)
    result = runtime.compute(request)
    assert result.data["count"] == 30
    assert result.data["complete"] is True
    assert result.evidence_event_ids == ()  # bounded provenance manifest, not all IDs
    earlier = runtime.compute(request.model_copy(update={"recorded_before": datetime(2026, 1, 1, 0, 0, 14, tzinfo=UTC)}))
    assert earlier.data["count"] == 5
    assert runtime.compute(ViewRequest(task_id="task", program="event.read", event_id=private.event_id)).status == "unavailable"
    assert runtime.compute(request.model_copy(update={"source_tasks": ("other",)})).status == "denied"
    assert runtime.compute(request.model_copy(update={"max_scan_events": 5})).status == "partial"
    with pytest.raises(ValueError, match="timezone"):
        ViewRequest(task_id="task", program="events.count", as_of=datetime(2026, 1, 1))


def test_command_receipts_notes_conflicts_restart_and_bounds(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    seed(store)
    service = ContextProgramService(store, "task", working_notes=True)
    assert service.execute("printf memory") is None
    assert service.execute("memory history; rm file")["status"] == "unavailable"
    assert service.execute("memory query --program history.model")["status"] == "unavailable"
    response = service.execute("memory query --program events.count")
    assert response["data"]["count"] == 6
    assert service.execute("memory query --program events.count") == response
    command = "memory note write --text 'remember password=topsecret123' --expected-version 0 --operation-id n1"
    note = service.execute(command)
    assert note["version"] == 1 and "topsecret" not in note["text"]
    assert service.execute(command) == note
    assert service.execute(command.replace("n1", "n2"))["status"] == "conflict"
    restarted = ContextProgramService(JsonlLedgerStore(store.path), "task", working_notes=True)
    assert restarted.note_read() == note
    assert len(restarted.handoff()["note_excerpt"]) <= 512
    assert ContextProgramService(store, "task", mode="shadow").execute("memory history")["status"] == "denied"
    assert ContextProgramService(store, "task", mode="shadow").shadow()["status"] == "ok"


def test_note_sink_repaired_on_retry(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    calls = []
    def sink(note):
        calls.append(note["event_id"])
        if len(calls) == 1:
            raise OSError("sink publication failed")
    service = ContextProgramService(store, "task", working_notes=True, on_note=sink)
    command = "memory note write --text 'next step' --expected-version 0 --operation-id n1"
    assert service.execute(command)["status"] == "unavailable"
    assert service.execute(command)["version"] == 1
    assert calls[0] == calls[1]
    assert len([e for e in store.read("task") if e.kind == "memory.note"]) == 1


def test_artifact_requires_exposed_reference_and_bounds(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    event = store.append(task_id="task", source="harness_event", source_id="artifact",
                         kind="tool.artifact_recorded", payload={"artifact_uri": "artifact://safe"})
    reads = []
    def read(uri, offset, limit):
        reads.append((uri, offset, limit))
        return {"model_text": "retained redacted bytes"}
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",), artifact_reader=read)
    request = ViewRequest(task_id="task", program="artifact.read", event_id=event.event_id,
                          artifact_uri="artifact://safe", offset=2, limit=3)
    assert runtime.compute(request).data["model_text"] == "retained redacted bytes"
    assert reads == [("artifact://safe", 2, 3)]
    assert runtime.compute(request.model_copy(update={"artifact_uri": "artifact://other"})).status == "unavailable"
    assert len(reads) == 1


def test_scope_mapping_reuse_deadline_and_output_budget(tmp_path: Path):
    current, prior = JsonlLedgerStore(tmp_path / "current"), JsonlLedgerStore(tmp_path / "prior")
    seed(current, 2)
    seed(prior, 3, "prior")
    runtime = MemoryProgramRuntime(current, authorized_tasks=("task", "prior"), source_ledgers={"prior": prior})
    request = ViewRequest(task_id="task", program="events.count", source_tasks=("task", "prior"))
    assert runtime.compute(request).data["count"] == 5
    assert runtime.compute(request.model_copy(update={"program": "failures.by_kind"})).status == "unavailable"
    runtime.reuse = True
    assert runtime.compute(request.model_copy(update={"program": "failures.by_kind"})).data["count"] == 0
    with pytest.raises(TimeoutError):
        bounded_events(current, ("task",), maximum=10, deadline=0)
    page = runtime.compute(ViewRequest(task_id="task", program="history.page", max_bytes=128))
    assert page.status == "partial" and "events" not in page.data
    prior.erase_task("prior")
    assert runtime.compute(request).status == "unavailable"
    restarted = MemoryProgramRuntime(current, authorized_tasks=("task", "prior"), source_ledgers={"prior": prior})
    assert restarted.compute(request).status == "unavailable"


def test_duckdb_context_backend_equality(tmp_path: Path):
    jsonl, duck = JsonlLedgerStore(tmp_path / "j"), DuckDbLedgerStore(tmp_path / "d")
    seed(jsonl)
    seed(duck)
    request = ViewRequest(task_id="task", program="events.count")
    a = MemoryProgramRuntime(jsonl, authorized_tasks=("task",)).compute(request)
    b = MemoryProgramRuntime(duck, authorized_tasks=("task",)).compute(request)
    assert a == b


def test_oversized_exact_event_is_recoverable_in_utf8_ranges(tmp_path: Path):
    from harness.ledger.models import canonical_json
    from harness.memory.context import project

    store = JsonlLedgerStore(tmp_path / "ledger")
    event = store.append(task_id="task", source="context", source_id="large", kind="context.history",
                         payload={"role": "user", "parts": [{"text": "évidence " * 500}]})
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    page = runtime.compute(ViewRequest(task_id="task", program="history.page", max_bytes=512))
    assert page.data["event_id"] == event.event_id
    offset, chunks = 0, []
    while offset is not None:
        part = runtime.compute(ViewRequest(task_id="task", program="event.read", event_id=event.event_id,
                                           byte_offset=offset, byte_limit=137, max_bytes=1024))
        chunks.append(part.data["text"])
        offset = part.data["next_byte_offset"]
    assert "".join(chunks) == canonical_json(project(event, runtime.redactor))
