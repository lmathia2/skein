"""Four seeded usability trials for recovery messages; not a qualification cohort."""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from evals.continuity import INPUT_BUDGET, MAX_CALLS, MODEL
from evals.continuity_cases import DevelopmentContinuation
from evals.memory_audit import audit_reads
from evals.runner import _atomic_write
from evals.verified_continuity import run_verified_case
from harness.core.config import parse_harness_composition
from scripts.run_harbor_eval import dotenv_value


def make_trial(root: Path, capture: str, updates: bool) -> DevelopmentContinuation:
    if capture not in {"complete", "partial"} or type(updates) is not bool:
        raise ValueError("fixed complete/partial capture and on/off message conditions required")
    trial = DevelopmentContinuation(root, "missing", "findings")
    trial.fixture["probe_limit"] = 400 if capture == "complete" else 3
    code = (f"captured = agent.fs.read('config/route_00.toml', limit={trial.fixture['probe_limit']})\n"
            "unfinished_result = 999\n1 / 0")
    trial.fixture["probe_code"] = code
    trial.fixture["goal"] = (
        "This is an explicit recovery diagnostic. First submit the following scratch cell once, unchanged, "
        "through execute_code. Its intentional Python exception occurs after the managed read. "
        "Do not catch the exception or repeat the scratch cell; the failed calculation is not a result. "
        "After that expected failure, finish the source question below using sufficient completed evidence.\n"
        f"```python\n{code}\n```\n" + trial.fixture["goal"])
    payload = trial.composition.model_dump(mode="json")
    payload["harness"]["config"]["notebook_ptc"]["emit_state_updates"] = updates
    trial.composition = parse_harness_composition(payload)
    return trial


def _syntax(code: str) -> str | None:
    try:
        return ast.dump(ast.parse(code), include_attributes=False)
    except (SyntaxError, ValueError, RecursionError):
        return None


def audit_probe(trial: DevelopmentContinuation) -> dict[str, Any]:
    events = trial.ledger.read(trial.task_id)
    submitted = [e for e in events if e.kind == "repl.cell_submitted"
                 and _syntax(e.payload.get("source", "")) == _syntax(trial.fixture["probe_code"])]
    report: dict[str, Any] = {"version": "recovery-message-probe@1", "exercised": False,
                              "matching_submissions": len(submitted)}
    if len(submitted) != 1:
        return report
    start = submitted[0]
    attempt = start.payload["attempt_id"]
    terminals = [e for e in events if e.payload.get("attempt_id") == attempt
                 and e.kind in {"repl.cell_completed", "repl.cell_failed", "repl.cell_timeout"}]
    reads = [e for e in events if e.payload.get("attempt_id") == attempt
             and e.kind == "capability.completed" and e.payload.get("status") == "ok"
             and e.payload.get("operation") == "fs.read" and e.payload.get("read_evidence")]
    if len(terminals) != 1 or len(reads) != 1:
        return report
    failure, read = terminals[0], reads[0]
    exception = failure.payload.get("exception", {})
    uri = read.payload["result_artifact_uri"]
    source = read.payload["read_evidence"]
    expected_lines = min(trial.fixture["probe_limit"], len(trial.fixture["files"]["config/route_00.toml"].splitlines()))
    preceding_cells = [e for e in events if e.kind == "repl.cell_submitted" and e.sequence < start.sequence]
    valid = (len(preceding_cells) == trial.seed_cells
             and start.sequence < read.sequence < failure.sequence and failure.kind == "repl.cell_failed"
             and exception.get("ename") == "ZeroDivisionError" and exception.get("state_preserved") is False
             and failure.payload.get("execution_started") is True and failure.payload.get("effect") == "observed"
             and source == {"path": "config/route_00.toml", "offset": 1, "returned_lines": expected_lines,
                            "sha256": hashlib.sha256(trial.fixture["files"]["config/route_00.toml"].encode()).hexdigest()})
    report.update(exercised=valid, attempt_id=attempt, failure_sequence=failure.sequence,
                  read_event_id=read.event_id, artifact_uri=uri,
                  post_failure_reads=audit_reads(events, cut_sequence=failure.sequence))
    loads = [e for e in events if e.sequence > failure.sequence and e.kind == "capability.completed"
             and e.payload.get("operation") == "artifacts.load" and e.payload.get("status") == "ok"
             and uri in e.payload.get("artifact_refs", [])]
    report["probe_artifact_load_events"] = [e.event_id for e in loads]
    report["direct_handle_exposed"] = False
    report["failed_kernel_exposed"] = False
    for path in sorted((trial.root / "wire").glob("*.json")):
        if path.stat().st_size > 16_000_000:
            raise ValueError("wire audit exceeds byte budget")
        for item in json.loads(path.read_text())["input"]:
            if item.get("type") != "function_call_output":
                continue
            output = json.loads(item["output"])
            if not isinstance(output, dict) or output.get("attempt_id") != attempt:
                continue
            report["failed_kernel_exposed"] |= output.get("kernel", {}).get("live") is False
            marker = "State updates (advisory; sources historical):\n"
            text = output.get("model_text", "")
            if marker in text:
                notice, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])
                report["direct_handle_exposed"] |= any(
                    row.get("historical_read", {}).get("artifact_uri") == uri and row.get("recover_expression")
                    for row in notice["entries"])
    report["exercised"] = valid and report["failed_kernel_exposed"]
    return report


async def campaign(output: Path, image: str, concurrency: int = 6) -> list[dict[str, Any]]:
    if not 1 <= concurrency <= 6:
        raise ValueError("concurrency must be within 1..6")
    semaphore, stopped = asyncio.Semaphore(concurrency), asyncio.Event()

    async def run(capture: str, updates: bool) -> dict[str, Any]:
        async with semaphore:
            root = output / f"{capture}-{'on' if updates else 'off'}"
            trial = make_trial(root, capture, updates)
            trial.command_image = image
            result: dict[str, Any] = {"terminal": "not_started_infrastructure_gate"}
            if not stopped.is_set():
                try:
                    async with asyncio.timeout(900):
                        result = await run_verified_case(trial)
                except Exception as exc:
                    progress = root / "progress.json"
                    result = json.loads(progress.read_text()) if progress.exists() else {}
                    result.update(accepted=False, terminal="wall_time_limit" if isinstance(exc, TimeoutError)
                                  else "harness_or_fixture_error", error=type(exc).__name__)
                else:
                    try:
                        result["probe"] = audit_probe(trial)
                    except Exception as exc:
                        # Keep the settled provider/verification record even if
                        # the independent diagnostic measurement itself fails.
                        result["measurement_error"] = type(exc).__name__
                if (result["terminal"] in {"harness_or_fixture_error", "provider_error", "false_acceptance",
                        "wall_time_limit", "context_control_budget_exceeded"} or "measurement_error" in result
                        or any(result.get(key, 0) for key in (
                            "unaccounted_model_calls", "usage_missing_calls", "cost_missing_calls", "extra_wire_attempts"))):
                    stopped.set()
            result.update(capture=capture, state_updates=updates)
            _atomic_write(root / "result.json", json.dumps(result, indent=2))
            print(json.dumps({key: result.get(key) for key in
                              ("capture", "state_updates", "terminal", "model_calls", "provider_cost_usd")}), flush=True)
            return result

    rows = await asyncio.gather(*(run(capture, updates) for capture in ("complete", "partial") for updates in (False, True)))
    _atomic_write(output / "results.json", json.dumps(rows, indent=2))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--sandbox-image", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fresh output directory required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.sandbox_image):
        raise ValueError("immutable cached Docker image required")
    if args.live:
        if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
            raise ValueError("live diagnostic requires a clean committed tree")
        actual = subprocess.check_output(["docker", "image", "inspect", args.sandbox_image, "--format", "{{.Id}}"], text=True).strip()
        if actual != args.sandbox_image:
            raise ValueError("cached image identity mismatch")
    trials = {f"{capture}-{'on' if enabled else 'off'}": make_trial(args.output / "unused", capture, enabled)
              for capture in ("complete", "partial") for enabled in (False, True)}
    manifest = {"version": "recovery-message-usability-v1", "scope": "seeded reused development diagnostic",
                "model": MODEL, "reasoning": "max", "trials": 4, "concurrency": 6, "repetitions": 1,
                "max_model_calls": MAX_CALLS, "input_budget": INPUT_BUDGET, "max_output_tokens": 8192,
                "wall_seconds_per_trial": 900, "sandbox_image": args.sandbox_image, "promotion": False,
                "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "driver_hashes": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (
                    "evals/recovery_messages.py", "evals/verified_continuity.py", "evals/continuity.py",
                    "evals/continuity_cases.py", "evals/continuity_oracle.py", "evals/memory_audit.py")},
                "fixture_hashes": {name: hashlib.sha256(json.dumps(trial.fixture, sort_keys=True).encode()).hexdigest()
                                   for name, trial in trials.items()},
                "configs": {name: trial.composition.model_dump(mode="json") for name, trial in trials.items()},
                "limitations": ["Host-seeded preparation is uncharged; all live provider work is charged",
                                "Representation, memory and capabilities shared; only emit_state_updates differs",
                                "Artifact acceptance and actual probe exercise are separately reported",
                                "No selective retries, paid expansion or held-out reliability claim"]}
    _atomic_write(args.output / "manifest.json", json.dumps(manifest, indent=2))
    if not args.live:
        print(json.dumps({"manifest": str(args.output / "manifest.json"), "live": False}))
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_value(args.dotenv, "OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY unavailable")
    os.environ["OPENROUTER_API_KEY"] = key
    asyncio.run(campaign(args.output, args.sandbox_image))


if __name__ == "__main__":
    main()
