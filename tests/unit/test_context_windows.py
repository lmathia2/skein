from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from harness.adk.context import ContextWindowPlugin, _complete_cuts, _serialized
from harness.config.models import ContextConfig
from harness.context import estimate_tokens
from harness.ledger import JsonlLedgerStore
from harness.models import TaskLedger, TaskRequest
from harness.state import EventKind, JsonlEventStore


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
        handoff=lambda _: note or {"note": {"status": "ok"}, "retrieval": "memory history"},
        require_notes=True,
    )
    context = SimpleNamespace(agent_name="coding_worker", invocation_id="invocation",
                              state={"task_id": "task"})
    return plugin, context, events, canonical


@pytest.mark.asyncio
async def test_three_fresh_epochs_restart_and_exact_history(tmp_path):
    plugin, context, events, canonical = setup(tmp_path)
    raw = [text("early exact evidence: PARSER-73")]
    for index in range(3):
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
    with pytest.raises(ValueError, match="note unavailable"):
        await plugin.before_model_callback(callback_context=context, llm_request=request)
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
