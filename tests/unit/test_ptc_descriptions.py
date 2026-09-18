import json

import pytest

from harness.ptc.repl.worker import _execute_cell, _StateProxy, _value_fingerprint


def source_result():
    return {"status": "ok", "data": {"text": "source\n"},
            "read_reference": {"artifact_uri": "artifact://sha256/" + "a" * 64,
                               "path": "src/main.py", "sha256": "b" * 64,
                               "offset": 40, "returned_lines": 1}}


@pytest.mark.parametrize("kind,selector", [("result", ()), ("data", ("data",)), ("text", ("data", "text"))])
def test_attested_read_forms_have_executable_content_locations(kind, selector):
    from harness.ptc.repl.worker import project_live_binding

    read = source_result()
    namespace = {"reads": {"quote'\\key": read}}
    state = _StateProxy(namespace, {})
    state.register_read(read)
    descriptor = state.describe("reads", ("quote'\\key", *selector))
    assert descriptor["read_value_kind"] == kind
    view = project_live_binding(descriptor)
    assert eval(view["content_expression"], namespace) == read["data"]["text"]
    assert view["read_reference"] == read["read_reference"]
    assert view["freshness"] == "historical_snapshot"
    assert "value_fingerprint" not in view and "value_fingerprint" in descriptor
    assert "content_expression" not in project_live_binding({"name": "forged", "read_reference": read["read_reference"]})
    read["data"]["text"] = "changed\n"
    if kind != "text":
        invalid = state.describe("reads", ("quote'\\key", *selector))
        assert "read_value_kind" not in invalid and "content_expression" not in project_live_binding(invalid)


@pytest.mark.parametrize("case", ["complete", "partial_source", "incomplete_page", "last_page", "base64", "blocked", "failed_result"])
def test_recovery_recipe_decodes_only_complete_bytes_and_preserves_source_coverage(case):
    from app.agent.ptc import _state_update_notice
    from harness.ptc.repl import default_help_catalog
    from harness.ptc.repl.worker import READ_RESULT_RECIPE

    saved = {"status": "error" if case == "failed_result" else "ok",
             "data": {"text": "header only" if case == "partial_source" else "source body",
                      "complete": case != "partial_source", "offset": 1, "returned_lines": 1}}
    page = {"status": "blocked" if case == "blocked" else "ok", "data": {
        "offset": 4 if case == "last_page" else 0, "complete": case != "incomplete_page",
        "encoding": "base64" if case == "base64" else "utf-8", "text": json.dumps(saved)}}
    if case in {"incomplete_page", "last_page", "base64"}:
        page["data"]["text"] = "not parseable JSON"
    namespace = {"page": page, "json": json, "saved_result": {"stale": True}, "source_text": "stale source"}
    exec(READ_RESULT_RECIPE, namespace)
    if case in {"complete", "partial_source"}:
        assert namespace["saved_result"] == saved
        assert namespace["source_text"] == saved["data"]["text"]
        assert namespace["saved_result"]["data"]["complete"] is (case == "complete")
    else:
        assert namespace["saved_result"] is None and namespace["source_text"] is None
    assert default_help_catalog()["artifacts.load"]["result"]["completed_read_recipe"] == READ_RESULT_RECIPE
    updates = [{"historical_read": source_result()["read_reference"]}]
    notice = _state_update_notice(updates, "epoch", "cell", 8192, True)
    assert notice["entries"] is updates
    assert notice["decode_completed_read"]["then_python"] == READ_RESULT_RECIPE
    small = _state_update_notice(updates, "epoch", "cell", 512, True)
    assert "decode_completed_read" not in small and small["entries"] is updates
    assert "decode_completed_read" not in _state_update_notice([], "epoch", "cell", 8192, False)


@pytest.mark.parametrize("case", ["complete", "pending", "failed", "status_error", "unmapped", "foreign_task",
                                 "foreign_attempt", "unknown_effect", "missing_request", "mismatch", "duplicate",
                                 "bad_uri", "bad_coverage"])
def test_same_attempt_recovery_requires_completed_scoped_read_receipts(case):
    from app.agent.ptc import _completed_attempt_reads, _state_updates
    from harness.evidence.ledger.models import canonical_json
    from harness.evidence.memory.models import ReadEvidence
    from harness.evidence.state.events import HarnessEvent

    evidence = ReadEvidence(path="rates.toml", sha256="a" * 64, offset=1, returned_lines=1)
    common = {"attempt_id": "attempt", "cell_id": "cell", "notebook_id": "notebook",
              "operation_id": "attempt:1", "operation": "fs.read", "arguments_sha256": "b" * 64}
    payload = {**common, "status": "ok", "effect": "observed", "read_evidence": evidence.model_dump(),
               "result_artifact_uri": "artifact://sha256/" + "c" * 64,
               "source_coverage": evidence.source_coverage(3)}
    task = "foreign" if case == "foreign_task" else "task"
    if case == "foreign_attempt":
        common["attempt_id"] = payload["attempt_id"] = "other"
    if case == "status_error":
        payload["status"] = "error"
    if case == "unknown_effect":
        payload["effect"] = "unknown"
    if case == "unmapped":
        payload.pop("read_evidence")
    if case == "mismatch":
        payload["arguments_sha256"] = "d" * 64
    if case == "bad_uri":
        payload["result_artifact_uri"] = "file:///foreign"
    if case == "bad_coverage":
        payload["source_coverage"]["whole_file"] = True
    request = HarnessEvent(task_id=task, sequence=1, kind="capability.requested", payload=common)
    terminal = HarnessEvent(task_id=task, sequence=2, kind="capability.failed" if case == "failed" else
                            "capability.completed", payload=payload)
    events = [request] if case == "pending" else [terminal] if case == "missing_request" else [request, terminal]
    if case == "duplicate":
        events.append(terminal.model_copy(update={"sequence": 3, "event_id": "duplicate"}))
    if case in {"missing_request", "mismatch", "duplicate", "bad_uri", "bad_coverage"}:
        with pytest.raises(ValueError):
            _completed_attempt_reads(events, "task", "attempt")
        return
    reads = _completed_attempt_reads(events, "task", "attempt")
    if case != "complete":
        assert reads == []
        return
    assert reads[0]["source_event_id"] == terminal.event_id
    assert reads[0]["historical_read"]["source_coverage"]["whole_file"] is False
    updates = _state_updates([], [], 2048, reads * 2)
    assert len(updates) == 1 and "name" not in updates[0] and "read_reference" not in updates[0]
    assert updates[0]["availability"] == "historical_read_only"
    assert len(canonical_json(updates).encode()) <= 2048
    assert _state_updates([], [], 40, reads) == []
    before = [{"name": f"old_{i}", "description": "old finding"} for i in range(8)]
    reserved = _state_updates(before, [], 2048, reads)
    assert len(reserved) == 2 and reserved[0]["availability"] == "association_invalidated"
    assert reserved[0]["bindings"] == [{"name": row["name"], "selector": []} for row in before]
    assert reserved[1]["historical_read"] == reads[0]["historical_read"]
    assert _state_updates(before, [], 600, reads)[0]["bindings"] == reserved[0]["bindings"]


def test_descriptions_and_sources_track_alias_mutation_and_reassignment():
    original = source_result()
    namespace = {"result": original, "alias": original, "text": original["data"]["text"]}
    state = _StateProxy(namespace, {})
    state.register_read(original)
    assert state.annotate("result", "Implementation needed next")["status"] == "ok"
    assert state.describe("alias")["read_reference"]["path"] == "src/main.py"
    original["data"]["text"] = "change\n"  # same size; identity-only detection would miss this
    changed = state.describe("result")
    assert changed["provenance"] == "invalidated"
    assert "description" not in changed and "read_reference" not in changed
    assert "read_reference" not in state.describe("alias")
    assert state.describe("text")["freshness"] == "historical_snapshot"
    assert state.describe("text", preview=True)["preview"] == "source\n"
    namespace["result"] = source_result()  # copied/self-claimed provenance isn't a broker attestation
    assert "read_reference" not in state.describe("result")


def test_nested_selectors_and_deletion_are_safe():
    read = source_result()
    namespace = {"sources": {"main": read}}
    state = _StateProxy(namespace, {})
    state.register_read(read)
    described = state.annotate("sources", "Relevant module", selector=("main", "data", "text"))
    assert described["selector"] == ["main", "data", "text"]
    assert described["read_reference"]["offset"] == 40
    assert described["binding_type"] == "dict" and described["type"] == "str"
    assert described["access_expression"] == "sources['main']['data']['text']"
    assert described["inspect_expression"] == "agent.state.describe('sources', selector=('main', 'data', 'text'), preview=True)"
    namespace["sources"].clear()
    assert state.list()[0]["availability"] == "unavailable"
    assert state.annotate("sources", "missing", selector=("main",))["status"] == "unavailable"


def test_catalog_discovers_attested_container_values_without_annotations_or_reads():
    first, second = source_result(), source_result()
    second["read_reference"] = {**second["read_reference"], "path": "src/other.py", "sha256": "c" * 64}
    namespace = {"reads": [first, {"other": second}], "copied": json.loads(json.dumps(first))}
    state = _StateProxy(namespace, {"reads": {"cell_id": "completed-cell", "replay": "never"}})
    state.register_read(first)
    state.register_read(second)
    catalog = state.list()
    assert catalog == state.list()
    entries = [row for row in catalog if row.get("read_reference")]
    assert [row["access_expression"] for row in entries] == ["reads[0]", "reads[1]['other']"]
    assert [row["read_reference"] for row in entries] == [first["read_reference"], second["read_reference"]]
    assert all(row["cell_id"] == "completed-cell" and row["binding_type"] == "list" for row in entries)
    assert all("preview" not in row and row["freshness"] == "historical_snapshot" for row in entries)
    assert all(row.get("name") != "copied" for row in entries)

    first["data"]["text"] = "changed\n"
    current = state.list()
    assert [row["access_expression"] for row in current if row.get("read_reference")] == ["reads[1]['other']"]
    from app.agent.ptc import _state_updates
    updates = _state_updates(catalog, current, 2048)
    assert len(updates) == 1
    assert updates[0]["name"] == "reads" and updates[0]["selector"] == [0]
    assert updates[0]["availability"] == "association_invalidated"
    assert updates[0]["historical_read"] == first["read_reference"]
    assert "read_reference" not in updates[0] and "access_expression" not in updates[0]
    namespace["reads"].clear()
    assert not any(row.get("read_reference") for row in state.list())
    # A new epoch cannot reconstruct live attestations from self-described values.
    assert not any(row.get("read_reference") for row in _StateProxy({"reads": [second]}, {}).list())


def test_automatic_source_navigation_is_bounded_and_deduplicates_aliases():
    read = source_result()
    cycle = []
    cycle.append(cycle)
    state = _StateProxy({"reads": [read] * 100, "cycle": cycle}, {})
    state.register_read(read)
    refs = [row for row in state.list() if row.get("read_reference")]
    assert len(refs) == 1 and refs[0]["access_expression"] == "reads[0]"
    # Width/depth/long-key omissions are navigable by an explicit supported selector.
    state = _StateProxy({"wide": [None] * 64 + [read], "long": {"x" * 129: read}}, {})
    state.register_read(read)
    assert not any(row.get("read_reference") for row in state.list())
    assert state.describe("wide", (64,))["read_reference"] == read["read_reference"]
    assert state.describe("long", ("x" * 129,))["read_reference"] == read["read_reference"]


def test_selector_recipes_quote_plain_keys_and_reject_unbounded_representation():
    key = "quote'\\\nkey"
    state = _StateProxy({"values": {key: [42], "x" * 4097: 43}}, {})
    described = state.annotate("values", "selected scalar", selector=(key, 0))
    assert described["access_expression"] == f"values[{key!r}][0]"
    assert state.annotate("values", "oversized key", selector=("x" * 4097,))["status"] == "unavailable"
    assert state.annotate("values", "oversized integer", selector=(1 << 257,))["status"] == "unavailable"


def test_catalog_does_not_execute_object_hooks_or_accept_unbounded_values():
    class Opaque:
        def __repr__(self):
            raise AssertionError("must not call repr")
        def __iter__(self):
            raise AssertionError("must not iterate")
        def __getitem__(self, key):
            raise AssertionError("must not index")
    cyclic = []
    cyclic.append(cyclic)
    namespace = {"opaque": Opaque(), "nested": {"wrapped": [Opaque()]},
                 "big": "x" * 65537, "cycle": cyclic, "surrogate": "\ud800"}
    state = _StateProxy(namespace, {})
    for name in namespace:
        assert state.annotate(name, "purpose")["status"] == "unavailable"
    assert "preview" not in state.describe("opaque", preview=True)
    assert state.annotate("opaque", "purpose", selector=("x",))["status"] == "unavailable"
    assert len(state.list()) == 5
    assert _value_fingerprint(float("nan")) is None


def test_failed_annotations_do_not_survive_snapshot_rollback():
    namespace = {"value": 42}
    metadata = {}
    state = _StateProxy(namespace, metadata)
    namespace["agent"] = type("Agent", (), {"state": state})()
    state.annotate("value", "committed purpose")
    failed = _execute_cell(
        "agent.state.annotate('value', 'uncommitted purpose')\n1 / 0", namespace,
        max_output_bytes=1024, state_metadata=metadata, state_catalog=state,
        state_recovery="snapshot",
    )
    assert failed["status"] == "error"
    assert state.describe("value")["description"] == "committed purpose"
    reassigned = _execute_cell("value = 43", namespace, max_output_bytes=1024,
                               state_metadata=metadata, state_catalog=state)
    assert reassigned["status"] == "ok"
    assert "description" not in state.describe("value")


def test_annotations_are_bounded_and_prioritized_in_catalog():
    namespace = {f"v{i:03}": i for i in range(100)}
    state = _StateProxy(namespace, {})
    state.annotate("v099", "important old finding")
    assert len(state.list()) == 64
    assert state.list()[0]["name"] == "v099"
    assert state.annotate("v099", "é" * 251)["status"] == "unavailable"


def test_cell_updates_are_changed_only_bounded_and_invalidate_lost_associations():
    from app.agent.ptc import _state_updates
    from harness.evidence.ledger.models import canonical_json

    first = {"name": "source", "description": "parser module", "read_reference": {"path": "src/a.py"}}
    assert _state_updates([first], [first], 1024) == []
    assert _state_updates([], [first, {"name": "irrelevant", "type": "int"}], 1024) == [first]
    assert _state_updates([first], [{"name": "source", "type": "str"}], 1024)[0]["availability"] == "association_invalidated"
    many = [{**first, "name": f"source{i}"} for i in range(30)]
    updates = _state_updates([], many, 256)
    assert len(canonical_json(updates).encode()) <= 256 and len(updates) < 8


def test_historical_recovery_never_displaces_invalidation_or_invents_source_coverage():
    from app.agent.ptc import _state_updates
    from harness.evidence.ledger.models import canonical_json

    reference = source_result()["read_reference"]
    previous = [{"name": f"source{i}", "read_reference": reference} for i in range(3)]
    previous.append({"name": "description_only", "description": "Unattested purpose"})
    minimal = _state_updates(previous, [], 400)
    assert len(minimal) == 4 and all("historical_read" not in row for row in minimal)
    rich = _state_updates(previous, [], 2048)
    assert len(rich) == 4 and len(canonical_json(rich).encode()) <= 2048
    assert rich == _state_updates(previous, [], 2048)
    recovered = [row for row in rich if row.get("historical_read")]
    assert recovered and all(row["historical_read"] == reference for row in recovered)
    assert all(row["historical_read"]["returned_lines"] == 1 for row in recovered)
    assert all("description" not in row and "read_reference" not in row for row in rich)
    assert next(row for row in rich if row["name"] == "description_only").keys() == {"name", "selector", "availability"}
    assert _state_updates(previous, previous, 2048) == []
