from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from typing import cast

import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from app.agent.factory import default_harness_registry
from harness.agent import SteeringCommand
from harness.config import (
    RuntimeBindings,
    SkeinConfig,
    load_harness_composition,
    parse_harness_composition,
)
from harness.state import EventKind, JsonlEventStore
from harness.tools.adk_adapter import AdkCodingTools, create_adk_tools


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
async def test_cancelled_tool_drains_synchronous_effect_before_return(tmp_path: Path) -> None:
    composition = _enabled_composition()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def effect(**_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        finished.set()
        return {"status": "ok"}

    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), tools=AdkCodingTools(read=effect, bash=effect, edit=effect, write=effect),
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
async def test_conversation_notebook_restores_only_safe_cells_with_run_attribution(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prior: tuple = ()
    for task_id in ("a", "b"):
        state = tmp_path / task_id
        events = JsonlEventStore(state / "events")
        worker = build_coding_worker(
            settings_from_composition(composition, RuntimeBindings(workspace=workspace, state_root=state, task_id=task_id)),
            cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc, event_store=events,
            conversation_notebook_id="conversation", prior_notebook_events=prior,
            notebook_root=tmp_path / "conversation",
        )
        assert worker.python is not None and worker.close is not None
        try:
            if task_id == "a":
                assert (await worker.python("value = 40"))["status"] == "ok"
                assert (await worker.python("dependent = value + 1"))["status"] == "ok"
            else:
                assert (await worker.python("value + 2"))["model_text"] == "42"
                assert (await worker.python("dependent"))["status"] == "error"
        finally:
            worker.close()
        prior = (*prior, *events.read(task_id))
    notebook = json.loads((tmp_path / "conversation" / "conversation.ipynb").read_text())
    assert {cell["metadata"]["agent"]["task_id"] for cell in notebook["cells"]} == {"a", "b"}
    assert set(notebook["metadata"]["agent"]["source_watermarks"]) == {"a", "b"}


def test_factory_exposes_only_python_when_notebook_ptc_is_enabled(tmp_path: Path) -> None:
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
    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in worker.tools
    }
    assert tool_names == {"python"}
    assert assembly.build_info.tool_names == ("python",)
    assert "never parse notebook JSON" in worker.static_instruction
    assert "Batch related bounded operations in one cell" in worker.static_instruction
    resources = registry.resources(
        composition,
        RuntimeBindings(workspace=workspace, state_root=tmp_path / "state", task_id="task"),
    )
    assert resources is not None
    assert {item.name for item in resources.items if item.kind == "tool"} == {"python"}
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
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", ""))
            for tool in worker.tools
        }
        assert tool_names == {"read", "bash", "edit", "write"}
        assert not (state / "ledger.duckdb").exists()
        assert not (state / "ledger.jsonl").exists()
    finally:
        assert assembly.close is None


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
        from harness.ledger import JsonlLedgerStore

        events = JsonlLedgerStore(state / "ledger.jsonl").read("task")
    else:
        from harness.ledger import DuckDbLedgerStore

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
    assert worker.python is not None
    python_tool = worker.python

    try:
        first = await python_tool("value = 40")
        second = await python_tool(
            'agent.fs.write("answer.txt", str(value + 2), expected_absent=True)\n'
            'agent.fs.read("answer.txt")["model_text"]'
        )
        rich = await python_tool('{"image/png": b"x" * 17000, "text/plain": "plot"}')
    finally:
        assert worker.close is not None
        worker.close()

    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in worker.agent.tools
    }
    assert tool_names == {"python"}
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert rich["status"] == "ok"
    assert len(rich["artifact_uris"]) == 1
    assert second["effect"] == "changed"
    assert (workspace / "answer.txt").read_text(encoding="utf-8") == "42"
    notebook_path = Path(str(second["notebook_path"]))
    assert notebook_path.exists()
    notebook_text = notebook_path.read_text(encoding="utf-8")
    assert notebook_text.count('"cell_type":"code"') == 3
    assert "application/vnd.agent.artifact+json" in notebook_text
    kinds = [event.kind for event in events.read("task-1")]
    assert kinds.count(EventKind.NOTEBOOK_CELL_ADDED) == 3
    assert EventKind.CAPABILITY_REQUESTED in kinds
    assert EventKind.CAPABILITY_COMPLETED in kinds
    assert kinds[-1] == EventKind.NOTEBOOK_SNAPSHOTTED
    assert '"tools":["python"]' in settings.static_prefix


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
        tools=create_adk_tools(
            workspace, state_root=state_root, task_scope="task-snapshot"
        ),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.python is not None
    result = await worker.python("answer = 42")
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
    assert payload["kernel_epoch"] == result["kernel_epoch"]
    digest = payload["notebook_sha256"]
    assert payload["artifact_uri"] == f"artifact://sha256/{digest}"
    artifact = state_root / "artifacts" / "sha256" / digest
    notebook = Path(result["notebook_path"])
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
        capabilities={"issues.search": lambda arguments: {"status": "ok", "items": [arguments["q"]]}},
    )
    assert worker.python is not None
    result = await worker.python("agent.mcp.call('issues.search', {'q': 'timeout'})")
    assert result["status"] == "ok"
    assert "timeout" in result["model_text"]
    blocked = await worker.python("agent.mcp.call('missing.tool', {})")
    assert blocked["status"] == "ok"
    assert "blocked" in blocked["model_text"]
    assert worker.close is not None
    worker.close()


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
    assert worker.python is not None
    try:
        selected = await worker.python(
            "records = agent.mcp.call('bulk.read', {})['items']\nlen(records)"
        )
        catalog = await worker.python("agent.state.describe('records')")
        reused = await worker.python("len(records)")
    finally:
        assert worker.close is not None
        worker.close()

    assert selected["model_text"] == "2000"
    assert reused["model_text"] == "2000"
    assert marker not in json.dumps(selected, sort_keys=True)
    assert marker not in json.dumps(catalog, sort_keys=True)
    assert "'size': 2000" in catalog["model_text"]


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
    assert worker.python is not None
    await worker.python("literal = {'value': 7}")
    await worker.python("derived = len(literal)")
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
    assert replacement.python is not None
    try:
        restored = await replacement.python("literal")
        missing = await replacement.python("derived")
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
    assert worker.python is not None
    python_tool = worker.python

    try:
        await python_tool("value = 9\nmarker = 'agent.'")
        failed = await python_tool("value = 99\n1 / 0")
        restored = await python_tool("value")
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
    assert replacement.python is not None
    try:
        after_restart = await replacement.python("value, marker")
    finally:
        assert replacement.close is not None
        replacement.close()

    assert after_restart["model_text"] == "(9, 'agent.')"
    assert EventKind.REPL_STATE_RESTORED in [event.kind for event in events.read("task-2")]
