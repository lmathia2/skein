from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.evidence.state import EventKind, HarnessEvent
from harness.ptc.notebook import (
    canonical_notebook_bytes,
    externalize_mime_bundle,
    materialize_notebook,
    reduce_notebook,
)


def _event(sequence: int, kind: EventKind, payload: dict[str, object]) -> HarnessEvent:
    return HarnessEvent(
        event_id=f"event-{sequence}",
        task_id="task-1",
        sequence=sequence,
        kind=kind,
        payload={"notebook_id": "notebook-1", **payload},
    )


def test_notebook_projection_preserves_order_provenance_outputs_and_artifacts() -> None:
    events = [
        _event(1, EventKind.NOTEBOOK_CELL_ADDED, {
            "cell_id": "cell-a", "source": "value = 40\n", "attempt_id": "attempt-a",
            "kernel_epoch": "kernel-1", "replay_policy": "safe",
        }),
        _event(2, EventKind.REPL_CELL_SUBMITTED, {
            "cell_id": "cell-a", "attempt_id": "attempt-a",
        }),
        _event(3, EventKind.NOTEBOOK_CELL_ADDED, {
            "cell_id": "cell-b", "source": "print(value + 2)\n", "attempt_id": "attempt-b",
            "kernel_epoch": "kernel-1", "replay_policy": "safe",
        }),
        _event(4, EventKind.REPL_CELL_COMPLETED, {
            "cell_id": "cell-a", "attempt_id": "attempt-a", "effect": "observed",
            "display": {"text/plain": "40"},
        }),
        _event(5, EventKind.REPL_CELL_COMPLETED, {
            "cell_id": "cell-b", "attempt_id": "attempt-b", "effect": "observed",
            "stdout": "42\n", "artifact_refs": ["artifact://sha256/b", "artifact://sha256/a"],
        }),
    ]

    state = reduce_notebook(reversed(events), "notebook-1")
    assert [cell.cell_id for cell in state.cells] == ["cell-a", "cell-b"]
    assert state.cells[1].artifact_refs == ["artifact://sha256/a", "artifact://sha256/b"]
    assert state.cells[1].status == "completed"
    notebook = json.loads(canonical_notebook_bytes(state))
    assert notebook["nbformat_minor"] == 5
    assert notebook["cells"][0]["metadata"]["agent"]["ledger_end_seq"] == 4
    assert notebook["cells"][1]["outputs"][0] == {
        "name": "stdout", "output_type": "stream", "text": "42\n",
    }


def test_notebook_projects_messages_and_compaction_with_time_provenance() -> None:
    events = [
        HarnessEvent(
            event_id="task-event",
            task_id="task-1",
            sequence=1,
            kind=EventKind.TASK_CREATED,
            payload={"ledger": {"goal": "Fix it", "acceptance_criteria": ["Tests pass"]}},
        ),
        HarnessEvent(
            event_id="message-event",
            task_id="task-1",
            sequence=2,
            kind=EventKind.MESSAGE_RECORDED,
            payload={"role": "assistant", "content": "Inspecting the failure."},
        ),
        HarnessEvent(
            event_id="compaction-event",
            task_id="task-1",
            sequence=3,
            kind=EventKind.COMPACTION_CREATED,
            payload={"summary": "Continue from verification."},
        ),
    ]

    document = json.loads(canonical_notebook_bytes(reduce_notebook(events, "notebook-1")))

    assert [cell["cell_type"] for cell in document["cells"]] == [
        "markdown", "markdown", "markdown",
    ]
    assert "# User request" in "".join(document["cells"][0]["source"])
    assert "# Assistant" in "".join(document["cells"][1]["source"])
    assert "# Compaction handoff" in "".join(document["cells"][2]["source"])
    assert document["cells"][2]["metadata"]["agent"]["observed_at"].endswith("+00:00")


def test_timeout_with_unknown_effect_and_failure_are_durable() -> None:
    events = [
        _event(1, EventKind.NOTEBOOK_CELL_ADDED, {
            "cell_id": "timeout", "source": "call_remote()", "attempt_id": "attempt-t",
            "kernel_epoch": "kernel-1",
        }),
        _event(2, EventKind.REPL_CELL_TIMEOUT, {
            "cell_id": "timeout", "attempt_id": "attempt-t", "effect": "unknown",
            "stderr": "deadline exceeded", "artifact_refs": ["artifact://sha256/partial"],
        }),
        _event(3, EventKind.NOTEBOOK_CELL_ADDED, {
            "cell_id": "failed", "source": "1 / 0", "attempt_id": "attempt-f",
            "kernel_epoch": "kernel-1", "replay_policy": "never",
        }),
        _event(4, EventKind.REPL_CELL_FAILED, {
            "cell_id": "failed", "attempt_id": "attempt-f", "effect": "none",
            "exception": {"ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": ["trace"]},
        }),
    ]

    state = reduce_notebook(events, "notebook-1")
    assert state.cells[0].status == "effect_unknown"
    assert state.cells[0].effect == "unknown"
    assert state.cells[1].status == "failed"
    assert state.cells[1].outputs[0]["output_type"] == "error"


def test_rematerialization_is_byte_stable_and_atomic(tmp_path: Path) -> None:
    events = [_event(1, EventKind.NOTEBOOK_CELL_ADDED, {
        "cell_id": "stable", "source": "print('stable')\n", "attempt_id": "attempt-1",
        "kernel_epoch": "kernel-1", "replay_policy": "safe",
    })]
    first_state = reduce_notebook(events, "notebook-1")
    second_state = reduce_notebook(events, "notebook-1")
    path = tmp_path / "session.ipynb"

    first = materialize_notebook(first_state, path)
    path.write_text("corrupted", encoding="utf-8")
    second = materialize_notebook(second_state, path)

    assert first == second == path.read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert list(tmp_path.iterdir()) == [path]


def test_large_rich_output_is_content_addressed_and_deduplicated(tmp_path: Path) -> None:
    bundle = {"image/png": b"image-bytes", "text/plain": "plot"}
    first, first_refs = externalize_mime_bundle(
        bundle, artifact_root=tmp_path / "artifacts", max_inline_bytes=4
    )
    second, second_refs = externalize_mime_bundle(
        bundle, artifact_root=tmp_path / "artifacts", max_inline_bytes=4
    )

    assert first == second
    assert first_refs == second_refs
    assert "image/png" not in first
    assert first["application/vnd.agent.artifact+json"][0]["media_type"] == "image/png"
    assert len(list((tmp_path / "artifacts").iterdir())) == 1
