"""Scripted model, real ADK workflow/tools: deterministic interaction contracts."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ConfigDict, PrivateAttr

from app.agent.factory import default_harness_registry
from harness.agent import (
    AdkHarnessAssembly,
    HarnessBuildInfo,
    HarnessDescriptor,
    HarnessRegistry,
    RuntimeCapability,
)
from harness.ai import ClosedAdkModelProviderRegistry
from harness.config import RuntimeBindings, load_harness_composition, parse_harness_composition
from harness.server.adk_mapper import AdkAgUiNormalizer
from harness.server.protocol import AgUiEventType


class ScriptedModel(BaseLlm):
    _responses: list[list[types.Part]] = PrivateAttr(default_factory=list)
    _calls: int = PrivateAttr(default=0)
    _requests: list[str] = PrivateAttr(default_factory=list)
    _gate: asyncio.Event | None = PrivateAttr(default=None)

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        if self._gate is not None:
            await self._gate.wait()
            self._gate = None
        assert self._responses, "unexpected extra model call"
        self._calls += 1
        self._requests.append(llm_request.model_dump_json())
        yield LlmResponse(content=types.Content(role="model", parts=self._responses.pop(0)))


class Provider:
    provider_id = "scripted"

    def __init__(self, model: ScriptedModel | dict[str, ScriptedModel]):
        self.model = model

    def build_model(self, config, *, secrets, bindings=None):
        return self.model[config.name] if isinstance(self.model, dict) else self.model


class AlternateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_name: str


class AlternateHarnessFactory:
    descriptor = HarnessDescriptor(implementation="alternate_fixture_v1", display_name="Alternate fixture harness",
        capabilities=frozenset({RuntimeCapability.STREAMING}))
    config_model = AlternateConfig

    def __init__(self, model: BaseLlm) -> None:
        self.model = model

    def build(self, composition, bindings) -> AdkHarnessAssembly:
        del bindings
        config = composition.harness.config
        assert isinstance(config, AlternateConfig)
        return AdkHarnessAssembly(descriptor=self.descriptor,
            app=App(name=composition.app.name, root_agent=LlmAgent(name=config.agent_name, model=self.model)),
            build_info=HarnessBuildInfo(behavior_sha256=composition.behavior_sha256, models={"root": self.model.model}))


def reply(status: str, message: str) -> list[types.Part]:
    return [types.Part(text=json.dumps({"status": status, "message": message}))]


async def run_fixture(tmp_path: Path, model: ScriptedModel, prompt: str, *, monkeypatch=None, observe=None, max_iterations=1):
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((Provider(model),)))
    composition = load_harness_composition(config_models=registry.config_models())
    config = composition.harness.config
    config = config.model_copy(update={
        "models": {name: value.model_copy(update={"provider": "scripted"})
                   for name, value in config.models.items()},
        "workflow": config.workflow.model_copy(update={"max_iterations": max_iterations}),
    })
    composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("A small fixture repository.\n")
    verification_calls: list[dict[str, Any]] = []
    if monkeypatch is not None:
        async def verify(deps, ctx, node_input):
            verification_calls.append(node_input)
            return {"report": {"passed": False, "recommended_next_action": "Tests must pass"}, "changed_paths": []}
        monkeypatch.setattr("app.agent.workflow._verify_task", verify)
    assembly = registry.build(composition, RuntimeBindings(
        workspace=workspace, state_root=tmp_path / "state", task_id="fixture-turn",
    ))
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=assembly.app.name, user_id="user", session_id="conversation")
    runner = Runner(app=assembly.app, session_service=sessions)
    mapper = AdkAgUiNormalizer(run_id="run", thread_id="conversation",
                              explicit_public_messages=assembly.explicit_public_messages)
    events = []
    async for event in runner.run_async(user_id="user", session_id="conversation",
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE)):
        public = mapper.push(event)
        events.extend(public)
        if observe is not None:
            observe(public)
    return events, workspace, verification_calls


class StreamingModel(ScriptedModel):
    _chunks: list[str] = PrivateAttr(default_factory=list)
    _released: asyncio.Event | None = PrivateAttr(default=None)
    _finished: bool = PrivateAttr(default=False)
    _final_parts: list[types.Part] = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        assert stream
        if self._responses:
            async for item in super().generate_content_async(llm_request, stream):
                yield item
            return
        self._calls += 1
        self._requests.append(llm_request.model_dump_json())
        for chunk in self._chunks:
            yield LlmResponse(partial=True, content=types.Content(role="model", parts=[types.Part(text=chunk)]))
        if self._released is not None:
            await self._released.wait()
        self._finished = True
        yield LlmResponse(partial=False, content=types.Content(role="model", parts=self._final_parts or [types.Part(text="".join(self._chunks))]))


@pytest.mark.asyncio
async def test_real_adk_publishes_markdown_before_model_finishes_without_json_or_duplicate_reply(tmp_path) -> None:
    model = StreamingModel(model="fixture")
    model._chunks = ['{"status":', '"answer"}', '\nHello ', '**streaming** reader.\n']
    model._released = asyncio.Event()
    def observe(events):
        if any(event.type == AgUiEventType.TEXT_MESSAGE_CONTENT for event in events) and not model._released.is_set():
            assert not model._finished
            model._released.set()
    events, _, _ = await asyncio.wait_for(run_fixture(tmp_path, model, "hello", observe=observe), timeout=10)
    assert model._calls == 1
    assert "".join(e.delta for e in events if e.type == AgUiEventType.TEXT_MESSAGE_CONTENT) == "Hello **streaming** reader.\n"
    assert sum(e.type == AgUiEventType.TEXT_MESSAGE_START for e in events) == 1
    assert sum(e.type == AgUiEventType.TEXT_MESSAGE_END for e in events) == 1
    assert next(e.value for e in events if e.name == "coding.workflow.output")["verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["write", "criteria", "coding", "done"])
async def test_streamed_candidate_with_coding_obligations_stays_private(tmp_path, monkeypatch, mode) -> None:
    model = StreamingModel(model="fixture")
    status = "done" if mode == "done" else "answer"
    model._chunks = [json.dumps({"status": status}), '\nUNVERIFIED_COMPLETION_CLAIM\n']
    if mode == "write":
        model._responses = [[types.Part(function_call=types.FunctionCall(id="write1", name="write", args={
            "path": "hello.py", "content": "print('hello')\n",
        }))]]
    prompt = json.dumps({"goal": "Implement the requested feature", **(
        {"acceptance_criteria": ["Behavior is tested"]} if mode == "criteria"
        else {"mode": "coding"} if mode == "coding" else {"mode": "auto"}
    )})
    events, _, calls = await run_fixture(
        tmp_path, model, prompt, monkeypatch=monkeypatch, max_iterations=2
    )
    assert calls
    assert "UNVERIFIED_COMPLETION_CLAIM" not in "".join(e.delta or "" for e in events if e.type == AgUiEventType.TEXT_MESSAGE_CONTENT)


@pytest.mark.asyncio
async def test_streamed_reply_cannot_be_followed_by_a_real_tool_mutation(tmp_path) -> None:
    model = StreamingModel(model="fixture")
    model._chunks = ['{"status":"answer"}\nPublic explanation.\n']
    model._final_parts = [types.Part(function_call=types.FunctionCall(id="bad", name="write", args={
        "path": "must-not-exist.py", "content": "bad",
    }))]
    with pytest.raises(ValueError, match="tool call"):
        await run_fixture(tmp_path, model, "hello")
    assert not (tmp_path / "workspace" / "must-not-exist.py").exists()


@pytest.mark.asyncio
async def test_cancelling_a_public_partial_closes_adk_without_finishing_the_model(tmp_path, caplog) -> None:
    model = StreamingModel(model="fixture")
    model._chunks = ['{"status":"answer"}\nPartial reply ']
    model._released = asyncio.Event()
    visible = asyncio.Event()
    def observe(events):
        if any(event.type == AgUiEventType.TEXT_MESSAGE_CONTENT for event in events):
            visible.set()
    task = asyncio.create_task(run_fixture(tmp_path, model, "hello", observe=observe))
    try:
        await asyncio.wait_for(visible.wait(), timeout=10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not model._finished
    assert "Failed to detach context" not in caplog.text


@pytest.mark.asyncio
async def test_steering_during_public_stream_runs_next_batch_without_duplicating_first_reply(tmp_path) -> None:
    from harness.state import SteeringQueue

    model = StreamingModel(model="fixture")
    model._chunks = ['{"status":"answer"}\nFirst streamed reply.\n']
    steered = False
    def observe(events):
        nonlocal steered
        if not steered and any(event.type == AgUiEventType.TEXT_MESSAGE_CONTENT for event in events):
            steered = True
            SteeringQueue(tmp_path / "state/state.db").enqueue("fixture-turn", "steering-marker-742")
            model._responses = [reply("answer", "Reply after steering")]
    events, _, _ = await run_fixture(tmp_path, model, "hello", observe=observe, max_iterations=2)
    assert model._calls == 2
    assert "steering-marker-742" in model._requests[-1]
    first_request, second_request = map(json.loads, model._requests)
    assert second_request["config"]["system_instruction"] == first_request["config"]["system_instruction"]
    assert second_request["config"]["tools"] == first_request["config"]["tools"]
    assert "".join(e.delta for e in events if e.type == AgUiEventType.TEXT_MESSAGE_CONTENT) == "First streamed reply.\nReply after steering"
    assert sum(e.type == AgUiEventType.TEXT_MESSAGE_START for e in events) == 2
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "answered"


@pytest.mark.asyncio
async def test_external_workspace_change_cannot_finish_a_streamed_answer(tmp_path, monkeypatch) -> None:
    model = StreamingModel(model="fixture")
    model._chunks = ['{"status":"answer"}\nPublic explanation.\n']
    fingerprint = "initial"
    monkeypatch.setattr("app.agent.workflow._workspace_fingerprint", lambda *_: fingerprint)
    def observe(events):
        nonlocal fingerprint
        if any(event.type == AgUiEventType.TEXT_MESSAGE_CONTENT for event in events):
            fingerprint = "changed externally"
    with pytest.raises(ValueError, match="changed during a streamed reply"):
        await run_fixture(tmp_path, model, "hello", observe=observe)


@pytest.mark.asyncio
async def test_greeting_finishes_in_one_model_call_without_tools_or_verification(tmp_path) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [reply("answer", "Hello!")]
    events, _, _ = await run_fixture(tmp_path, model, "hello")
    assert model._calls == 1
    assert [e.delta for e in events if e.type == AgUiEventType.TEXT_MESSAGE_CONTENT] == ["Hello!"]
    assert not any(e.type == AgUiEventType.TOOL_CALL_START for e in events)
    results = [e.value for e in events if e.name == "coding.workflow.output"]
    assert results == [{"status": "answered", "verified": False, "changed_paths": []}]


@pytest.mark.asyncio
async def test_terminal_continue_does_not_start_a_second_coding_loop(tmp_path) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [reply("continue", "I stopped early"), reply("done", "unused")]

    events, _, _ = await run_fixture(tmp_path, model, "Implement the requested feature", max_iterations=2)

    assert model._calls == 1
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "blocked"


@pytest.mark.asyncio
async def test_read_only_explanation_can_answer_without_coding_verification(tmp_path) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [[types.Part(function_call=types.FunctionCall(
        id="read1", name="read", args={"path": "README.md"},
    ))], reply("answer", "A small fixture repository.")]
    events, _, _ = await run_fixture(tmp_path, model, "Explain README.md without changing files")
    assert [e.tool_call_name for e in events if e.type == AgUiEventType.TOOL_CALL_START] == ["read"]
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "answered"


@pytest.mark.asyncio
async def test_answer_after_write_is_withheld_and_forces_verification(tmp_path, monkeypatch) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [[types.Part(function_call=types.FunctionCall(
        id="write1", name="write", args={"path": "hello.py", "content": "print('hello')\n"},
    ))], reply("answer", "Unverified completion claim"), reply("done", "Reviewed")]
    events, workspace, calls = await run_fixture(
        tmp_path,
        model,
        "Create hello.py",
        monkeypatch=monkeypatch,
        max_iterations=2,
    )
    assert (workspace / "hello.py").read_text() == "print('hello')\n"
    assert model._calls == 3
    assert "Perform the single criterion-gap review" in model._requests[-1]
    assert "Create hello.py: MISSING concrete implementation or test evidence" in model._requests[-1]
    assert "request verification immediately" in model._requests[-1]
    assert len(calls) == 1
    assert calls[0]["request"]["mode"] == "coding"
    assert calls[0]["request"]["acceptance_criteria"] == ["Create hello.py"]
    assert all(e.delta != "Unverified completion claim" for e in events)
    assert next(e.value for e in events if e.name == "coding.workflow.output")["status"] == "blocked"


@pytest.mark.asyncio
async def test_repeated_identical_verification_failure_blocks_without_unbounded_loop(
    tmp_path, monkeypatch
) -> None:
    model = ScriptedModel(model="fixture")
    model._responses = [reply("done", "Implemented") for _ in range(6)]

    events, _, calls = await run_fixture(
        tmp_path,
        model,
        "Implement the requested feature",
        monkeypatch=monkeypatch,
        max_iterations=10,
    )

    result = next(e.value for e in events if e.name == "coding.workflow.output")
    assert result["status"] == "blocked"
    assert model._calls == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_two_turns_keep_adk_history_but_reset_task_budgets_and_skills(tmp_path) -> None:
    from harness.persistence import build_service_bundle, settings_from_composition
    from harness.server.registry import SqliteRunEventStore
    from harness.server.runtime import AdkRunExecutionFactory

    model = ScriptedModel(model="fixture")
    model._responses = [reply("answer", "First reply"), reply("answer", "Second reply")]
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((Provider(model),)))
    composition = load_harness_composition(config_models=registry.config_models())
    config = composition.harness.config
    config = config.model_copy(update={"models": {
        name: value.model_copy(update={"provider": "scripted"})
        for name, value in config.models.items()
    }})
    composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services = build_service_bundle(settings_from_composition(composition.persistence, state_root=tmp_path / "state"))
    factory = AdkRunExecutionFactory(composition=composition,
        bindings=RuntimeBindings(workspace=workspace, state_root=tmp_path / "state"),
        registry=registry, services=services)
    store = SqliteRunEventStore(tmp_path / "runs.db")
    for index, prompt in enumerate(["Remember conversation-marker-731", "What did I ask before?"]):
        record, _ = store.create_run(request_id=str(index), idempotency_key=str(index),
            thread_id="conversation", user_id="user", input=prompt)
        store.update_status(record.run_id, "running")
        execution = await factory.create(record)
        assert execution.max_llm_calls == 5_000
        try:
            batches = [batch async for batch in execution.events()]
        finally:
            await execution.aclose()
        public = [event for batch in batches for event in batch.events]
        assert next(e.value for e in public if e.name == "coding.workflow.output")["status"] == "answered"
        store.update_status(record.run_id, "completed")
        session = await services.session_service.get_session(app_name=composition.app.name,
            user_id="user", session_id="conversation")
        assert session.state["harness_task_id"] == record.run_id
        if index == 0:
            # Poison only stale per-task state. The next turn must not inherit it.
            from google.adk.events import Event, EventActions
            await services.session_service.append_event(session, Event(author="fixture", actions=EventActions(state_delta={
                "estimated_task_input_tokens": 999_999_999,
                "verification_required_task": record.run_id,
                "skill_selection_initialized": True,
                "skill_context_text": "STALE_SKILL_SHOULD_NOT_LEAK",
            })))
    assert model._calls == 2
    assert "conversation-marker-731" in model._requests[1]
    assert "STALE_SKILL_SHOULD_NOT_LEAK" not in model._requests[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", ["adk-client", "model-client", "approval-client", "stream-client", "harness-client"])
async def test_pi_terminal_client_over_real_websocket_and_adk(tmp_path, fixture: str, monkeypatch) -> None:
    import asyncio
    import os
    import shutil
    import socket
    import subprocess

    import uvicorn

    from harness.ai.selection import ModelChoice, load_model_default
    from harness.persistence import build_service_bundle, settings_from_composition
    from harness.sandbox import LocalSandbox
    from harness.server.models import CatalogModel
    from harness.server.registry import RunEventBroker, SqliteRunEventStore
    from harness.server.runtime import AdkRunExecutionFactory, RunCoordinator
    from harness.server.websocket import (
        LocalBearerAuthenticator,
        WebSocketServerSettings,
        create_websocket_app,
    )

    client = Path(__file__).resolve().parents[2] / f"clients/terminal/dist/test/{fixture}-fixture.js"
    node = shutil.which("node")
    if not node or not client.is_file():
        pytest.skip("run npm ci && npm run build in clients/terminal for the cross-language gate")
    model = ScriptedModel(model="fixture")
    model._gate = asyncio.Event()
    model._responses = [reply("answer", "First reply"), [types.Part(function_call=types.FunctionCall(
        id="read1", name="read", args={"path": "README.md"},
    ))], reply("answer", "Repository explanation"), reply("answer", "Final queued reply")]
    if fixture == "stream-client":
        model = StreamingModel(model="fixture")
        model._chunks = ['{"status":"answer"}\nHello ', '**streaming** reader.\n']
        model._released = asyncio.Event()
    elif fixture == "harness-client":
        model._gate = None
        model._responses = [[types.Part(text="Alternate harness reply.")]]
    models = {"alpha": model}
    commands: list[str] = []
    if fixture == "approval-client":
        model._gate = None
        model._responses = []
        for action in ("approved", "denied", "cancelled"):
            model._responses.append([types.Part(function_call=types.FunctionCall(
                id=f"bash-{action}", name="bash", args={"command": f"command printf fixture-{action}"}))])
            if action != "cancelled":
                model._responses.append(reply("blocked", "Fixture stops after the tool result"))
        execute = LocalSandbox.execute
        def recorded_execute(self, request):
            commands.append(request.command)
            return execute(self, request)
        monkeypatch.setattr(LocalSandbox, "execute", recorded_execute)
        model._responses.append(reply("done", "Verification complete"))
    if fixture == "model-client":
        model.model = "alpha"
        model._responses = [reply("answer", "Alpha fixture reply")]
        for name in ("beta", "gamma"):
            models[name] = ScriptedModel(model=name)
            models[name]._responses = [reply("answer", f"{name} fixture reply")]
    if fixture == "harness-client":
        registry = HarnessRegistry()
        registry.register(AlternateHarnessFactory(model))
        composition = parse_harness_composition({"schema_version": 1, "app": {"name": "alternate_fixture"},
            "harness": {"implementation": "alternate_fixture_v1", "api_version": 1,
                "required_capabilities": ["streaming"], "config": {"agent_name": "alternate_root"}},
            "server": {"protocol": "ag_ui_websocket_v1"}}, config_models=registry.config_models())
    else:
        registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((Provider(models),)))
        composition = load_harness_composition(config_models=registry.config_models())
        config = composition.harness.config
        config = config.model_copy(update={"models": {
            name: value.model_copy(update={"provider": "scripted", "name": "alpha"}) for name, value in config.models.items()
        }})
        composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("Bridge fixture.\n")
    if fixture == "approval-client":
        subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    if fixture == "model-client":
        skill = workspace / ".agents/skills/python-checks"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: python-checks\ndescription: Check Python fixture requests\n---\nPRIVATE_SKILL_BODY\n")
        (workspace / "AGENTS.md").write_text("PRIVATE_PROJECT_INSTRUCTION\n")
    state_root = tmp_path / "state"
    services = build_service_bundle(settings_from_composition(composition.persistence, state_root=state_root))
    coordinator = RunCoordinator(store=SqliteRunEventStore(state_root / "runs.db"), broker=RunEventBroker(),
        execution_factory=AdkRunExecutionFactory(composition=composition,
            bindings=RuntimeBindings(workspace=workspace, state_root=state_root, project_trusted=fixture == "model-client"), registry=registry, services=services))
    if fixture != "harness-client":
        assert coordinator.models is not None
        coordinator.models._catalog = lambda: tuple(CatalogModel(choice=ModelChoice(provider="scripted", name=name), display_name=name) for name in models)
    token = "synthetic-test-token-" + "x" * 32
    app = create_websocket_app(coordinator, authenticator=LocalBearerAuthenticator(token),
        settings=WebSocketServerSettings(path="/v1/agent"))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        process = await asyncio.create_subprocess_exec(node, str(client),
            env={"PATH": os.environ.get("PATH", ""), "ADK_TEST_URL": f"ws://127.0.0.1:{port}/v1/agent", "ADK_TEST_TOKEN": token},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if model._gate is not None:
            async with asyncio.timeout(15):
                while True:
                    if process.returncode is not None:
                        stdout, stderr = await process.communicate()
                        pytest.fail(f"Terminal exited before queuing follow-ups: {stderr.decode()}")
                    threads = coordinator.conversations.store.threads("local-user")
                    if threads and len(coordinator.conversations.store.pending("local-user", threads[0].thread_id)) == (1 if fixture == "model-client" else 2):
                        break
                    await asyncio.sleep(0.01)
            model._gate.set()
        if fixture == "stream-client":
            assert isinstance(model, StreamingModel) and model._released is not None
            assert process.stdout is not None
            phase = await asyncio.wait_for(process.stdout.readline(), timeout=15)
            assert json.loads(phase) == {"phase": "partial-resumed"}
            assert not model._finished
            model._released.set()
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=40)
        assert process.returncode == 0, stderr.decode()
        if fixture == "stream-client":
            assert json.loads(stdout) == {"streamed": True, "resumed": True, "entries": 2}
            assert model._calls == 1
        elif fixture == "model-client":
            assert json.loads(stdout) == {"turns": 3, "active_model_preserved": True, "default": "gamma", "resumed": True}
            assert [(name, model._calls) for name, model in models.items()] == [("alpha", 1), ("beta", 1), ("gamma", 1)]
            assert load_model_default(state_root).name == "gamma"
            assert "First fixture turn" in models["beta"]._requests[0]
            assert "PRIVATE_SKILL_BODY" in models["alpha"]._requests[0]
        elif fixture == "approval-client":
            assert json.loads(stdout) == {"approved": True, "denied": True, "cancelled": True, "resumed": True, "verified": True}
            assert commands == ["command printf fixture-approved", "git diff --check", "command printf fixture-verification"]
            assert model._calls == 6
        elif fixture == "harness-client":
            assert json.loads(stdout) == {"harness": "Alternate fixture harness", "reply": "Alternate harness reply."}
            assert model._calls == 1
        else:
            assert json.loads(stdout) == {"turns": 3, "entries": 7, "status": "answered"}
            assert model._calls == 4
            assert "bridge-marker-731" in model._requests[1]
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
        listener.close()
