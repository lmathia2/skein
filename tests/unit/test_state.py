from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.models.checkpoint import Checkpoint
from harness.models.ledger import TaskLedger
from harness.orchestration import replan_ledger
from harness.state import (
    CheckpointStore,
    EventKind,
    JsonlEventStore,
    ProgressRoute,
    SteeringQueue,
    ToolReceiptStore,
    rebuild_ledger,
    register_action,
    register_action_batch,
    route_for_progress,
    verification_fingerprint,
)


def _ledger(task_id: str = "task-1") -> TaskLedger:
    return TaskLedger(
        task_id=task_id,
        goal="Fix authentication",
        acceptance_criteria=["Login succeeds"],
        base_revision="abc123",
        workspace_id="workspace-1",
        branch_id="main",
    )


def test_event_store_is_idempotent_and_replayable(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path / "events")
    ledger = _ledger()
    created = store.append(
        ledger.task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
        idempotency_key="create",
    )
    duplicate = store.append(
        ledger.task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
        idempotency_key="create",
    )
    assert duplicate.event_id == created.event_id
    store.append(
        ledger.task_id,
        EventKind.LEDGER_PATCHED,
        {"set_fields": {"next_action": "Inspect auth service"}},
    )

    rebuilt = rebuild_ledger(store.read(ledger.task_id))
    assert rebuilt.next_action == "Inspect auth service"


def test_event_store_rejects_idempotency_conflict(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path)
    store.append("task", "one", {"value": 1}, idempotency_key="same")
    with pytest.raises(ValueError):
        store.append("task", "two", {"value": 2}, idempotency_key="same")


def test_tool_receipts_prevent_duplicate_side_effects(tmp_path: Path) -> None:
    store = ToolReceiptStore(tmp_path / "state.db")
    first = store.begin(
        task_id="task",
        invocation_id="inv-1",
        tool_call_id="call-1",
        tool_name="write",
        arguments_hash="args",
        side_effect_key="write:file.py:content",
    )
    duplicate = store.begin(
        task_id="task",
        invocation_id="inv-2",
        tool_call_id="call-2",
        tool_name="write",
        arguments_hash="args",
        side_effect_key="write:file.py:content",
    )
    assert duplicate.tool_call_id == first.tool_call_id
    completed = store.finish(
        task_id="task",
        tool_call_id=first.tool_call_id,
        status="completed",
        result_hash="result",
    )
    assert completed.status == "completed"


def test_steering_queue_lease_ack_and_idempotency(tmp_path: Path) -> None:
    queue = SteeringQueue(tmp_path / "steering.db")
    first = queue.enqueue("task", "Stop and use the public API", idempotency_key="m1")
    duplicate = queue.enqueue("task", "Stop and use the public API", idempotency_key="m1")
    assert duplicate.message_id == first.message_id

    leased = queue.lease("task", "worker-1")
    assert [item.message_id for item in leased] == [first.message_id]
    assert queue.lease("task", "worker-2") == []
    assert queue.ack([first.message_id], "worker-1") == 1
    assert queue.lease("task", "worker-2") == []


def test_steering_queue_bounds_content_and_exposes_delivery_state(
    tmp_path: Path,
) -> None:
    queue = SteeringQueue(tmp_path / "steering.db")
    first = queue.enqueue("task", "  Use the public API  ", priority=10)
    second = queue.enqueue("task", "Keep the compatibility shim")

    assert first.content == "Use the public API"
    assert queue.has_pending("task")
    leased = queue.lease("task", "worker-1", limit=1)
    assert [item.message_id for item in leased] == [first.message_id]
    assert [item.message_id for item in queue.leased_by("task", "worker-1")] == [
        first.message_id
    ]
    assert queue.has_pending("task")
    assert [item.message_id for item in queue.list_messages("task")] == [
        first.message_id,
        second.message_id,
    ]

    with pytest.raises(ValueError, match="4096 UTF-8 bytes"):
        queue.enqueue("task", "x" * 4_097)
    assert queue.ack([first.message_id], "worker-1") == 1
    assert [
        item.message_id for item in queue.lease("task", "worker-2")
    ] == [second.message_id]


def test_checkpoint_store_returns_latest(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoints.db")
    checkpoint = Checkpoint(
        checkpoint_id="cp-1",
        task_id="task",
        session_id="session",
        invocation_id="invocation",
        branch_id="main",
        workspace_id="workspace",
        base_revision="abc",
        git_tree_hash="tree",
        ledger_version=1,
        ledger_hash="ledger",
        created_at=datetime.now(UTC),
    )
    store.save(checkpoint)
    assert store.get("cp-1") == checkpoint
    assert store.latest("task") == checkpoint


def test_no_progress_routes_to_replan_then_human() -> None:
    ledger = _ledger()
    for _ in range(3):
        ledger = register_action(
            ledger,
            tool_name="read",
            arguments={"path": "auth.py"},
            result_hash="same",
        )
    assert route_for_progress(ledger) == ProgressRoute.REPLAN
    for _ in range(2):
        ledger = register_action(
            ledger,
            tool_name="read",
            arguments={"path": "auth.py"},
            result_hash="same",
        )
    assert route_for_progress(ledger) == ProgressRoute.NEEDS_INPUT


def test_action_batches_detect_loops_across_replans() -> None:
    ledger = register_action_batch(_ledger(), ["read-a", "bash-a"])
    assert ledger.no_progress_count == 0
    for expected_count in range(1, 5):
        ledger = register_action_batch(ledger, ["read-a", "bash-a"])
        assert ledger.no_progress_count == expected_count
        if expected_count == 2:
            ledger = replan_ledger(ledger)
            assert ledger.no_progress_count == 2

    assert route_for_progress(ledger) == ProgressRoute.NEEDS_INPUT


def test_verification_fingerprint_ignores_volatile_fields() -> None:
    report = {
        "passed": False,
        "validations": [{
            "category": "test",
            "command": "pytest -q",
            "required": True,
            "strength": "behavioral",
            "status": "error",
            "exit_code": 1,
            "summary": "1 failed",
            "duration_ms": 100,
            "artifact_uri": "artifact://first",
        }],
        "tests_failed": 1,
    }
    changed = {
        **report,
        "validations": [{**report["validations"][0], "duration_ms": 200, "artifact_uri": "artifact://second"}],
    }
    different = {
        **report,
        "validations": [{**report["validations"][0], "exit_code": 2}],
    }

    assert verification_fingerprint(report) == verification_fingerprint(changed)
    assert verification_fingerprint(report) != verification_fingerprint(different)
