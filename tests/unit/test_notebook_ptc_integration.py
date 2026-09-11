from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from app.agent.factory import default_harness_registry
from app.agent.ptc import RegisteredCapability
from harness.adapters.providers.codex_responses import (
    build_codex_request_body,
    provider_request_profile,
)
from harness.core.agent import SteeringCommand
from harness.core.config import (
    RuntimeBindings,
    SkeinConfig,
    load_harness_composition,
    parse_harness_composition,
)
from harness.evidence.state import EventKind, JsonlEventStore
from harness.execution.safety.redaction import SecretRedactor
from harness.execution.tools.adk_adapter import AdkCodingTools, create_adk_tools


def _enabled_composition():
    composition = load_harness_composition()
    config = cast(SkeinConfig, composition.harness.config)
    enabled = config.model_copy(
        update={"notebook_ptc": config.notebook_ptc.model_copy(update={"enabled": True})}
    )
    return composition.model_copy(
        update={"harness": composition.harness.model_copy(update={"config": enabled})}
    )


@pytest.mark.asyncio
async def test_notebook_ptc_rejects_a_different_task_scope(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="owned"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc,
    )
    assert worker.execute_code is not None
    try:
        with pytest.raises(ValueError, match="outside this owned run"):
            await worker.execute_code(
                "42",
                tool_context=SimpleNamespace(
                    state={"task_id": "other"}, invocation_id="inv", function_call_id="call"
                ),
            )
    finally:
        assert worker.close is not None
        worker.close()


@pytest.mark.asyncio
async def test_cancelled_tool_drains_synchronous_effect_before_return(tmp_path: Path) -> None:
    composition = _enabled_composition()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def effect(**_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        finished.set()
        return {"status": "ok"}

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=effect, bash=effect, edit=effect, write=effect),
    )
    pending = asyncio.create_task(worker.bash("bounded command"))
    assert await asyncio.to_thread(entered.wait, 5)
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert finished.is_set()


@pytest.mark.asyncio
async def test_parallel_reads_overlap_and_keep_ordered_receipts(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")
    barrier = threading.Barrier(2)

    def read(*, path: str, **_kwargs):
        barrier.wait(timeout=2)
        return {"status": "ok", "model_text": path, "data": {"text": path}}

    def unused(**_kwargs):
        raise AssertionError("unexpected capability")

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            "agent.parallel(["
            "{'operation': 'fs.read', 'arguments': {'path': 'a'}},"
            "{'operation': 'fs.read', 'arguments': {'path': 'b'}}"
            "])",
            tool_context=SimpleNamespace(
                state={"task_id": "task"}, invocation_id="inv", function_call_id="call"
            ),
        )
    finally:
        assert worker.close is not None
        worker.close()

    assert result["status"] == "ok"
    assert "'text': 'a'" in result["model_text"]
    assert result["model_text"].index("'text': 'a'") < result["model_text"].index("'text': 'b'")
    capabilities = [event for event in events.read("task") if event.kind.startswith("capability.")]
    assert [event.kind for event in capabilities[:2]] == [
        EventKind.CAPABILITY_REQUESTED,
        EventKind.CAPABILITY_REQUESTED,
    ]
    assert [event.payload["operation_id"] for event in capabilities[:2]] == [
        f"{result['attempt_id']}:1",
        f"{result['attempt_id']}:2",
    ]


@pytest.mark.asyncio
async def test_parallel_rejects_effects_before_dispatch(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    called = False

    def effect(**_kwargs):
        nonlocal called
        called = True
        return {"status": "ok"}

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=effect, bash=effect, edit=effect, write=effect),
        ptc_config=config.notebook_ptc,
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            "agent.parallel([{'operation': 'fs.write', 'arguments': {'path': 'x'}}])"
        )
    finally:
        assert worker.close is not None
        worker.close()

    assert result["status"] == "error"
    assert called is False


@pytest.mark.asyncio
async def test_capability_call_limit_stops_before_dispatch(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    ptc_config = config.notebook_ptc.model_copy(update={"max_capability_calls_per_cell": 2})
    events = JsonlEventStore(tmp_path / "state" / "events")
    calls: list[str] = []

    def read(*, path: str, **_kwargs):
        calls.append(path)
        return {"status": "ok", "model_text": path, "data": {"text": path}}

    def unused(**_kwargs):
        raise AssertionError("unexpected capability")

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
        ptc_config=ptc_config,
        event_store=events,
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            "[agent.fs.read(name) for name in ['a', 'b', 'c']]",
            tool_context=SimpleNamespace(
                state={"task_id": "task"}, invocation_id="inv", function_call_id="call"
            ),
        )
    finally:
        assert worker.close is not None
        worker.close()

    assert result["status"] == "error"
    assert calls == ["a", "b"]
    requested = [
        event for event in events.read("task") if event.kind == EventKind.CAPABILITY_REQUESTED
    ]
    assert len(requested) == 2


@pytest.mark.asyncio
async def test_failed_read_does_not_require_effect_reconciliation(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")

    def read(**_kwargs):
        return {"status": "error", "model_text": "FileNotFoundError: missing"}

    def unused(**_kwargs):
        raise AssertionError("unexpected capability")

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(
                workspace=tmp_path,
                state_root=tmp_path / "state",
                task_id="task",
            ),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    try:
        failed = await worker.execute_code('agent.fs.read("missing")["data"]["text"]')
        continued = await worker.execute_code("40 + 2")
    finally:
        assert worker.close is not None
        worker.close()

    assert failed["status"] == "error"
    assert failed["effect"] == "none"
    assert continued["status"] == "ok"
    assert continued["model_text"] == "42"
    capability = next(
        event for event in events.read("task") if event.kind == EventKind.CAPABILITY_FAILED
    )
    assert capability.payload["effect"] == "none"


@pytest.mark.asyncio
async def test_nested_failure_has_uniform_data_envelope(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)

    def read(**_kwargs):
        return {"status": "error", "model_text": "missing"}

    def unused(**_kwargs):
        raise AssertionError("unexpected capability")

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
        ptc_config=config.notebook_ptc,
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            "failed = agent.fs.read('missing')\nfailed['status'], failed['data']"
        )
    finally:
        assert worker.close is not None
        worker.close()

    assert result["status"] == "ok"
    assert result["model_text"] == "('error', {})"


@pytest.mark.asyncio
async def test_pre_execution_failure_preserves_kernel_state(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc,
    )
    assert worker.execute_code is not None
    try:
        await worker.execute_code("value = 42")
        failed = await worker.execute_code("value =")
        restored = await worker.execute_code("value")
    finally:
        assert worker.close is not None
        worker.close()

    assert failed["failure_stage"] == "parse"
    assert failed["state_preserved"] is True
    assert failed["error_line"] == 1
    assert restored["model_text"] == "42"


@pytest.mark.asyncio
async def test_snapshot_policy_restores_safe_heap_after_runtime_failure(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    reads = 0

    def read(**_kwargs):
        nonlocal reads
        reads += 1
        return {"status": "ok", "data": {"text": "observed"}}

    def unused(**_kwargs):
        raise AssertionError("unexpected capability")

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
        ptc_config=config.notebook_ptc.model_copy(update={"state": "snapshot"}),
    )
    assert worker.execute_code is not None
    try:
        await worker.execute_code("value = [1, 2]")
        failed = await worker.execute_code("agent.fs.read('x')\nvalue.append(3)\n1 / 0")
        restored = await worker.execute_code("value")
    finally:
        assert worker.close is not None
        worker.close()

    assert failed["state_preserved"] is True
    assert restored["model_text"] == "[1, 2]"
    assert reads == 1


@pytest.mark.asyncio
async def test_conversation_notebook_restores_only_safe_cells_with_run_attribution(
    tmp_path: Path,
) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prior: tuple = ()
    for task_id in ("a", "b"):
        state = tmp_path / task_id
        events = JsonlEventStore(state / "events")
        worker = build_coding_worker(
            settings_from_composition(
                composition, RuntimeBindings(workspace=workspace, state_root=state, task_id=task_id)
            ),
            cast(BaseLlm, "test-model"),
            ptc_config=config.notebook_ptc,
            event_store=events,
            conversation_notebook_id="conversation",
            prior_notebook_events=prior,
            notebook_root=tmp_path / "conversation",
        )
        assert worker.execute_code is not None and worker.close is not None
        try:
            if task_id == "a":
                assert (await worker.execute_code("value = 40"))["status"] == "ok"
                assert (await worker.execute_code("dependent = value + 1"))["status"] == "ok"
            else:
                assert (await worker.execute_code("value + 2"))["model_text"] == "42"
                assert (await worker.execute_code("dependent"))["status"] == "error"
        finally:
            worker.close()
        prior = (*prior, *events.read(task_id))
    notebook = json.loads((tmp_path / "conversation" / "conversation.ipynb").read_text())
    assert {cell["metadata"]["agent"]["task_id"] for cell in notebook["cells"]} == {"a", "b"}
    assert set(notebook["metadata"]["agent"]["source_watermarks"]) == {"a", "b"}


def test_factory_exposes_only_execute_code_when_notebook_ptc_is_enabled(tmp_path: Path) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(config_models=registry.config_models()).model_dump(
        mode="python"
    )
    payload["harness"]["config"]["notebook_ptc"]["enabled"] = True
    composition = parse_harness_composition(payload, config_models=registry.config_models())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assembly = registry.build(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state", task_id="task"),
    )

    worker = cast(LlmAgent, assembly.agents["coding_worker"])
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in worker.tools}
    assert tool_names == {"execute_code"}
    assert worker.include_contents == "default"
    assert "include_contents" in worker.model_fields_set
    assert "Capability calls\nreturn mappings" in worker.static_instruction
    assert "agent.parallel" in worker.static_instruction
    assert "`open()`" in worker.static_instruction
    assert assembly.build_info.tool_names == ("execute_code",)
    assert "never parse notebook JSON" in worker.static_instruction
    resources = registry.resources(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state", task_id="task"),
    )
    assert resources is not None
    assert {item.name for item in resources.items if item.kind == "tool"} == {"execute_code"}
    assert assembly.close is not None
    assembly.close()


def test_default_factory_keeps_main_four_tool_path_without_canonical_memory(
    tmp_path: Path,
) -> None:
    registry = default_harness_registry()
    composition = load_harness_composition(config_models=registry.config_models())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    assembly = registry.build(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state, task_id="task"),
    )
    try:
        worker = cast(LlmAgent, assembly.agents["coding_worker"])
        tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in worker.tools}
        assert tool_names == {"read", "bash", "edit", "write"}
        assert worker.include_contents == "none"
        assert "include_contents" in worker.model_fields_set
        assert not (state / "ledger.duckdb").exists()
        assert not (state / "ledger.jsonl").exists()
    finally:
        assert assembly.close is None


def test_pi_memory_keeps_simple_adk_history_without_a_second_ledger(tmp_path: Path) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(config_models=registry.config_models()).model_dump(
        mode="python"
    )
    payload["harness"]["config"]["memory"].update(enabled=True, implementation="pi")
    composition = parse_harness_composition(payload, config_models=registry.config_models())
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()

    assembly = registry.build(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state, task_id="task"),
    )

    assert assembly.build_info.tool_names == ("read", "bash", "edit", "write")
    assert assembly.app.events_compaction_config is not None
    assert assembly.app.events_compaction_config.token_threshold == 183_616
    assert (
        "## Critical Context" in assembly.app.events_compaction_config.summarizer._prompt_template
    )
    assert not (state / "ledger.jsonl").exists()
    assert not (state / "ledger.duckdb").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger_backend", ["jsonl", "duckdb"])
async def test_configured_memory_backend_captures_the_same_runtime_event(
    tmp_path: Path, ledger_backend: str
) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(config_models=registry.config_models()).model_dump(
        mode="python"
    )
    payload["harness"]["config"]["memory"] = {
        "enabled": True,
        "ledger": ledger_backend,
        "retrieval": "lexical",
    }
    composition = parse_harness_composition(payload, config_models=registry.config_models())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    assembly = registry.build(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state, task_id="task"),
    )

    assert assembly.controls is not None
    receipt = await assembly.controls.steer(
        SteeringCommand(run_id="task", content="remember this", idempotency_key="one")
    )
    assert receipt.accepted
    if ledger_backend == "jsonl":
        from harness.evidence.ledger import JsonlLedgerStore

        events = JsonlLedgerStore(state / "ledger.jsonl").read("task")
    else:
        from harness.evidence.ledger import DuckDbLedgerStore

        events = DuckDbLedgerStore(state / "ledger.duckdb").read("task")
    assert [(event.kind, event.payload["content"]) for event in events] == [
        ("steering.queued", "remember this")
    ]


def test_factory_rejects_notebook_ptc_with_docker(tmp_path: Path) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(config_models=registry.config_models()).model_dump(
        mode="python"
    )
    payload["harness"]["config"]["notebook_ptc"]["enabled"] = True
    payload["harness"]["config"]["sandbox"] = {
        "kind": "docker",
        "image": "example.invalid/harness:latest",
    }

    composition = parse_harness_composition(payload, config_models=registry.config_models())
    with pytest.raises(ValueError, match="requires the local sandbox"):
        registry.build(
            composition,
            RuntimeBindings(
                workspace=tmp_path / "workspace",
                state_root=tmp_path / "state",
                task_id="task",
            ),
        )


def test_factory_rejects_unwired_live_lance_retrieval(tmp_path: Path) -> None:
    registry = default_harness_registry()
    payload = load_harness_composition(config_models=registry.config_models()).model_dump(
        mode="python"
    )
    payload["harness"]["config"]["memory"] = {
        "enabled": True,
        "ledger": "duckdb",
        "retrieval": "lance",
    }
    composition = parse_harness_composition(payload, config_models=registry.config_models())

    with pytest.raises(ValueError, match="embedding provider"):
        registry.build(
            composition,
            RuntimeBindings(
                workspace=tmp_path / "workspace",
                state_root=tmp_path / "state",
                task_id="task",
            ),
        )


@pytest.mark.asyncio
async def test_notebook_native_ptc_is_one_tool_and_persists_code_state_and_effects(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-1"),
    )
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-1"),
        tool_config=config.tools,
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    execute_code_tool = worker.execute_code

    try:
        first = await execute_code_tool("value = 40")
        second = await execute_code_tool(
            'agent.fs.write("answer.txt", str(value + 2), expected_absent=True)\n'
            'agent.fs.read("answer.txt")["model_text"]'
        )
        rich = await execute_code_tool('{"image/png": b"x" * 17000, "text/plain": "plot"}')
    finally:
        assert worker.close is not None
        worker.close()

    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in worker.agent.tools
    }
    assert tool_names == {"execute_code"}
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert rich["status"] == "ok"
    assert len(rich["artifact_uris"]) == 1
    assert second["effect"] == "changed"
    assert (workspace / "answer.txt").read_text(encoding="utf-8") == "42"
    notebook_path = next((state_root / "notebooks").glob("*.ipynb"))
    assert notebook_path.exists()
    notebook_text = notebook_path.read_text(encoding="utf-8")
    assert notebook_text.count('"cell_type":"code"') == 3
    assert "application/vnd.agent.artifact+json" in notebook_text
    kinds = [event.kind for event in events.read("task-1")]
    assert kinds.count(EventKind.NOTEBOOK_CELL_ADDED) == 3
    assert EventKind.CAPABILITY_REQUESTED in kinds
    assert EventKind.CAPABILITY_COMPLETED in kinds
    terminal = next(
        event
        for event in events.read("task-1")
        if event.kind == EventKind.REPL_CELL_COMPLETED
        and event.payload["cell_id"] == second["attempt_id"]
    )
    assert terminal.payload["capability_count"] == 2
    assert terminal.payload["capability_operations"] == ["fs.write", "fs.read"]
    capability_events = [
        event
        for event in events.read("task-1")
        if event.kind == EventKind.CAPABILITY_COMPLETED
    ]
    assert len(capability_events) == 2
    for event in capability_events:
        uri = event.payload["result_artifact_uri"]
        artifact = state_root / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]
        stored = artifact.read_bytes()
        assert len(stored) == event.payload["result_bytes"]
        assert event.payload["result_media_type"] == "application/json"
        assert json.loads(stored)["status"] == "ok"
        assert uri in event.payload["artifact_refs"]
    assert "notebook_path" not in second and "state_delta" not in second
    assert kinds[-1] == EventKind.NOTEBOOK_SNAPSHOTTED
    assert '"tools":["execute_code"]' in settings.static_prefix
    assert "During verify, group already-selected" in settings.static_instruction


@pytest.mark.asyncio
async def test_successful_complete_ptc_shell_result_becomes_validation_evidence(
    tmp_path: Path,
) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")

    def command(**_kwargs):
        return {
            "status": "ok",
            "model_text": "1 passed",
            "exit_code": 0,
            "duration_ms": 12,
            "truncated": False,
            "omitted_bytes": 0,
        }

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=command, bash=command, edit=command, write=command),
        ptc_config=config.notebook_ptc,
        event_store=events,
        workspace_fingerprint=lambda: "stable",
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            'agent.shell.run("pytest -q")',
            tool_context=SimpleNamespace(
                state={"task_id": "task", "workspace_fingerprint": "stable"},
                invocation_id="invocation",
                function_call_id="python-call",
            ),
        )
    finally:
        assert worker.close is not None
        worker.close()
    assert result["status"] == "ok"
    observed = [
        event for event in events.read("task") if event.kind == "execution.validation_observed"
    ]
    assert len(observed) == 1
    assert observed[0].payload["command"] == "pytest -q"
    assert observed[0].payload["workspace_before"] == "stable"


@pytest.mark.asyncio
async def test_worker_close_snapshots_complete_notebook_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(
            workspace=workspace,
            state_root=state_root,
            task_id="task-snapshot",
        ),
    )
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-snapshot"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    result = await worker.execute_code("answer = 42")
    message = events.append(
        "task-snapshot",
        EventKind.MESSAGE_RECORDED,
        {"role": "assistant", "content": "Final answer."},
    )
    assert worker.close is not None
    worker.close()
    worker.close()

    snapshots = [
        event
        for event in events.read("task-snapshot")
        if event.kind == EventKind.NOTEBOOK_SNAPSHOTTED
    ]
    assert len(snapshots) == 1
    payload = snapshots[0].payload
    assert payload["source_watermark"] == message.sequence
    terminal = next(
        event
        for event in events.read("task-snapshot")
        if event.kind == EventKind.REPL_CELL_COMPLETED
        and event.payload["cell_id"] == result["attempt_id"]
    )
    assert payload["kernel_epoch"] == terminal.payload["kernel_epoch"]
    digest = payload["notebook_sha256"]
    assert payload["artifact_uri"] == f"artifact://sha256/{digest}"
    artifact = state_root / "artifacts" / "sha256" / digest
    notebook = next((state_root / "notebooks").glob("*.ipynb"))
    assert artifact.read_bytes() == notebook.read_bytes()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
    document = json.loads(artifact.read_bytes())
    assert [cell["cell_type"] for cell in document["cells"]] == ["code", "markdown"]
    assert "Final answer." in "".join(document["cells"][1]["source"])


@pytest.mark.asyncio
async def test_python_routes_registered_mcp_capability_and_blocks_unknown(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-mcp"),
    )
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-mcp"),
        tool_config=config.tools,
        ptc_config=config.notebook_ptc,
        capabilities={
            "issues.search": lambda arguments: {"status": "ok", "items": [arguments["q"]]}
        },
    )
    assert worker.execute_code is not None
    result = await worker.execute_code("agent.mcp.call('issues.search', {'q': 'timeout'})")
    assert result["status"] == "ok"
    assert "timeout" in result["model_text"]
    blocked = await worker.execute_code("agent.mcp.call('missing.tool', {})")
    assert blocked["status"] == "ok"
    assert "blocked" in blocked["model_text"]
    assert worker.close is not None
    worker.close()


@pytest.mark.asyncio
async def test_python_help_exposes_registered_capability_and_project_commands(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1"\n[tool.pytest.ini_options]\n'
    )
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-help"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc,
        capabilities={
            "issues.search": RegisteredCapability(
                handler=lambda arguments: {"status": "ok", "q": arguments["q"]},
                description="Search issues",
                arguments={"type": "object", "required": ["q"]},
                result={"type": "object"},
                effect="read",
                approval="automatic",
            )
        },
    )
    assert worker.execute_code is not None
    try:
        capability = await worker.execute_code("agent.help('mcp.issues.search', details=True)")
        cli = await worker.execute_code("agent.help('cli', details=True)")
    finally:
        assert worker.close is not None
        worker.close()

    assert "Search issues" in capability["model_text"]
    assert "'effect': 'read'" in capability["model_text"]
    assert "'approval': 'automatic'" in capability["model_text"]
    assert "pyproject.toml" in cli["model_text"]
    assert "search grep --pattern TEXT" in cli["model_text"]


@pytest.mark.asyncio
async def test_nested_result_remains_in_python_state_until_explicitly_selected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-isolation"),
    )
    marker = "nested-payload-must-not-enter-model-result"
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-isolation"),
        ptc_config=config.notebook_ptc,
        capabilities={"bulk.read": lambda _arguments: {"status": "ok", "items": [marker] * 2000}},
    )
    assert worker.execute_code is not None
    try:
        selected = await worker.execute_code(
            "records = agent.mcp.call('bulk.read', {})['items']\nlen(records)"
        )
        catalog = await worker.execute_code("agent.state.describe('records')")
        reused = await worker.execute_code("len(records)")
    finally:
        assert worker.close is not None
        worker.close()

    assert selected["model_text"] == "2000"
    assert reused["model_text"] == "2000"
    assert marker not in json.dumps(selected, sort_keys=True)
    assert marker not in json.dumps(catalog, sort_keys=True)
    assert "'size': 2000" in catalog["model_text"]


@pytest.mark.asyncio
async def test_truncated_cell_output_is_reloadable_from_an_artifact(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    ptc_config = config.notebook_ptc.model_copy(update={"max_output_bytes": 1024})
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=state_root, task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=ptc_config,
        event_store=events,
    )
    assert worker.execute_code is not None
    complete = "HEAD" + ("x" * 2000) + "TAIL\n"
    try:
        result = await worker.execute_code("print('HEAD' + ('x' * 2000) + 'TAIL')")
    finally:
        assert worker.close is not None
        worker.close()

    assert result["truncated"] is True
    assert "HEAD" in result["model_text"] and "TAIL" in result["model_text"]
    assert "complete output artifacts:" in result["model_text"]
    terminal = next(
        event
        for event in events.read("task")
        if event.kind == EventKind.REPL_CELL_COMPLETED
    )
    output = terminal.payload["output_artifacts"]
    assert len(output) == 1 and output[0]["stream"] == "stdout"
    uri = output[0]["artifact_uri"]
    assert uri in result["artifact_uris"]
    artifact = state_root / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]
    assert artifact.read_text(encoding="utf-8") == complete


@pytest.mark.asyncio
async def test_ptc_artifacts_are_task_scoped_reloadable_and_explicitly_publishable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("durable payload", encoding="utf-8")
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    try:
        await worker.execute_code("agent.fs.read('note.txt')")
        loaded = await worker.execute_code(
            "uri = agent.artifacts.list()['data']['artifacts'][0]['uri']\n"
            "agent.artifacts.load(uri)['data']['text']"
        )
        first = await worker.execute_code(
            "agent.artifacts.publish({'answer': 42}, 'Final Report', 'User deliverable')"
        )
        second = await worker.execute_code(
            "agent.artifacts.publish({'answer': 42}, 'Final Report', 'User deliverable')"
        )
        published = await worker.execute_code(
            "[item for item in agent.artifacts.list()['data']['artifacts'] "
            "if item.get('published')]"
        )
        denied = await worker.execute_code(
            "agent.artifacts.load('artifact://sha256/' + ('0' * 64))"
        )
        denied_name = await worker.execute_code("agent.artifacts.publish('x', '../../')")
    finally:
        assert worker.close is not None
        worker.close()

    assert "durable payload" in loaded["model_text"]
    assert "Final_Report" in first["model_text"]
    assert first["artifact_uris"] == second["artifact_uris"]
    assert "'published': True" in published["model_text"]
    assert "PermissionError" in denied["model_text"]
    assert "ValueError" in denied_name["model_text"]
    publish_events = [
        event for event in events.read("task") if event.kind == EventKind.ARTIFACT_PUBLISHED
    ]
    assert len(publish_events) == 2
    assert publish_events[0].payload["artifact_uri"] == publish_events[1].payload["artifact_uri"]
    assert publish_events[0].payload["host_visible"] is True


@pytest.mark.asyncio
async def test_ptc_redacts_registered_results_before_python_and_artifact_persistence(
    tmp_path: Path,
) -> None:
    secret = "known-secret-value"
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=state_root, task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc,
        capabilities={
            "private.read": lambda _arguments: {
                "status": "ok",
                "data": {"text": secret, "token": secret},
            }
        },
        redactor=SecretRedactor(known_secrets=(secret,)),
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code("agent.mcp.call('private.read', {})")
    finally:
        assert worker.close is not None
        worker.close()

    assert secret not in result["model_text"]
    assert "<redacted>" in result["model_text"]
    artifacts = (state_root / "artifacts" / "sha256").iterdir()
    assert all(secret.encode() not in artifact.read_bytes() for artifact in artifacts)


@pytest.mark.asyncio
async def test_provider_payload_growth_tracks_selected_egress_not_ptc_heap(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-egress"),
    )
    marker = "raw-nested-record-that-must-stay-in-the-heap"
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-egress"),
        ptc_config=config.notebook_ptc,
        capabilities={"bulk.read": lambda _arguments: {"items": [marker] * 2_000}},
    )
    assert worker.execute_code is not None
    try:
        selected = await worker.execute_code(
            "records = agent.mcp.call('bulk.read', {})['items']\nlen(records)"
        )
        assert (await worker.execute_code("len(records)"))["model_text"] == "2000"
    finally:
        assert worker.close is not None
        worker.close()

    contents: list[types.Content] = []
    profiles: list[dict[str, Any]] = []
    for index in range(24):
        call_id = f"cell-{index}"
        call = types.Part.from_function_call(name="execute_code", args={"code": "len(records)"})
        assert call.function_call is not None
        call.function_call.id = call_id
        result = types.Part.from_function_response(name="execute_code", response=selected)
        assert result.function_response is not None
        result.function_response.id = call_id
        contents.extend(
            [
                types.Content(role="model", parts=[call]),
                types.Content(role="user", parts=[result]),
            ]
        )
        body = build_codex_request_body(
            LlmRequest(contents=list(contents)), model="test-model", reasoning_effort=None
        )
        profiles.append(provider_request_profile(body))

    encoded = json.dumps(body, separators=(",", ":"))
    assert marker not in encoded
    assert profiles[-1]["regions"]["input"]["bytes"] < len(marker) * 2_000
    growth = [
        profiles[index + 1]["bytes"] - profiles[index]["bytes"]
        for index in range(len(profiles) - 1)
    ]
    assert max(growth) - min(growth) <= 2  # call-id width changes at cell-10
    assert max(growth) < 500


@pytest.mark.asyncio
async def test_restart_replays_only_self_contained_data_cells(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-replay"),
    )
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-replay"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    await worker.execute_code("literal = {'value': 7}")
    await worker.execute_code("derived = len(literal)")
    assert worker.close is not None
    worker.close()

    policies = [
        event.payload["replay_policy"]
        for event in events.read("task-replay")
        if event.kind == EventKind.NOTEBOOK_CELL_ADDED
    ]
    assert policies == ["safe", "requires_reconciliation"]

    replacement = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-replay"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert replacement.execute_code is not None
    try:
        restored = await replacement.execute_code("literal")
        missing = await replacement.execute_code("derived")
    finally:
        assert replacement.close is not None
        replacement.close()

    assert restored["model_text"] == "{'value': 7}"
    assert missing["status"] == "error"
    assert "NameError" in missing["model_text"]


@pytest.mark.asyncio
async def test_failed_cell_rolls_back_partial_namespace_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(
        composition,
        RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task-2"),
    )
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-2"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    execute_code_tool = worker.execute_code

    try:
        await execute_code_tool("value = 9\nmarker = 'agent.'")
        failed = await execute_code_tool("value = 99\n1 / 0")
        restored = await execute_code_tool("value")
    finally:
        assert worker.close is not None
        worker.close()

    assert failed["status"] == "error"
    assert "ZeroDivisionError" in failed["model_text"]
    assert restored["model_text"] == "9"
    assert EventKind.REPL_CELL_FAILED in [event.kind for event in events.read("task-2")]

    replacement = build_coding_worker(
        settings,
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task-2"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert replacement.execute_code is not None
    try:
        after_restart = await replacement.execute_code("value, marker")
    finally:
        assert replacement.close is not None
        replacement.close()

    assert after_restart["model_text"] == "(9, 'agent.')"
    assert EventKind.REPL_STATE_RESTORED in [event.kind for event in events.read("task-2")]
