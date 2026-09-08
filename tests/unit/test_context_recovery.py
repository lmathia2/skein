from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.models.checkpoint import Checkpoint
from harness.models.ledger import TaskLedger
from harness.server.ownership import WorkspaceOwner
from harness.state import CheckpointStore, EventKind, JsonlEventStore, ToolReceiptStore
from harness.state.recovery import validate_recovery_evidence
from harness.tools.adk_adapter import create_adk_tools


def _evidence(tmp_path: Path):
    events = JsonlEventStore(tmp_path / "events")
    ledger = TaskLedger(task_id="task", goal="Fix bug", acceptance_criteria=["tests pass"],
                        base_revision="base", workspace_id="workspace", branch_id="main")
    event = events.append("task", EventKind.TASK_CREATED, {"ledger": ledger.model_dump(mode="json")})
    checkpoint = Checkpoint(
        checkpoint_id="cp", task_id="task", session_id="session", invocation_id="invocation",
        branch_id="main", workspace_id="workspace", base_revision="base", git_tree_hash="before",
        ledger_version=1, ledger_hash=hashlib.sha256(ledger.model_dump_json().encode()).hexdigest(),
        event_stream_hash=hashlib.sha256(event.model_dump_json().encode()).hexdigest(),
        created_at=datetime.now(UTC),
    )
    events.append("task", EventKind.CHECKPOINT_CREATED, {"checkpoint_id": "cp"})
    return checkpoint, events


def test_checkpoint_capture_failure_never_publishes(tmp_path: Path) -> None:
    checkpoint, _ = _evidence(tmp_path)

    def fail(_checkpoint):
        raise OSError("capture unavailable")

    store = CheckpointStore(tmp_path / "state.db", on_save=fail)
    with pytest.raises(OSError):
        store.save(checkpoint)
    assert store.latest("task") is None
    store.on_save = None
    store.save(checkpoint)
    store.save(checkpoint)
    with pytest.raises(ValueError, match="identity"):
        store.save(checkpoint.model_copy(update={"git_tree_hash": "different"}))


def test_recovery_validates_prefix_and_post_checkpoint_own_writes(tmp_path: Path) -> None:
    checkpoint, events = _evidence(tmp_path)
    receipts = ToolReceiptStore(tmp_path / "tools.db")
    receipts.begin(task_id="task", invocation_id="invocation", tool_call_id="call",
                   tool_name="write", arguments_hash="arguments", workspace_before="before")
    receipts.finish(task_id="task", tool_call_id="call", status="completed", workspace_after="after")
    args = dict(invocation_id="invocation", session_id="session", workspace_fingerprint="after")
    validate_recovery_evidence(checkpoint, events.read("task"), receipts.for_task("task"), **args)
    with pytest.raises(ValueError, match="diverged"):
        validate_recovery_evidence(checkpoint, events.read("task"), receipts.for_task("task"),
                                   **{**args, "workspace_fingerprint": "outside-change"})
    with pytest.raises(ValueError, match="incomplete"):
        validate_recovery_evidence(checkpoint, events.read("task")[1:], [], **args)
    receipts.begin(task_id="task", invocation_id="invocation", tool_call_id="unknown",
                   tool_name="bash", arguments_hash="arguments")
    with pytest.raises(ValueError, match="reconciliation"):
        validate_recovery_evidence(checkpoint, events.read("task"), receipts.for_task("task"), **args)


def test_failed_capability_is_a_terminal_recovery_event(tmp_path: Path) -> None:
    checkpoint, events = _evidence(tmp_path)
    events.append(
        "task",
        EventKind.CAPABILITY_REQUESTED,
        {"operation_id": "read:1", "operation": "fs.read"},
    )
    events.append(
        "task",
        EventKind.CAPABILITY_FAILED,
        {"operation_id": "read:1", "operation": "fs.read", "effect": "none"},
    )

    validate_recovery_evidence(
        checkpoint,
        events.read("task"),
        [],
        invocation_id="invocation",
        session_id="session",
        workspace_fingerprint="before",
    )
    events.append(
        "task",
        EventKind.CAPABILITY_REQUESTED,
        {"operation_id": "write:1", "operation": "fs.write"},
    )
    events.append(
        "task",
        EventKind.CAPABILITY_FAILED,
        {"operation_id": "write:1", "operation": "fs.write", "effect": "unknown"},
    )
    with pytest.raises(ValueError, match="reconciliation"):
        validate_recovery_evidence(
            checkpoint,
            events.read("task"),
            [],
            invocation_id="invocation",
            session_id="session",
            workspace_fingerprint="before",
        )


def test_operation_identity_allows_intentional_repeat_but_not_unknown_retry(tmp_path: Path) -> None:
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state")
    args = dict(task_scope="task", invocation_id="invocation")
    first = tools.write("result", "one", operation_id="one", **args)
    tools.write("result", "two", operation_id="two", **args)
    assert tools.write("result", "one", operation_id="one", **args)["replayed"]
    assert (tmp_path / "result").read_text() == "two"
    assert tools.write("result", "one", operation_id="three", **args)["status"] == "ok"
    assert (tmp_path / "result").read_text() == "one"
    store = ToolReceiptStore(tmp_path / "state" / "managed-tools.db")
    existing = store.get("task", first["receipt_id"])
    assert existing is not None
    # Simulate death after effect, before its durable completion receipt.
    with store._connect() as connection:
        connection.execute("UPDATE tool_receipts SET status='started' WHERE tool_call_id=?", (first["receipt_id"],))
    blocked = tools.write("result", "one", operation_id="one", **args)
    assert blocked["reconciliation_required"]


def test_workspace_owner_is_exclusive_and_releasable(tmp_path: Path) -> None:
    owner = WorkspaceOwner(tmp_path)
    try:
        with pytest.raises(ValueError, match="owned"):
            WorkspaceOwner(tmp_path)
    finally:
        owner.close()
    WorkspaceOwner(tmp_path).close()
