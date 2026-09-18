from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from harness.adapters.adk.context import (
    ContextWindowPlugin,
    _complete_cuts,
    _evidence_manifest,
    _serialized,
    render_handoff,
    select_context_cut,
)
from harness.core.config.models import ContextConfig
from harness.core.context import estimate_tokens
from harness.core.models import TaskLedger, TaskRequest
from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.state import EventKind, JsonlEventStore


def text(value):
    return types.Content(role="user", parts=[types.Part.from_text(text=value)])


def setup(tmp_path, *, policy="fresh", note=None):
    events = JsonlEventStore(tmp_path / "events")
    task = TaskLedger.from_request(TaskRequest(goal="Retain the constraint", constraints=["No network"]),
                                  task_id="task", workspace_id="workspace", base_revision="base")
    events.append("task", EventKind.TASK_CREATED, {"ledger": task.model_dump(mode="json")})
    canonical = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    plugin = ContextWindowPlugin(
        events=events, ledger=canonical,
        config=ContextConfig(work_packet_tokens=2000, reconstruction=policy, window_management=True,
                             ledger_tokens=400, compaction_tokens=200,
                             steering_tokens=200, recent_event_tokens=200),
        handoff=lambda _: note or {"note": {"status": "ok", "version": 1},
                                  "note_excerpt": "Preserve the constraint; inspect parser next.",
                                  "retrieval": "memory history"},
        require_notes=True,
    )
    context = SimpleNamespace(agent_name="coding_worker", invocation_id="invocation",
                              state={"task_id": "task"})
    return plugin, context, events, canonical


def test_evidence_manifest_is_bounded_deduplicated_and_newest_first(tmp_path):
    _, _, events, _ = setup(tmp_path)
    for index in range(10):
        events.append("task", EventKind.CAPABILITY_COMPLETED, {
            "read_evidence": {
                "path": f"src/{index}.py", "sha256": str(index) * 64,
                "offset": 1, "returned_lines": 20,
            }
        })
    events.append("task", "execution.validation_observed", {
        "command_sha256": "a" * 64,
        "result": {"status": "ok", "exit_code": 0},
    })

    manifest = _evidence_manifest(events.read("task"), ["z.py", "a.py"])

    assert manifest["modified_paths"] == ["a.py", "z.py"]
    assert len(manifest["reads_newest_first"]) == 10
    assert manifest["reads_newest_first"][0]["path"] == "src/9.py"
    assert manifest["validations_newest_first"] == [{
        "command_sha256": "a" * 64, "exit_code": 0, "status": "ok",
    }]


def test_read_index_collapses_only_same_version_containment(tmp_path):
    _, _, events, _ = setup(tmp_path)
    for offset, count, digest in [(1, 40, "a"), (10, 10, "a"), (30, 20, "a"), (1, 40, "b")]:
        events.append("task", EventKind.CAPABILITY_COMPLETED, {"read_evidence": {
            "path": "src/a.py", "sha256": digest * 64, "offset": offset, "returned_lines": count,
        }})
    reads = _evidence_manifest(events.read("task"), [])["reads_newest_first"]
    assert [(r["offset"], r["returned_lines"], r["sha256"][0]) for r in reads] == [(1, 40, "b"), (30, 20, "a"), (1, 40, "a")]


@pytest.mark.asyncio
async def test_compaction_handoff_carries_prior_read_evidence(tmp_path):
    plugin, context, events, _ = setup(tmp_path)
    plugin.config = plugin.config.model_copy(update={"compaction_tokens": 600})
    events.append("task", EventKind.CAPABILITY_COMPLETED, {
        "read_evidence": {
            "path": "src/parser.py", "sha256": "a" * 64,
            "offset": 21, "returned_lines": 40,
        }
    })
    request = LlmRequest(contents=[text("old:" + "x" * 9_000)])

    await plugin.before_model_callback(callback_context=context, llm_request=request)

    visible = "".join(
        part.text or "" for content in request.contents for part in content.parts or ()
    )
    assert "src/parser.py" in visible
    assert '"offset": 21' in visible
    assert '"reads_newest_first"' in visible


@pytest.mark.asyncio
async def test_three_fresh_epochs_restart_and_exact_history(tmp_path):
    note = {"note": {"status": "ok", "version": 1}, "note_excerpt": "Preserve the constraint"}
    plugin, context, events, canonical = setup(tmp_path, note=note)
    raw = [text("early exact evidence: PARSER-73")]
    for index in range(3):
        note["note"]["version"] = index + 1
        raw.append(text(f"old-{index}:" + "x" * 9000))
        request = LlmRequest(contents=list(raw))
        await plugin.before_model_callback(callback_context=context, llm_request=request)
        visible = _serialized(request.contents)
        assert "PARSER-73" not in visible
        assert "No network" in visible
        assert estimate_tokens(visible) <= 2000
    assert sum(event.kind == EventKind.COMPACTION_CREATED for event in events.read("task")) == 3
    retained = canonical.read("task")
    assert any("PARSER-73" in str(event.payload) for event in retained)
    # A new plugin instance reconstructs the same published cut from disk.
    replacement = ContextWindowPlugin(events=events, ledger=canonical, config=plugin.config,
                                      handoff=plugin.handoff, require_notes=True)
    request = LlmRequest(contents=list(raw))
    await replacement.before_model_callback(callback_context=context, llm_request=request)
    assert "PARSER-73" not in _serialized(request.contents)


@pytest.mark.asyncio
async def test_published_handoff_is_frozen_until_the_next_epoch(tmp_path):
    calls = 0

    def handoff(_task_id):
        nonlocal calls
        calls += 1
        return {"note": {"status": "ok", "version": 1}, "note_excerpt": "Working intent",
                "retrieval": f"view-{calls}"}

    plugin, context, events, _ = setup(tmp_path)
    plugin.handoff = handoff
    raw = [text("old:" + "x" * 9000)]
    from harness.core.config.models import ContextConfig

    first = LlmRequest(contents=list(raw), config=types.GenerateContentConfig(response_schema=ContextConfig))
    await plugin.before_model_callback(callback_context=context, llm_request=first)
    assert "view-1" in _serialized(first.contents)

    # The provider-visible header remains the published epoch even though the
    # advisory handoff program now produces different bytes.
    second = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=second)
    assert "view-1" in _serialized(second.contents)
    assert "view-2" not in _serialized(second.contents)
    assert sum(event.kind == EventKind.COMPACTION_CREATED for event in events.read("task")) == 1


@pytest.mark.asyncio
async def test_soft_pressure_preserves_epoch_until_unconsumed_result_can_be_cut(tmp_path):
    plugin, context, events, _ = setup(tmp_path)
    raw = [text("old evidence " + "x" * 9000)]
    await plugin.before_model_callback(callback_context=context, llm_request=LlmRequest(contents=list(raw)))
    first = next(event for event in events.read("task") if event.kind == EventKind.COMPACTION_CREATED)
    raw.extend([
        types.Content(role="model", parts=[types.Part.from_function_call(name="read", args={})]),
        types.Content(role="user", parts=[types.Part.from_function_response(name="read", response={"data": "y" * 9000})]),
    ])
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert len([event for event in events.read("task") if event.kind == EventKind.COMPACTION_CREATED]) == 1
    assert request.contents[0].model_dump(mode="json", exclude_none=True) == first.payload["header"]
    assert request.contents[-1] == raw[-1]
    raw.append(text("Result consumed; continue"))
    pending = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=pending)
    assert "working-note checkpoint is pending" in _serialized(pending.contents)
    assert len([event for event in events.read("task") if event.kind == EventKind.COMPACTION_CREATED]) == 1
    # One opportunity only: an ignored refresh falls back with explicit staleness.
    await plugin.before_model_callback(callback_context=context, llm_request=LlmRequest(contents=list(raw)))
    assert len([event for event in events.read("task") if event.kind == EventKind.COMPACTION_CREATED]) == 2
    assert events.read("task")[-1].payload["note_stale"] is True
    assert events.read("task")[-1].payload["checkpoint_requested"] is True


@pytest.mark.asyncio
async def test_next_cut_requests_updated_findings_then_publishes_the_new_note(tmp_path):
    note = {"note": {"status": "ok", "version": 1}, "note_excerpt": "Original observation"}
    plugin, context, events, _ = setup(tmp_path, note=note)
    raw = [text("old:" + "x" * 9000)]
    await plugin.before_model_callback(callback_context=context, llm_request=LlmRequest(contents=list(raw)))
    raw.append(text("New completed evidence and changed output:" + "y" * 9000))
    pending = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=pending)
    assert pending.contents[-2] == raw[-1]
    assert len([e for e in events.read("task") if e.kind == EventKind.COMPACTION_CREATED]) == 1
    note.update(note={"status": "ok", "version": 2}, note_excerpt="New finding; validation still blocked")
    refreshed = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=refreshed)
    cut = events.read("task")[-1]
    assert cut.kind == EventKind.COMPACTION_CREATED
    assert cut.payload["note"]["version"] == 2
    assert cut.payload["note_stale"] is False
    assert "New finding; validation still blocked" in _serialized(refreshed.contents)


@pytest.mark.asyncio
async def test_missing_note_has_one_soft_checkpoint_opportunity(tmp_path):
    plugin, context, events, _ = setup(tmp_path, note={"note": {"status": "unavailable"}})
    raw = [text("old:" + "x" * 9000)]
    await plugin.before_model_callback(callback_context=context, llm_request=LlmRequest(contents=list(raw)))
    assert not any(e.kind == EventKind.COMPACTION_CREATED for e in events.read("task"))
    await plugin.before_model_callback(callback_context=context, llm_request=LlmRequest(contents=list(raw)))
    assert events.read("task")[-1].payload["note_stale"] is True


@pytest.mark.asyncio
async def test_phase_boundary_defers_soft_limit_but_hard_limit_compacts(tmp_path):
    plugin, context, events, _ = setup(tmp_path)
    plugin.config = ContextConfig(
        window_management=True,
        reconstruction="fresh",
        compaction_timing="phase_boundary",
        work_packet_tokens=2_000,
        max_task_input_tokens=20_000,
        max_context_tokens=20_000,
        ledger_tokens=400,
        compaction_tokens=200,
        steering_tokens=200,
        recent_event_tokens=200,
    )
    context.state["task_phase"] = "plan"
    soft = [text("soft:" + "x" * 40_000)]

    first = LlmRequest(contents=list(soft))
    await plugin.before_model_callback(callback_context=context, llm_request=first)
    assert len(first.contents) == 2
    assert not any(event.kind == EventKind.COMPACTION_CREATED for event in events.read("task"))

    context.state["task_phase"] = "implement"
    boundary = LlmRequest(contents=list(soft))
    await plugin.before_model_callback(callback_context=context, llm_request=boundary)
    assert len(boundary.contents) == 1
    assert events.read("task")[-1].payload["trigger"] == "phase_boundary"

    context.invocation_id = "hard-invocation"
    context.state["task_phase"] = "implement"
    hard = LlmRequest(contents=[text("hard:" + "x" * 90_000)])
    await plugin.before_model_callback(callback_context=context, llm_request=hard)
    assert events.read("task")[-1].payload["trigger"] == "hard_limit"


@pytest.mark.asyncio
async def test_failed_publication_does_not_mutate_request(tmp_path, monkeypatch):
    plugin, context, events, _ = setup(tmp_path)
    request = LlmRequest(contents=[text("evidence" + "x" * 9000)])
    before = _serialized(request.contents)
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(events, "append", fail)
    with pytest.raises(OSError):
        await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert _serialized(request.contents) == before


@pytest.mark.asyncio
async def test_missing_note_refuses_transition_and_private_parts_are_not_retained(tmp_path):
    plugin, context, _, canonical = setup(tmp_path, note={"note": {"status": "unavailable"}})
    request = LlmRequest(contents=[types.Content(role="model", parts=[
        types.Part(text="private-thought", thought=True), types.Part(text="public-evidence" + "x" * 9000),
    ])])
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert "working-note checkpoint is pending" in _serialized(request.contents)
    retained = str([event.payload for event in canonical.read("task")])
    assert "private-thought" not in retained
    assert "public-evidence" in retained


def test_only_complete_tool_interactions_can_be_cut():
    call = types.Content(role="model", parts=[types.Part.from_function_call(name="read", args={})])
    result = types.Content(role="user", parts=[types.Part.from_function_response(name="read", response={})])
    assert _complete_cuts([text("task"), call, result]) == [0, 1, 3]
    with pytest.raises(ValueError, match="pending"):
        _complete_cuts([call])
    with pytest.raises(ValueError, match="unmatched"):
        _complete_cuts([result])


def test_context_selection_is_pure_bounded_and_keeps_unconsumed_result() -> None:
    old = text("old " * 5_000)
    call = types.Content(
        role="model", parts=[types.Part.from_function_call(name="read", args={})]
    )
    result = types.Content(
        role="user",
        parts=[types.Part.from_function_response(name="read", response={"value": 42})],
    )
    raw = [old, call, result]
    before = _serialized(raw)
    header = text("bounded handoff")
    config = ContextConfig(
        window_management=True,
        reconstruction="fresh",
        work_packet_tokens=2_000,
    )

    first = select_context_cut(
        raw, prior_cut=0, header=header, transient=[], config=config
    )
    second = select_context_cut(
        raw, prior_cut=0, header=header, transient=[], config=config
    )

    assert first == second == 1
    assert _serialized(raw) == before
    selected = [header, *raw[first:]]
    assert estimate_tokens(_serialized(selected)) <= config.work_packet_tokens
    assert any(part.function_call for part in selected[1].parts or ())
    assert any(part.function_response for part in selected[2].parts or ())

    result.parts[0].function_response.response = {"value": "x" * 16000}
    assert select_context_cut(
        raw, prior_cut=0, header=header, transient=[], config=config,
        available_tokens=8000,
    ) == 1
    with pytest.raises(ValueError, match="required control context"):
        select_context_cut(
            raw, prior_cut=0, header=header, transient=[], config=config,
            available_tokens=3000,
        )


def test_recent_tail_budget_is_independent_of_header_budget() -> None:
    old = text("old" * 20_000)
    recent = text("recent" * 16_000)
    header = text("bounded handoff")
    config = ContextConfig(
        window_management=True,
        work_packet_tokens=2_000,
        recent_event_tokens=32_000,
        max_context_tokens=100_000,
    )

    cut = select_context_cut(
        [old, recent], prior_cut=0, header=header, transient=[], config=config
    )

    assert cut == 1
    assert estimate_tokens(_serialized([header, recent])) > config.work_packet_tokens


def test_history_capture_only_reads_and_appends_the_new_tail(tmp_path, monkeypatch):
    plugin, _, _, canonical = setup(tmp_path)
    reads = 0
    appends = 0
    original_read = canonical.read
    original_append = canonical.append

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    def append(*args, **kwargs):
        nonlocal appends
        appends += 1
        return original_append(*args, **kwargs)

    monkeypatch.setattr(canonical, "read", read)
    monkeypatch.setattr(canonical, "append", append)
    history = [text("first"), text("second")]
    plugin._capture("task", "invocation", history)
    plugin._capture("task", "invocation", history)
    history.append(text("third"))
    plugin._capture("task", "invocation", history)

    assert reads == 1
    assert appends == 3


def test_history_capture_does_not_reserialize_retained_objects(tmp_path, monkeypatch):
    plugin, _, _, _ = setup(tmp_path)
    first, second = text("first"), text("second")
    plugin._capture("task", "invocation", [first])

    def fail(*args, **kwargs):
        raise AssertionError("retained content was serialized again")

    original = types.Content.model_dump
    monkeypatch.setattr(types.Content, "model_dump", lambda self, *args, **kwargs:
                        fail() if self is first else original(self, *args, **kwargs))
    plugin._capture("task", "invocation", [first, second])


def test_history_capture_rejects_changed_captured_prefix(tmp_path):
    plugin, _, _, _ = setup(tmp_path)
    plugin._capture("task", "invocation", [text("first")])

    with pytest.raises(ValueError, match="changed before"):
        plugin._capture("task", "invocation", [text("changed")])


@pytest.mark.asyncio
async def test_empty_note_defers_then_published_header_survives_state_changes(tmp_path):
    note = {"note": {"status": "ok", "version": 0}, "note_excerpt": ""}
    plugin, context, events, _ = setup(tmp_path, note=note)
    raw = [text("evidence " * 2000)]
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert not any(e.kind == EventKind.COMPACTION_CREATED for e in events.read("task"))
    note.update(note={"status": "ok", "version": 1}, note_excerpt="Fix parser; preserve constraint")
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    frozen = _serialized(request.contents)
    context.state["skill_context_text"] = "new dynamic skill text"
    note["note_excerpt"] = "newer intent"
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert _serialized(request.contents) == frozen


@pytest.mark.asyncio
async def test_hard_context_limit_is_independent_of_task_budget(tmp_path):
    plugin, context, _, _ = setup(tmp_path, note={"note": {"status": "ok", "version": 0}})
    plugin.config = plugin.config.model_copy(update={"max_context_tokens": 8000,
                                                    "max_task_input_tokens": 2_000_000})
    request = LlmRequest(contents=[text("evidence " * 5000)])
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert len(request.contents) == 1


@pytest.mark.asyncio
async def test_provider_usage_plus_new_turn_delta_controls_compaction(tmp_path):
    plugin, context, events, _ = setup(tmp_path)
    plugin.config = plugin.config.model_copy(update={
        "max_context_tokens": 100_000,
        "compaction_threshold_ratio": 0.8,
        "compaction_timing": "phase_boundary",
    })
    context.state.update(
        task_phase="implement",
        context_window_phase="implement",
        context_provider_input_tokens=79_900,
    )
    raw = [text("old:" + "x" * 40_000)]
    anchor = __import__("hashlib").sha256(_serialized(raw[:1]).encode()).hexdigest()
    context.state[f"context_request_estimate:task:invocation:{anchor}"] = 10_000
    request = LlmRequest(contents=list(raw))

    await plugin.before_model_callback(callback_context=context, llm_request=request)

    epoch = next(e for e in events.read("task") if e.kind == EventKind.COMPACTION_CREATED)
    assert epoch.payload["tokens_before"] >= 80_000
    assert epoch.payload["token_estimate_source"] == "provider_previous_plus_delta"
    assert epoch.payload["threshold_tokens"] == 80_000


@pytest.mark.asyncio
async def test_handoff_reports_matching_live_kernel(tmp_path):
    plugin, context, events, _ = setup(tmp_path, note={
        "note": {"status": "ok", "version": 1},
        "note_excerpt": "Continue",
        "kernel": {"live": True, "kernel_epoch": "epoch-1"},
    })
    events.append("task", EventKind.REPL_CELL_COMPLETED, {
        "kernel_epoch": "epoch-1", "state": {"manifest": [{"name": "result"}]}
    })
    request = LlmRequest(contents=[text("old:" + "x" * 40_000)])
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    visible = "".join(part.text or "" for content in request.contents for part in content.parts or ())
    assert '"availability": "live"' in visible
    assert '"live": true' in visible


@pytest.mark.asyncio
@pytest.mark.parametrize("windows", [False, True])
async def test_note_requested_before_phase_boundary_in_both_arms(tmp_path, windows):
    note = {"note": {"status": "ok", "version": 0}, "note_excerpt": ""}
    plugin, context, events, _ = setup(tmp_path, note=note)
    plugin.config = plugin.config.model_copy(update={
        "compaction_timing": "phase_boundary", "window_management": windows,
    })
    context.state.update(task_phase="understand", context_window_phase="understand")
    raw = [text("evidence " * 2000)]
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert "working-note checkpoint is pending" in _serialized(request.contents)
    note.update(note={"status": "ok", "version": 1}, note_excerpt="Investigate parser")
    request = LlmRequest(contents=list(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert not any(e.kind == EventKind.COMPACTION_CREATED for e in events.read("task"))


def test_whole_handoff_entries_preserve_structure_and_hide_dead_bindings():
    import json

    details = {"history_boundary": 12, "kernel": {"live": False, "kernel_epoch": None},
               "retrieval": "memory note read", "note": {"event_id": "n1"},
               "note_excerpt": "oversized " * 1000,
               "working_set": {"data": {"findings": [{"finding": {"id": "relevant", "text": "public finding"}}]}},
               "notebook": {"availability": "restart_pending_safe_restore", "state": {"manifest": [
                   {"name": "dead_binding", "description": "must not be advertised as live"}]}},
               "evidence_manifest": {"touched_paths": ["src/a.py"]}}
    value = render_handoff(details, max_tokens=250)
    assert estimate_tokens(value) <= 250
    advisory = json.loads(value.split("Advisory memory (not execution authority):\n")[1])
    assert advisory["omitted_count"] > 0
    assert any(item["kind"] == "findings" for item in advisory["entries"])
    assert "dead_binding" not in value and "..." not in value
    assert render_handoff(details, max_tokens=250) == value


def test_read_manifest_keeps_old_relevant_paths_and_direct_recovery_handles(tmp_path):
    _, _, events, _ = setup(tmp_path)
    for index in range(40):
        events.append("task", EventKind.READ_OBSERVED, {
            "read_evidence": {"path": f"src/{index}.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 10},
            "result_artifact_uri": f"artifact://sha256/{index:064x}",
        })
    events.append("task", EventKind.CAPABILITY_COMPLETED, {"changed_paths": ["src/0.py"]})
    manifest = _evidence_manifest(events.read("task"), [], ("src/0.py",))
    assert manifest["reads_newest_first"][0]["path"] == "src/0.py"
    assert manifest["reads_newest_first"][0]["artifact_uri"].startswith("artifact://")
    assert manifest["touched_paths"] == ["src/0.py"] and manifest["modified_paths"] == []
    assert len(manifest["reads_newest_first"]) == 32 and manifest["omitted_reads"] == 8
