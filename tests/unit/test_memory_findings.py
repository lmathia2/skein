import hashlib
import json
import shlex

import pytest

from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory import ViewRequest
from harness.evidence.memory.models import WorkingNoteInput
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


def test_note_schema_matches_write_validation_and_is_bounded_read_only(tmp_path):
    service, _, uri = service_at(tmp_path)
    before = service.ledger.read("task")
    response = service.execute("memory note schema")
    assert response["status"] == "ok" and response["effect"] == "none"
    assert response["version"] == 3
    assert response["input_schema"] == WorkingNoteInput.model_json_schema()
    assert response["budget_bytes"] == 8000
    contract = {k: v for k, v in response.items() if k not in {"status", "effect", "contract_sha256"}}
    assert response["contract_sha256"] == hashlib.sha256(canonical_json(contract).encode()).hexdigest()
    assert service.execute("memory note schema") == response
    assert service.ledger.read("task") == before
    assert len(canonical_json(response).encode()) <= service.max_result_bytes
    entry = {"id": "source-v1", "kind": "observation", "text": "Source finding", "evidence_refs": [uri]}
    for entries in ([{**entry, "id": "source.json"}], [{**entry, "kind": "todo"}], [entry] * 65):
        rejected = write(service, entries)
        assert rejected["status"] == "unavailable" and rejected["effect"] == "none"
        assert service.note_read()["version"] == 0
    accepted = write(service, [entry])
    assert accepted["status"] == "ok"
    assert service.execute("memory note schema") == response  # no task content in schema
    for mode, notes in (("off", True), ("shadow", True), ("active", False)):
        disabled = ContextProgramService(service.ledger, "task", mode=mode, working_notes=notes)
        assert disabled.execute("memory note schema")["status"] == "denied"
    service.max_result_bytes = 128
    limited = service.execute("memory note schema")
    assert limited["status"] == "unavailable" and "input_schema" not in limited
    assert len(canonical_json(limited).encode()) <= 128


def test_findings_survive_restart_text_updates_and_idempotent_retries(tmp_path):
    service, event, uri = service_at(tmp_path)
    entry = {"id": "parser", "kind": "observation", "text": "Parser dispatch is in src/a.py",
             "evidence_refs": [uri], "task_links": ["criterion-1"], "related_paths": ["src/a.py"]}
    note = write(service, [entry])
    assert note["status"] == "ok"
    retained = service.note_read()
    assert retained["entries"][0]["source_dependencies"] == [event.payload["read_evidence"]]
    assert "request_hash" not in note
    assert write(service, [entry]) == note
    assert write(service, [{**entry, "text": "changed"}])["status"] == "unavailable"
    assert service.note_write(text="new checkpoint", expected_version=1, operation_id="text")["version"] == 2
    assert service.note_read()["entries"] == retained["entries"]
    restarted = ContextProgramService(service.ledger, "task", working_notes=True)
    view = restarted.runtime.compute(ViewRequest(task_id="task", program="working_set"))
    finding = view.data["findings"][0]
    assert finding["finding"]["id"] == "parser" and finding["revision"] == 1
    assert finding["authority"] == "advisory" and finding["freshness"] == "historical_snapshot"
    assert write(restarted, [entry]) == note  # retry after later revisions is the original receipt


def test_note_commit_receipt_is_small_and_full_published_evidence_is_recoverable(tmp_path):
    service, _, uri = service_at(tmp_path)
    published = []
    service.on_note = published.append
    entries = [{"id": f"source_{i}", "kind": "observation", "text": f"Finding {i}: " + "evidence " * 70,
                "evidence_refs": [uri]} for i in range(4)]
    receipt = write(service, entries)
    assert receipt["status"] == "ok" and receipt["receipt_version"] == 1
    assert receipt["entry_count"] == 4 and receipt["version"] == 1
    assert not {"text", "entries", "evidence_event_ids", "request_hash"}.intersection(receipt)
    note = service.note_read()
    assert published == [note] and len(note["entries"]) == 4
    assert len(canonical_json(receipt).encode()) < 512
    assert len(canonical_json(receipt).encode()) < len(canonical_json(note).encode()) / 5
    event = next(e for e in service.ledger.read("task") if e.event_id == receipt["event_id"])
    assert receipt["payload_hash"] == event.payload_hash
    assert hashlib.sha256(canonical_json(event.payload).encode()).hexdigest() == receipt["payload_hash"]
    assert service.execute(receipt["recovery"]["latest"]) == note
    def recover_committed():
        offset, chunks = 0, []
        while offset is not None:
            page = service.execute(receipt["recovery"]["committed"] + f" --byte-offset {offset} --byte-limit 1000")
            assert page["status"] in {"ok", "partial"}
            chunks.append(page["data"]["text"])
            offset = page["data"]["next_byte_offset"]
        return json.loads("".join(chunks))["payload"]
    assert recover_committed()["entries"] == note["entries"]
    service.note_write(text="next checkpoint", expected_version=1, operation_id="next")
    assert write(service, entries) == receipt  # exact prior acknowledgement, not latest version
    assert service.note_read()["version"] == 2
    assert recover_committed()["version"] == 1
    assert len([e for e in service.ledger.read("task") if e.kind == "memory.note"]) == 2


def test_legacy_note_retry_and_disabled_event_recovery_keep_receipt_honest(tmp_path):
    service, _, _ = service_at(tmp_path)
    legacy = service.ledger.append(task_id="task", source="context_note", source_id="task:legacy",
                                   kind="memory.note", status="completed", idempotency_key="memory-note:legacy",
                                   payload={"version": 1, "expected_version": 0, "text": "legacy", "evidence_event_ids": []})
    service.programs = {"history.page": 1}
    receipt = service.note_write(text="legacy", expected_version=0, operation_id="legacy")
    assert receipt["event_id"] == legacy.event_id and receipt["payload_hash"] == legacy.payload_hash
    assert receipt["recovery"] == {"latest": "memory note read"}
    assert service.note_read()["text"] == "legacy"
    assert len([e for e in service.ledger.read("task") if e.kind == "memory.note"]) == 1


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
    retained = service.note_read()
    assert "topsecret123" not in canonical_json(retained)
    huge = [{"id": f"e{i}", "kind": "hypothesis", "text": "x" * 900} for i in range(9)]
    rejected = write(service, huge, 1, "large")
    assert rejected["status"] == "unavailable" and rejected["effect"] == "none"
    assert rejected["required_bytes"] > rejected["budget_bytes"]
    assert service.note_read() == retained
    invalid = write(service, [{"id": "too-long", "kind": "hypothesis", "text": "x" * 1001}], 1, "invalid")
    assert invalid["effect"] == "none"
    assert service.note_read() == retained
    assert write(service, [{"id": "bad", "kind": "decision", "text": "x", "supersedes": ["absent"]}], 1, "bad")["status"] == "unavailable"
    assert service.note_read() == retained
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
    assert latest["entry_count"] == 1
    assert [item["finding"]["id"] for item in service.note_read()["entries"]] == ["b"]
    assert len([event for event in service.ledger.read("task") if event.kind == "memory.note"]) == 3


def test_revising_existing_ids_fits_budget_preserves_history_and_cas(tmp_path):
    service, source, uri = service_at(tmp_path)
    entries = [{"id": f"claim_{i}", "kind": "observation", "text": str(i) + "x" * 989,
                "evidence_refs": [uri]} for i in range(4)]
    initial = write(service, entries)
    assert initial["status"] == "ok"
    old_note = service.note_read()
    fresh_uri = "artifact://sha256/" + "c" * 64
    fresh_source = service.ledger.append(task_id="task", source="harness", source_id="new-read", kind="capability.completed",
        payload={**source.payload, "result_artifact_uri": fresh_uri,
                 "read_evidence": {**source.payload["read_evidence"], "sha256": "d" * 64}})
    updates = [{**e, "text": "revised " + "y" * 982, "evidence_refs": [fresh_uri]} for e in entries[:2]]
    parallel = [{**e, "id": e["id"] + "_current"} for e in updates]
    rejected = write(service, parallel, 1, "parallel")
    assert rejected["status"] == "unavailable" and rejected["effect"] == "none"
    assert rejected["required_bytes"] > rejected["budget_bytes"] == 8000
    assert service.note_read() == old_note
    receipt = write(service, updates, 1, "update")
    assert receipt["status"] == "ok" and receipt["entry_count"] == 4 and receipt["version"] == 2
    assert write(service, updates, 1, "update") == receipt
    assert write(service, updates, 1, "stale")["status"] == "conflict"
    notes = [e for e in service.ledger.read("task") if e.kind == "memory.note"]
    assert len(notes) == 2
    assert notes[0].payload["entries"] == old_note["entries"]
    assert len(canonical_json(notes[1].payload).encode()) <= 8000
    for entry in notes[1].payload["entries"][:2]:
        assert entry["source_dependencies"] == [fresh_source.payload["read_evidence"]]
        assert entry["finding"]["evidence_refs"] == [fresh_uri]
        assert entry["revision"] == 2
