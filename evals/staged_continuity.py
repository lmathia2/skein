"""Unseeded repeated-cut fixtures with explicitly authorized source revisions."""
from __future__ import annotations

import hashlib
import json

from harness.evidence.memory.models import ReadEvidence

STAGED_CASES = ("staged_dispatch", "staged_allocation")


def staged_fixture(case: str) -> dict:
    if case == "staged_dispatch":
        files = {
            "deployments.json": json.dumps([
                {"region": "north", "sequence": 19, "state": "completed", "build": "n30"},
                {"region": "south", "sequence": 8, "state": "completed", "build": "s22"},
                {"region": "south", "sequence": 50, "state": "queued", "build": "s99"},
                {"region": "south", "sequence": 14, "state": "completed", "build": "s41"},
            ], indent=2) + "\n",
            "policy.toml": 'active_region = "north"\n',
            "rules.md": "Use policy.toml's active_region. Only completed deployments qualify; select the greatest completed sequence within that region. Queued work is not live.\n",
        }
        replacement = 'active_region = "south"\n'
        acquisition = "Preserve the latest completed deployment by region, queued records separately, and the active-region rule."
        question = "Write answer.json with exactly build (string), region (string), and sequence (integer) for the current active region's live deployment."
        expected = {"build": "s41", "region": "south", "sequence": 14}
    elif case == "staged_allocation":
        files = {
            "stock.csv": "warehouse,state,units\ncobalt,completed,78\nopal,completed,54\nsaffron,pending,999\n",
            "policy.toml": "reserve_units = 8\n",
            "rules.md": "Only completed stock counts. Available units equal units minus the current reserve_units. Select the warehouse with greatest available units; pending stock cannot support an allocation.\n",
        }
        replacement = "reserve_units = 17\n"
        acquisition = "Preserve completed warehouse stock, pending stock separately, and the reserve/availability rule."
        question = "Write answer.json with exactly warehouse (string) and available (integer units) for the largest eligible allocation under the current reserve policy."
        expected = {"warehouse": "cobalt", "available": 61}
    else:
        raise ValueError("unknown staged continuity case")
    final_files = {**files, "policy.toml": replacement}
    def requirements(contents):
        return [ReadEvidence(path=p, sha256=hashlib.sha256(t.encode()).hexdigest(), offset=1,
                             returned_lines=len(t.splitlines())).model_dump() for p, t in sorted(contents.items())]
    learning, final = requirements(files), requirements(final_files)
    goal = (
        f"Prepare for a later question by reading {', '.join(sorted(files))} with agent.fs.read. {acquisition} "
        "Preserve a concise public checkpoint in working notes when available, otherwise PTC artifacts or a printed summary. "
        "Do not write answer.json yet. The coordinator will send an explicit policy revision after the first checkpoint. "
        "Only policy.toml may be changed, and only as subsequently instructed; no other source changes are permitted. "
        "After preparing the initial checkpoint print LEARNING_COMPLETE on its own line in a successful cell. "
        "Acknowledge the coordinator's checkpoint request before proceeding. There will be two context cuts, "
        "each stopping the idle Python worker; durable evidence/artifacts remain but live values may be lost. "
        "Do not search for host oracle code."
    )
    revision = (
        "Follow-up replaces the acknowledgement request. Apply the authorized policy revision through agent.fs: "
        f"replace policy.toml with these exact UTF-8 bytes represented as a JSON string: {json.dumps(replacement)}. "
        "Use a current-version guard. Read the completed revised file with agent.fs.read to establish its new version. "
        "Update the public checkpoint, correcting any policy-dependent findings. Keep applicable unchanged evidence; "
        "do not reread unchanged source files just for confirmation. Do not write answer.json yet. "
        "Print REVISION_COMPLETE on its own line in a successful cell after preserving the revised checkpoint. "
        "Another acknowledgement and worker-loss context cut precede the final question."
    )
    followup = (
        f"Final question replaces the acknowledgement request. {question} "
        "Use completed evidence for the revised policy, not the earlier snapshot, and retain applicable unchanged facts. "
        "Pending operations cannot supply completed evidence. Recover only what is needed; then request verification."
    )
    return {"family": case, "variant": 0, "files": files, "target": "policy.toml", "expected": expected,
            "goal": goal, "followup": followup, "expect_abstention": False,
            "permitted_paths": ["answer.json", "policy.toml"],
            "source_requirements": final, "final_source_hashes": {r["path"]: r["sha256"] for r in final},
            "revision_text": replacement,
            "stages": [
                {"learning_requirements": learning, "checkpoint_marker": "LEARNING_COMPLETE", "worker_loss": True, "followup": revision},
                {"learning_requirements": final, "checkpoint_marker": "REVISION_COMPLETE", "worker_loss": True,
                 "required_changes": {"policy.toml": hashlib.sha256(replacement.encode()).hexdigest()}, "followup": followup},
            ]}
