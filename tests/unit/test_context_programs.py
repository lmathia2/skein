from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.ledger import DuckDbLedgerStore, JsonlLedgerStore
from harness.memory import MemoryProgramRuntime, ViewRequest, available_programs
from harness.memory.context import bounded_events
from harness.tools.memory import ContextProgramService


def seed(store, n=6, task="task"):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [store.append(task_id=task, source="context", source_id=f"{task}-{i}",
                         kind="context.history", observed_at=start + timedelta(seconds=i),
                         recorded_at=start + timedelta(seconds=i + 10),
                         payload={"role": "user", "parts": [{"text": f"fact {i}"}]})
            for i in range(n)]


def test_configured_program_versions_restrict_the_live_service(tmp_path: Path):
    from harness.config.models import ContextProgramConfig

    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    seed(store)
    config = ContextProgramConfig(mode="active", programs={"events.count": 1})
    service = ContextProgramService(store, "task", **config.model_dump())
    assert service.execute("memory query --program events.count")["status"] == "ok"
    assert service.execute("memory history")["status"] == "unavailable"
    assert service.execute("memory query --program events.count --version 2")["status"] == "unavailable"
    for programs in (
        {"events.count": 2}, {"tools.usage": 2}, {"unknown": 1},
        {"failures.by_kind": 1},
    ):
        with pytest.raises(NotImplementedError):
            ContextProgramConfig(mode="active", programs=programs)
    assert ContextProgramConfig().programs is None
    assert ContextProgramConfig(mode="active", reuse=True, programs={"failures.by_kind": 1})
    assert ContextProgramConfig(mode="active", programs={"tools.usage": 1})


def test_one_registry_controls_standard_and_reviewed_programs() -> None:
    assert available_programs(reuse=False, model_visible=True) == {
        "artifact.read": 1,
        "event.read": 1,
        "events.count": 1,
        "history.page": 1,
        "tools.usage": 1,
    }
    assert available_programs(reuse=True, model_visible=True) == {
        **available_programs(reuse=False, model_visible=True),
        "failures.by_kind": 1,
    }


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


@pytest.mark.parametrize("backend", ["jsonl", "duckdb"])
def test_tool_usage_view_separates_top_level_nested_and_native(tmp_path: Path, backend: str):
    store = (
        JsonlLedgerStore(tmp_path / "ledger.jsonl")
        if backend == "jsonl"
        else DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    )
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    def append(source_id, kind, payload):
        store.append(
            task_id="task", source="metric" if kind == "metric.tool" else "harness_event",
            source_id=source_id, kind=kind, payload=payload,
            observed_at=timestamp, recorded_at=timestamp, idempotency_key=source_id,
        )

    append("execute", "metric.tool", {
        "invocation_id": "ptc", "tool_name": "execute_code", "status": "ok",
        "model_visible_bytes": 100, "omitted_bytes": 10,
    })
    append("nested-read", "metric.tool", {
        "invocation_id": "ptc", "tool_name": "read", "status": "ok",
        "model_visible_bytes": 50, "omitted_bytes": 0,
    })
    append("direct-bash", "metric.tool", {
        "invocation_id": "direct", "tool_name": "bash", "status": "error",
        "model_visible_bytes": 20, "omitted_bytes": 5, "raw_args": "secret-value",
    })
    append("notebook-write", "capability.completed", {
        "operation": "fs.write", "status": "ok", "effect": "changed",
        "omitted_bytes": 3,
    })
    append("prime", "prime.cell_terminal", {
        "effect": "native_untracked", "result": {"status": "ok"},
    })

    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    result = runtime.compute(ViewRequest(task_id="task", program="tools.usage"))

    assert result.data["count"] == 4
    assert result.data["top_level"] == {
        "count": 2, "by_name": {"bash": 1, "execute_code": 1},
        "by_status": {"error": 1, "ok": 1},
    }
    assert result.data["nested"] == {
        "count": 2, "by_name": {"fs.write": 1, "read": 1},
        "by_status": {"ok": 2},
    }
    assert result.data["native_untracked_cells"] == 1
    assert result.data["model_visible_bytes"] == 170
    assert result.data["omitted_bytes"] == 18
    assert runtime.compute(
        ViewRequest(task_id="task", program="tools.usage", query="write")
    ).data["count"] == 1
    failed = runtime.compute(
        ViewRequest(task_id="task", program="tools.usage", statuses=("error",))
    )
    assert failed.data["top_level"]["by_name"] == {"bash": 1}
    history = runtime.compute(
        ViewRequest(task_id="task", program="history.page", kinds=("metric.tool",))
    )
    assert "secret-value" not in history.model_dump_json()


def test_duckdb_count_projection_tracks_appends_restart_and_erasure(tmp_path: Path):
    path = tmp_path / "ledger.duckdb"
    store = DuckDbLedgerStore(path)
    seed(store, 3)
    store.append(task_id="task", source="context", source_id="failed",
                 kind="action.recorded", status="failed")
    runtime = MemoryProgramRuntime(DuckDbLedgerStore(path), authorized_tasks=("task",), reuse=True)
    assert runtime.compute(ViewRequest(task_id="task", program="events.count")).data["count"] == 4
    failures = runtime.compute(ViewRequest(task_id="task", program="failures.by_kind"))
    assert failures.data["by_kind"] == {"action.recorded": 1}
    store.erase_task("task")
    assert store.event_counts("task") == (0, "", [])


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
