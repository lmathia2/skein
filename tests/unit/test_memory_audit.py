from pathlib import Path

import pytest

from evals.memory_audit import audit_emissions, audit_reads, merged
from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.memory.models import ReadEvidence


def test_interval_union_and_read_versions(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "ledger")
    def read(offset, lines, version="a", path="source.py"):
        return store.append(task_id="task", source="harness_event", source_id=str(len(list(store.read("task"))) + 1),
                            kind="capability.completed", payload={"operation": "fs.read",
                            "read_evidence": {"path": path, "sha256": version * 64,
                                              "offset": offset, "returned_lines": lines}})
    read(1, 10)
    cut = read(5, 10)
    read(3, 8)  # covered subset, not identical to either earlier request
    read(10, 10)  # five lines overlap the union, not six from double counting
    read(20, 5)
    read(20, 5)  # repeated after cut, but absent before cut
    read(1, 10, "b")
    read(1, 3, path="other.py")
    result = audit_reads(store.read("task"), cut_sequence=cut.sequence)
    assert merged([(1, 11), (5, 15), (15, 20)]) == [(1, 20)]
    assert result["counts"]["pre_cut_overlap_lines"] == 13
    assert result["counts"]["all_earlier_overlap_lines"] == 18
    assert result["counts"]["fully_covered"] == 1
    assert result["counts"]["new_range"] == 2
    assert result["counts"]["different_version"] == 1
    assert result["exposure"]["model_visible_duplicate_lines"] is None
    assert audit_reads(store.read("task"), cut_sequence=cut.sequence) == result


def test_invalid_read_evidence_is_not_silently_counted():
    with pytest.raises(ValueError):
        ReadEvidence(path="a", sha256="not-a-hash", offset=0, returned_lines=-1)


def test_emission_audit_separates_fetched_selected_and_provider_content():
    body = "parser_limit = 431\nunused_buffer = 723\n"
    snapshots = [{"read_evidence": {"path": "parser.py", "sha256": "a" * 64, "offset": 10, "returned_lines": 2}, "text": body}]
    records = [
        {"id": "pre", "sequence": 1, "route": "ptc_output", "text": "parser_limit = 431"},
        {"id": "shell", "sequence": 3, "route": "shell_output", "text": "parser_limit = 431"},
        {"id": "artifact", "sequence": 4, "route": "artifact_output", "text": "unused_buffer = 723"},
        {"id": "read", "sequence": 5, "route": "direct_read", "text": "    10 | parser_limit = 431"},
        {"id": "provider", "sequence": 6, "route": "provider_request", "text": body + body},
    ]
    result = audit_emissions(snapshots, records, cut_sequence=2)
    assert result["counts"]["post_cut_emitted_duplicate_lines"] == 2
    assert result["counts"]["post_cut_emitted_mapped_lines"] == 3
    assert result["counts"]["provider_transmitted_mapped_lines"] == 4
    assert result["counts"]["provider_within_request_duplicate_lines"] == 2
    assert audit_emissions(snapshots, records, cut_sequence=2) == result
    ambiguous = [{**snapshots[0], "read_evidence": {**snapshots[0]["read_evidence"], "sha256": "b" * 64}}]
    assert audit_emissions([*snapshots, *ambiguous], records, cut_sequence=2)["counts"]["ambiguous_lines"] > 0
    with pytest.raises(ValueError):
        audit_emissions(snapshots, [*records, records[0]], cut_sequence=2)


def test_structured_recovery_text_counts_as_exposure_without_decoding_arbitrary_output():
    import json
    body = "parser_limit = 431\nunused_buffer = 723\n"
    snapshots = [{"read_evidence": {"path": "parser.py", "sha256": "a" * 64, "offset": 10, "returned_lines": 2}, "text": body}]
    recovered = json.dumps({"status": "ok", "program": "read.recover", "data": {"text": body}})
    records = [{"id": "pre", "sequence": 1, "route": "ptc_output", "text": body},
               {"id": "post", "sequence": 3, "route": "shell_output", "text": recovered},
               {"id": "wire", "sequence": 4, "route": "provider_request", "text": recovered},
               {"id": "unknown", "sequence": 5, "route": "ptc_output", "text": json.dumps({"message": body})},
               {"id": "malformed", "sequence": 6, "route": "ptc_output", "text": '{"status": {}, "data": {}}'}]
    result = audit_emissions(snapshots, records, cut_sequence=2)
    assert result["counts"]["post_cut_emitted_duplicate_lines"] == 2
    assert result["counts"]["provider_transmitted_mapped_lines"] == 2
