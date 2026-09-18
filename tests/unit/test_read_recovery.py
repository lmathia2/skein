import hashlib
import json
from pathlib import Path

import pytest

from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.memory import MemoryProgramRuntime, ViewRequest
from harness.execution.tools.adk_adapter import _ArtifactResolver
from harness.execution.tools.memory import ContextProgramService
from harness.ptc.notebook.artifacts import put_artifact


def setup_read(tmp_path: Path, text="alpha\nβeta\ngamma\n", *, start=41, total_lines=None):
    store = JsonlLedgerStore(tmp_path / "ledger")
    evidence = {"path": "src/example.py", "sha256": "a" * 64,
                "offset": start, "returned_lines": len(text.splitlines())}
    content = json.dumps({"status": "ok", "data": {**evidence, "text": text, "total_lines": total_lines}}).encode()
    uri = put_artifact(tmp_path / "artifacts" / "sha256", content)
    event = store.append(task_id="task", source="harness_event", source_id="read",
                         kind="capability.completed", payload={"operation": "fs.read",
                         "status": "ok", "read_evidence": evidence,
                         "result_artifact_uri": uri, "artifact_refs": [uri]})
    resolver = _ArtifactResolver(workspace=tmp_path / "workspace", state_root=tmp_path)
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",),
                                   read_result_reader=resolver.recover_read,
                                   artifact_reader=lambda uri, offset, limit: resolver.read(uri, offset=offset, limit=limit))
    return store, event, uri, resolver, runtime


def test_lookup_and_exact_range_recovery_without_workspace_or_worker(tmp_path: Path):
    store, event, uri, resolver, runtime = setup_read(tmp_path)
    lookup = runtime.compute(ViewRequest(task_id="task", program="reads.lookup", path="src/example.py"))
    assert lookup.evidence_event_ids == (event.event_id,)
    assert lookup.data["events"][0]["payload"]["result_artifact_uri"] == uri
    assert runtime.compute(ViewRequest(task_id="task", program="history.page", query="src/example.py")).evidence_event_ids == (event.event_id,)
    assert not runtime.compute(ViewRequest(task_id="task", program="reads.lookup", source_sha256="b" * 64)).evidence_event_ids
    service = ContextProgramService(store, "task", read_result_reader=resolver.recover_read)
    recovered = service.execute(f"memory query --program read.recover --event-id {event.event_id} --offset 2 --limit 1")
    assert recovered["status"] == "ok"
    assert recovered["data"]["text"] == "βeta\n"
    assert recovered["data"]["source_offset"] == 42
    assert recovered["data"]["freshness"] == "historical_snapshot"
    assert recovered["data"]["complete_scope"] == "selected_capture_page"
    assert recovered["data"]["source_coverage"] == {"total_lines": None, "whole_file": None, "next_unread_offset": 1}
    assert not (tmp_path / "workspace").exists()
    assert runtime.compute(ViewRequest(task_id="task", program="artifact.read", event_id=event.event_id, artifact_uri=uri)).status == "ok"
    assert runtime.compute(ViewRequest(task_id="task", program="read.recover", artifact_uri=uri)).data["text"] == "alpha\nβeta\ngamma\n"


def test_scope_identity_corruption_and_missing_artifact(tmp_path: Path):
    store, event, uri, _resolver, runtime = setup_read(tmp_path)
    request = ViewRequest(task_id="task", program="read.recover", event_id=event.event_id)
    assert runtime.compute(request.model_copy(update={"source_tasks": ("other",)})).status == "denied"
    assert runtime.compute(request.model_copy(update={"artifact_uri": "artifact://sha256/" + "b" * 64})).status == "unavailable"
    mismatched = store.append(task_id="task", source="harness_event", source_id="mismatch",
                             kind="capability.completed", payload={**event.payload,
                             "read_evidence": {**event.payload["read_evidence"], "sha256": "b" * 64}})
    assert runtime.compute(request.model_copy(update={"event_id": mismatched.event_id})).status == "unavailable"
    artifact = tmp_path / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]
    artifact.write_bytes(b"corrupt")
    assert runtime.compute(request).status == "unavailable"
    artifact.unlink()
    assert runtime.compute(request).status == "unavailable"


@pytest.mark.parametrize(("start", "total", "whole", "next_unread"), [
    (1, 24, False, 4), (1, 3, True, None), (4, 6, False, 1),
])
def test_complete_recovery_page_does_not_claim_complete_source(tmp_path, start, total, whole, next_unread):
    _, event, _, _, runtime = setup_read(tmp_path, start=start, total_lines=total)
    recovered = runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=event.event_id))
    assert recovered.status == "ok" and recovered.data["complete"] is True
    assert recovered.data["source_coverage"] == {"total_lines": total, "whole_file": whole, "next_unread_offset": next_unread}


@pytest.mark.parametrize("total", [True, -1, 2, "24"])
def test_contradictory_source_coverage_fails_closed(tmp_path, total):
    _, event, _, _, runtime = setup_read(tmp_path, start=1, total_lines=total)
    recovered = runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=event.event_id))
    assert recovered.status == "unavailable"


def test_unicode_byte_paging_long_line_is_exact_and_bounded(tmp_path: Path):
    text = "β🪢" * 2000 + "\n"
    _, event, _, _, runtime = setup_read(tmp_path, text)
    offset = 0
    pieces = []
    while True:
        result = runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=event.event_id,
                                            byte_offset=offset, max_bytes=1000))
        assert len(json.dumps(result.data, ensure_ascii=False).encode()) <= 1000
        pieces.append(result.data["text"])
        if result.status == "ok":
            break
        assert result.status == "partial"
        assert result.data["next_byte_offset"] > offset
        offset = result.data["next_byte_offset"]
    assert "".join(pieces) == text
    malformed = runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=event.event_id, byte_offset=1))
    assert malformed.status == "unavailable"
    assert runtime.compute(ViewRequest(task_id="task", program="read.recover", event_id=event.event_id, max_bytes=128)).status == "partial"


def test_artifact_resolution_rejects_escaped_symlink_and_large_source(tmp_path: Path):
    _, _, uri, resolver, _ = setup_read(tmp_path)
    with pytest.raises(ValueError, match="byte limit"):
        resolver._read_content(uri, max_source_bytes=1)
    external = tmp_path / "outside"
    external.write_bytes(b"outside")
    digest = hashlib.sha256(b"outside").hexdigest()
    (tmp_path / "artifacts" / "sha256" / digest).symlink_to(external)
    with pytest.raises(ValueError, match="outside"):
        resolver._read_content(f"artifact://sha256/{digest}")


def test_prior_read_requires_both_ledger_scope_and_artifact_root(tmp_path: Path):
    prior = tmp_path / "prior"
    store, event, _, _, _ = setup_read(prior)
    current = tmp_path / "current"
    current_store = JsonlLedgerStore(current / "ledger")
    request = ViewRequest(task_id="current", source_tasks=("task",),
                          program="read.recover", event_id=event.event_id)
    resolver = _ArtifactResolver(workspace=current, state_root=current,
                                 authorized_state_roots=(prior,))
    runtime = MemoryProgramRuntime(current_store, authorized_tasks=("current", "task"),
                                   source_ledgers={"task": store},
                                   read_result_reader=resolver.recover_read)
    assert runtime.compute(request).data["text"] == "alpha\nβeta\ngamma\n"
    runtime.authorized_tasks = ("current",)
    assert runtime.compute(request).status == "denied"
    runtime.authorized_tasks = ("current", "task")
    runtime.read_result_reader = _ArtifactResolver(workspace=current, state_root=current).recover_read
    assert runtime.compute(request).status == "unavailable"
