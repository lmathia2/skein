"""Canonical nbformat 4.5 serialization and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from .models import NotebookCell, NotebookState


def canonical_notebook_bytes(state: NotebookState) -> bytes:
    cells = []
    for cell in state.cells:
        if not isinstance(cell, NotebookCell):
            cells.append(
                {
                    "cell_type": "markdown",
                    "id": cell.cell_id,
                    "metadata": {
                        "agent": {
                            "event_id": cell.event_id,
                            "task_id": cell.task_id,
                            "event_kind": cell.event_kind,
                            "ledger_seq": cell.ledger_seq,
                            "observed_at": cell.observed_at.isoformat(),
                            "program_version": cell.program_version,
                            "source_sha256": cell.source_sha256,
                        }
                    },
                    "source": cell.source.splitlines(keepends=True),
                }
            )
            continue
        cells.append(
            {
                "cell_type": "code",
                "execution_count": None,
                "id": cell.cell_id,
                "metadata": {
                    "agent": {
                        "artifact_refs": cell.artifact_refs,
                        "task_id": cell.task_id,
                        "attempt_id": cell.attempt_id,
                        "cell_event_id": cell.cell_event_id,
                        "effect": cell.effect,
                        "kernel_epoch": cell.kernel_epoch,
                        "ledger_end_seq": cell.ledger_end_seq,
                        "ledger_start_seq": cell.ledger_start_seq,
                        "observed_at": cell.observed_at.isoformat(),
                        "completed_at": (
                            cell.completed_at.isoformat() if cell.completed_at else None
                        ),
                        "program_version": cell.program_version,
                        "replay_policy": cell.replay_policy,
                        "source_sha256": cell.source_sha256,
                        "status": cell.status,
                    }
                },
                "outputs": cell.outputs,
                "source": cell.source.splitlines(keepends=True),
            }
        )
    document = {
        "cells": cells,
        "metadata": {
            "agent": {
                "notebook_id": state.notebook_id,
                "renderer_version": 2,
                "source_watermark": state.source_watermark,
                "source_watermarks": state.source_watermarks,
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def materialize_notebook(state: NotebookState, path: Path) -> bytes:
    """Atomically write canonical notebook bytes and return them."""

    content = canonical_notebook_bytes(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return content
