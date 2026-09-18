"""Frozen, unseeded worker-loss qualification cases; not prompt-development fixtures."""
from __future__ import annotations

import hashlib
import json

from harness.evidence.memory.models import ReadEvidence

HELDOUT_CASES = ("heldout_settlements", "heldout_rollout", "heldout_units")


def heldout_fixture(case: str) -> dict:
    if case == "heldout_settlements":
        files = {
            "batches/north.csv": "account,status,amount_cents\nK42,settled,718\nM18,settled,412\nK42,pending,9900\n",
            "batches/east.csv": "account,status,amount_cents\nM18,void,810\nK42,settled,-113\nR73,settled,260\n",
            "batches/west.csv": "account,status,amount_cents\nK42,void,6500\nR73,pending,700\nK42,settled,946\n",
            "rules.md": "Only settled rows contribute to an account total. Signed amounts are cents. Pending and void rows are not completed evidence of settlement.\n",
        }
        acquisition = "Preserve per-account settled totals with their source rows, and distinguish pending/void rows."
        question = "For account K42, write answer.json with exactly settled_cents and settled_rows across all batches."
        expected = {"settled_cents": 1551, "settled_rows": 3}
    elif case == "heldout_rollout":
        files = {
            "deployments.json": json.dumps([
                {"service": "beech", "sequence": 12, "status": "completed", "build": "b17", "region": "eu-west"},
                {"service": "elm", "sequence": 15, "status": "completed", "build": "e31", "region": "ap-east"},
                {"service": "beech", "sequence": 18, "status": "completed", "build": "b24", "region": "us-north"},
                {"service": "beech", "sequence": 21, "status": "queued", "build": "b99", "region": "ap-east"},
            ], indent=2) + "\n",
            "routing.toml": '[beech]\nenabled = true\nalias = "forest-api"\n[elm]\nenabled = false\nalias = "archive-api"\n',
            "rules.md": "Resolve aliases through routing.toml. For an enabled service, only completed deployments count; choose the greatest completed sequence. A queued deployment is not live.\n",
        }
        acquisition = "Preserve alias/enabled mappings and the latest completed deployment for each service, keeping queued work separate."
        question = "For alias forest-api, write answer.json with exactly build and region for its live deployment."
        expected = {"build": "b24", "region": "us-north"}
    elif case == "heldout_units":
        readings = {
            "gauges/amber.json": {"sensor": "amber", "state": "complete", "value": 2800, "unit": "ms"},
            "gauges/indigo.json": {"sensor": "indigo", "state": "complete", "value": 3200000, "unit": "us"},
            "gauges/silver.json": {"sensor": "silver", "state": "pending", "value": 99, "unit": "s"},
        }
        files = {p: json.dumps(v, indent=2) + "\n" for p, v in readings.items()}
        files["units.toml"] = "microseconds_per_ms = 1000\nmilliseconds_per_s = 1000\n"
        files["rules.md"] = "Only complete measurements are eligible. Normalize units before comparing; a pending value is not a completed measurement.\n"
        acquisition = "Preserve sensor/value/unit/state facts and conversion factors; do not rank incomparable raw values."
        question = "Write answer.json with exactly sensor and peak_ms for the largest eligible completed measurement."
        expected = {"sensor": "indigo", "peak_ms": 3200}
    else:
        raise ValueError("unknown held-out continuity case")
    paths = sorted(files)
    requirements = [ReadEvidence(path=p, sha256=hashlib.sha256(files[p].encode()).hexdigest(),
                                  offset=1, returned_lines=len(files[p].splitlines())).model_dump() for p in paths]
    goal = (
        f"Prepare for a later source question. Read these files using agent.fs.read: {', '.join(paths)}. "
        f"{acquisition} Preserve a concise public checkpoint using working notes if available, otherwise "
        "available Python/artifact facilities or a printed summary. You may retain values or publish a "
        "bounded artifact; do not modify source files or create answer.json yet. After preparing your "
        "checkpoint, print LEARNING_COMPLETE on its own line in a successful execute_code cell. "
        "The coordinator will request one acknowledgement, then deliver the question at a context boundary. "
        "The Python worker will stop between completed cells at that boundary; durable artifacts/history "
        "remain, and normal safe restoration remains enabled. Do not search for host oracle code."
    )
    followup = (
        f"Follow-up replaces the acknowledgement request. The worker stopped after the completed checkpoint. {question} "
        "Use completed evidence only. Reuse appropriate retained findings, artifacts, or replay-safe data; "
        "fetch source ranges when needed, not merely to reconfirm unchanged evidence. Then request verification."
    )
    return {"family": case, "variant": 0, "files": files, "target": paths[0], "expected": expected,
            "goal": goal, "followup": followup, "expect_abstention": False,
            "learning_requirements": requirements, "source_requirements": requirements,
            "checkpoint_marker": "LEARNING_COMPLETE", "worker_loss": True}
