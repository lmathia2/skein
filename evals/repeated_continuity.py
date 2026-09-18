"""Fresh one/three-use fixtures; no host-seeded knowledge or model answers."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from harness.evidence.memory.models import ReadEvidence

REPEATED_CASES = tuple(f"reuse_{family}_{uses}" for family in ("repository", "config", "reconcile") for uses in (1, 3))


def repeated_fixture(case: str) -> dict[str, Any]:
    if case not in REPEATED_CASES:
        raise ValueError("unknown repeated-use case")
    _, family, raw_uses = case.split("_")
    uses = int(raw_uses)
    revision: dict[str, str] = {}
    if family == "repository":
        files = {
            "app/api.py": "from domain.checkout import submit\n\ndef create_order(items):\n    return submit(items)\n",
            "app/jobs.py": "from domain.checkout import preview\n\ndef audit(items):\n    return preview(items)\n",
            "domain/checkout.py": "from domain.money import gross\nfrom domain.rules import threshold\n\ndef submit(items):\n    return min(gross(items), threshold())\n\ndef preview(items):\n    return gross(items)\n",
            "domain/money.py": "def gross(items):\n    return sum(item['cents'] for item in items)\n",
            "domain/rules.py": "def threshold():\n    return 4200\n",
        }
        preservation = "Preserve import aliases, direct named-function calls, entry-point paths and the cap rule."
        questions = [
            ("Return callers: the sorted list of fully qualified functions that directly call domain.money.gross.",
             {"callers": ["domain.checkout.preview", "domain.checkout.submit"]}, ["domain/checkout.py", "domain/money.py"]),
            ("For app.api.create_order return cap_module (fully qualified module string) and cap_cents (integer).",
             {"cap_module": "domain.rules", "cap_cents": 4200}, ["app/api.py", "domain/checkout.py", "domain/rules.py"]),
            ("For app.jobs.audit return calls_threshold (boolean) and path (ordered fully qualified named functions from audit through gross, inclusive). Exclude builtins.",
             {"calls_threshold": False, "path": ["app.jobs.audit", "domain.checkout.preview", "domain.money.gross"]},
             ["app/jobs.py", "domain/checkout.py", "domain/money.py"]),
        ]
    elif family == "config":
        files = {
            "defaults.toml": "timeout_ms = 1000\nretries = 2\ncache = true\n",
            "environments.toml": "[prod]\ntimeout_ms = 2750\ncache = false\n[staging]\nretries = 4\n",
            "services.toml": '[api]\nenvironment = "prod"\nretries = 7\n[importer]\nenvironment = "staging"\ntimeout_ms = 1800\n',
            "rules.md": "Resolve each key by overlaying defaults, then the service's named environment, then service values. Missing keys inherit; false is a value, not absence.\n",
        }
        revision = {"environments.toml": "[prod]\ntimeout_ms = 3600\ncache = true\n[staging]\nretries = 4\n"}
        preservation = "Preserve per-key precedence, explicit false values, and both services' effective configurations with supporting sources."
        questions = [
            ("For api return timeout_ms and retries, both integers.", {"timeout_ms": 2750, "retries": 7}, list(files)),
            ("For importer return cache (boolean) and retries (integer).", {"cache": True, "retries": 4}, list(files)),
            ("For api under the revised configuration return timeout_ms (integer) and cache (boolean).",
             {"timeout_ms": 3600, "cache": True}, list(files)),
        ]
    else:
        files = {
            "accounts.json": json.dumps({"display_to_account": {"birch": "A7", "cedar": "B4"}}, indent=2) + "\n",
            "batch_a.csv": "id,account,state,cents\nt11,A7,settled,840\nt12,A7,settled,-125\nt13,B4,settled,330\nt14,A7,pending,9000\n",
            "batch_b.csv": "id,account,state,cents\nt15,A7,settled,-205\nt16,B4,settled,-40\nt17,A7,failed,1100\n",
            "rules.md": "Join display names to account IDs. Sum signed cents only from settled rows in both batches; negative amounts reduce the total. Return supporting row IDs sorted lexicographically.\n",
        }
        preservation = "Preserve aliases, signed settled totals and supporting row IDs per account, keeping pending/failed input records separate."
        questions = [
            ("For birch return net_cents (signed integer) and row_ids (sorted string list of its settled rows).",
             {"net_cents": 510, "row_ids": ["t11", "t12", "t15"]}, list(files)),
            ("For cedar return net_cents (signed integer) and row_ids (sorted string list of its settled rows).",
             {"net_cents": 290, "row_ids": ["t13", "t16"]}, list(files)),
            ("Across both accounts return net_cents (signed integer) and settled_rows (integer count, not row IDs).",
             {"net_cents": 800, "settled_rows": 5}, ["batch_a.csv", "batch_b.csv", "rules.md"]),
        ]
    if uses == 1:
        revision = {}
    final_files = {**files, **revision}

    def requirements(contents: dict[str, str], paths: list[str]) -> list[dict[str, Any]]:
        return [ReadEvidence(path=p, sha256=hashlib.sha256(contents[p].encode()).hexdigest(),
                             offset=1, returned_lines=len(contents[p].splitlines())).model_dump() for p in sorted(paths)]

    answers, followups = [], []
    for index, (question, expected, paths) in enumerate(questions[:uses]):
        path = "answer.json" if index == uses - 1 else f"answers/use_{index + 1}.json"
        answers.append({"path": path, "expected": expected, "checkpoint": index,
                        "required": requirements(final_files if index == 2 else files, paths)})
        followup = (f"Question {index + 1}/{uses} replaces the acknowledgement request. Write a JSON object at workspace-relative {path}, "
                    f"with exactly these top-level keys: {json.dumps(list(expected))}. {question} ")
        followup += "Reuse completed evidence; do not reread unchanged sources just to confirm. "
        if index == uses - 1:
            followup += "Keep every earlier answer intact. Request final verification."
        else:
            if revision and index == 1:
                path, content = next(iter(revision.items()))
                followup += (f"Then replace {path}, using a current-version guard, with these exact UTF-8 bytes as a JSON string: {json.dumps(content)}. "
                             "Read the completed new version and update the public checkpoint. ")
            else:
                followup += "No source changes are authorized. Existing notes/artifacts may be reused without a new note version. "
            followup += f"After the answer and any required update, print USE_{index + 1}_COMPLETE. Await a new acknowledgement; do not verify yet."
        followups.append(followup)
    stages = []
    for index in range(uses):
        changed = bool(revision and index == 2)
        stages.append({
            "learning_requirements": requirements(final_files if changed else files, list(files)),
            "checkpoint_marker": "LEARNING_COMPLETE" if index == 0 else f"USE_{index}_COMPLETE",
            "worker_loss": True, "followup": followups[index],
            "reuse_checkpoint": index > 0 and not changed,
            "required_changes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in revision.items()} if changed else {},
        })
    goal = (
        f"Prepare for {uses} delayed source questions by reading {', '.join(sorted(files))} with agent.fs.read. {preservation} "
        "Preserve useful public findings in working notes when available, otherwise PTC artifacts or a concise printed summary. "
        "Do not write answer files before their question. Print LEARNING_COMPLETE after acquisition and checkpointing. "
        "Each question follows a fresh acknowledgement and a context cut that stops the idle Python worker; durable evidence/artifacts survive. "
        "Answer files are separately source-gated and all remain subject to final independent verification. "
        "Only requested answer paths and explicitly instructed source revisions may change. Do not search for oracle code."
    )
    return {"family": case, "variant": uses, "uses": uses, "files": files, "target": next(iter(files)),
            "goal": goal, "followup": followups[-1], "expected": answers[-1]["expected"], "expect_abstention": False,
            "answers": answers, "stages": stages, "source_requirements": answers[-1]["required"],
            "permitted_paths": [a["path"] for a in answers] + list(revision),
            "final_source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in final_files.items()},
            "revision_files": revision, "max_model_calls": 24, "input_budget": 350_000}
