"""Prepare isolated, provider-free context pilot fixtures and grade disk outputs.

This prepares evidence, not a simulated score. Multi-turn/fault schedules need the
real runtime; a one-shot eval-run cannot stand in for a restart experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from harness.ledger.models import canonical_json

CASE_IDS = (
    "early-evidence", "temporal-correction", "full-set-aggregation",
    "oversized-artifact", "interrupted-mutation", "later-run-recall",
)


def _evidence(case: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows = [{"id": f"e{i:03}", "observed": i, "recorded": i,
             "kind": "verification", "status": "failed" if i % 3 == 0 else "passed"}
            for i in range(150)]
    schedules: list[dict[str, Any]] = [{"action": "inspect", "source": "evidence.jsonl"}]
    if case == "early-evidence":
        rows[0]["value"] = "constraint-781"
        expected = {"value": "constraint-781", "evidence_ids": ["e000"]}
        schedules += [{"action": "tool_calls", "minimum": 50},
                      {"action": "context_transitions", "minimum": 3}]
    elif case == "temporal-correction":
        rows = [
            {"id": "original", "observed": 1, "recorded": 1, "value": "blue"},
            {"id": "correction", "observed": 1, "recorded": 8, "value": "green"},
        ]
        expected = {"known_at_5": "blue", "known_at_9": "green",
                    "evidence_ids": ["original", "correction"]}
    elif case == "full-set-aggregation":
        expected = {"failed_before_100": 34,
                    "evidence_ids": [f"e{i:03}" for i in range(100) if i % 3 == 0]}
    elif case == "oversized-artifact":
        rows = [{"id": "large", "value": "padding-" * 10000 + "tail-exact-923"}]
        expected = {"value": "tail-exact-923", "evidence_ids": ["large"]}
    elif case == "interrupted-mutation":
        rows = [{"id": "operation", "operation_id": "increment-once", "value": 1}]
        expected = {"counter": 1, "evidence_ids": ["operation"]}
        schedules += [{"action": "kill", "boundary": "effect_before_receipt"},
                      {"action": "restart", "expected": "block_unknown_effect"},
                      {"action": "reconcile", "evidence": "counter.txt"}]
    elif case == "later-run-recall":
        rows = [{"id": "prior-fact", "value": "prior-642"}]
        expected = {"value": "prior-642", "evidence_ids": ["prior-fact"]}
        schedules += [{"action": "close_run"}, {"action": "restart_process"},
                      {"action": "new_run", "conversation": "same-owned-conversation"},
                      {"action": "explicit_source_selection", "source": "prior_run"}]
    else:
        raise ValueError(f"unknown context case: {case}")
    return rows, expected, schedules


def prepare_case(destination: Path, case: str, profile: Path) -> Path:
    """Create a new trial only; refuse existing paths, including active run roots."""
    from harness.config import load_harness_composition

    rows, expected, schedule = _evidence(case)
    composition = load_harness_composition(profile)
    destination = destination.expanduser().absolute()
    destination.mkdir(parents=True, exist_ok=False)
    workspace = destination / "workspace"
    workspace.mkdir()
    source = "".join(canonical_json(row) + "\n" for row in rows)
    (workspace / "evidence.jsonl").write_text(source, encoding="utf-8")
    if case == "interrupted-mutation":
        (workspace / "counter.txt").write_text("0\n", encoding="utf-8")
    # Grading oracle is outside the worker workspace, never included in its prompt.
    (destination / "expected.json").write_text(canonical_json(expected) + "\n", encoding="utf-8")
    (destination / "config.json").write_text(
        canonical_json(composition.model_dump(mode="json")) + "\n", encoding="utf-8",
    )
    questions = {
        "early-evidence": "Return the value from the earliest evidence record.",
        "temporal-correction": "Return known_at_5 and known_at_9 using recorded time, not observation time.",
        "full-set-aggregation": "Return failed_before_100: count every failed verification with observed < 100.",
        "oversized-artifact": "Return the exact final sentinel following all padding in the large record.",
        "interrupted-mutation": "Increment counter.txt exactly once under operation increment-once; never retry an uncertain effect.",
        "later-run-recall": "Return the prior run's fact from explicitly selected evidence.",
    }
    prompt = (questions[case] + " Write answer.json with the requested fields and ordered evidence_ids. "
              "Do not modify evidence.jsonl. Completion requires independent verification.")
    manifest = {
        "schema_version": "skein-context-case-v1", "case_id": case,
        "fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "composition_sha256": composition.composition_sha256,
        "behavior_sha256": composition.behavior_sha256,
        "profile": str(profile.resolve()), "workspace": str(workspace),
        "state_root": str(destination / "state"), "prompt": prompt,
        "schedule": schedule, "expected": str(destination / "expected.json"),
        "verification": ["python", "-m", "harness.evals.context_cases", "verify", str(destination)],
        "model_quality_evaluated": False,
    }
    manifest_path = destination / "trial.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    for command in (
        ["git", "init", "--quiet"], ["git", "add", "evidence.jsonl", *(
            ["counter.txt"] if case == "interrupted-mutation" else []
        )],
        ["git", "-c", "user.name=Context fixture", "-c", "user.email=fixture@invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Context fixture"],
    ):
        subprocess.run(command, cwd=workspace, check=True, capture_output=True, timeout=30)
    return manifest_path


def verify_case(destination: Path) -> bool:
    """Grade exact structured evidence and fixture integrity, not model prose."""
    try:
        manifest = json.loads((destination / "trial.json").read_text())
        expected = json.loads((destination / "expected.json").read_text())
        source = (destination / "workspace/evidence.jsonl").read_bytes()
        answer = json.loads((destination / "workspace/answer.json").read_text())
        if hashlib.sha256(source).hexdigest() != manifest["fixture_sha256"]:
            return False
        if answer != expected:
            return False
        if manifest["case_id"] == "interrupted-mutation":
            return (destination / "workspace/counter.txt").read_text().strip() == "1"
        return True
    except (OSError, ValueError, KeyError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("case", choices=CASE_IDS)
    prepare.add_argument("destination", type=Path)
    prepare.add_argument("--config", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare_case(args.destination, args.case, args.config))
    else:
        passed = verify_case(args.destination)
        print(json.dumps({"verified": passed}))
        raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
