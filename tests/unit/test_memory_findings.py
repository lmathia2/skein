import json
import shlex

import pytest

from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory import ViewRequest
from harness.execution.tools.memory import ContextProgramService


def service_at(tmp_path, task="task"):
    store = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    uri = "artifact://sha256/" + "a" * 64
    event = store.append(task_id=task, source="harness", source_id="read", kind="capability.completed",
                         payload={"operation": "fs.read", "status": "ok", "result_artifact_uri": uri,
                                  "read_evidence": {"path": "src/a.py", "sha256": "b" * 64,
                                                    "offset": 10, "returned_lines": 20}})
    return ContextProgramService(store, task, working_notes=True), event, uri


def write(service, entries, version=0, operation="n1", text="checkpoint"):
    command = (f"memory note write --text {shlex.quote(text)} --expected-version {version} "
               f"--operation-id {operation} --entries {shlex.quote(json.dumps(entries))}")
    return service.execute(command)


def test_findings_survive_restart_text_updates_and_idempotent_retries(tmp_path):
    service, event, uri = service_at(tmp_path)
    entry = {"id": "parser", "kind": "observation", "text": "Parser dispatch is in src/a.py",
             "evidence_refs": [uri], "task_links": ["criterion-1"], "related_paths": ["src/a.py"]}
    note = write(service, [entry])
    assert note["status"] == "ok"
    assert note["entries"][0]["source_dependencies"] == [event.payload["read_evidence"]]
    assert "request_hash" not in note
    assert write(service, [entry]) == note
    assert write(service, [{**entry, "text": "changed"}])["status"] == "unavailable"
    assert service.note_write(text="new checkpoint", expected_version=1, operation_id="text")["entries"] == note["entries"]
    restarted = ContextProgramService(service.ledger, "task", working_notes=True)
    view = restarted.runtime.compute(ViewRequest(task_id="task", program="working_set"))
    finding = view.data["findings"][0]
    assert finding["finding"]["id"] == "parser" and finding["revision"] == 1
    assert finding["authority"] == "advisory" and finding["freshness"] == "historical_snapshot"
    assert write(restarted, [entry]) == note  # retry after later revisions is the original receipt


def test_observations_cannot_cite_private_forged_or_foreign_evidence(tmp_path):
    service, _, _ = service_at(tmp_path)
    private = service.ledger.append(task_id="task", source="adk_session", source_id="p",
                                    kind="adk.event", payload={"thinking": "private"})
    foreign = service.ledger.append(task_id="foreign", source="harness", source_id="f",
                                    kind="context.history", payload={"role": "user", "parts": []})
    for refs in ([], ["forged"], [private.event_id], [foreign.event_id]):
        result = write(service, [{"id": "x", "kind": "observation", "text": "claim", "evidence_refs": refs}])
        assert result["status"] == "unavailable"
    assert service.note_read()["version"] == 0
    assert write(service, [{"id": "x", "kind": "hypothesis", "text": "unproven"}])["status"] == "ok"


def test_revisions_conflicts_and_explicit_supersession_retain_history(tmp_path):
    service, event, _ = service_at(tmp_path)
    first = {"id": "a", "kind": "observation", "text": "initial claim", "evidence_refs": [event.event_id]}
    write(service, [first])
    conflict = {"id": "b", "kind": "hypothesis", "text": "alternate claim", "conflicts_with": ["a"]}
    assert write(service, [conflict], 1, "n2")["status"] == "ok"
    view = service.runtime.compute(ViewRequest(task_id="task", program="working_set"))
    assert {item["finding"]["status"] for item in view.data["findings"]} == {"disputed"}
    corrected = {"id": "c", "kind": "observation", "text": "corrected claim",
                 "evidence_refs": [event.event_id], "supersedes": ["a", "b"]}
    assert write(service, [corrected], 2, "n3")["status"] == "ok"
    current = service.runtime.compute(ViewRequest(task_id="task", program="working_set"))
    assert [item["finding"]["id"] for item in current.data["findings"]] == ["c"]
    old = service.runtime.compute(ViewRequest(task_id="task", program="working_set", watermark=2))
    assert old.data["findings"][0]["finding"]["text"] == "initial claim"
    assert len([event for event in service.ledger.read("task") if event.kind == "memory.note"]) == 3


def test_working_set_relevance_whole_entry_budget_and_prior_authorization(tmp_path):
    service, _, _ = service_at(tmp_path)
    write(service, [{"id": "old", "kind": "decision", "text": "relevant", "task_links": ["c1"]}])
    write(service, [{"id": "new", "kind": "next_action", "text": "recent"}], 1, "n2")
    request = ViewRequest(task_id="task", program="working_set", focus=("c1",), limit=1)
    view = service.runtime.compute(request)
    assert view.data["findings"][0]["finding"]["id"] == "old"
    assert view.data["omitted_count"] == 1 and view.status == "partial"
    tiny = service.runtime.compute(request.model_copy(update={"max_bytes": 128}))
    assert tiny.data["findings"] == [] and tiny.status == "partial"
    assert len(canonical_json(tiny.data).encode()) <= 128
    other = ContextProgramService(service.ledger, "other", authorized_tasks=("task",))
    prior = other.runtime.compute(request.model_copy(update={"task_id": "other", "source_tasks": ("task",)}))
    assert prior.data["findings"][0]["source_task_id"] == "task"
    assert ContextProgramService(service.ledger, "other").runtime.compute(
        request.model_copy(update={"task_id": "other", "source_tasks": ("task",)})
    ).status == "denied"
    service.ledger.erase_task("task")
    assert other.runtime.compute(request.model_copy(update={"task_id": "other", "source_tasks": ("task",)})).status == "unavailable"


def test_bounds_redaction_and_failed_update_keep_last_checkpoint(tmp_path):
    service, _, _ = service_at(tmp_path)
    note = write(service, [{"id": "x", "kind": "hypothesis", "text": "password=topsecret123"}])
    assert "topsecret123" not in canonical_json(note)
    huge = [{"id": f"e{i}", "kind": "hypothesis", "text": "x" * 900} for i in range(9)]
    assert write(service, huge, 1, "large")["status"] == "unavailable"
    assert service.note_read() == note
    assert write(service, [{"id": "bad", "kind": "decision", "text": "x", "supersedes": ["absent"]}], 1, "bad")["status"] == "unavailable"
    assert service.note_read() == note
    with pytest.raises(ValueError):
        ViewRequest(task_id="task", program="working_set", query="may revive older notes")


def test_findings_freshness_distinguishes_mutation_unknown_shell_and_fresh_read(tmp_path):
    service, event, uri = service_at(tmp_path)
    write(service, [{"id": "x", "kind": "observation", "text": "captured source", "evidence_refs": [uri]}])
    request = ViewRequest(task_id="task", program="working_set")
    initial = service.runtime.compute(request.model_copy(update={"watermark": 2}))
    def freshness():
        return service.runtime.compute(request).data["findings"][0]["freshness"]
    service.ledger.append(task_id="task", source="harness", source_id="edit", kind="capability.completed",
                          payload={"operation": "fs.edit", "changed_paths": ["src/a.py"],
                                   "content_hashes": {"src/a.py": "c" * 64}, "workspace_may_have_changed": True})
    assert freshness() == "changed_since_capture"
    service.ledger.append(task_id="task", source="harness", source_id="shell", kind="workspace.effect_observed",
                          payload={"operation": "shell.run", "workspace_may_have_changed": True})
    assert freshness() == "revalidation_required"
    service.ledger.append(task_id="task", source="harness", source_id="reread", kind="read.observed", payload=event.payload)
    assert freshness() == "historical_snapshot"  # observed original bytes again, never "currently fresh"
    assert service.runtime.compute(request.model_copy(update={"watermark": 2})) == initial


def test_superseded_entries_retire_without_erasing_old_versions(tmp_path):
    service, _, _ = service_at(tmp_path)
    write(service, [{"id": "a", "kind": "hypothesis", "text": "old"}])
    write(service, [{"id": "b", "kind": "decision", "text": "replacement", "supersedes": ["a"]}], 1, "n2")
    latest = service.note_write(text="checkpoint", expected_version=2, operation_id="n3")
    assert [item["finding"]["id"] for item in latest["entries"]] == ["b"]
    assert len([event for event in service.ledger.read("task") if event.kind == "memory.note"]) == 3
