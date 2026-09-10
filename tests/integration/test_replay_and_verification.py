from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from harness.core.models.checkpoint import Checkpoint
from harness.core.models.ledger import TaskLedger
from harness.evidence.state import (
    CheckpointStore,
    EventKind,
    JsonlEventStore,
    rebuild_ledger,
)
from harness.execution.repo import build_repository_manifest
from harness.execution.sandbox import LocalSandbox
from harness.execution.tools.adk_adapter import create_adk_tools
from harness.execution.workspace import GitWorktreeManager
from harness.verification import (
    ManagedValidationExecutor,
    discover_validation_plan,
    run_validation_plan,
)


def _run(root: Path, *args: str) -> None:
    subprocess.run(args, cwd=root, check=True, capture_output=True)


def _source_repository(root: Path) -> Path:
    root.mkdir()
    _run(root, "git", "init", "-q")
    _run(root, "git", "config", "user.email", "test@example.com")
    _run(root, "git", "config", "user.name", "Test")
    (root / "pyproject.toml").write_text(
        "[project]\nname='replay-fixture'\nversion='0.0.0'\n"
        "[dependency-groups]\ndev=['pytest']\n",
        encoding="utf-8",
    )
    (root / "calculator.py").write_text(
        "def multiply(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (root / "test_calculator.py").write_text(
        "from calculator import multiply\n\n"
        "def test_multiply():\n    assert multiply(6, 7) == 42\n",
        encoding="utf-8",
    )
    _run(root, "git", "add", ".")
    _run(root, "git", "commit", "-qm", "initial")
    return root


def test_interrupted_task_replays_state_and_mutation_without_duplication(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source_repository(tmp_path / "source")
    state = tmp_path / "state"
    manager = GitWorktreeManager(source, state)
    workspace = manager.create("task-replay")
    monkeypatch.setenv("SKEIN_STATE_DIR", str(state))

    ledger = TaskLedger(
        task_id="task-replay",
        goal="Fix multiply",
        acceptance_criteria=["multiply(6, 7) returns 42"],
        base_revision=workspace.base_revision,
        workspace_id=workspace.workspace_id,
        branch_id=workspace.branch or "detached",
    )
    events = JsonlEventStore(state / "events")
    events.append(
        ledger.task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
        idempotency_key="create",
    )

    tools = create_adk_tools(workspace.path)
    first = tools.edit(
        "calculator.py",
        "return left + right",
        "return left * right",
    )
    assert first["status"] == "ok"

    fingerprint = manager.fingerprint(ledger.task_id)
    checkpoint = Checkpoint(
        checkpoint_id="checkpoint-1",
        task_id=ledger.task_id,
        session_id="session-1",
        invocation_id="invocation-1",
        branch_id=ledger.branch_id,
        workspace_id=ledger.workspace_id,
        base_revision=ledger.base_revision,
        git_tree_hash=fingerprint,
        ledger_version=1,
        ledger_hash=hashlib.sha256(ledger.model_dump_json().encode()).hexdigest(),
        created_at=datetime.now(UTC),
    )
    checkpoints = CheckpointStore(state / "state.db")
    checkpoints.save(checkpoint)

    # Simulate process loss: reconstruct every durable component and invoke the exact
    # same mutation. The receipt returns success without applying a second edit.
    restored_manager = GitWorktreeManager(source, state)
    restored_workspace = restored_manager.create(ledger.task_id)
    restored_tools = create_adk_tools(restored_workspace.path)
    replay = restored_tools.edit(
        "calculator.py",
        "return left + right",
        "return left * right",
    )
    assert replay["status"] == "ok"
    assert replay["replayed"] is True
    assert restored_manager.fingerprint(ledger.task_id) == checkpoint.git_tree_hash
    assert rebuild_ledger(events.read(ledger.task_id)) == ledger
    assert checkpoints.latest(ledger.task_id) == checkpoint

    manifest = build_repository_manifest(restored_workspace.path)
    plan = discover_validation_plan(manifest, ["calculator.py"])
    report, results = run_validation_plan(
        restored_workspace.path,
        plan,
        acceptance_criteria=ledger.acceptance_criteria,
        criterion_evidence={
            "multiply(6, 7) returns 42": ["test_calculator.py::test_multiply"]
        },
        executor=ManagedValidationExecutor(
            restored_workspace.path, state_root=state, task_id=ledger.task_id,
            sandbox=LocalSandbox(
                restored_workspace.path, state / "artifacts",
                environment={"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]},
            ),
        ),
    )
    assert report.passed, "\n".join(f"{result.command}: {result.stderr}" for result in results)
