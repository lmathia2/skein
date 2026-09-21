from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
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
from harness.evidence.state.recovery import unresolved_execution
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
async def test_committed_plain_value_checkpoint_restores_without_new_source_read(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state = tmp_path / "state"
    events = JsonlEventStore(state / "events")
    (tmp_path / "source.txt").write_text("learned finding\n", encoding="utf-8")
    tools = create_adk_tools(tmp_path, state_root=state, search_mode="disabled")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools, event_store=events,
        ptc_config=config.notebook_ptc.model_copy(update={
            "recover_committed_values": True, "snapshot_max_bytes": 100_000,
        }),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        learned = await worker.execute_code(
            "source = agent.fs.read('source.txt')\n"
            "assert source['status'] == 'ok'\n"
            "finding = {'path': 'source.txt', 'text': source['data']['text']}"
        )
        assert learned["status"] == "ok"
        checkpoints = [e for e in events.read("task") if e.kind == EventKind.REPL_STATE_CHECKPOINTED]
        assert checkpoints[-1].payload["available"]
        assert "finding" in checkpoints[-1].payload["selected_names"]
        (tmp_path / "source.txt").write_text("changed version\n", encoding="utf-8")
        assert (await worker.execute_code("partial = {'bad': True}\nmissing_name"))["status"] == "error"
        reused = await worker.execute_code("print(finding['text'])")
        assert reused["status"] == "ok" and "learned finding" in reused["model_text"]
        observed = events.read("task")
        reads = [e for e in observed if e.kind == EventKind.CAPABILITY_COMPLETED
                 and e.payload.get("operation") == "fs.read"]
        assert len(reads) == 1
        restored = [e for e in observed if e.kind == EventKind.REPL_STATE_RESTORED][-1]
        assert any(item["name"] == "finding" and item["freshness"] == "historical_checkpoint"
                   for item in restored.payload["state"]["manifest"])
    finally:
        worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("eager", (False, True))
async def test_corrupt_committed_plain_checkpoint_fails_closed(tmp_path: Path, eager: bool) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state = tmp_path / "state"
    events = JsonlEventStore(state / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), event_store=events,
        ptc_config=config.notebook_ptc.model_copy(update={
            "recover_committed_values": True, "eager_committed_restore": eager,
        }),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        assert (await worker.execute_code("saved = {'value': 42}"))["status"] == "ok"
        digest = [e for e in events.read("task") if e.kind == EventKind.REPL_STATE_CHECKPOINTED][-1].payload[
            "checkpoint_sha256"]
        (state / "ptc-committed" / "sha256" / digest).write_text("tampered", encoding="utf-8")
        if eager:
            with pytest.raises(ValueError, match="checkpoint content mismatch"):
                await worker.execute_code("missing_name")
        else:
            assert (await worker.execute_code("missing_name"))["status"] == "error"
            with pytest.raises(ValueError, match="checkpoint content mismatch"):
                await worker.execute_code("print(saved)")
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_grounded_checkpoint_ranks_exact_source_and_reports_all_restored_names(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state = tmp_path / "state"
    events = JsonlEventStore(state / "events")
    (tmp_path / "source.txt").write_text("grounded source\n", encoding="utf-8")
    tools = create_adk_tools(tmp_path, state_root=state, search_mode="disabled")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools, event_store=events,
        ptc_config=config.notebook_ptc.model_copy(update={
            "recover_committed_values": True, "ground_committed_bindings": True,
            "snapshot_max_bytes": 100_000,
        }),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        created = await worker.execute_code(
            "result = agent.fs.read('source.txt')\n"
            "source_text_exact = result['data']['text']\n"
            + "\n".join(f"a_{i:02d} = {i}" for i in range(75))
        )
        assert created["status"] == "ok", created
        checkpoint = [e for e in events.read("task") if e.kind == EventKind.REPL_STATE_CHECKPOINTED][-1]
        summaries = checkpoint.payload["binding_summaries"]
        assert summaries["program"] == "committed_value_summaries@2"
        assert summaries["bindings"][0]["name"] == "source_text_exact"
        source_ref = summaries["bindings"][0]["historical_source_refs"][0]
        assert source_ref["path"] == "source.txt"
        assert source_ref["sha256"] == hashlib.sha256(b"grounded source\n").hexdigest()
        assert source_ref["read_event_id"] in {e.event_id for e in events.read("task")
                                                 if e.kind == EventKind.CAPABILITY_COMPLETED}
        (tmp_path / "source.txt").write_text("new version\n", encoding="utf-8")
        assert (await worker.execute_code("missing_name"))["status"] == "error"
        assert (await worker.execute_code("print(source_text_exact)"))["status"] == "ok"
        restored = [e for e in events.read("task") if e.kind == EventKind.REPL_STATE_RESTORED][-1]
        assert restored.payload["restored_names"] == checkpoint.payload["selected_names"]
        assert {item["name"] for item in restored.payload["state"]["manifest"]} == set(
            checkpoint.payload["selected_names"])
        assert (await worker.execute_code("probe = agent.fs.read('source.txt', offset=1, limit=1)"))["status"] == "ok"
        current_read = [e for e in events.read("task") if e.kind == EventKind.CAPABILITY_COMPLETED
                        and e.payload.get("operation") == "fs.read"][-1]
        assert current_read.payload["read_evidence"]["sha256"] != source_ref["sha256"]
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_eager_restoration_is_acknowledged_before_the_model_can_replan(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state = tmp_path / "state"
    events = JsonlEventStore(state / "events")
    (tmp_path / "source.txt").write_text("retained source\n", encoding="utf-8")
    tools = create_adk_tools(tmp_path, state_root=state, search_mode="disabled")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools, event_store=events,
        ptc_config=config.notebook_ptc.model_copy(update={
            "recover_committed_values": True, "ground_committed_bindings": True,
            "eager_committed_restore": True,
        }),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        assert (await worker.execute_code(
            "read = agent.fs.read('source.txt')\nsaved_source_exact = read['data']['text' ]"
        ))["status"] == "ok"
        failed = await worker.execute_code("raise AttributeError('boom')")
        assert failed["status"] == "error" and failed["kernel"]["live"]
        assert "already restored" in failed["model_text"]
        assert "saved_source_exact" in failed["model_text"] and "source.txt" in failed["model_text"]
        observed = events.read("task")
        failure = [e for e in observed if e.kind == EventKind.REPL_CELL_FAILED][-1]
        restored = [e for e in observed if e.kind == EventKind.REPL_STATE_RESTORED][-1]
        assert failure.sequence < restored.sequence
        assert restored.payload["recovery_timing"] == "after_failed_cell"
        assert failed["kernel"]["kernel_epoch"] == restored.payload["kernel_epoch"]
        assert (await worker.execute_code("print(saved_source_exact)"))["status"] == "ok"
        assert len([e for e in events.read("task") if e.kind == EventKind.CAPABILITY_COMPLETED
                    and e.payload.get("operation") == "fs.read"]) == 1
        timed_out = await worker.execute_code("while True: pass", timeout_seconds=1)
        assert timed_out["effect"] == "unknown" and not timed_out["kernel"]["live"]
        assert len([e for e in events.read("task") if e.kind == EventKind.REPL_STATE_RESTORED]) == 1
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_quoted_workspace_program_uses_same_policy_in_direct_and_ptc(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state = tmp_path / "state"
    events = JsonlEventStore(state / "events")
    tools = create_adk_tools(tmp_path, state_root=state, search_mode="disabled")
    command = shlex.join(["python3", "-c", "import json; print(json.dumps({'result': 'a;b|c'}))"])
    direct = tools.bash(command, task_scope="task")
    assert direct["status"] == "ok" and direct["exit_code"] == 0
    assert json.loads(direct["data"]["stdout"]) == {"result": "a;b|c"}
    forbidden = command + " && curl https://example.com"
    assert tools.bash(forbidden, task_scope="task")["status"] == "blocked"
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools, ptc_config=config.notebook_ptc, event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        result = await worker.execute_code(
            f"process = agent.shell.run({command!r})\n"
            "assert process['status'] == 'ok' and process['exit_code'] == 0\n"
            "assert json.loads(process['data']['stdout']) == {'result': 'a;b|c'}\n"
            f"denied = agent.shell.run({forbidden!r})\n"
            "assert denied['status'] == 'blocked'\n"
            "retained = 42"
        )
        assert result["status"] == "ok"
        assert (await worker.execute_code("assert retained == 42"))["status"] == "ok"
    finally:
        worker.close()
    terminals = [e for e in events.read("task") if e.kind in {
        EventKind.CAPABILITY_COMPLETED, EventKind.CAPABILITY_BLOCKED,
    } and e.payload.get("operation") == "shell.run"]
    assert [e.kind for e in terminals] == [EventKind.CAPABILITY_COMPLETED, EventKind.CAPABILITY_BLOCKED]
    assert terminals[-1].payload["effect"] == "none"


@pytest.mark.asyncio
async def test_description_notices_spill_selected_utf8_output_and_redact_values(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc.model_copy(update={"max_output_bytes": 2048, "emit_state_updates": True}),
        redactor=SecretRedactor(known_secrets=("private-fixture-token",)),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        result = await worker.execute_code(
            "value = 42\nagent.state.annotate('value', 'retained finding')\n"
            "print('é' * 900)\n'private-fixture-token'"
        )
        assert result["status"] == "ok" and result["truncated"]
        assert len(result["model_text"].encode()) <= 2048
        assert "private-fixture-token" not in json.dumps(result)
        assert result["artifact_uris"]
        for uri in result["artifact_uris"]:
            assert "private-fixture-token" not in (tmp_path / "state" / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]).read_text()
        failure = await worker.execute_code("raise ValueError('private-fixture-token')")
        assert failure["status"] == "error"
        assert "private-fixture-token" not in json.dumps(failure)
        context = SimpleNamespace(state={"task_id": "task"}, invocation_id="inv", function_call_id="secret")
        code = "private_value = 'private-fixture-token'"
        assert (await worker.execute_code(code, tool_context=context))["status"] == "ok"
        assert (await worker.execute_code(code, tool_context=context))["replayed"]
        assert (await worker.execute_code(code + " # changed", tool_context=context))["status"] == "blocked"
        events = JsonlEventStore(tmp_path / "state" / "events").read("task")
        assert "private-fixture-token" not in json.dumps([event.payload for event in events])
        submitted = [event for event in events if event.kind == EventKind.REPL_CELL_SUBMITTED]
        assert submitted[-1].payload["replay_policy"] == "never"
    finally:
        worker.close()


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
async def test_oversized_parallel_batch_preserves_heap_and_dispatches_nothing(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc,
    )
    context = SimpleNamespace(state={"task_id": "task"}, invocation_id="inv", function_call_id="first")
    assert worker.execute_code is not None
    try:
        first = await worker.execute_code("sentinel = 42", tool_context=context)
        context.function_call_id = "second"
        result = await worker.execute_code(
            "results = agent.parallel([{'operation': 'fs.read', 'arguments': {'path': 'missing'}}] * 5)\n"
            "assert all(r['error_code'] == 'invalid_arguments' for r in results)\n"
            "assert sentinel == 42\nprint(sentinel)", tool_context=context,
        )
        assert first["status"] == result["status"] == "ok"
        assert "42" in result["model_text"]
    finally:
        assert worker.close is not None
        worker.close()


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
async def test_ptc_read_catalog_reuses_exact_and_partial_ranges_but_not_changed_source(
    tmp_path: Path,
) -> None:
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    original = "".join(f"line {index} " + "x" * 100 + "\n" for index in range(1, 7))
    changed = original.replace("line 1", "changed 1")
    (workspace / "source.txt").write_text(original)
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state / "events")
    base = create_adk_tools(workspace, state_root=state, task_scope="task")
    calls: list[tuple[int, int]] = []

    def tracked_read(*, path: str, offset: int = 1, limit: int = 400):
        calls.append((offset, limit))
        return base.read(path=path, offset=offset, limit=limit)

    tools = AdkCodingTools(read=tracked_read, bash=base.bash, edit=base.edit, write=base.write)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=workspace, state_root=state, task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools,
        ptc_config=config.notebook_ptc, event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        first = await worker.execute_code("source_module = agent.fs.read('source.txt', 1, 3)")
        assert first["status"] == "ok"
        exact = await worker.execute_code(
            f"same_source = agent.fs.read({str(workspace / 'source.txt')!r}, 1, 3)\n"
            "assert same_source['data']['text'] == source_module['data']['text']\n"
            "assert same_source['read_handle'] == source_module['read_handle'] == 'read:1'\n"
            "assert same_source['read_reference'] == source_module['read_reference']\n"
            "assert agent.state.cite(source_module['read_handle']) == source_module['read_reference']['artifact_uri']\n"
            "print(same_source['data']['text'])"
        )
        assert exact["status"] == "ok"
        assert original[:200] not in exact["model_text"]
        assert "source already retained as read:1" in exact["model_text"]
        terminal = next(event for event in reversed(events.read("task"))
                        if event.kind == EventKind.REPL_CELL_COMPLETED)
        assert terminal.payload["state"]["retained_read_uses"] == ["read:1"]

        partial = await worker.execute_code("wider_source = agent.fs.read('source.txt', 1, 5)")
        assert partial["status"] == "ok"
        assert (await worker.execute_code(
            "assert wider_source['data']['text'] == " + repr("".join(original.splitlines(keepends=True)[:5]))
        ))["status"] == "ok"
        (workspace / "source.txt").write_text(changed)
        refreshed = await worker.execute_code("changed_source = agent.fs.read('source.txt', 1, 5)")
        assert refreshed["status"] == "ok"
        assert (await worker.execute_code(
            "assert changed_source['data']['text'].startswith('changed 1')"
        ))["status"] == "ok"
    finally:
        worker.close()

    assert calls == [(1, 3), (1, 1), (1, 1), (4, 2), (1, 1), (1, 5)]
    reads = [event.payload for event in events.read("task")
             if event.kind == EventKind.CAPABILITY_COMPLETED and event.payload.get("operation") == "fs.read"]
    assert reads[1]["read_reuse"] == {
        "reused_lines": 3, "source_read_lines": 0,
        "identity_probe_lines": 1,
        "reused_ranges": [[1, 3]], "source_read_ranges": [],
        "reused_artifact_uris": [reads[0]["result_artifact_uri"]],
    }
    assert reads[2]["read_reuse"]["reused_ranges"] == [[1, 3]]
    assert reads[2]["read_reuse"]["source_read_ranges"] == [[4, 5]]
    assert "read_reuse" not in reads[3]


@pytest.mark.asyncio
async def test_read_catalog_survives_worker_epoch_loss_when_source_hash_matches(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    base = create_adk_tools(workspace, state_root=state_root, task_scope="task")
    calls: list[tuple[int, int]] = []

    def tracked_read(*, path: str, offset: int = 1, limit: int = 400):
        calls.append((offset, limit))
        return base.read(path=path, offset=offset, limit=limit)

    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=workspace, state_root=state_root, task_id="task")),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=tracked_read, bash=base.bash, edit=base.edit, write=base.write),
        ptc_config=config.notebook_ptc.model_copy(update={"cross_epoch_read_reuse": True}),
        event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        first = await worker.execute_code("source = agent.fs.read('source.txt', 1, 3)")
        lost = await worker.execute_code(
            "agent.parallel([{'operation': 'shell.run', 'arguments': {'command': 'pwd'}}])"
        )
        second = await worker.execute_code("recovered = agent.fs.read('source.txt', 1, 3)")
    finally:
        worker.close()
    assert first["status"] == "ok" and lost["status"] == "error" and second["status"] == "ok"
    assert first["kernel"]["kernel_epoch"] != second["kernel"]["kernel_epoch"]
    assert calls == [(1, 3), (1, 1)]
    reads = [event.payload for event in events.read("task")
             if event.kind == EventKind.CAPABILITY_COMPLETED and event.payload.get("operation") == "fs.read"]
    assert reads[-1]["read_reuse"]["reused_lines"] == 3
    assert reads[-1]["read_reuse"]["source_read_lines"] == 0


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
    assert failed["execution_started"] is False
    assert failed["state_preserved"] is True
    assert failed["error_line"] == 1
    assert restored["model_text"] == "42"


@pytest.mark.asyncio
async def test_rejected_late_dunder_access_does_not_produce_a_new_read_binding(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    reads = []
    def read(**kwargs):
        reads.append(kwargs)
        return {"status": "ok", "data": {"text": "new source"}}
    def unused(**kwargs):
        raise AssertionError("unexpected capability")
    events = JsonlEventStore(tmp_path / "state" / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc, event_store=events,
        tools=AdkCodingTools(read=read, bash=unused, edit=unused, write=unused),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        await worker.execute_code("r = {'status': 'ok', 'data': {'text': 'old note'}}")
        rejected = await worker.execute_code(
            "r = agent.fs.read('answer.json')\n"
            "if False:\n    print(type(r).__name__)"
        )
        assert rejected["failure_stage"] == "source_validation"
        assert rejected["execution_started"] is False and rejected["state_preserved"]
        assert rejected["model_text"].startswith("Cell rejected before execution:")
        assert not reads
        old = await worker.execute_code("r['data']['text']")
        assert old["model_text"] == "'old note'"
        fresh = await worker.execute_code("r = agent.fs.read('answer.json')\nr['data']['text']")
        assert fresh["model_text"] == "'new source'" and fresh["status"] == "ok"
        assert "execution_started" not in fresh
        assert len(reads) == 1
        failed = next(e for e in events.read("task") if e.kind == EventKind.REPL_CELL_FAILED)
        assert failed.payload["execution_started"] is False
        assert failed.payload["capability_count"] == 0
    finally:
        worker.close()


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
    assert failed["execution_started"] is True
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

    context = SimpleNamespace(
        state={"task_id": "task-1", "task_phase": "understand"},
        invocation_id="phase-test", function_call_id="write-cell",
    )
    try:
        first = await execute_code_tool("value = 40")
        second = await execute_code_tool(
            'agent.fs.write("answer.txt", str(value + 2), expected_absent=True)\n'
            'agent.fs.read("answer.txt")["model_text"]',
            tool_context=context,
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
    assert context.state["task_phase"] == "implement"
    assert any(
        event.kind == EventKind.LEDGER_PATCHED
        and event.payload == {"set_fields": {"phase": "implement"}}
        for event in events.read("task-1")
    )
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
    read_event = next(
        event for event in capability_events if event.payload["operation"] == "fs.read"
    )
    assert read_event.payload["read_evidence"] == {
        "path": "answer.txt",
        "sha256": hashlib.sha256(b"42").hexdigest(),
        "offset": 1,
        "returned_lines": 1,
    }
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
            "receipt_id": "a" * 64,
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
    completed = next(
        event
        for event in events.read("task")
        if event.kind == EventKind.CAPABILITY_COMPLETED
    )
    assert completed.payload["receipt_id"] == "a" * 64


@pytest.mark.asyncio
async def test_direct_verify_runs_and_rejects_failed_pipeline(tmp_path: Path) -> None:
    import subprocess

    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")

    def shell(command, **kwargs):
        completed = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        return {"status": "ok" if completed.returncode == 0 else "error",
                "exit_code": completed.returncode,
                "data": {"stdout": completed.stdout, "stderr": completed.stderr,
                         "exit_code": completed.returncode}}

    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(read=shell, bash=shell, edit=shell, write=shell),
        ptc_config=config.notebook_ptc, event_store=events, thin_loop=True,
        bounded_work_batches=False,
    )
    try:
        tool = worker.agent.tools[0]
        passed = await tool(code='print(verify("printf verified"))')
        assert passed["status"] == "ok", passed
        assert "verified" in passed["model_text"]
        failed = await tool(code='verify("false | true")')
        assert failed["status"] != "ok", failed
        assert "AssertionError" in failed["model_text"]
        observed = [event.payload["status"] for event in events.read("task")
                    if event.kind == "execution.ptc_verification"]
        assert observed == ["ok", "error"]
    finally:
        worker.close()


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
@pytest.mark.parametrize("case", ["unavailable", "invalid", "backend_failure"])
async def test_search_rejection_effects_survive_ptc_publication_and_replay(tmp_path, case):
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")
    dispatches = []

    def execute(request):
        raise AssertionError("managed search must not dispatch the sandbox")

    def health():
        dispatches.append("backend")
        raise RuntimeError("backend failed after dispatch")

    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state", task_scope="task",
                             sandbox=cast(Any, SimpleNamespace(execute=execute)),
                             search_backend=cast(Any, SimpleNamespace(health=health)) if case == "backend_failure" else None)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), tools=tools, ptc_config=config.notebook_ptc, event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    command = "search grep --pattern x | echo unsafe" if case == "invalid" else "search health"
    code = f"result = agent.shell.run({command!r})\nassert result['status'] == 'error'\nprint(result['model_text'])"
    context = SimpleNamespace(state={"task_id": "task"}, invocation_id="inv", function_call_id="search")
    try:
        result = await worker.execute_code(code, tool_context=context)
        assert result["effect"] == ("unknown" if case == "backend_failure" else "none")
        failures = [e for e in events.read("task") if e.kind == EventKind.CAPABILITY_FAILED]
        assert len(failures) == 1 and failures[0].payload["effect"] == result["effect"]
        if case == "backend_failure":
            assert {"cell", "capability"} <= {r["kind"] for r in unresolved_execution(events.read("task"))}
        else:
            assert not unresolved_execution(events.read("task"))
            replay = await worker.execute_code(code, tool_context=context)
            assert replay["replayed"]  # Duplicate acknowledgement is not a new execution result.
            assert not unresolved_execution(events.read("task"))
            assert len([e for e in events.read("task") if e.kind == EventKind.CAPABILITY_FAILED]) == 1
        assert dispatches == (["backend"] if case == "backend_failure" else [])
    finally:
        worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case,same_cell", [(case, False) for case in
    ["complete", "partial", "changed", "corrupt", "unknown_effect", "snapshot", "parse"]] +
    [(case, True) for case in ["complete", "partial", "changed", "corrupt", "unknown_effect", "missing"]])
async def test_lost_binding_recovers_completed_artifact_not_failed_calculation(tmp_path, case, same_cell):
    from app.agent.ptc import _completed_attempt_reads, _state_update_notice, _state_updates
    from harness.evidence.ledger.models import canonical_json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = "base = 31\nunit = 7\n"
    (workspace / "rates.toml").write_text(original)
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    ptc_config = config.notebook_ptc.model_copy(update={
        "state": "snapshot" if case == "snapshot" else config.notebook_ptc.state,
        "emit_state_updates": True,
    })
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=workspace, state_root=state_root, task_id="task")),
        cast(BaseLlm, "test-model"), ptc_config=ptc_config, event_store=events,
        capabilities={"fixture.fail": lambda _: {"status": "error", "effect": "unknown"}},
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        read_code = f"rates = agent.fs.read({'missing.toml' if case == 'missing' else 'rates.toml'!r}, limit={1 if case == 'partial' else 400})"
        if not same_cell:
            captured = await worker.execute_code(read_code)
            assert captured["status"] == "ok"
        code = "if :" if case == "parse" else (
            "agent.mcp.call('fixture.fail', {})\n" if case == "unknown_effect" else ""
        ) + "unfinished_result = 999\n1 / 0"
        if same_cell:
            code = read_code + "\n" + code
        context = SimpleNamespace(state={"task_id": "task"}, invocation_id="inv", function_call_id="failed")
        failed = await worker.execute_code(code, tool_context=context)
        assert failed["status"] == "error"
        terminal = next(e for e in reversed(events.read("task")) if e.kind == EventKind.REPL_CELL_FAILED)
        metadata = terminal.payload["state_updates_view"]
        prior = next((e for e in events.read("task") if e.event_id == metadata["prior_state_event_id"]), None)
        previous = prior.payload["state"]["manifest"] if prior else []
        current = (previous if metadata["current_state_policy"] == "prior" else []
                   if metadata["current_state_policy"] == "empty" else terminal.payload["state"]["manifest"])
        completed = _completed_attempt_reads([e for e in events.read("task") if e.sequence <= metadata["source_watermark"]],
                                            "task", terminal.payload["attempt_id"])
        assert metadata["completed_read_event_ids"] == [row["source_event_id"] for row in completed]
        updates = _state_updates(previous, current, metadata["max_bytes"], completed)
        assert updates == terminal.payload["state_updates"]
        assert metadata["input_hash"] == hashlib.sha256(canonical_json([previous, current, completed]).encode()).hexdigest()
        assert metadata["content_hash"] == hashlib.sha256(canonical_json(updates).encode()).hexdigest()
        notice = _state_update_notice(updates, terminal.payload["kernel_epoch"], terminal.payload["cell_id"],
                                      metadata["recipe_notice_max_bytes"], not failed["state_preserved"])
        assert metadata["notice_hash"] == hashlib.sha256(canonical_json(notice).encode()).hexdigest()
        assert metadata["source_watermark"] < terminal.sequence
        if case == "missing":
            assert not completed and not updates and failed["effect"] == "none"
            assert not any(e.payload.get("read_evidence") for e in events.read("task"))
            return
        if case in {"parse", "snapshot"}:
            assert failed["state_preserved"] and failed["kernel"]["live"]
            if case == "parse":
                assert not any(row.get("historical_read") for row in updates)
            # Snapshot rollback can preserve copied values while invalidating
            # object-identity attestations. Recovery remains historical, not live.
            assert all("read_reference" not in row for row in updates if row.get("historical_read"))
            assert (await worker.execute_code("assert rates['data']['text'] == 'base = 31\\nunit = 7\\n'"))["status"] == "ok"
            return
        assert not failed["state_preserved"] and not failed["kernel"]["live"]
        row = next(row for row in updates if row.get("historical_read", {}).get("path") == "rates.toml")
        reference = row["historical_read"]
        assert row["availability"] == ("historical_read_only" if same_cell else "association_invalidated")
        if same_cell:
            assert "name" not in row and "selector" not in row
            assert row["source_event_id"] in metadata["completed_read_event_ids"]
        assert "read_reference" not in row and "access_expression" not in row
        assert reference["path"] == "rates.toml"
        assert reference["artifact_uri"] in failed["model_text"]
        assert "recover_expression" in failed["model_text"] and len(failed["model_text"].encode()) <= ptc_config.max_output_bytes
        replay = await worker.execute_code(code, tool_context=context)
        assert replay["status"] == "blocked" and replay["reconciliation_required"]
        if case == "unknown_effect":
            assert {"capability", "cell"} <= {row["kind"] for row in unresolved_execution(events.read("task"))}
            recovery = await worker.execute_code(row["recover_expression"])
            assert recovery["status"] == "blocked" and recovery["reconciliation_required"]
            return
        if case == "changed":
            (workspace / "rates.toml").write_text("base = 99\nunit = 7\n")
        elif case == "corrupt":
            (state_root / "artifacts" / "sha256" / reference["artifact_uri"].rsplit("/", 1)[-1]).write_text("corrupt")
        recovery = await worker.execute_code(
            "import json, tomllib\n"
            "try:\n    agent.state.describe('unfinished_result')\n"
            "except KeyError:\n    pass\n"
            "else:\n    raise AssertionError('failed calculation leaked')\n"
            f"page = {row['recover_expression']}\n"
            "assert page['status'] == 'ok' and page['data']['complete']\n"
            "saved = json.loads(page['data']['text'])\nassert saved['status'] == 'ok'\n"
            "facts = tomllib.loads(saved['data']['text'])\nassert facts['base'] == 31\n"
            + ("assert 'unit' not in facts\nassert not saved['data']['complete']\n" if case == "partial"
               else "computed = facts['base'] + 2 * facts['unit']\nassert computed == 45\n")
        )
        if case == "corrupt":
            assert recovery["status"] == "error" and recovery["effect"] == "unknown"
            assert unresolved_execution(events.read("task"))
            return
        assert recovery["status"] == "ok", recovery
        reads = [e for e in events.read("task") if e.payload.get("read_evidence")]
        assert len(reads) == 1  # Recovery did not reread the workspace or complete the failed calculation.
        if case == "changed":
            current_read = await worker.execute_code(
                "fresh = agent.fs.read('rates.toml', limit=1)\n"
                "assert fresh['data']['sha256'] != saved['data']['sha256']\n"
                "assert '99' in fresh['data']['text']"
            )
            assert current_read["status"] == "ok", current_read
        assert not unresolved_execution(events.read("task"))
    finally:
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
        await worker.execute_code("original_read = agent.fs.read('note.txt')")
        routes = await worker.execute_code(
            "process = agent.shell.run('pwd')\n"
            "assert process['result_kind'] == 'process' and process['exit_code'] == 0\n"
            "assert isinstance(process['data']['stdout'], str)\n"
            "managed = agent.shell.run('search health')\n"
            "assert managed['result_kind'] == 'managed' and 'exit_code' not in managed\n"
            "assert 'result_kind' in agent.help('shell.run', details=True)['shell.run']['result']\n"
        )
        assert routes["status"] == "ok", routes
        loaded = await worker.execute_code(
            "uri = original_read['read_reference']['artifact_uri']\n"
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
        from harness.ptc.notebook import put_artifact

        foreign = put_artifact(state_root / "artifacts" / "sha256", b"unreferenced foreign payload")
        denied_existing = await worker.execute_code(f"agent.artifacts.load({foreign!r})")
        assert "unreferenced foreign payload" not in json.dumps(denied_existing)
        assert denied_existing["effect"] == "none"
        denied_name = await worker.execute_code("agent.artifacts.publish('x', '../../')")
        invalid = await worker.execute_code(
            "for args in [{'uri': 'artifact://sha256/abc...'}, {'uri': {}},\n"
            "             {'uri': uri, 'offset': -1}, {'uri': uri, 'offset': True},\n"
            "             {'uri': uri, 'limit': 0}, {'uri': uri, 'limit': True}]:\n"
            "    r = agent.artifacts.load(**args)\n"
            "    assert r['status'] == 'error' and r['effect'] == 'none', r\n"
            "assert agent.artifacts.publish('x', 'valid', 'x' * 501)['effect'] == 'none'\n"
        )
        assert invalid["status"] == "ok", invalid
        assert not unresolved_execution(events.read("task"))
        recovered = await worker.execute_code(
            "items = agent.artifacts.list()['data']['artifacts']\n"
            "exact_uri = next(item['uri'] for item in items if item['uri'] == uri)\n"
            "assert agent.artifacts.load(exact_uri)['status'] == 'ok'\n"
        )
        assert recovered["status"] == "ok", recovered
        read_event = next(event for event in events.read("task")
                          if event.kind == EventKind.CAPABILITY_COMPLETED and
                          event.payload.get("operation") == "fs.read")
        uri = read_event.payload["result_artifact_uri"]
        (state_root / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]).write_bytes(b"corrupt")
        corrupt = await worker.execute_code(f"agent.artifacts.load({uri!r})")
        assert {"capability", "cell"} <= {r["kind"] for r in unresolved_execution(events.read("task"))}
    finally:
        assert worker.close is not None
        worker.close()

    assert "durable payload" in loaded["model_text"]
    assert "Final_Report" in first["model_text"]
    assert first["artifact_uris"] == second["artifact_uris"]
    assert "'published': True" in published["model_text"]
    assert "PermissionError" in denied["model_text"]
    assert "ValueError" in denied_name["model_text"]
    assert "content hash does not match" in corrupt["model_text"]
    publish_events = [
        event for event in events.read("task") if event.kind == EventKind.ARTIFACT_PUBLISHED
    ]
    assert len(publish_events) == 2
    assert publish_events[0].payload["artifact_uri"] == publish_events[1].payload["artifact_uri"]
    assert publish_events[0].payload["host_visible"] is True


@pytest.mark.asyncio
async def test_reserved_memory_command_request_cannot_invalidate_workspace_findings(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state_root / "events")
    base = create_adk_tools(workspace, state_root=state_root, task_scope="task")

    def memory_command(command: str, **_: Any) -> dict[str, Any]:
        assert command.startswith("memory ")
        return {
            "status": "ok",
            "data": {"status": "ok"},
            "effect": "none",
            "ui_details": {"memory": True},
        }

    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=workspace, state_root=state_root, task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        tools=AdkCodingTools(
            read=base.read,
            bash=memory_command,
            edit=base.edit,
            write=base.write,
        ),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None
    try:
        result = await worker.execute_code(
            "response = agent.shell.run('memory query --program working_set')\n"
            "assert response['result_kind'] == 'managed'"
        )
    finally:
        assert worker.close is not None
        worker.close()

    assert result["status"] == "ok"
    memory_events = [
        event for event in events.read("task")
        if event.payload.get("operation") == "shell.run"
    ]
    assert [event.kind for event in memory_events] == [
        EventKind.CAPABILITY_REQUESTED,
        EventKind.CAPABILITY_COMPLETED,
    ]
    assert all(event.payload["discovery_kind"] == "memory" for event in memory_events)
    assert all(event.payload["workspace_may_have_changed"] is False for event in memory_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("published", (False, True))
async def test_artifact_publication_failure_retains_unknown_effect(tmp_path, monkeypatch, published):
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(tmp_path / "state" / "events")
    append = events.append

    def interrupted(task_id, kind, payload, **kwargs):
        if kind == EventKind.ARTIFACT_PUBLISHED:
            if published:
                append(task_id, kind, payload, **kwargs)
            raise OSError("fixture publication interrupted")
        return append(task_id, kind, payload, **kwargs)

    monkeypatch.setattr(events, "append", interrupted)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc, event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        result = await worker.execute_code("agent.artifacts.publish({'value': 42}, 'Fixture')")
        assert result["effect"] == "unknown", result
        assert {"capability", "cell"} <= {r["kind"] for r in unresolved_execution(events.read("task"))}
        assert sum(e.kind == EventKind.ARTIFACT_PUBLISHED for e in events.read("task")) == int(published)
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_artifact_byte_pages_roundtrip_unicode_and_binary_without_redaction_bypass(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", task_id="task")),
        cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc,
        redactor=SecretRedactor(known_secrets=("private-fixture-token",)),
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        result = await worker.execute_code(
            "import base64\n"
            "contract = agent.help('artifacts.load', details=True)['artifacts.load']['result']['data']\n"
            "for payload in ['Aé🙂Z'.encode(), bytes([0, 255, 128, 65]), b'']:\n"
            "    uri = agent.artifacts.publish(payload, 'Paging_fixture')['data']['uri']\n"
            "    for limit in [1, 2, 3, 4, 16]:\n"
            "        offset, restored = 0, b''\n"
            "        while True:\n"
            "            response = agent.artifacts.load(uri, offset=offset, limit=limit)\n"
            "            assert response['status'] == 'ok', response\n"
            "            page = response['data']\n"
            "            assert set(page) <= set(contract)\n"
            "            chunk = page['text'].encode() if page['encoding'] == 'utf-8' else base64.b64decode(page['base64'], validate=True)\n"
            "            assert chunk == payload[offset:offset + limit]\n"
            "            assert page['offset'] == offset and page['returned_bytes'] == len(chunk)\n"
            "            assert page['total_bytes'] == len(payload)\n"
            "            restored += chunk\n"
            "            if page['complete']:\n"
            "                assert page['next_offset'] is None\n"
            "                break\n"
            "            assert page['next_offset'] > offset\n"
            "            offset = page['next_offset']\n"
            "        assert restored == payload\n"
            "print('exact_pages_verified')"
        )
        assert result["status"] == "ok", result
        assert "exact_pages_verified" in result["model_text"]
        denied = await worker.execute_code(
            "unsafe = b'private-' + b'fixture-token' + bytes([255])\n"
            "uri = agent.artifacts.publish(unsafe, 'Unsafe_fixture')['data']['uri']\n"
            "agent.artifacts.load(uri, offset=7, limit=2)"
        )
        assert "artifact requires redaction" in denied["model_text"]
        assert "private-fixture-token" not in json.dumps(denied)
        assert denied["effect"] == "observed"  # Publication succeeded; denied loading added no effect.
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_three_source_notice_supplies_reusable_content_within_existing_budget(tmp_path: Path) -> None:
    from app.agent.ptc import _state_updates
    from harness.evidence.ledger.models import canonical_json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sources = {"contract.md": "# Contract\n", "router.py": "mode = 'ordered'\n", "routing.json": '{"rules": []}\n'}
    for path, text in sources.items():
        (workspace / path).write_text(text)
    task_id = "qualification_ordered_rules-findings"
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(workspace=workspace, state_root=state_root, task_id=task_id)),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc.model_copy(update={"emit_state_updates": True}), event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    try:
        # Reproduce the live cell's parallel container, dictionary and final loop alias.
        context = SimpleNamespace(state={"task_id": task_id}, invocation_id="inv", function_call_id="read")
        initial = await worker.execute_code(
            f"paths = {list(sources)!r}\n"
            "reads_batch = agent.parallel([{'operation': 'fs.read', 'arguments': {'path': p}} for p in paths])\n"
            "reads_current = {r['data']['path']: r for r in reads_batch}\n"
            "for p in paths:\n    r = reads_current[p]\n", tool_context=context)
        assert initial["status"] == "ok", initial
        notice, _ = json.JSONDecoder().raw_decode(initial["model_text"].split(
            "State updates (advisory; sources historical):\n", 1)[1])
        entries = notice["entries"]
        assert len(entries) == 3 and len(canonical_json(entries).encode()) <= 2048
        assert {entry["read_reference"]["path"] for entry in entries} == set(sources)
        completed = next(e for e in reversed(events.read(task_id)) if e.kind == EventKind.REPL_CELL_COMPLETED)
        manifest = completed.payload["state"]["manifest"]
        assert entries == _state_updates([], manifest, 2048)
        assert all("value_fingerprint" in row for row in manifest if row.get("read_reference"))
        code = "\n".join(f"assert {entry['content_expression']} == {sources[entry['read_reference']['path']]!r}"
                         for entry in entries)
        reused = await worker.execute_code(code)
        assert reused["status"] == "ok", reused
        assert reused["kernel"]["kernel_epoch"] == initial["kernel"]["kernel_epoch"]
        assert len([e for e in events.read(task_id) if e.kind == EventKind.CAPABILITY_COMPLETED
                    and e.payload.get("operation") == "fs.read"]) == 3
        assert not any(e.payload.get("operation") == "artifacts.load" for e in events.read(task_id))
    finally:
        worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", (False, True))
async def test_ptc_descriptions_capture_broker_provenance_without_rereading(tmp_path: Path, nested: bool) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("historical source\n")
    state_root = tmp_path / "state"
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(
            workspace=workspace, state_root=state_root, task_id="task")),
        cast(BaseLlm, "test-model"),
        tools=create_adk_tools(workspace, state_root=state_root, task_scope="task"),
        ptc_config=config.notebook_ptc.model_copy(update={"emit_state_updates": True}), event_store=events,
    )
    assert worker.execute_code is not None
    try:
        if nested:
            initial = await worker.execute_code(
                "reads = agent.parallel([{'operation': 'fs.read', 'arguments': {'path': 'note.txt'}}])")
            assert initial["status"] == "ok", initial
            assert "\"content_expression\":\"agent.state.reuse('read:1')['data']['text']\"" in initial["model_text"]
            assert "historical source" not in initial["model_text"]
            await worker.execute_code("source = reads[0]")
        else:
            await worker.execute_code("source = agent.fs.read('note.txt')")
        await worker.execute_code("agent.state.annotate('source', 'Module needed for the next edit')")
        description = await worker.execute_code("agent.state.describe('source')")
        citation = await worker.execute_code(
            "contract = agent.help('fs.read', details=True)['fs.read']['result']\n"
            "assert 'read_reference' in contract and 'read_reference' not in contract['data']\n"
            "reference = source['read_reference']\n"
            "assert set(contract['read_reference']) == set(reference)\n"
            "assert reference['source_coverage']['whole_file'] is True\n"
            "assert reference['artifact_uri'] != 'artifact://sha256/' + source['data']['sha256']\n"
            "assert agent.state.describe('source')['read_reference'] == reference\n"
            "print(reference['artifact_uri'])"
        )
        assert citation["status"] == "ok", citation
        source_events = [event for event in events.read("task")
                         if event.kind == EventKind.CAPABILITY_COMPLETED and
                         event.payload.get("operation") == "fs.read"]
        assert len(source_events) == 1
        assert source_events[0].payload["result_artifact_uri"] in description["model_text"]
        assert source_events[0].payload["result_artifact_uri"] in citation["model_text"]
        assert "Module needed for the next edit" in description["model_text"]
        assert "historical source" not in description["model_text"]
        assert "register_read" not in (await worker.execute_code("dir(agent.state)"))["model_text"]
        changed = await worker.execute_code(
            "source['data']['text'] = 'different text'\nagent.state.describe('source')"
        )
        assert "invalidated" in changed["model_text"]
        assert "Module needed for the next edit" not in changed["model_text"]
    finally:
        assert worker.close is not None
        worker.close()


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


@pytest.mark.asyncio
async def test_review_decision_guard_rejects_cell_before_worker_submission(tmp_path: Path) -> None:
    composition = _enabled_composition()
    config = cast(SkeinConfig, composition.harness.config)
    state_root = tmp_path / "state"
    events = JsonlEventStore(state_root / "events")
    worker = build_coding_worker(
        settings_from_composition(
            composition,
            RuntimeBindings(workspace=tmp_path, state_root=state_root, task_id="task"),
        ),
        cast(BaseLlm, "test-model"),
        ptc_config=config.notebook_ptc,
        event_store=events,
    )
    assert worker.execute_code is not None and worker.close is not None
    tool_state = {"task_id": "task", "ptc_work_batch_id": "2", "review_decision_pending": True}
    try:
        result = await worker.execute_code(
            "retained = 9",
            tool_context=SimpleNamespace(
                state=tool_state, invocation_id="inv", function_call_id="call"
            ),
        )
        callback = worker.agent.before_model_callback
        request = LlmRequest(model="test-model", contents=[])
        assert await callback(SimpleNamespace(state=tool_state), request) is None
        assert tool_state["review_decision_rejections"] == 1
        tool_state["review_decision_rejections"] = 2
        forced = await callback(SimpleNamespace(state=tool_state), request)
        assert forced is not None and "status" in forced.content.parts[0].text
    finally:
        worker.close()
    assert result["status"] == "blocked"
    assert [event.kind for event in events.read("task")] == [EventKind.TOOL_CALL_REJECTED]
