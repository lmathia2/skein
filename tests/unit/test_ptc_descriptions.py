from harness.ptc.repl.worker import _execute_cell, _StateProxy, _value_fingerprint


def source_result():
    return {"status": "ok", "data": {"text": "source\n"},
            "read_reference": {"artifact_uri": "artifact://sha256/" + "a" * 64,
                               "path": "src/main.py", "sha256": "b" * 64,
                               "offset": 40, "returned_lines": 1}}


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
    namespace = {"opaque": Opaque(), "big": "x" * 65537, "cycle": cyclic, "surrogate": "\ud800"}
    state = _StateProxy(namespace, {})
    for name in namespace:
        assert state.annotate(name, "purpose")["status"] == "unavailable"
    assert "preview" not in state.describe("opaque", preview=True)
    assert state.annotate("opaque", "purpose", selector=("x",))["status"] == "unavailable"
    assert len(state.list()) == 4
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
