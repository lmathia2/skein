"""Isolated process driver for context pilot schedules (explicit invocation only)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from harness.config import SkeinConfig
from harness.evals.context_cases import verify_case
from harness.evals.runner import (
    AssemblyBuilder,
    EvaluationError,
    EvaluationRunRequest,
    EvaluationRunResult,
    EvaluationStatus,
    _artifact_paths,
    _atomic_write,
    _model_identity,
    _private_run_data,
    _public_error,
    _public_result,
    _run_id,
    prepare_evaluation_config,
)
from harness.ledger import open_ledger
from harness.ledger.models import canonical_json
from harness.server.bootstrap import build_server_assembly
from harness.server.protocol import StartTaskMessage
from harness.state import EventKind, JsonlEventStore
from harness.state.receipts import ToolReceiptStore

FAULT_EXIT = 85


def _seed(request: EvaluationRunRequest, trial: Path, task_id: str, backend: Literal["jsonl", "duckdb"]) -> None:
    ledger = open_ledger(request.state_root / "runs" / _run_id(task_id), backend)
    for row in map(json.loads, (trial / "workspace/evidence.jsonl").read_text().splitlines()):
        ledger.append(task_id=_run_id(task_id), source="context", source_id=f"fixture:{row['id']}",
                      kind="context.history", payload={"role": "user", "parts": [{"text": canonical_json(row)}]},
                      status=row.get("status", "observed"),
                      observed_at=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=row.get("observed", 0)),
                      recorded_at=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=row.get("recorded", 0)))


async def run_phase(request: EvaluationRunRequest, trial: Path, phase: str, *,
                    assembly_builder: AssemblyBuilder | None = None) -> EvaluationRunResult:
    """One real coordinator lifecycle, also injectable with a scripted provider."""
    started = time.monotonic()
    manifest = json.loads((trial / "trial.json").read_text())
    source_turn = phase == "source"
    recover = phase == "recover"
    task_id = request.task_id + ("-source" if source_turn else "-task")
    config_path, composition = prepare_evaluation_config(request)
    config = composition.harness.config
    if not isinstance(config, SkeinConfig):
        raise ValueError("context schedules require Skein configuration")
    # Prior evidence exists only in the source run, never reseeded into recall.
    if (not recover and getattr(config.memory, "enabled", False)
            and (source_turn or manifest["case_id"] != "later-run-recall")):
        _seed(request, trial, task_id, config.memory.ledger)
    assembly = (assembly_builder or build_server_assembly)(
        workspace=request.workspace, state_root=request.state_root,
        auth_state_root=request.auth_state_root, config_path=config_path,
        trust_project=request.trust_project,
    )
    original_finish = ToolReceiptStore.finish
    if phase == "fault":
        def crash_before_receipt(self, **kwargs):
            counter = request.workspace / "counter.txt"
            if counter.exists() and counter.read_text().strip() == "1":
                _atomic_write(trial / "fault.json", canonical_json({
                    "boundary": "effect_before_receipt", "run_id": _run_id(task_id),
                    "task_id": kwargs.get("task_id"), "tool_call_id": kwargs.get("tool_call_id"),
                }))
                os._exit(FAULT_EXIT)  # Dedicated child only: simulate death, no graceful shutdown.
            return original_finish(self, **kwargs)
        ToolReceiptStore.finish = crash_before_receipt
    try:
        if recover:
            assembly.coordinator.recover_interrupted_runs()
            record = await assembly.coordinator.wait(_run_id(task_id))
        else:
            if source_turn:
                prompt = ("Inspect evidence.jsonl and retain the fact for a later run. "
                          "Do not repeat the fact in your public reply; do not create answer.json. "
                          "If notes are available, record its evidence reference, not new authority.")
            else:
                prompt = request.prompt
                if manifest["case_id"] == "early-evidence":
                    prompt = ("First inspect evidence.jsonl, then make at least 50 separate bounded read calls "
                              "to padding.txt before answering. Do not answer early. " + prompt)
                elif manifest["case_id"] == "interrupted-mutation":
                    prompt = "Use write to set counter.txt to 1 exactly once. " + prompt
            previous = (_run_id(request.task_id + "-source"),) if (
                manifest["case_id"] == "later-run-recall" and not source_turn
                and getattr(config.memory, "prior_runs", False)
            ) else ()
            record, created = await assembly.coordinator.start(StartTaskMessage(
                type="task.start", request_id=task_id, idempotency_key=task_id,
                thread_id=f"context:{request.task_id}", input=prompt,
                source_run_ids=previous,
            ), user_id="evaluation")
            if not created:
                raise ValueError("phase already exists; refusing accidental rerun")
            record = await assembly.coordinator.wait(record.run_id)
        artifacts = _artifact_paths(request.state_root, record.run_id).model_copy(update={
            "result": trial / f"result-{phase}.json",
        })
        answer, verification, changed, metrics = _private_run_data(artifacts, record.run_id)
        public = _public_result(assembly, record.run_id)
        code, error = _public_error(assembly, record.run_id)
        status = str(public.get("status", "failed")) if public else record.status
        if status not in {"complete", "answered", "blocked", "failed", "cancelled"}:
            status = "failed"
        verified = verification.passed if verification else False
        if status == "complete" and not verified:
            status, code, error = "failed", "unverified_completion", "Completion lacked verification"
        result = EvaluationRunResult(
            task_id=task_id, run_id=record.run_id, status=cast(EvaluationStatus, status),
            final_answer=answer, verified=verified, changed_paths=changed, verification=verification,
            model=_model_identity(composition), metrics=metrics, artifacts=artifacts,
            wall_time_ms=int((time.monotonic() - started) * 1000),
            error=EvaluationError(code=code or "run_failed", message=error or record.error or "Run failed")
            if status in {"failed", "cancelled"} else None,
        )
        _atomic_write(artifacts.result, result.model_dump_json(indent=2))
        return result
    finally:
        ToolReceiptStore.finish = original_finish
        await assembly.coordinator.aclose()


def run_schedule(request: EvaluationRunRequest, trial: Path, *,
                 child_command: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Launch only new owned children; never signal another server or process."""
    trial = trial.resolve()
    manifest = json.loads((trial / "trial.json").read_text())
    if request.workspace.resolve() != trial / "workspace" or request.state_root.resolve() != trial / "state":
        raise ValueError("schedule workspace/state must be the prepared trial's isolated paths")
    if request.state_root.exists():
        raise FileExistsError("schedule state already exists; prepare a new trial")
    # Resolve once before any child executes: later phases cannot accidentally load
    # a profile that was edited while the trial was running.
    frozen_config, resolved = prepare_evaluation_config(request)
    request = request.model_copy(update={"config_template": frozen_config})
    phases = ["source", "task"] if manifest["case_id"] == "later-run-recall" else (
        ["fault", "recover"] if manifest["case_id"] == "interrupted-mutation" else ["task"]
    )
    _atomic_write(trial / "request.json", request.model_dump_json())
    # Large output drives selection pressure without changing the model/tool surface.
    _atomic_write(request.workspace / "padding.txt", "irrelevant filler\n" * 500)
    outcomes = []
    source_intact = True
    for phase in phases:
        with (trial / f"process-{phase}.log").open("w") as log:
            try:
                completed = subprocess.run(
                    [*(child_command or (sys.executable, "-m", "harness.evals.context_driver")),
                     "child", str(trial), "--phase", phase],
                    stdout=log, stderr=subprocess.STDOUT, check=False,
                    timeout=request.wall_time_seconds + 30,
                )
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                returncode = 124  # subprocess.run kills/reaps its own child on timeout.
        outcomes.append({"phase": phase, "returncode": returncode})
        if phase == "fault" and returncode != FAULT_EXIT:
            break
        if phase != "fault" and returncode != 0:
            break
        if phase == "source":
            # Remove current-workspace evidence before cold-process prior-run recall.
            # Git history remains a possible alternative; trace its use separately.
            import hashlib

            source_intact = hashlib.sha256((request.workspace / "evidence.jsonl").read_bytes()).hexdigest() == manifest["fixture_sha256"]
            _atomic_write(request.workspace / "evidence.jsonl", "")
    events = JsonlEventStore(request.state_root / "runs" / _run_id(request.task_id + "-task") / "events").read(
        _run_id(request.task_id + "-task"))
    epochs = [event.payload["context_epoch"] for event in events
              if event.kind == EventKind.COMPACTION_CREATED and event.payload.get("context_epoch")]
    calls = len(ToolReceiptStore(request.state_root / "runs" / _run_id(request.task_id + "-task")
                                / "managed-tools.db").for_task(_run_id(request.task_id + "-task")))
    schedule_ok = len(outcomes) == len(phases) and all(
        item["returncode"] == (FAULT_EXIT if item["phase"] == "fault" else 0) for item in outcomes)
    if manifest["case_id"] == "early-evidence":
        config = resolved.harness.config
        expected_epochs = 3 if isinstance(config, SkeinConfig) and config.context.window_management else 0
        schedule_ok = schedule_ok and calls >= 50 and len(epochs) >= expected_epochs
    if manifest["case_id"] == "interrupted-mutation":
        schedule_ok = schedule_ok and (trial / "fault.json").exists()
    # Restore only immutable fixture input for independent integrity grading, not for a model call.
    if manifest["case_id"] == "later-run-recall":
        source_intact = source_intact and (request.workspace / "evidence.jsonl").read_text() == ""
        if source_intact:
            original = subprocess.run(["git", "show", "HEAD:evidence.jsonl"], cwd=request.workspace,
                                      check=True, capture_output=True, text=True, timeout=30).stdout
            _atomic_write(request.workspace / "evidence.jsonl", original)
    recovery_safe = None
    if manifest["case_id"] == "interrupted-mutation" and (trial / "result-recover.json").exists():
        recovery = EvaluationRunResult.model_validate_json((trial / "result-recover.json").read_text())
        recovery_safe = (recovery.error is not None
                         and recovery.error.code in {"server_restarted", "recovery_blocked"}
                         and (request.workspace / "counter.txt").read_text().strip() == "1")
    report = {"schema_version": "skein-context-schedule-v1", "case_id": manifest["case_id"],
              "phases": outcomes, "schedule_satisfied": schedule_ok, "context_epochs": epochs,
              "tool_calls": calls, "safe_recovery_block": recovery_safe,
              "independent_verification": source_intact and verify_case(trial)}
    _atomic_write(trial / "schedule-result.json", canonical_json(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "child"))
    parser.add_argument("trial", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--phase", choices=("source", "task", "fault", "recover"))
    args = parser.parse_args()
    request = EvaluationRunRequest.model_validate_json(
        (args.request or args.trial / "request.json").read_text())
    if args.command == "child":
        if args.phase is None:
            parser.error("child requires --phase")
        asyncio.run(run_phase(request, args.trial, args.phase))
    else:
        report = run_schedule(request, args.trial)
        print(canonical_json(report))
        raise SystemExit(0 if report["schedule_satisfied"] and (
            report["independent_verification"] or report["safe_recovery_block"]
        ) else 1)


if __name__ == "__main__":
    main()
