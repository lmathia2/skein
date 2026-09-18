import hashlib
import json
import shlex

import pytest

from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory import ViewRequest
from harness.evidence.memory.findings import source_freshness, source_observations
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
    assert response["schema_version"] == 7
    assert "version" not in response  # No task-note CAS revision in this read-only contract.
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
    wrong_version = write(service, [entry], version=response["schema_version"], operation="wrong-version")
    assert wrong_version == {"status": "conflict", "current_version": 0, "effect": "none"}
    assert service.ledger.read("task") == before
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


@pytest.mark.parametrize("case", ["stable", "prior_unknown", "intervening_unknown", "wrong_operation", "wrong_task",
                                  "missing_exit", "boolean_exit", "changed_workspace", "failed", "truncated", "malformed"])
def test_successful_validation_retracts_only_its_own_provisional_source_invalidation(case):
    need = {"path": "module.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 2}
    rows = []
    def add(kind, task="task", **payload):
        rows.append({"task_id": task, "sequence": len(rows) + 1, "kind": kind, "payload": payload})
    add("capability.completed", operation="fs.read", status="ok", read_evidence=need)
    if case == "prior_unknown":
        add("capability.failed", operation="shell.run", status="error", effect="unknown", workspace_may_have_changed=True)
    add("capability.failed" if case == "failed" else "capability.completed", operation="shell.run",
        operation_id="checked", status="error" if case == "failed" else "ok",
        effect="unknown" if case == "failed" else "observed", workspace_may_have_changed=True)
    if case == "intervening_unknown":
        add("workspace.effect_observed", operation="shell.run", workspace_may_have_changed=True)
    pending = source_freshness(need, source_observations(rows), "task")
    assert pending["status"] == "revalidation_required"
    result = {"status": "ok", "exit_code": None if case == "missing_exit" else False if case == "boolean_exit" else 0,
              "truncated": case == "truncated"}
    add("execution.validation_observed", task="other" if case == "wrong_task" else "task",
        operation_id="different" if case == "wrong_operation" else "checked",
        workspace_before="b" * 64, workspace_after="c" * 64 if case == "changed_workspace" else "b" * 64,
        result=[] if case == "malformed" else result)
    current = source_freshness(need, source_observations(rows), "task")
    assert current["status"] == ("historical_snapshot" if case == "stable" else "revalidation_required")


@pytest.mark.parametrize("case", ["stable", "prior_unknown", "intervening_unknown", "changed_workspace",
                                  "failed_result", "truncated", "wrong_receipt", "missing_receipt"])
def test_completed_shell_receipt_retracts_only_its_paired_request_invalidation(case):
    need = {"path": "module.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 2}
    rows = []
    def add(kind, row_status="observed", **payload):
        rows.append({"task_id": "task", "sequence": len(rows) + 1, "kind": kind,
                     "status": row_status, "payload": payload})
    add("capability.completed", operation="fs.read", status="ok", read_evidence=need)
    if case == "prior_unknown":
        add("capability.failed", operation="shell.run", effect="unknown", workspace_may_have_changed=True)
    add("capability.requested", row_status="started", operation="shell.run", operation_id="op",
        workspace_may_have_changed=True)
    if case == "intervening_unknown":
        add("workspace.effect_observed", operation="shell.run", workspace_may_have_changed=True)
    receipt_id = "b" * 64
    result = {"status": "error" if case == "failed_result" else "ok", "exit_code": 0,
              "truncated": case == "truncated", "omitted_bytes": 0}
    add("tool.bash", row_status="completed", task_id="task", tool_call_id=receipt_id,
        tool_name="bash", arguments_hash="c" * 64, status="completed",
        workspace_before="d" * 64,
        workspace_after="e" * 64 if case == "changed_workspace" else "d" * 64,
        result_json=json.dumps(result))
    add("capability.completed", row_status="completed", operation="shell.run", operation_id="op",
        receipt_id=None if case == "missing_receipt" else "f" * 64 if case == "wrong_receipt" else receipt_id,
        status="ok", effect="observed", workspace_may_have_changed=True)

    current = source_freshness(need, source_observations(rows), "task")
    assert current["status"] == ("historical_snapshot" if case == "stable" else "revalidation_required")


@pytest.mark.parametrize("case", ["unrelated", "source", "noop", "intervening", "failed", "missing_id"])
def test_completed_file_effect_replaces_only_its_request_wide_uncertainty(case):
    need = {"path": "module.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 2}
    rows = [{"task_id": "task", "sequence": 1, "kind": "capability.completed", "status": "completed",
             "payload": {"operation": "fs.read", "status": "ok", "read_evidence": need}}]
    operation_id = None if case == "missing_id" else "write-op"
    rows.append({"task_id": "task", "sequence": 2, "kind": "capability.requested", "status": "started",
                 "payload": {"operation": "fs.write", "operation_id": operation_id,
                             "workspace_may_have_changed": True}})
    if case == "intervening":
        rows.append({"task_id": "task", "sequence": 3, "kind": "workspace.effect_observed", "status": "observed",
                     "payload": {"operation": "shell.run", "workspace_may_have_changed": True}})
    changed = ["module.py" if case == "source" else "answer.json"]
    if case == "noop":
        changed = []
    rows.append({"task_id": "task", "sequence": len(rows) + 1, "kind": "capability.failed" if case == "failed" else "capability.completed",
                 "status": "failed" if case == "failed" else "completed",
                 "payload": {"operation": "fs.write", "operation_id": operation_id,
                             "status": "error" if case == "failed" else "ok",
                             "effect": "unknown" if case == "failed" else "observed" if case == "noop" else "changed",
                             "changed_paths": changed,
                             "content_hashes": {"answer.json": "b" * 64} if case == "noop" else {},
                             "workspace_may_have_changed": True}})

    current = source_freshness(need, source_observations(rows), "task")
    assert current["status"] == ("historical_snapshot" if case in {"unrelated", "noop"} else "revalidation_required")


def test_managed_memory_commands_do_not_invalidate_the_findings_they_manage():
    need = {"path": "module.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 2}
    rows = [
        {"task_id": "task", "sequence": 1, "kind": "capability.completed",
         "payload": {"operation": "fs.read", "status": "ok", "read_evidence": need}},
        {"task_id": "task", "sequence": 2, "kind": "capability.requested",
         "payload": {"operation": "shell.run", "discovery_kind": "memory",
                     "workspace_may_have_changed": False}},
        {"task_id": "task", "sequence": 3, "kind": "capability.completed",
         "payload": {"operation": "shell.run", "discovery_kind": "memory", "status": "ok",
                     "effect": "none", "workspace_may_have_changed": False}},
    ]

    assert source_freshness(need, source_observations(rows), "task")["status"] == "historical_snapshot"


@pytest.mark.parametrize("case", ["write", "edit", "prior_unknown", "failed", "unknown", "missing_hash", "bad_hash", "shell"])
def test_known_single_file_noop_does_not_invalidate_unrelated_findings(case):
    need = {"path": "source.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 2}
    rows = [{"task_id": "task", "sequence": 1, "kind": "read.observed",
             "payload": {"operation": "fs.read", "status": "ok", "read_evidence": need}}]
    if case == "prior_unknown":
        rows.append({"task_id": "task", "sequence": 2, "kind": "capability.failed",
                     "payload": {"operation": "shell.run", "effect": "unknown", "workspace_may_have_changed": True}})
    hashes = {} if case == "missing_hash" else {"answer.json": "invalid" if case == "bad_hash" else "b" * 64}
    rows.append({"task_id": "task", "sequence": len(rows) + 1,
                 "kind": "capability.failed" if case == "failed" else "capability.completed",
                 "payload": {"operation": "shell.run" if case == "shell" else "fs.edit" if case == "edit" else "fs.write",
                             "status": "error" if case == "failed" else "ok", "effect": "unknown" if case == "unknown" else "observed",
                             "changed_paths": [], "content_hashes": hashes, "workspace_may_have_changed": True}})
    result = source_freshness(need, source_observations(rows), "task")
    assert result["status"] == ("historical_snapshot" if case in {"write", "edit"} else "revalidation_required")


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


def test_later_artifact_use_cannot_shadow_a_read_citation_source_dependency(tmp_path):
    service, event, uri = service_at(tmp_path)
    service.ledger.append(
        task_id="task", source="harness", source_id="load", kind="capability.completed",
        payload={"operation": "artifacts.load", "status": "ok", "artifact_uri": uri},
    )

    note = write(service, [{"id": "source", "kind": "observation", "text": "captured fact",
                            "evidence_refs": [uri]}])

    assert note["status"] == "ok"
    assert service.note_read()["entries"][0]["source_dependencies"] == [event.payload["read_evidence"]]


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


@pytest.mark.parametrize("bad_reference,issue", [
    ("artifact://sha256/" + "a" * 63, "malformed_artifact_address"),
    ("artifact://sha256/" + "c" * 64, "unavailable_current_task_reference"),
    ("unknown-event", "unavailable_current_task_reference"),
])
def test_missing_note_citation_recovers_exact_local_reference_without_mutation(tmp_path, bad_reference, issue):
    service, event, uri = service_at(tmp_path)
    entry = {"id": "source", "kind": "observation", "text": "Captured source", "evidence_refs": [uri]}
    assert write(service, [entry])["status"] == "ok"
    retained, before = service.note_read(), service.ledger.read("task")
    rejected = write(service, [{**entry, "evidence_refs": [bad_reference]}], 1, "bad-citation")
    assert rejected["status"] == "unavailable" and rejected["effect"] == "none"
    assert rejected["recovery"]["issue"] == issue
    assert rejected["recovery"]["strategy"] == "recover_exact_current_task_reference"
    assert rejected["recovery"]["local_note_scope"] == "current_task_public_evidence_only"
    assert bad_reference not in canonical_json(rejected)
    assert service.note_read() == retained and service.ledger.read("task") == before
    # Reuse a receipt value, not a guessed repair, and preserve the CAS version.
    corrected = {**entry, "evidence_refs": [event.payload["result_artifact_uri"]]}
    assert write(service, [corrected], 1, "corrected-citation")["status"] == "ok"
    assert service.note_read()["entries"][0]["finding"]["evidence_refs"] == [uri]


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


def test_prior_working_set_tracks_consumer_versions_without_selecting_consumer_notes(tmp_path):
    source, event, uri = service_at(tmp_path, task="producer")
    write(source, [{"id": "policy", "kind": "observation", "text": "Prior learned policy", "evidence_refs": [uri]}])
    consumer = ContextProgramService(source.ledger, "consumer", authorized_tasks=("producer",), working_notes=True)
    write(consumer, [{"id": "private_to_selection", "kind": "hypothesis", "text": "Unselected consumer note"}])
    request = ViewRequest(task_id="consumer", program="working_set", source_tasks=("producer",))
    initial = consumer.runtime.compute(request)
    initial_consumer_time = source.ledger.read("consumer")[-1].recorded_at
    assert set(initial.source_manifest) == {"producer", "consumer"}
    assert [i["finding"]["id"] for i in initial.data["findings"]] == ["policy"]
    item = initial.data["findings"][0]
    assert item["consumer_versions"]["status"] == "unobserved"
    assert item["consumer_versions"]["sources"] == []  # No invented observation from the producer's hash.
    assert initial == consumer.runtime.compute(request)
    producer_manifest = initial.source_manifest["producer"]

    def observe(identity, digest):
        return source.ledger.append(task_id="consumer", source="harness", source_id=identity, kind="read.observed",
            payload={"operation": "fs.read", "status": "ok", "read_evidence": {
                **event.payload["read_evidence"], "sha256": digest, "offset": 1, "returned_lines": 1}})

    observed = observe("identity", "b" * 64)
    matching = consumer.runtime.compute(request)
    item = matching.data["findings"][0]
    assert item["consumer_versions"]["status"] == "matching_observations"
    assert item["consumer_versions"]["task_id"] == "consumer"
    assert item["consumer_versions"]["sources"][0]["observation_sequence"] == observed.sequence
    assert item["applicability"] == "prior_run_version_observed"
    assert item["authority"] == "advisory"
    assert item["source_dependencies"] == initial.data["findings"][0]["source_dependencies"]
    assert matching.source_manifest["producer"] == producer_manifest
    assert matching.source_manifest["consumer"] != initial.source_manifest["consumer"]
    assert matching.content_hash != initial.content_hash
    historical = consumer.runtime.compute(request.model_copy(update={"recorded_before": initial_consumer_time}))
    assert historical.data["findings"][0]["consumer_versions"]["status"] == "unobserved"
    limited = consumer.runtime.compute(request.model_copy(update={"max_scan_events": 1}))
    assert limited.status == "partial" and "findings" not in limited.data
    assert consumer.runtime.compute(request.model_copy(update={"watermark": 1})).data["findings"] == []

    observe("changed", "c" * 64)
    changed = consumer.runtime.compute(request).data["findings"][0]
    assert changed["consumer_versions"]["status"] == "changed_since_capture"
    assert changed["freshness"] == "historical_snapshot"  # Producer and consumer clocks remain distinct.
    source.ledger.append(task_id="consumer", source="harness", source_id="unknown", kind="capability.failed",
                        payload={"operation": "shell.run", "status": "error", "effect": "unknown", "workspace_may_have_changed": True})
    assert consumer.runtime.compute(request).data["findings"][0]["consumer_versions"]["status"] == "revalidation_required"
    observe("restored", "b" * 64)
    assert consumer.runtime.compute(request).data["findings"][0]["consumer_versions"]["status"] == "matching_observations"
    # This view never reconciles the failed command or proves the consumer learned missing lines.


def test_independent_finding_dependencies_survive_sibling_changes_without_validating_joint_claim(tmp_path):
    source, _, _ = service_at(tmp_path, task="producer")
    reads = []
    entries = []
    # One batched checkpoint can retain independent facts without a larger budget.
    for index in range(7):
        uri = "artifact://sha256/" + hashlib.sha256(f"artifact-{index}".encode()).hexdigest()
        read = {"path": f"rules/{index}.toml", "sha256": hashlib.sha256(f"source-{index}".encode()).hexdigest(),
                "offset": 1, "returned_lines": 12}
        source.ledger.append(task_id="producer", source="harness", source_id=f"rule-{index}",
                             kind="capability.completed", payload={"operation": "fs.read", "status": "ok",
                             "result_artifact_uri": uri, "read_evidence": read})
        reads.append(read)
        entries.append({"id": f"rule_{index}", "kind": "observation", "text": f"Rule {index} learned fact",
                        "evidence_refs": [uri], "related_paths": [read["path"]]})
    joint = {"id": "joint", "kind": "observation", "text": "Conclusion requires both rules 0 and 1",
             "evidence_refs": [entry["evidence_refs"][0] for entry in entries[:2]],
             "related_paths": [read["path"] for read in reads[:2]]}
    assert write(source, [*entries, joint])["status"] == "ok"
    original = source.note_read()
    assert len(canonical_json(source.ledger.read("producer")[-1].payload).encode()) <= 8000
    consumer = ContextProgramService(source.ledger, "consumer", authorized_tasks=("producer",), working_notes=True)
    request = ViewRequest(task_id="consumer", program="working_set", source_tasks=("producer",))

    def selected():
        return {row["finding"]["id"]: row for row in consumer.runtime.compute(request).data["findings"]}

    def observe(index, digest):
        source.ledger.append(task_id="consumer", source="harness", source_id=f"identity-{index}-{digest}",
                             kind="capability.completed", payload={"operation": "fs.read", "status": "ok",
                             "read_evidence": {**reads[index], "sha256": digest, "returned_lines": 1}})

    observe(0, reads[0]["sha256"])
    rows = selected()
    assert rows["rule_0"]["consumer_versions"]["status"] == "matching_observations"
    assert rows["rule_1"]["consumer_versions"]["status"] == "unobserved"
    assert rows["joint"]["consumer_versions"]["status"] == "unobserved"
    observe(1, "f" * 64)
    rows = selected()
    assert rows["rule_0"]["consumer_versions"]["status"] == "matching_observations"
    assert rows["rule_1"]["consumer_versions"]["status"] == "changed_since_capture"
    assert rows["joint"]["consumer_versions"]["status"] == "changed_since_capture"
    focused = consumer.runtime.compute(request.model_copy(update={"focus": (reads[0]["path"],), "limit": 2}))
    assert {row["finding"]["id"] for row in focused.data["findings"]} == {"rule_0", "joint"}
    assert focused == consumer.runtime.compute(request.model_copy(update={"focus": (reads[0]["path"],), "limit": 2}))
    assert source.note_read() == original  # No automatic splitting or rewriting of historical claims.
    assert all(row["authority"] == "advisory" for row in rows.values())
    assert len(rows["joint"]["source_dependencies"]) == 2


def test_prior_reuse_recovers_exact_source_note_without_admitting_foreign_citations(tmp_path):
    source, event, uri = service_at(tmp_path, task="producer with spaces")
    entry = {"id": "policy", "kind": "observation", "text": "Historical policy", "evidence_refs": [uri]}
    committed = write(source, [entry])
    consumer = ContextProgramService(source.ledger, "consumer", authorized_tasks=(source.task_id,), working_notes=True)
    view = consumer.runtime.compute(ViewRequest(task_id="consumer", program="working_set", source_tasks=(source.task_id,)))
    finding = view.data["findings"][0]
    assert finding["reuse"]["strategy"] == "reference_in_place"
    assert finding["reuse"]["local_note_scope"] == "new_current_task_learning"
    recovered = consumer.execute(finding["reuse"]["source_note_command"])
    assert recovered["status"] == "ok"
    assert recovered["evidence_event_ids"] == [committed["event_id"]]
    assert recovered["data"]["events"][0]["payload"]["entries"][0]["finding"] == source.note_read()["entries"][0]["finding"]
    before = consumer.note_read()
    rejected = write(consumer, [entry])
    assert rejected["effect"] == "none" and rejected["status"] == "unavailable"
    assert rejected["recovery"]["strategy"] == "recover_exact_current_task_reference"
    assert rejected["recovery"]["issue"] == "unavailable_current_task_reference"
    assert rejected["recovery"]["local_note_scope"] == "current_task_public_evidence_only"
    assert consumer.note_read() == before
    # Recovered source prose and a source-note command do not widen authorization.
    denied = ContextProgramService(source.ledger, "outsider", working_notes=True).execute(finding["reuse"]["source_note_command"])
    assert denied["status"] == "denied" and not denied.get("evidence_event_ids")
    local = source.ledger.append(task_id="consumer", source="harness", source_id="current-read", kind="read.observed",
                                payload={**event.payload, "result_artifact_uri": "artifact://sha256/" + "d" * 64})
    accepted = write(consumer, [{**entry, "evidence_refs": [local.event_id]}], operation="local")
    assert accepted["status"] == "ok"


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
