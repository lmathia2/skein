from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.google_llm import Gemini

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from app.agent.workflow import _verify_task
from harness.approvals import ApprovalStore
from harness.approvals.waiting import ApprovalWaiter
from harness.config import RuntimeBindings, load_harness_composition
from harness.sandbox import MANAGED_COMMAND_ENVIRONMENT, SandboxResult
from harness.state import JsonlEventStore
from harness.tools.adk_adapter import create_adk_tools
from harness.verification import (
    CommandResult,
    ManagedValidationExecutor,
    ValidationCommand,
    ValidationPlan,
)


async def pending(waiter: ApprovalWaiter, task: asyncio.Task | None = None) -> dict:
    async with asyncio.timeout(3):
        while not waiter.pending():
            if task is not None and task.done():
                await task
                pytest.fail("task ended without requesting approval")
            await asyncio.sleep(0.005)
    return waiter.pending()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied", "timeout", "cancel"])
async def test_real_tool_waits_and_only_approval_executes(tmp_path: Path, decision: str) -> None:
    calls = []
    class Sandbox:
        def execute(self, request):
            calls.append(request.command)
            return SandboxResult(status="ok", exit_code=0, stdout="approved", stderr="", duration_ms=0)
    composition = load_harness_composition()
    settings = settings_from_composition(composition, RuntimeBindings(workspace=tmp_path,
        state_root=tmp_path / "state", task_id="task"))
    tools = create_adk_tools(tmp_path, state_root=settings.state_root, sandbox=Sandbox(), search_mode="disabled", task_scope="task")
    waiter = ApprovalWaiter(ApprovalStore(settings.state_root / "approvals.db"), "task", timeout=0.1 if decision == "timeout" else 5)
    worker = build_coding_worker(settings, Gemini(model="unused-fixture"), tools=tools, approvals=waiter)
    running = asyncio.create_task(worker.bash("command printf approved"))
    item = await pending(waiter)
    assert calls == [] and not running.done()
    assert item["operation"] == "command printf approved"
    if decision in {"approved", "denied"}:
        with pytest.raises(ValueError, match="does not match"):
            await waiter.decide(item["request_id"], "wrong", "approved", actor="reviewer")
        result = await waiter.decide(item["request_id"], item["fingerprint"], decision, actor="reviewer")
        assert await waiter.decide(item["request_id"], item["fingerprint"], decision, actor="reviewer") == result
        with pytest.raises(ValueError, match="already decided"):
            await waiter.decide(item["request_id"], item["fingerprint"], "denied" if decision == "approved" else "approved", actor="other")
    elif decision == "cancel":
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    if decision != "cancel":
        result = await asyncio.wait_for(running, 3)
        assert result["status"] == ("ok" if decision == "approved" else "blocked")
        if decision != "approved":
            assert not result["approval_required"]
    assert calls == (["command printf approved"] if decision == "approved" else [])
    assert waiter.pending() == []
    if decision in {"cancel", "timeout"}:
        assert waiter.store.get(item["request_id"]).status == "expired"
        with pytest.raises(ValueError, match="expired"):
            await waiter.decide(item["request_id"], item["fingerprint"], "approved", actor="late")


@pytest.mark.asyncio
async def test_wait_identity_and_duplicate_registration_fail_closed(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.db")
    request = store.request(task_id="task", fingerprint="f" * 64, operation="command", risk="unknown", reason="review")
    waiter = ApprovalWaiter(store, "task")
    with pytest.raises(ValueError, match="another task"):
        await waiter.wait(request.request_id, "different")
    task = asyncio.create_task(waiter.wait(request.request_id, "task"))
    await pending(waiter)
    try:
        with pytest.raises(ValueError, match="already active"):
            await waiter.wait(request.request_id, "task")
        # The existing CLI/store route remains observable while a TUI wait is active.
        store.decide(request.request_id, decision="denied", actor="cli")
        assert (await asyncio.wait_for(task, 2)).status == "denied"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_verification_uses_same_wait_and_never_blocks_the_event_loop(tmp_path, monkeypatch, decision) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Sandbox:
        def execute(self, request):
            calls.append(request.command)
            entered.set()
            assert release.wait(3), "verification blocked the event loop"
            return SandboxResult(status="ok", exit_code=0, stdout="verified", stderr="", duration_ms=0)
    command = ValidationCommand(category="custom", command="command printf verification", source="fixture", strength="behavioral")
    monkeypatch.setattr("app.agent.workflow.discover_validation_plan", lambda *args, **kwargs: ValidationPlan(commands=[command], changed_paths=["test.py"]))
    state = tmp_path / "state"
    executor = ManagedValidationExecutor(tmp_path, state_root=state, task_id="task", sandbox=Sandbox())
    waiter = ApprovalWaiter(executor.approvals, "task")
    repository = SimpleNamespace(
        manifest=lambda: SimpleNamespace(),
        changed_paths=lambda base: ["test.py"],
        fingerprint=lambda: "fixture-workspace",
    )
    deps = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path), repository=repository, approvals=waiter,
                           workspace_manager=None, event_store=JsonlEventStore(state / "events"),
                           validation_executor=lambda task: executor)
    ctx = SimpleNamespace(get_invocation_context=lambda: SimpleNamespace(invocation_id="verification-fixture"))
    running = asyncio.create_task(_verify_task(deps, ctx, {"request": {"goal": "verify"}, "ledger": {
        "task_id": "task", "goal": "verify", "acceptance_criteria": ["verified"], "base_revision": "base", "workspace_id": "workspace"}}))
    try:
        item = await pending(waiter, running)
        assert not calls
        await waiter.decide(item["request_id"], item["fingerprint"], decision, actor="reviewer")
        if decision == "approved":
            assert await asyncio.to_thread(entered.wait, 3)
            assert not running.done()
            release.set()
        result = await asyncio.wait_for(running, 3)
        assert result["report"]["passed"] is (decision == "approved")
        assert calls == ([command.command] if decision == "approved" else [])
        assert result["commands"][0]["status"] == ("ok" if decision == "approved" else "blocked")
    finally:
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_verification_runs_targeted_test_once_per_workspace(
    tmp_path, monkeypatch
) -> None:
    state = tmp_path / "state"
    command = "pytest tests/test_solver.py"
    monkeypatch.setattr(
        "app.agent.workflow.discover_validation_plan",
        lambda *args, **kwargs: ValidationPlan(
            commands=[
                ValidationCommand(
                    category="test",
                    command=command,
                    source="adjacent test",
                    targeted=True,
                ),
                ValidationCommand(
                    category="diff", command="git diff --check", source="git"
                )
            ],
            changed_paths=["solver.py"],
        ),
    )
    calls: list[str] = []

    def execute(validation: ValidationCommand) -> CommandResult:
        calls.append(validation.command)
        return CommandResult(
            category=validation.category,
            command=validation.command,
            exit_code=0,
            stdout="1 passed" if validation.category == "test" else "",
        )

    deps = SimpleNamespace(
        settings=SimpleNamespace(workspace=tmp_path, state_root=state),
        repository=SimpleNamespace(
            manifest=lambda: SimpleNamespace(),
            changed_paths=lambda base: ["solver.py"],
            fingerprint=lambda: "unchanged-workspace",
        ),
        approvals=None,
        workspace_manager=None,
        event_store=JsonlEventStore(state / "events"),
        validation_executor=lambda task: execute,
    )
    deps.event_store.append(
        "task",
        "execution.validation_observed",
        {
            "operation_id": "cell:1",
            "receipt_id": "receipt",
            "command": command,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "environment": dict(MANAGED_COMMAND_ENVIRONMENT),
            "workspace_before": "unchanged-workspace",
            "workspace_after": "unchanged-workspace",
            "result": {
                "status": "ok", "exit_code": 0, "stdout": "1 passed", "duration_ms": 10,
            },
        },
    )
    ctx = SimpleNamespace(
        get_invocation_context=lambda: SimpleNamespace(invocation_id="verify")
    )
    request = {"goal": "fix solver", "mode": "coding"}

    results = []
    for iteration in (1, 2):
        results.append(
            await _verify_task(
                deps,
                ctx,
                {
                    "request": request,
                    "ledger": {
                        "task_id": "task",
                        "goal": "fix solver",
                        "acceptance_criteria": ["solver works"],
                        "base_revision": "base",
                        "workspace_id": "workspace",
                        "iteration": iteration,
                    },
                },
            )
        )

    assert all(result["report"]["passed"] for result in results)
    assert calls == ["git diff --check"]
    completed = [
        event
        for event in deps.event_store.read("task")
        if event.kind == "execution.validation_completed"
    ]
    assert [event.payload["cached"] for event in completed] == [True, False, True, True]
