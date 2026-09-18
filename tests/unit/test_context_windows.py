from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from harness.adapters.adk.context import (
    ContextWindowPlugin,
    _complete_cuts,
    _evidence_manifest,
    _project_advisory,
    _serialized,
    prior_applicability_update,
    render_handoff,
    select_context_cut,
)
from harness.core.config.models import ContextConfig
from harness.core.context import estimate_tokens
from harness.core.context.compiler import ContextBudgetExceeded
from harness.core.models import TaskLedger, TaskRequest
from harness.core.orchestration import build_work_packet
from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory.models import ViewResult
from harness.evidence.state import EventKind, JsonlEventStore, rebuild_ledger


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
    check = events.append("task", "execution.validation_observed", {
        "command_sha256": "a" * 64,
        "command": "pytest tests/test_parser.py", "operation_id": "check-1",
        "workspace_after": "workspace-fingerprint",
        "result": {"status": "ok", "exit_code": 0},
    })

    manifest = _evidence_manifest(events.read("task"), ["z.py", "a.py"])

    assert manifest["modified_paths"] == ["a.py", "z.py"]
    assert len(manifest["reads_newest_first"]) == 10
    assert manifest["reads_newest_first"][0]["path"] == "src/9.py"
    assert manifest["validations_newest_first"] == [{
        "harness_event_id": check.event_id,
        "command_sha256": "a" * 64, "exit_code": 0, "status": "ok",
        "command": "pytest tests/test_parser.py", "operation_id": "check-1",
        "workspace_after": "workspace-fingerprint",
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
@pytest.mark.parametrize("over_total", (False, True))
async def test_steering_uses_whole_packet_reserve_or_explicitly_stops(tmp_path, over_total):
    plugin, context, events, _ = setup(tmp_path)
    steering = "Keep this newest constraint intact. " * (300 if over_total else 40)
    assert estimate_tokens(steering) > plugin.config.steering_tokens
    events.append("task", EventKind.STEERING_RECEIVED, {"content": steering})
    request = LlmRequest(contents=[text("old " * 3000)])
    original = _serialized(request.contents)
    if over_total:
        with pytest.raises(ContextBudgetExceeded):
            await plugin.before_model_callback(callback_context=context, llm_request=request)
        assert _serialized(request.contents) == original
        assert not any(e.kind == EventKind.COMPACTION_CREATED for e in events.read("task"))
    else:
        await plugin.before_model_callback(callback_context=context, llm_request=request)
        assert any(steering.strip() in (part.text or "") for content in request.contents for part in content.parts or [])


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
    advisory = json.loads(visible.split("Advisory memory (not execution authority):\n")[1])
    assert any(item["kind"] == "reads_newest_first" and item["value"]["offset"] == 21
               for item in advisory["entries"])


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


@pytest.mark.parametrize("max_tokens", [600, 1300, 10000])
def test_prompt_projection_roundtrips_selected_findings_and_scoped_versions(max_tokens):
    dependency = {"path": "policy.toml", "sha256": "a" * 64, "offset": 1, "returned_lines": 1,
                  "status": "historical_snapshot", "observed_sha256": "a" * 64, "observation_sequence": 40}
    findings = [{
        "finding": {"id": f"finding_{index}", "kind": "next_action" if index == 0 else "observation",
                    "text": f"Recorded conclusion {index}: naïve ✓", "evidence_refs": ["e" * 64],
                    "status": "disputed" if index == 2 else "active", "conflicts_with": ["older"]},
        "revision": index + 1, "source_task_id": "current" if index < 3 else "authorized_prior",
        "note_event_id": "f" * 64, "authority": "advisory", "freshness": "historical_snapshot",
        "applicability": "current_task_advisory" if index < 3 else "prior_run_requires_current_validation",
        "provenance": "available", "source_dependencies": [deepcopy(dependency)],
    } for index in range(4)]
    # Distinct observed versions, ranges and source-task scopes must not collapse.
    findings[2]["source_dependencies"].append({**dependency, "sha256": "b" * 64,
                                            "offset": 2, "observed_sha256": "b" * 64})
    details = {"history_boundary": 42, "kernel": {"live": False}, "unresolved_effects": ["pending-op"],
               "retrieval": "memory note read", "working_set": {"program": "working_set", "version": 1,
               "watermark": 42, "data": {"findings": findings, "omitted_count": 2}}}
    original = deepcopy(details)
    rendered = render_handoff(details, max_tokens=max_tokens)
    required, advisory_text = rendered.split("\nAdvisory memory (not execution authority):\n")
    metadata = json.loads(required.split("Required continuation metadata:\n")[1])
    assert metadata["unresolved_effects"] == ["pending-op"]
    advisory = json.loads(advisory_text)
    if any(item["value"]["finding"]["kind"] == "next_action" for item in advisory["entries"]):
        assert "historical proposals" in advisory["recorded_actions"]
    recovered = []
    for entry in advisory["entries"]:
        value = deepcopy(entry["value"])
        if "finding_context_ref" in value:
            value.update(advisory["finding_contexts"][value.pop("finding_context_ref")])
        value["source_dependencies"] = [advisory["source_dependencies"][item["source_dependency_ref"]]
                                        if "source_dependency_ref" in item else item
                                        for item in value["source_dependencies"]]
        recovered.append(value)
    assert recovered
    assert all(value == findings[int(value["finding"]["id"].split("_")[1])] for value in recovered)
    assert len(recovered) + advisory["omitted_count"] == len(findings)
    assert advisory["upstream_omitted_count"] == 2
    assert estimate_tokens(rendered) <= max_tokens
    assert render_handoff(details, max_tokens=max_tokens) == rendered and details == original
    if max_tokens == 10000:
        assert recovered == findings
        assert "finding_contexts" in advisory and "source_dependencies" in advisory
        inline = {"entries": [{"kind": "findings", "value": value} for value in findings],
                  "omitted_count": 0, "upstream_omitted_count": 2}
        assert len(advisory_text.encode()) < len(canonical_json(inline).encode()) * .85


@pytest.mark.parametrize("freshness", ["changed_since_capture", "revalidation_required"])
@pytest.mark.parametrize("prior", [False, True])
def test_invalidated_conclusion_is_withheld_but_scoped_recovery_survives(freshness, prior):
    old, new = "a" * 64, "b" * 64
    capture = {"path": "environments.toml", "sha256": new, "offset": 1, "returned_lines": 5,
               "artifact_uri": "artifact://sha256/" + "c" * 64}
    finding = {
        "finding": {"id": "api_effective", "kind": "observation", "status": "active",
                    "text": "Obsolete derived values: timeout_ms=2750 and cache=false.",
                    "related_paths": ["environments.toml"], "evidence_refs": ["artifact://sha256/" + "d" * 64]},
        "freshness": freshness, "source_task_id": "prior" if prior else "task",
        "applicability": "prior_run_requires_current_validation" if prior else "current_task_advisory",
        "note_event_id": "e" * 64, "source_dependencies": [{
            "path": "environments.toml", "sha256": old, "observed_sha256": new,
            "offset": 1, "returned_lines": 5, "status": freshness}],
    }
    details = {"kernel": {"live": False}, "unresolved_effects": {"count": 1},
               "note_excerpt": "api timeout_ms=2750 and cache=false are the answer.",
               "working_set": {"data": {"findings": [finding]}},
               "evidence_manifest": {"reads_newest_first": [capture]}}
    original = deepcopy(details)
    rendered = render_handoff(details, max_tokens=1000)
    advisory = json.loads(rendered.split("Advisory memory (not execution authority):\n")[1])
    entry = next(e for e in advisory["entries"] if e["kind"] == "invalidated_findings")["value"]
    assert not entry["usable_as_current_fact"] and entry["freshness"] == freshness
    assert "text" not in entry["finding"] and "status" not in entry["finding"]
    assert entry["finding"]["id"] == "api_effective" and entry["note_event_id"] == finding["note_event_id"]
    assert entry["newer_recorded_captures"] == ([capture] if not prior and freshness == "changed_since_capture" else [])
    assert "2750" not in rendered and "cache=false" not in rendered
    assert any(e["kind"] == "historical_note" and e["value"]["text_withheld"] for e in advisory["entries"])
    assert estimate_tokens(rendered) <= 1000
    assert details == original and render_handoff(details, max_tokens=1000) == rendered


@pytest.mark.parametrize("consumer_status,provenance,shown", [
    ("matching_observations", "available", True), ("matching_observations", "unavailable", False),
    ("changed_since_capture", "available", False), ("revalidation_required", "available", False),
])
def test_prior_handoff_separates_consumer_version_observation_from_producer_state(consumer_status, provenance, shown):
    finding = {"finding": {"id": "policy", "kind": "observation", "text": "Historical learned policy", "evidence_refs": ["source"]},
               "source_task_id": "producer", "authority": "advisory", "freshness": "revalidation_required", "provenance": provenance,
               "applicability": "prior_run_version_observed" if shown else "prior_run_requires_current_validation",
               "consumer_versions": {"task_id": "consumer", "status": consumer_status,
                                     "scope": "last recorded version identity only", "sources": []}}
    details = {"working_set": {"data": {"findings": [finding]}}, "unresolved_effects": {"count": 1}}
    original = deepcopy(details)
    rendered = render_handoff(details, max_tokens=1000)
    advisory = json.loads(rendered.split("Advisory memory (not execution authority):\n")[1])
    item = advisory["entries"][0]
    assert item["kind"] == ("findings" if shown else "invalidated_findings")
    assert ("text" in item["value"]["finding"]) is shown
    assert item["value"]["authority"] == "advisory"
    assert item["value"]["consumer_versions"]["task_id"] == "consumer"
    assert details == original and render_handoff(details, max_tokens=1000) == rendered
    assert '"unresolved_effects": {"count": 1}' in rendered


def test_prompt_projection_does_not_factor_tiny_or_unique_values():
    for values in [[{"authority": "advisory"}] * 2, [{"source_task_id": "only", "revision": 1}]]:
        advisory = {"entries": [{"kind": "findings", "value": value} for value in values]}
        assert _project_advisory(advisory) == advisory


def test_optional_action_guidance_cannot_displace_required_metadata():
    details = {"unresolved_effects": ["pending-op"], "working_set": {"data": {"findings": [
        {"finding": {"id": "old-action", "kind": "next_action", "text": "too large " * 1000}},
    ]}}}
    rendered = render_handoff(details, max_tokens=100)
    assert "pending-op" in rendered
    advisory = json.loads(rendered.split("Advisory memory (not execution authority):\n")[1])
    assert advisory["entries"] == [] and advisory["omitted_count"] == 1
    assert "recorded_actions" not in advisory


@pytest.mark.asyncio
async def test_work_batch_navigation_appends_once_and_replays_captured_bytes(tmp_path, monkeypatch):
    plugin, context, events, canonical = setup(tmp_path)
    plugin.config = plugin.config.model_copy(update={"window_management": False, "compaction_tokens": 2000,
                                                    "work_packet_tokens": 6000})
    task = rebuild_ledger(events.read("task"))
    assert plugin.work_batch_handoff(task, "invocation") == ""
    raw = [text(build_work_packet(task))]
    first = LlmRequest(contents=deepcopy(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=first)
    evidence = {"path": "src/a.py", "sha256": "a" * 64, "offset": 7, "returned_lines": 3,
                "artifact_uri": "artifact://sha256/" + "b" * 64}
    read = events.append("task", EventKind.READ_OBSERVED, {
        "read_evidence": {k: v for k, v in evidence.items() if k != "artifact_uri"},
        "result_artifact_uri": evidence["artifact_uri"],
    })
    events.append("task", EventKind.REPL_CELL_COMPLETED, {
        "cell_id": "completed", "kernel_epoch": "epoch1", "state": {"manifest": [
            {"access_expression": "reads[0]", "read_reference": evidence},
        ]},
    })
    plugin.handoff = lambda _: {"kernel": {"live": True, "kernel_epoch": "epoch1"},
                               "unresolved_effects": {"count": 0}}
    task.iteration = 1
    snapshot = plugin.work_batch_handoff(task, "invocation")
    published = [e for e in events.read("task") if e.kind == EventKind.EVIDENCE_NAVIGATION_CREATED]
    assert len(published) == 1
    payload = published[0].payload
    assert payload["source_watermark"] >= read.sequence
    assert payload["parameters"]["work_batch_id"] == "2"
    assert render_handoff(payload["inputs"], max_tokens=2000) == snapshot
    assert '"access_expression":"reads[0]"' in snapshot
    assert '"offset":7,"path":"src/a.py","returned_lines":3' in snapshot
    assert estimate_tokens(snapshot) <= 2000
    raw += [types.Content(role="model", parts=[types.Part.from_text(text="batch complete")]),
            text(build_work_packet(task, evidence_navigation=snapshot))]
    second = LlmRequest(contents=deepcopy(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=second)
    assert _serialized(second.contents[:len(first.contents)]) == _serialized(first.contents)
    assert snapshot in second.contents[-1].parts[0].text
    events.append("task", EventKind.READ_OBSERVED, {"read_evidence": {
        "path": "src/a.py", "sha256": "c" * 64, "offset": 10, "returned_lines": 1}})
    raw += [types.Content(role="model", parts=[types.Part.from_function_call(name="execute_code", args={})]),
            types.Content(role="user", parts=[types.Part.from_function_response(name="execute_code", response={"status": "ok"})])]
    third = LlmRequest(contents=deepcopy(raw))
    await plugin.before_model_callback(callback_context=context, llm_request=third)
    assert _serialized(third.contents[:len(second.contents)]) == _serialized(second.contents)
    assert len([e for e in events.read("task") if e.kind == EventKind.EVIDENCE_NAVIGATION_CREATED]) == 1
    assert not any(e.kind == EventKind.COMPACTION_CREATED for e in events.read("task"))
    restarted = ContextWindowPlugin(events=events, ledger=canonical, config=plugin.config,
                                     handoff=lambda _: pytest.fail("replay must not query or refresh"))
    assert restarted.work_batch_handoff(task, "invocation") == snapshot
    with pytest.raises(ValueError, match="identity"):
        restarted.work_batch_handoff(task.model_copy(update={"next_action": "different task intent"}), "invocation")
    corrupted = events.read("task")
    next(e for e in corrupted if e.kind == EventKind.EVIDENCE_NAVIGATION_CREATED).payload["content"] += "corrupt"
    monkeypatch.setattr(events, "read", lambda _: corrupted)
    with pytest.raises(ValueError, match="mismatch"):
        restarted.work_batch_handoff(task, "invocation")


@pytest.mark.parametrize("worker", ("lost", "unknown", "live"))
def test_boundary_snapshot_preserves_partial_historical_sources_and_worker_fences(tmp_path, worker):
    plugin, _, events, _ = setup(tmp_path)
    plugin.config = plugin.config.model_copy(update={"compaction_tokens": 2000})
    task = rebuild_ledger(events.read("task")).model_copy(update={"iteration": 1})
    evidence = {"path": "policy.toml", "sha256": "a" * 64, "offset": 4, "returned_lines": 1}
    events.append("task", EventKind.READ_OBSERVED, {"read_evidence": evidence})
    events.append("task", EventKind.REPL_CELL_COMPLETED, {
        "cell_id": "old", "kernel_epoch": "old", "state": {"manifest": [
            {"access_expression": "reads[0]", "read_reference": evidence},
        ]},
    })
    if worker == "unknown":
        events.append("task", EventKind.REPL_CELL_TIMEOUT, {"effect": "unknown"})
    plugin.handoff = lambda _: {"kernel": {"live": worker != "lost", "kernel_epoch": "old"},
                               "unresolved_effects": {"count": int(worker == "unknown")}}
    snapshot = plugin.work_batch_handoff(task, "invocation")
    required, advisory = snapshot.split("\nAdvisory memory (not execution authority):\n")
    metadata = json.loads(required.removeprefix("Required continuation metadata:\n"))
    assert metadata["notebook"]["availability"] == {
        "lost": "restart_pending_safe_restore", "unknown": "effect_reconciliation_required", "live": "live"}[worker]
    assert metadata["unresolved_effects"]["count"] == int(worker == "unknown")
    entries = json.loads(advisory)["entries"]
    assert any(e["kind"] == "live_bindings" for e in entries) is (worker == "live")
    assert next(e["value"] for e in entries if e["kind"] == "reads_newest_first") == evidence


def test_navigation_overflow_and_publication_failure_never_return_a_snapshot(tmp_path, monkeypatch):
    plugin, _, events, _ = setup(tmp_path)
    task = rebuild_ledger(events.read("task")).model_copy(update={"iteration": 1})
    plugin.config = plugin.config.model_copy(update={"compaction_tokens": 10})
    with pytest.raises(ContextBudgetExceeded):
        plugin.work_batch_handoff(task, "invocation")
    assert not any(e.kind == EventKind.EVIDENCE_NAVIGATION_CREATED for e in events.read("task"))
    plugin.config = plugin.config.model_copy(update={"compaction_tokens": 2000})
    def fail(*args, **kwargs):
        raise OSError("publication failed")
    monkeypatch.setattr(events, "append", fail)
    with pytest.raises(OSError, match="publication"):
        plugin.work_batch_handoff(task, "invocation")


def test_boundary_prefers_focused_bindings_and_collapses_only_identical_read_references():
    reference = {"path": "focus.py", "sha256": "a" * 64, "offset": 1, "returned_lines": 3,
                 "artifact_uri": "artifact://sha256/" + "b" * 64}
    bindings = [{"name": "irrelevant", "read_reference": {**reference, "path": "other.py"}}]
    bindings += [{"name": f"alias_{i}", "read_reference": reference} for i in range(40)]
    bindings += [{"name": "annotated", "description": "Source needed for this review", "read_reference": reference},
                 {"name": "different_range", "read_reference": {**reference, "offset": 4}},
                 {"name": "different_version", "read_reference": {**reference, "sha256": "c" * 64}}]
    details = {"navigation": {"parameters": {"focus_paths": ["focus.py"]}},
               "notebook": {"availability": "live", "state": {"manifest": bindings}}}
    original = deepcopy(details)
    rendered = render_handoff(details, max_tokens=2000)
    advisory = json.loads(rendered.split("Advisory memory (not execution authority):\n")[1])
    names = [e["value"]["name"] for e in advisory["entries"]]
    assert names == ["annotated", "different_range", "different_version", "irrelevant"]
    assert advisory["upstream_omitted_count"] == 40
    assert details == original and render_handoff(details, max_tokens=2000) == rendered


def test_boundary_redacts_recorded_inputs_and_rendered_snapshot(tmp_path):
    from harness.execution.safety import SecretRedactor
    plugin, _, events, _ = setup(tmp_path)
    plugin.config = plugin.config.model_copy(update={"compaction_tokens": 2000})
    secret = "private-token-not-for-context"
    plugin.redactor = SecretRedactor(known_secrets=(secret,))
    plugin.handoff = lambda _: {"note_excerpt": secret}
    task = rebuild_ledger(events.read("task")).model_copy(update={"iteration": 1})
    result = plugin.work_batch_handoff(task, "invocation")
    assert secret not in result
    event = events.read("task")[-1]
    assert secret not in canonical_json(event.payload)
    assert render_handoff(event.payload["inputs"], max_tokens=2000) == result


def _prior_details(status="matching_observations", sequence=10, unknown=0):
    view = ViewResult(task_id="task", program="working_set", version=1, watermark=sequence, view_id="view",
                      evidence_event_ids=("note",), program_hash="a" * 64, execution_hash="b" * 64,
                      source_manifest={"task": {"watermark": sequence, "hash": "c" * 64},
                                       "producer": {"watermark": 3, "hash": "d" * 64}},
                      data={"findings": [{
                          "source_task_id": "producer", "note_event_id": "note", "revision": 1,
                          "finding": {"id": "policy", "status": "active", "text": "NEVER_AUTO_EXPOSE_LEARNED_TEXT"},
                          "provenance": "available", "source_dependencies": [{"path": "policy.toml"}],
                          "consumer_versions": {"task_id": "task", "status": status, "sources": [
                              {"path": "policy.toml", "observed_sha256": "e" * 64, "status": status,
                               "observation_sequence": sequence}]},
                          "reuse": {"strategy": "reference_in_place"},
                      }]})
    return {"working_set": view.model_dump(mode="json"), "unresolved_effects": {"count": unknown}}


@pytest.mark.parametrize("status", ("matching_observations", "changed_since_capture", "revalidation_required", "unobserved"))
def test_prior_update_is_bounded_identity_only_and_suppresses_unchanged_observations(status):
    details = _prior_details(status)
    original = deepcopy(details)
    result, states = prior_applicability_update(details, task_id="task", paths=("policy.toml",), known={}, max_bytes=2048)
    assert result and len(canonical_json(result).encode()) <= 2048
    assert result["entries"][0]["consumer_versions"]["status"] == status
    assert "NEVER_AUTO_EXPOSE_LEARNED_TEXT" not in canonical_json(result)
    assert result["source_view"]["source_manifest"] == details["working_set"]["source_manifest"]
    assert details == original
    assert prior_applicability_update(_prior_details(status, sequence=11), task_id="task", paths=("policy.toml",),
                                      known=states, max_bytes=2048) == (None, {})
    changed, _ = prior_applicability_update(_prior_details(status, sequence=12, unknown=1), task_id="task",
                                           paths=("policy.toml",), known=states, max_bytes=2048)
    assert changed and changed["unresolved_effect_count"] == 1
    for paths, maximum in ((("irrelevant.py",), 2048), (("policy.toml",), 128)):
        assert prior_applicability_update(details, task_id="task", paths=paths, known={}, max_bytes=maximum) == (None, {})
    corrupt = deepcopy(details)
    corrupt["working_set"]["data"]["findings"][0]["consumer_versions"]["status"] = "forged"
    with pytest.raises(ValueError, match="content_hash"):
        prior_applicability_update(corrupt, task_id="task", paths=("policy.toml",), known={}, max_bytes=2048)
    with pytest.raises(ValueError, match="identity"):
        prior_applicability_update(details, task_id="other", paths=("policy.toml",), known={}, max_bytes=2048)


@pytest.mark.asyncio
async def test_completed_ptc_read_emits_prior_metadata_once_without_changing_execution_result(tmp_path, monkeypatch):
    plugin, context, events, _ = setup(tmp_path)
    plugin.refresh_prior = True
    plugin.config = plugin.config.model_copy(update={"continuity_representation": "findings"})
    plugin.handoff = lambda _: _prior_details()
    tool = SimpleNamespace(name="execute_code")
    result = {"status": "ok", "attempt_id": "cell-1", "model_text": "évidence complete", "result_hash": "execution-hash"}
    base = deepcopy(result)
    kwargs = dict(tool=tool, tool_args={}, tool_context=context, result=result)
    assert await plugin.after_tool_callback(**kwargs) is None  # A supplied success is not a completed cell receipt.
    assert result == base
    events.append("task", EventKind.CAPABILITY_COMPLETED, {
        "attempt_id": "cell-1", "operation": "fs.read", "status": "ok", "read_evidence": {
            "path": "policy.toml", "sha256": "e" * 64, "offset": 1, "returned_lines": 1}})
    assert await plugin.after_tool_callback(**kwargs) is None
    assert result == base  # Not before the submitted cell has completed.
    terminal = events.append("task", EventKind.REPL_CELL_COMPLETED, {"attempt_id": "cell-1"})
    assert await plugin.after_tool_callback(**kwargs) is None  # Do not short-circuit later ADK observers.
    content = result["prior_applicability"]
    assert {k: v for k, v in result.items() if k != "prior_applicability"} == base
    assert len(canonical_json(result).encode()) <= plugin.max_tool_result_bytes
    published = events.read("task")[-1]
    assert published.kind == EventKind.PRIOR_APPLICABILITY_CREATED
    assert published.payload["source_cell_event_id"] == terminal.event_id
    assert prior_applicability_update(published.payload["inputs"], task_id="task", paths=tuple(published.payload["paths"]),
                                      known={}, max_bytes=published.payload["max_bytes"])[0] == content
    plugin.handoff = lambda _: pytest.fail("replay must not recompute")
    again = deepcopy(base)
    assert await plugin.after_tool_callback(**{**kwargs, "result": again}) is None
    assert again == result and events.read("task")[-1] == published
    with pytest.raises(ValueError, match="identity"):
        await plugin.after_tool_callback(**{**kwargs, "result": {**base, "model_text": "changed"}})
    corrupt = events.read("task")
    corrupt[-1].payload["content"]["entries"] = []
    monkeypatch.setattr(events, "read", lambda _: corrupt)
    with pytest.raises(ValueError, match="identity"):
        await plugin.after_tool_callback(**{**kwargs, "result": deepcopy(base)})


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("failed_cell", "disabled", "metadata", "full_output", "publication_failure"))
async def test_prior_updates_do_not_override_failure_profile_or_egress_contracts(tmp_path, monkeypatch, case):
    plugin, context, events, _ = setup(tmp_path)
    plugin.refresh_prior = case != "disabled"
    plugin.config = plugin.config.model_copy(update={"continuity_representation": "metadata" if case == "metadata" else "findings"})
    plugin.max_tool_result_bytes = 1024 if case == "full_output" else 16000
    plugin.handoff = lambda _: _prior_details()
    events.append("task", EventKind.CAPABILITY_COMPLETED, {"attempt_id": "cell", "operation": "fs.read", "status": "ok",
                                                          "read_evidence": {"path": "policy.toml"}})
    events.append("task", EventKind.REPL_CELL_FAILED if case == "failed_cell" else EventKind.REPL_CELL_COMPLETED,
                  {"attempt_id": "cell"})
    result = {"status": "ok", "attempt_id": "cell", "model_text": "é" * (500 if case == "full_output" else 1)}
    original = deepcopy(result)
    kwargs = dict(tool=SimpleNamespace(name="execute_code"), tool_args={}, tool_context=context, result=result)
    if case == "publication_failure":
        def fail(*args, **kwargs):
            raise OSError("publication failed")
        monkeypatch.setattr(events, "append", fail)
        with pytest.raises(OSError, match="publication"):
            await plugin.after_tool_callback(**kwargs)
    else:
        assert await plugin.after_tool_callback(**kwargs) is None
    assert result == original
    assert not any(e.kind == EventKind.PRIOR_APPLICABILITY_CREATED for e in events.read("task"))
