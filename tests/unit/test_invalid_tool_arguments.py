from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from harness.adapters.adk.tool_calls import InvalidToolArgumentsPlugin
from harness.adapters.providers.codex_responses import (
    INVALID_ARGUMENTS_KEY,
    _function_call_part,
    _ResponseAccumulator,
)
from harness.adapters.providers.openrouter_responses import build_openrouter_request_body
from harness.core.config import RuntimeBindings, load_harness_composition
from harness.evidence.state import EventKind, JsonlEventStore
from harness.evidence.telemetry.adk_plugin import HarnessMetricsPlugin
from harness.execution.safety.redaction import SecretRedactor


def _context(**overrides):
    return SimpleNamespace(**{"state": {"task_id": "task"}, "invocation_id": "invocation",
                              "function_call_id": "call", **overrides})


def _plugin(tmp_path, **overrides):
    return InvalidToolArgumentsPlugin(**{
        "event_store": JsonlEventStore(tmp_path / "events"), "artifact_root": tmp_path / "artifacts",
        "redactor": SecretRedactor(known_secrets=("fixture-secret-value",)), "default_task_id": "task",
        **overrides,
    })


def _response(raw='{"code":' + '\t' * 40_000, *, call_id="call"):
    part = _function_call_part({"name": "code", "call_id": call_id, "arguments": raw})
    return LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"code":' + '\t' * 40_000, '{"code":"fixture-secret-value',
                                  '["not an object"]', 'null', '{"code":NaN}', '{"code":"\\ud800"}'],
                         ids=["large", "secret", "array", "null", "nonfinite", "surrogate"])
async def test_archived_rejection_is_bounded_redacted_and_replayable(tmp_path, raw):
    plugin = _plugin(tmp_path)
    response = _response(raw)
    await plugin.after_model_callback(callback_context=_context(), llm_response=response)
    events = plugin.event_store.read("task")
    assert len(events) == 1 and events[0].kind == EventKind.TOOL_CALL_REJECTED
    record = events[0].payload
    retained = plugin.redactor.redact_text(raw).encode()
    assert (plugin.artifact_root / record["artifact_uri"].rsplit("/", 1)[-1]).read_bytes() == retained
    assert record["original_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert record["execution_started"] is False and record["effect"] == "none"
    assert record["view"]["redacted"] == (raw.encode() != retained)
    wire = build_openrouter_request_body(LlmRequest(contents=[response.content]), model="test", reasoning_effort="max")
    assert len(json.dumps(wire["input"]).encode()) < 1024
    assert "fixture-secret-value" not in json.dumps(wire) + events[0].model_dump_json()
    restored = LlmResponse.model_validate_json(response.model_dump_json())
    restarted = _plugin(tmp_path)
    result = await restarted.before_tool_callback(tool=SimpleNamespace(name="code"),
        tool_args=restored.content.parts[0].function_call.args, tool_context=_context())
    assert result["status"] == "error" and result["execution_started"] is False
    assert result["artifact_uri"] == record["artifact_uri"]
    await restarted.after_model_callback(callback_context=_context(), llm_response=_response(raw))
    assert restarted.event_store.read("task") == events
    extended = LlmRequest(contents=[restored.content, types.Content(role="user", parts=[
        types.Part.from_function_response(name="code", response=result)])])
    second = build_openrouter_request_body(extended, model="test", reasoning_effort="max")
    assert second["input"][:len(wire["input"])] == wire["input"]


@pytest.mark.asyncio
async def test_partial_and_final_share_evidence_and_keep_usage(tmp_path):
    plugin = _plugin(tmp_path)
    accumulator = _ResponseAccumulator()
    item = {"type": "function_call", "name": "code", "call_id": "call", "arguments": '{"code":'}
    partial = accumulator.consume({"type": "response.output_item.done", "item": item})
    await plugin.after_model_callback(callback_context=_context(), llm_response=LlmResponse(
        content=types.Content(role="model", parts=[partial]), partial=True))
    assert plugin.event_store.read("task") == []
    assert len(partial.model_dump_json()) < 1024
    accumulator.consume({"type": "response.completed", "response": {"output": [item],
        "usage": {"input_tokens": 123, "output_tokens": 45, "cost": .01}}})
    final = accumulator.final_response("test")
    await plugin.after_model_callback(callback_context=_context(), llm_response=final)
    assert len(plugin.event_store.read("task")) == 1
    assert final.usage_metadata.prompt_token_count == 123
    assert final.usage_metadata.candidates_token_count == 45
    assert final.custom_metadata["provider_cost_usd"] == .01
    assert "artifact_uri" in final.content.parts[0].function_call.args[INVALID_ARGUMENTS_KEY]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["publication", "artifact", "task", "call-content"])
async def test_rejection_faults_fail_closed_before_publication(tmp_path, monkeypatch, fault):
    plugin = _plugin(tmp_path)
    response = _response()
    context = _context()
    if fault == "publication":
        def fail(*args, **kwargs):
            raise OSError("volume unavailable")
        monkeypatch.setattr(plugin.event_store, "append", fail)
    elif fault == "artifact":
        root = plugin.artifact_root
        root.mkdir()
        digest = hashlib.sha256(response.content.parts[0]._raw_arguments.encode()).hexdigest()
        (root / digest).write_bytes(b"corrupt")
    elif fault == "task":
        context = _context(state={"task_id": "foreign"})
    else:
        await plugin.after_model_callback(callback_context=context, llm_response=_response('{"other":'))
    before = response.model_dump_json()
    with pytest.raises((OSError, ValueError)):
        await plugin.after_model_callback(callback_context=context, llm_response=response)
    assert response.model_dump_json() == before


@pytest.mark.asyncio
async def test_reserved_marker_cannot_execute_code_or_grant_artifact_access(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.before_tool_callback(tool=SimpleNamespace(name="code"),
        tool_args={INVALID_ARGUMENTS_KEY: {"artifact_uri": "artifact://sha256/" + "a" * 64},
                   "code": "raise AssertionError('must not run')"}, tool_context=_context())
    assert result["execution_started"] is False and "artifact_uri" not in result
    assert plugin.event_store.read("task") == []
    assert await plugin.before_tool_callback(tool=SimpleNamespace(name="code"),
        tool_args={"code": "answer = 42"}, tool_context=_context()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["tool", "view", "foreign-task", "call"])
async def test_dispatch_checks_exact_receipt_identity(tmp_path, fault):
    plugin = _plugin(tmp_path)
    response = _response()
    await plugin.after_model_callback(callback_context=_context(), llm_response=response)
    args = response.content.parts[0].function_call.args
    tool = SimpleNamespace(name="code")
    context = _context()
    if fault == "tool":
        tool.name = "write"
    elif fault == "view":
        args = {**args, "code": "must not run"}
    elif fault == "foreign-task":
        context.state = {"task_id": "foreign"}
    else:
        context.function_call_id = "unknown-call"
        result = await plugin.before_tool_callback(tool=tool, tool_args=args, tool_context=context)
        assert result["execution_started"] is False and "artifact_uri" not in result
        return
    with pytest.raises(ValueError):
        await plugin.before_tool_callback(tool=tool, tool_args=args, tool_context=context)


class _MalformedThenCorrectedModel(BaseLlm):
    parallel_sibling: bool = False
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(llm_request.model_copy(deep=True))
        step = len(self._requests) - 1
        if step == 0:
            response = _response(json.dumps({"code": "retained = 42"}), call_id="warm")
        elif step == 1:
            response = _response()
            if self.parallel_sibling:
                sibling = _response(json.dumps({"code": "assert retained == 42"}), call_id="sibling")
                response.content.parts.extend(sibling.content.parts)
            if stream:
                yield response.model_copy(update={"partial": True}, deep=True)
        elif step == 2:
            rejected = [part.function_response.response for content in llm_request.contents
                        for part in content.parts or [] if part.function_response
                        and part.function_response.id == "call"]
            assert len(rejected) == 1 and rejected[0]["execution_started"] is False
            uri = rejected[0]["artifact_uri"]
            response = _response(json.dumps({"code": "assert retained == 42\n"
                f"diagnostic = agent.artifacts.load({uri!r}, limit=100)\n"
                "assert diagnostic['status'] == 'ok'\n"
                "assert diagnostic['data']['returned_bytes'] == 100\n"
                "assert diagnostic['data']['complete'] is False"}), call_id="corrected")
        elif step == 3:
            response = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Finished diagnostic.")]))
        else:
            raise AssertionError("unexpected model retry")
        response.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=123, candidates_token_count=45)
        response.custom_metadata = {"provider_cost_usd": .01}
        yield response


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [StreamingMode.NONE, StreamingMode.SSE])
@pytest.mark.parametrize("parallel_sibling", [False, True])
async def test_real_adk_ptc_rejects_bad_call_preserves_heap_and_recovers_artifact(tmp_path, streaming, parallel_sibling):
    composition = load_harness_composition()
    settings = settings_from_composition(composition, RuntimeBindings(
        workspace=tmp_path, state_root=tmp_path / "state", task_id="task"))
    store = JsonlEventStore(settings.state_root / "events")
    plugin = _plugin(tmp_path, event_store=store,
                     artifact_root=settings.state_root / "artifacts" / "sha256")
    model = _MalformedThenCorrectedModel(model="test-model", parallel_sibling=parallel_sibling)
    worker = build_coding_worker(settings, model, event_store=store,
        ptc_config=composition.harness.config.notebook_ptc.model_copy(update={"enabled": True}))
    metrics = HarnessMetricsPlugin(database=tmp_path / "metrics.db", static_prefix_hash="test",
        static_prefix_tokens=0, default_model="test-model", default_task_id="task")
    service = InMemorySessionService()
    app = App(name="invalid_call_test", root_agent=worker.agent, plugins=[metrics, plugin])
    runner = Runner(app=app, session_service=service)
    await service.create_session(app_name=app.name, user_id="user", session_id="session", state={"task_id": "task"})
    try:
        observed = [event async for event in runner.run_async(user_id="user", session_id="session",
            new_message=types.Content(role="user", parts=[types.Part(text="Run the diagnostic")]),
            run_config=RunConfig(streaming_mode=streaming, max_llm_calls=5))]
    finally:
        await runner.close()
        worker.close()
    assert len(model._requests) == 4
    events = store.read("task")
    assert len([e for e in events if e.kind == EventKind.TOOL_CALL_REJECTED]) == 1
    submitted = [e for e in events if e.kind == EventKind.REPL_CELL_SUBMITTED]
    completed = [e for e in events if e.kind == EventKind.REPL_CELL_COMPLETED]
    assert len(submitted) == len(completed) == 2 + int(parallel_sibling)
    assert len({event.payload["kernel_epoch"] for event in completed}) == 1
    assert not any(e.kind == EventKind.REPL_CELL_FAILED for e in events)
    serialized = "\n".join(e.model_dump_json() for e in observed)
    assert "\\t" * 100 not in serialized and "_raw_arguments" not in serialized
    with sqlite3.connect(tmp_path / "metrics.db") as connection:
        assert connection.execute("SELECT count(*), sum(cost_usd) FROM model_usage").fetchone() == (4, .04)
        statuses = connection.execute("SELECT status FROM tool_usage ORDER BY rowid").fetchall()
        if parallel_sibling:
            assert sorted(statuses) == [("error",), ("ok",), ("ok",), ("ok",)]
        else:
            assert statuses == [("ok",), ("error",), ("ok",)]
