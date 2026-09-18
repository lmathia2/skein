import hashlib
import json

import pytest

from evals.continuity_oracle import (
    ORACLE_COMMAND,
    AnswerSpec,
    HostOracleSandbox,
    audit_answer_contracts,
)
from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.memory.models import ReadEvidence
from harness.execution.sandbox import SandboxRequest


@pytest.mark.parametrize("defect", [None, "failed", "pending", "missing", "late_read", "wrong_version",
                                  "early_first", "late_first", "missing_request", "missing_last"])
def test_each_answer_requires_its_own_completed_evidence_and_cut_window(tmp_path, defect):
    store = JsonlLedgerStore(tmp_path / "events.jsonl")
    def append(kind, **payload):
        return store.append(task_id="task", source="fixture", source_id=str(len(store.read("task"))),
                            kind=kind, payload=payload)
    needs = [ReadEvidence(path=f"source-{i}.txt", sha256=str(i) * 64, offset=1, returned_lines=2) for i in (1, 2)]
    def read(need, status="ok", kind="capability.completed"):
        append(kind, operation="fs.read", status=status, read_evidence=need.model_dump())
    def write(path, identity):
        common = {"operation": "fs.write", "operation_id": identity, "arguments_sha256": "a" * 64}
        if not (defect == "missing_request" and path == "answer.json"):
            append("capability.requested", **common)
        append("capability.completed", **common, status="ok", changed_paths=[path], content_hashes={path: "b" * 64})
    read(needs[0])
    if defect == "early_first":
        write("answers/first.json", "early")
    first = append("compaction.created").sequence
    if defect != "early_first":
        write("answers/first.json", "first")
    second = append("compaction.created").sequence
    if defect not in {"late_read", "missing"}:
        need = needs[1].model_copy(update={"sha256": "c" * 64}) if defect == "wrong_version" else needs[1]
        read(need, "error" if defect == "failed" else "ok",
             "capability.requested" if defect == "pending" else "capability.completed")
    if defect != "missing_last":
        write("answer.json", "last")
    if defect == "late_read":
        read(needs[1])
    if defect == "late_first":
        write("answers/first.json", "late")
    specs = [AnswerSpec(path=path, expected={"value": i}, required=[need], checkpoint=i)
             for i, (path, need) in enumerate(zip(("answers/first.json", "answer.json"), needs, strict=True))]
    audit = audit_answer_contracts(store.read("task"), specs, [first, second])
    assert audit["all_latest_supported"] is (defect is None)
    assert audit["all_submissions_supported"] is (defect is None)
    assert set(audit["artifacts"]) == {"answers/first.json", "answer.json"}
    if defect in {"early_first", "late_first"}:
        assert not audit["artifacts"]["answers/first.json"]["latest_supported"]
        assert audit["artifacts"]["answer.json"]["latest_supported"]
    assert audit_answer_contracts(store.read("task"), specs, [first, second]) == audit
    before_second = [event for event in store.read("task") if event.sequence < second]
    assert not audit_answer_contracts(before_second, specs, [first])["all_latest_supported"]
    with pytest.raises(ValueError, match="every published checkpoint"):
        audit_answer_contracts(store.read("task"), specs, [first])


def test_multi_answer_oracle_checks_all_values_hashes_and_confines_nested_paths(tmp_path):
    class Commands:
        workspace = tmp_path
        def execute(self, request):
            raise AssertionError("unexpected delegation")
    expected = {"answers/first.json": {"value": 17}}
    digests = {}
    oracle = HostOracleSandbox(Commands(), {"value": 29}, lambda hashes: hashes == digests,
                               additional_answers=expected)
    expected["answers/first.json"]["value"] = 999  # oracle expectations are frozen
    check = SandboxRequest(ORACLE_COMMAND)
    (tmp_path / "answers").mkdir()
    for path, value in (("answers/first.json", 17), ("answer.json", 29)):
        raw = json.dumps({"value": value}).encode()
        (tmp_path / path).write_bytes(raw)
        digests[path] = hashlib.sha256(raw).hexdigest()
    assert oracle.execute(check).exit_code == 0
    first = tmp_path / "answers/first.json"
    first.write_text('{"value":18}')
    assert oracle.execute(check).exit_code == 1  # last artifact alone cannot pass
    first.write_text('{"value":17}')
    assert oracle.execute(check).exit_code == 1  # right value, different bytes from receipt
    first.write_text(json.dumps({"value": 17}))
    assert oracle.execute(check).exit_code == 0
    (tmp_path / "answers").rename(tmp_path / "alias-target")
    (tmp_path / "answers").symlink_to(tmp_path / "alias-target", target_is_directory=True)
    assert oracle.execute(check).exit_code == 1  # even an internal parent alias is rejected
    assert not oracle.check_artifacts()["answers/first.json"]["passed"]


@pytest.mark.parametrize("path", ["", ".", "../outside", "/absolute", "a/../b", "a//b", "a/./b", "a/", "a\\b", "a\x00b"])
def test_answer_contract_rejects_ambiguous_or_unconfined_paths(path):
    with pytest.raises(ValueError):
        AnswerSpec(path=path, expected={}, required=[ReadEvidence(path="source", sha256="a" * 64,
                                                               offset=1, returned_lines=1)], checkpoint=0)
