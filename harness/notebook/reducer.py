"""Reduce notebook and REPL events into the canonical workbench state."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from harness.state.events import EventKind, HarnessEvent

from .models import NotebookCell, NotebookMarkdownCell, NotebookState

_TERMINAL_KINDS: dict[str, str] = {
    EventKind.REPL_CELL_COMPLETED.value: "completed",
    EventKind.REPL_CELL_FAILED.value: "failed",
    EventKind.REPL_CELL_TIMEOUT.value: "timeout",
}


def _markdown_source(event: HarnessEvent) -> str | None:
    payload = event.payload
    if event.kind == EventKind.TASK_CREATED:
        ledger = payload.get("ledger")
        if not isinstance(ledger, dict):
            return None
        lines = ["# User request", "", str(ledger.get("goal", ""))]
        criteria = ledger.get("acceptance_criteria")
        if isinstance(criteria, list) and criteria:
            lines.extend(("", "## Acceptance criteria", ""))
            lines.extend(f"- {item}" for item in criteria)
        return "\n".join(lines).rstrip() + "\n"
    if event.kind == EventKind.MESSAGE_RECORDED:
        role = str(payload.get("role", "message")).capitalize()
        return f"# {role}\n\n{payload.get('content', '')}\n"
    if event.kind == EventKind.STEERING_RECEIVED:
        return f"# User steering\n\n{payload.get('content', '')}\n"
    if event.kind == EventKind.COMPACTION_CREATED:
        return f"# Compaction handoff\n\n{payload.get('summary', '')}\n"
    if event.kind == "memory.note_updated":
        return f"# Advisory working note (version {payload.get('version', 0)})\n\n{payload.get('text', '')}\n"
    return None


def _markdown_cell(event: HarnessEvent, source: str) -> NotebookMarkdownCell:
    digest = hashlib.sha256(f"markdown:{event.event_id}".encode()).hexdigest()
    return NotebookMarkdownCell(
        task_id=event.task_id,
        cell_id=digest[:32],
        source=source,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        event_id=event.event_id,
        event_kind=event.kind,
        ledger_seq=event.sequence,
        observed_at=event.timestamp,
    )


def _outputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for name in ("stdout", "stderr"):
        value = payload.get(name)
        if value:
            outputs.append({"name": name, "output_type": "stream", "text": str(value)})
    display = payload.get("display")
    if isinstance(display, dict):
        outputs.append({"data": display, "metadata": {}, "output_type": "display_data"})
    exception = payload.get("exception")
    if isinstance(exception, dict):
        outputs.append(
            {
                "ename": str(exception.get("ename", "Error")),
                "evalue": str(exception.get("evalue", "")),
                "output_type": "error",
                "traceback": [str(line) for line in exception.get("traceback", [])],
            }
        )
    return outputs


def reduce_notebook(events: Iterable[HarnessEvent], notebook_id: str) -> NotebookState:
    """Build one notebook deterministically from its ordered event stream."""

    source_events = list(events)
    # Caller supplies frozen prior-run order; sequence is local to each run.
    run_order = {task_id: index for index, task_id in enumerate(dict.fromkeys(event.task_id for event in source_events))}
    ordered = sorted(source_events, key=lambda event: (run_order[event.task_id], event.sequence))
    cells: dict[str, NotebookCell | NotebookMarkdownCell] = {}
    order: list[str] = []
    watermark = 0
    source_watermarks: dict[str, int] = {}
    for event in ordered:
        payload = event.payload
        markdown = _markdown_source(event)
        if markdown is not None:
            watermark = max(watermark, event.sequence)
            source_watermarks[event.task_id] = event.sequence
            narrative = _markdown_cell(event, markdown)
            if narrative.cell_id not in cells:
                cells[narrative.cell_id] = narrative
                order.append(narrative.cell_id)
            continue
        if payload.get("notebook_id") != notebook_id:
            continue
        if event.kind == EventKind.NOTEBOOK_SNAPSHOTTED:
            continue
        watermark = max(watermark, event.sequence)
        source_watermarks[event.task_id] = event.sequence
        cell_id = payload.get("cell_id")
        if event.kind == EventKind.NOTEBOOK_CELL_ADDED:
            if not isinstance(cell_id, str) or not cell_id:
                raise ValueError("notebook.cell_added requires cell_id")
            source = str(payload.get("source", ""))
            candidate = NotebookCell(
                task_id=event.task_id,
                cell_id=cell_id,
                source=source,
                source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                cell_event_id=event.event_id,
                attempt_id=str(payload["attempt_id"]),
                kernel_epoch=str(payload["kernel_epoch"]),
                ledger_start_seq=event.sequence,
                ledger_end_seq=event.sequence,
                replay_policy=payload.get("replay_policy", "requires_reconciliation"),
                artifact_refs=sorted(set(payload.get("artifact_refs", []))),
                program_version=payload.get("program_version", 1),
                observed_at=event.timestamp,
            )
            if cell_id in cells and cells[cell_id] != candidate:
                raise ValueError(f"conflicting notebook cell: {cell_id}")
            if cell_id not in cells:
                cells[cell_id] = candidate
                order.append(cell_id)
            continue
        if not isinstance(cell_id, str) or cell_id not in cells:
            continue
        cell = cells[cell_id]
        if not isinstance(cell, NotebookCell):
            continue
        if payload.get("attempt_id", cell.attempt_id) != cell.attempt_id:
            raise ValueError(f"attempt does not match notebook cell: {cell_id}")
        if event.kind == EventKind.REPL_CELL_SUBMITTED:
            cell = cell.model_copy(update={"ledger_end_seq": event.sequence})
        elif event.kind in _TERMINAL_KINDS:
            status = _TERMINAL_KINDS[event.kind]
            effect = payload.get("effect", "unknown" if status == "timeout" else "none")
            if effect == "unknown" and status in {"failed", "timeout"}:
                status = "effect_unknown"
            cell = cell.model_copy(
                update={
                    "ledger_end_seq": event.sequence,
                    "status": status,
                    "effect": effect,
                    "artifact_refs": sorted(
                        set(cell.artifact_refs) | set(payload.get("artifact_refs", []))
                    ),
                    "outputs": _outputs(payload),
                    "completed_at": event.timestamp,
                }
            )
        elif event.kind == EventKind.NOTEBOOK_CELL_DELETED:
            del cells[cell_id]
            order.remove(cell_id)
            continue
        cells[cell_id] = cell
    return NotebookState(
        notebook_id=notebook_id,
        cells=[cells[cell_id] for cell_id in order],
        source_watermark=watermark,
        source_watermarks=source_watermarks,
    )
