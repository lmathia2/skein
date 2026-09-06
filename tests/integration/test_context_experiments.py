"""Offline context ablations through the production factory and real ADK Runner.

Scripted responses test wiring, never model quality; no provider credentials needed.
"""
from __future__ import annotations

import json
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk import Runner
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from app.agent.factory import default_harness_registry
from harness.adk.context import ContextWindowPlugin, _serialized
from harness.ai import ClosedAdkModelProviderRegistry
from harness.config import RuntimeBindings, load_harness_composition, parse_harness_composition
from harness.config.models import ContextConfig
from harness.evals.context_benchmark import benchmark_scan
from harness.evals.context_cases import CASE_IDS, prepare_case, verify_case
from harness.evals.context_driver import run_phase, run_schedule
from harness.evals.runner import EvaluationRunRequest
from harness.ledger import open_ledger
from harness.models import TaskLedger, TaskRequest
from harness.server.bootstrap import build_server_assembly
from harness.state import EventKind, JsonlEventStore

PROFILES = Path(__file__).resolve().parents[2] / "harness/config/profiles"


class CapturingModel(BaseLlm):
    _responses: list[list[types.Part]] = PrivateAttr(default_factory=list)
    _requests: list[dict] = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        del stream
        self._requests.append(llm_request.model_dump(mode="json", exclude_none=True))
        assert self._responses, "unexpected extra model call"
        yield LlmResponse(content=types.Content(role="model", parts=self._responses.pop(0)))


class ScriptedProvider:
    provider_id = "context_fixture"

    def __init__(self, model: CapturingModel) -> None:
        self.model = model

    def build_model(self, config, *, secrets, bindings=None):
        del config, secrets, bindings
        return self.model


async def capture_trial(root: Path, workspace: Path, profile: str, calls: int = 1,
                        commands: list[str] | None = None, packet_tokens: int | None = None) -> list[dict]:
    model = CapturingModel(model="context-fixture")
    model._responses = [[types.Part(function_call=types.FunctionCall(
        id=f"read-{index}", name="read", args={"path": "evidence.txt"},
    ))] for index in range(calls)]
    if commands is not None:
        model._responses = [[types.Part(function_call=types.FunctionCall(
            id=f"memory-{index}", name="bash", args={"command": command},
        ))] for index, command in enumerate(commands)]
    model._responses.append([types.Part(text=json.dumps({
        "status": "blocked" if commands is not None else "answer",
        "message": "Fixture capture complete; no completion claim.",
    }))])
    registry = default_harness_registry(
        model_providers=ClosedAdkModelProviderRegistry((ScriptedProvider(model),)),
    )
    payload = load_harness_composition(PROFILES / f"context-{profile}.yaml").model_dump(mode="json")
    config = payload["harness"]["config"]
    config["models"]["coding"]["provider"] = "context_fixture"
    config["models"]["coding"]["name"] = "context-fixture"
    config["tools"]["search"]["backend"] = "disabled"
    if packet_tokens is not None:
        config["context"]["work_packet_tokens"] = packet_tokens
    composition = parse_harness_composition(payload, config_models=registry.config_models())
    assembly = registry.build(composition, RuntimeBindings(
        workspace=workspace, state_root=root, task_id="context-fixture",
    ))
    sessions = InMemorySessionService()
    await sessions.create_session(app_name=assembly.app.name, user_id="fixture", session_id="fixture")
    runner = Runner(app=assembly.app, session_service=sessions)
    events = [event async for event in runner.run_async(
        user_id="fixture", session_id="fixture",
        new_message=types.Content(role="user", parts=[types.Part(text="Inspect evidence.txt without changing files.")]),
    )]
    assert events and not model._responses
    return model._requests


@pytest.mark.parametrize("profile", [
    "baseline", "capture", "shadow", "retrieval", "notes", "fresh", "recovery",
    "ptc", "continuity", "reuse", "prior-runs", "windows",
])
def test_context_profiles_parse_and_keep_safety(profile: str) -> None:
    config = load_harness_composition(PROFILES / f"context-{profile}.yaml").harness.config
    assert not config.safety.allow_network
    assert not config.safety.allow_dependency_install
    assert not config.safety.allow_git_history_mutation
    assert not config.safety.allow_unknown_commands


def test_window_and_retrieval_profiles_isolate_one_setting() -> None:
    baseline = load_harness_composition(PROFILES / "context-capture.yaml").model_dump(mode="json")
    windows = load_harness_composition(PROFILES / "context-windows.yaml").model_dump(mode="json")
    retrieval = load_harness_composition(PROFILES / "context-retrieval.yaml").model_dump(mode="json")
    baseline["harness"]["config"]["context"]["window_management"] = True
    assert baseline == windows
    windows["harness"]["config"]["memory"]["context_programs"]["mode"] = "active"
    assert windows == retrieval


@pytest.mark.asyncio
async def test_capture_and_shadow_preserve_actual_provider_request(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = "early-evidence: exact-value-781\n"
    (workspace / "evidence.txt").write_text(evidence)
    results = [await capture_trial(tmp_path / name, workspace, name)
               for name in ("baseline", "capture", "shadow")]
    assert results[0] == results[1] == results[2]
    assert evidence.strip() in json.dumps(results[0][-1])
    # Independent filesystem assertion: a model answer is not proof of non-mutation.
    assert (workspace / "evidence.txt").read_text() == evidence


@pytest.mark.asyncio
async def test_fifty_tool_calls_keep_provider_prefix_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "evidence.txt").write_text("selected evidence\n")
    requests = await capture_trial(tmp_path / "state", workspace, "baseline", calls=50)
    assert len(requests) == 51
    prefixes = [request["config"]["system_instruction"] for request in requests]
    assert all(prefix == prefixes[0] for prefix in prefixes)
    assert "selected evidence" not in json.dumps(prefixes[0])
    assert (workspace / "evidence.txt").read_text() == "selected evidence\n"


@pytest.mark.parametrize("case", CASE_IDS)
def test_pilot_fixture_independent_verifier(tmp_path: Path, case: str) -> None:
    trial = tmp_path / case
    prepare_case(trial, case, PROFILES / "context-baseline.yaml")
    assert not verify_case(trial)
    (trial / "workspace/answer.json").write_text((trial / "expected.json").read_text())
    if case == "interrupted-mutation":
        (trial / "workspace/counter.txt").write_text("1\n")
    assert verify_case(trial)
    (trial / "workspace/evidence.jsonl").write_text("corrupted\n")
    assert not verify_case(trial)
    with pytest.raises(FileExistsError):
        prepare_case(trial, case, PROFILES / "context-baseline.yaml")


def test_scan_benchmark_reports_completeness_without_provider_calls() -> None:
    report = benchmark_scan(3, attempts=1)
    assert report["events"] == 3
    assert report["statuses"] == ["ok"]
    assert report["complete"] is True
    with pytest.raises(ValueError):
        benchmark_scan(100001)


@pytest.mark.asyncio
async def test_active_history_and_full_set_count_reach_real_provider(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    ledger = open_ledger(state, "jsonl")
    for index in range(120):
        ledger.append(task_id="context-fixture", source="context", source_id=f"seed-{index}",
                      kind="context.history", observed_at=datetime(2025, 1, 1, tzinfo=UTC),
                      recorded_at=datetime(2025, 1, 1, tzinfo=UTC),
                      payload={"role": "user", "parts": [{"text": f"seed-evidence-{index:03}"}]})
    requests = await capture_trial(state, workspace, "retrieval", commands=[
        "memory history --query seed-evidence-000",
        "memory query --program events.count --kinds context.history --as-of 2025-01-02T00:00:00Z",
    ])
    responses = [part["function_response"]["response"]
                 for content in requests[-1]["contents"] for part in content.get("parts", [])
                 if "function_response" in part]
    assert "seed-evidence-000" in json.dumps(responses)
    assert any('"count":120' in json.dumps(response).replace(" ", "").replace('\\"', '"')
               for response in responses)


@pytest.mark.asyncio
async def test_fresh_provider_requests_roll_over_and_recover_exact_evidence(tmp_path: Path) -> None:
    from harness.state import EventKind, JsonlEventStore

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    ledger = open_ledger(state, "jsonl")
    for index in range(10):
        ledger.append(task_id="context-fixture", source="context", source_id=f"seed-{index}",
                      kind="context.history", payload={"role": "user", "parts": [{
                          "text": f"early-secret-{index:03} " + "padding " * 50,
                      }]})
    requests = await capture_trial(state, workspace, "fresh", packet_tokens=2000, commands=[
        *["memory history --limit 3" for _ in range(15)],
        "memory history --query early-secret-000 --limit 1",
    ])
    epochs = [event for event in JsonlEventStore(state / "events").read("context-fixture")
              if event.kind == EventKind.COMPACTION_CREATED and event.payload.get("context_epoch")]
    assert len(epochs) >= 3
    assert all(event.payload["tokens_after"] <= 2000 for event in epochs)
    assert "early-secret-000" in json.dumps(requests[-1]["contents"])
    assert all(request["config"]["system_instruction"] == requests[0]["config"]["system_instruction"]
               for request in requests)


@pytest.mark.asyncio
async def test_schedule_phase_uses_real_coordinator_and_existing_result_contract(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    prepare_case(trial, "later-run-recall", PROFILES / "context-baseline.yaml")
    model = CapturingModel(model="fixture")
    model._responses = [[types.Part(text=json.dumps({"status": "answer", "message": "Stored."}))]]
    provider = ScriptedProvider(model)
    provider.provider_id = "openrouter"
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((provider,)))

    def build(**kwargs):
        return build_server_assembly(**kwargs, registry=registry)

    request = EvaluationRunRequest(
        workspace=trial / "workspace", state_root=trial / "state", auth_state_root=trial / "auth",
        task_id="schedule-fixture", prompt="Return the prior fact", provider="openrouter",
        model="fixture", config_template=PROFILES / "context-baseline.yaml",
    )
    result = await run_phase(request, trial, "source", assembly_builder=build)
    assert result.schema_version == "skein-eval-run-v1"
    assert result.status == "answered"
    assert len(model._requests) == 1
    assert (trial / "result-source.json").exists()
    with pytest.raises(FileExistsError):
        run_schedule(request, trial)


def scripted_driver_main() -> None:
    """Dedicated child entrypoint for subprocess tests; never selects a real provider."""
    import harness.evals.context_driver as driver

    model = CapturingModel(model="fixture")
    if sys.argv[-1] == "fault":
        model._responses = [[types.Part(function_call=types.FunctionCall(
            id="counter-write", name="write", args={"path": "counter.txt", "content": "1\n"},
        ))]]
    else:
        model._responses = [[types.Part(text=json.dumps({"status": "answer", "message": "Fixture."}))]]
    provider = ScriptedProvider(model)
    provider.provider_id = "openrouter"
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((provider,)))
    driver.build_server_assembly = lambda **kwargs: build_server_assembly(**kwargs, registry=registry)
    driver.main()


@pytest.mark.parametrize("case,profile", [
    ("later-run-recall", "baseline"), ("interrupted-mutation", "recovery"),
])
def test_schedule_drives_owned_process_restart_without_live_provider(tmp_path: Path, case: str, profile: str) -> None:
    trial = tmp_path / "trial"
    prepare_case(trial, case, PROFILES / f"context-{profile}.yaml")
    request = EvaluationRunRequest(
        workspace=trial / "workspace", state_root=trial / "state", auth_state_root=trial / "auth",
        task_id="schedule-fixture", prompt="Fixture task", provider="openrouter", model="fixture",
        config_template=PROFILES / f"context-{profile}.yaml", wall_time_seconds=30,
    )
    script = (f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
              "from test_context_experiments import scripted_driver_main; scripted_driver_main()")
    report = run_schedule(request, trial, child_command=(sys.executable, "-c", script))
    assert report["schedule_satisfied"], [path.read_text() for path in trial.glob("process-*.log")]
    assert not report["independent_verification"]  # Scripted text never passes the output oracle.
    if case == "interrupted-mutation":
        assert report["safe_recovery_block"]
        assert (trial / "workspace/counter.txt").read_text() == "1\n"


def _adversarial_context(tmp_path):
    events = JsonlEventStore(tmp_path / "events")
    task = TaskLedger.from_request(TaskRequest(goal="Inspect evidence", constraints=["No network"]),
                                  task_id="task", workspace_id="workspace", base_revision="base")
    events.append("task", EventKind.TASK_CREATED, {"ledger": task.model_dump(mode="json")})
    plugin = ContextWindowPlugin(
        events=events, ledger=open_ledger(tmp_path, "jsonl"),
        config=ContextConfig(work_packet_tokens=2000, reconstruction="fresh", window_management=True),
        handoff=lambda _: {"note": {"status": "ok"}},
    )
    context = SimpleNamespace(agent_name="coding_worker", invocation_id="invocation",
                              state={"task_id": "task"})
    return plugin, context


@pytest.mark.asyncio
async def test_fresh_rollover_does_not_discard_unconsumed_tool_result(tmp_path: Path) -> None:
    plugin, context = _adversarial_context(tmp_path)
    request = LlmRequest(contents=[
        types.Content(role="user", parts=[types.Part(text="old history " * 1000)]),
        types.Content(role="model", parts=[types.Part.from_function_call(name="read", args={"path": "evidence"})]),
        types.Content(role="user", parts=[types.Part.from_function_response(name="read", response={"text": "NEW-UNCONSUMED-RESULT-42"})]),
    ])
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert "NEW-UNCONSUMED-RESULT-42" in _serialized(request.contents)


@pytest.mark.asyncio
async def test_epoch_survives_ephemeral_steering_appended_between_adk_calls(tmp_path: Path) -> None:
    from harness.adk import SteeringPlugin
    from harness.state import SteeringQueue

    plugin, context = _adversarial_context(tmp_path)
    context.state.update({"steering_owner": "worker", "steering_packet_message_ids": []})
    queue = SteeringQueue(tmp_path / "steering.db")
    steering = SteeringPlugin(queue=queue, event_store=plugin.events, lease_seconds=60, mark_context=True)
    queue.enqueue("task", "Preserve new constraint")
    initial = types.Content(role="user", parts=[types.Part(text="old history " * 1000)])
    # SteeringPlugin appends this same leased message independently to each request;
    # ADK session history itself never contains that plugin-generated message.
    first = LlmRequest(contents=[initial])
    await steering.before_model_callback(callback_context=context, llm_request=first)
    await plugin.before_model_callback(callback_context=context, llm_request=first)
    request = LlmRequest(contents=[initial,
        types.Content(role="model", parts=[types.Part.from_function_call(name="read", args={})]),
        types.Content(role="user", parts=[types.Part.from_function_response(name="read", response={"text": "result"})]),
    ])
    await steering.before_model_callback(callback_context=context, llm_request=request)
    await plugin.before_model_callback(callback_context=context, llm_request=request)
    assert "Preserve new constraint" in _serialized(request.contents)
