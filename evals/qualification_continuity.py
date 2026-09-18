"""Fresh continuity fixtures; never seed model findings or semantic answers."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from evals.transfer_continuity import TRANSFER_CASES, transfer_fixture
from harness.evidence.memory.models import ReadEvidence

QUALIFICATION_CASES = (
    *(f"qualification_{family}_{variant}"
      for family in ("routes", "partial", "changed", "conflict", "validation", "prior")
      for variant in (1, 2)),
    "qualification_routes_3",
    "qualification_routes_5",
    "qualification_routes_7",
    "qualification_routes_9",
    "qualification_routes_11",
    "qualification_routes_13",
    "qualification_routes_15",
    "qualification_partial_3",
    "qualification_changed_3",
    "qualification_changed_4",
    "qualification_conflict_3",
    "qualification_validation_4",
    *TRANSFER_CASES,
)


def qualification_fixture(case: str) -> dict[str, Any]:
    if case in TRANSFER_CASES:
        return transfer_fixture(case)
    if case not in QUALIFICATION_CASES:
        raise ValueError("unknown qualification fixture")
    _, family, number = case.split("_")
    variant = int(number)
    if family == "prior":
        return _prior_fixture(case, variant)
    if family == "validation":
        return _validation_fixture(case, variant)
    if family in {"changed", "conflict"}:
        return _revision_or_conflict_fixture(case, family, variant)
    files: dict[str, str] = {}
    questions: list[tuple[str, dict[str, Any], list[str]]] = []
    initial_ranges: dict[str, tuple[int, int]] = {}
    if family == "routes":
        # All branches are real source relationships. The control retains ordinary
        # artifacts and the same read-index budget; no recovery route is disabled.
        for branch in range(4 + 2 * ((variant - 1) % 2)):
            for depth in range(3):
                path = f"pipeline/branch_{branch}_{depth}.py"
                if depth < 2:
                    files[path] = (f"from pipeline.branch_{branch}_{depth + 1} import apply as downstream\n\n"
                                   f"def apply(value):\n    return downstream(value + {11 + 7 * branch + depth + variant})\n")
                else:
                    files[path] = f"def apply(value):\n    return value * {3 + branch + variant}\n"
        for use, branch in enumerate((0, 2, 1)):
            argument = 17 + variant * 13 + use * 5
            increment = sum(11 + 7 * branch + depth + variant for depth in range(2))
            expected = {"result": (argument + increment) * (3 + branch + variant)}
            questions.append((f"For pipeline.branch_{branch}_0.apply({argument}), compute result as an integer.",
                              expected, [f"pipeline/branch_{branch}_{depth}.py" for depth in range(3)]))
        acquisition = ("Read the listed pipeline sources. Preserve useful relationships and learned transformations "
                       "with their evidence; inputs and selected entry points arrive later.")
    else:
        quota, workers = 29 + 12 * variant, 5 + variant
        files["settings.toml"] = (
            f"[legacy]\nquota = {900 + variant}\nenabled = true\n\n"
            + "".join(f"# reserved migration slot {i:02d}\n" for i in range(18 + 9 * variant))
            + f"[current]\nquota = {quota}\nenabled = false\n")
        files["dispatch.toml"] = f'[service]\nprofile = "current"\nworkers = {workers}\n'
        initial_ranges["settings.toml"] = (1, 4)
        acquisition = ("Read only lines 1-4 of settings.toml during preparation and read dispatch.toml. "
                       "Preserve observed values and their coverage, not assumptions about uncaptured sections. "
                       "Do not inspect the rest of settings.toml until the delayed question.")
        questions.append(("Using the service's selected profile, return total_quota (workers times profile quota, integer) "
                          "and enabled (the profile's boolean). Legacy is a different profile, not a fallback.",
                          {"total_quota": quota * workers, "enabled": False}, list(files)))

    def need(path: str, *, initial: bool = False) -> dict[str, Any]:
        offset, count = initial_ranges.get(path, (1, len(files[path].splitlines()))) if initial else (1, len(files[path].splitlines()))
        if family == "partial" and path == "settings.toml" and not initial:
            offset, count = len(files[path].splitlines()) - 2, 3
        return ReadEvidence(path=path, sha256=hashlib.sha256(files[path].encode()).hexdigest(),
                            offset=offset, returned_lines=count).model_dump()

    answers, stages = [], []
    for index, (question, expected, paths) in enumerate(questions):
        path = "answer.json" if index == len(questions) - 1 else f"answers/use_{index + 1}.json"
        answers.append({"path": path, "expected": expected, "checkpoint": index,
                        "required": [need(p) for p in sorted(paths)]})
        followup = (f"Question {index + 1}/{len(questions)} replaces the acknowledgement request. "
                    f"Write a JSON object at workspace-relative {path} with exactly these top-level keys: {', '.join(expected)}. "
                    + question + " Existing applicable evidence may be reused; acquire missing evidence as needed. ")
        followup += ("Keep earlier answers intact and request independent final verification." if index == len(questions) - 1
                     else f"Do not modify sources. Existing checkpoints may be reused. Print USE_{index + 1}_COMPLETE "
                          "after writing the answer, then await a new acknowledgement rather than verifying yet.")
        stages.append({"learning_requirements": [need(p, initial=True) for p in sorted(files)],
                       "checkpoint_marker": "LEARNING_COMPLETE" if index == 0 else f"USE_{index}_COMPLETE",
                       "worker_loss": True, "reuse_checkpoint": index > 0, "followup": followup})
    goal = (acquisition + f" Sources: {', '.join(sorted(files))}. "
            "Checkpoint learned evidence and open questions using working notes when available, otherwise normal "
            "PTC/artifact facilities. Do not create answer files yet. Print LEARNING_COMPLETE after preparation. "
            "A fresh acknowledgement and idle-worker-loss context cut precede each question. Only requested answer "
            "files may change; do not inspect oracle code. Completion is independently source- and value-verified.")
    required_no_change_command = (
        "git status --short" if family == "routes" and variant in {11, 13, 15} else None
    )
    if required_no_change_command:
        goal += (
            " After the working-note or artifact checkpoint succeeds and before printing LEARNING_COMPLETE, "
            f"run exactly {required_no_change_command!r} once through agent.shell.run and inspect its completed "
            "successful result. This check must not modify the workspace or trigger source reacquisition."
        )
    return {"family": case, "variant": variant, "files": files, "target": next(iter(files)),
            "goal": goal, "followup": stages[-1]["followup"], "expected": answers[-1]["expected"],
            "expect_abstention": False, "answers": answers, "stages": stages,
            "source_requirements": answers[-1]["required"], "initial_ranges": initial_ranges,
            "permitted_paths": [a["path"] for a in answers],
            "final_source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in files.items()},
            "max_model_calls": 24, "input_budget": 350_000,
            **({"required_no_change_command": required_no_change_command} if required_no_change_command else {})}


def _prior_fixture(case: str, variant: int) -> dict[str, Any]:
    files = {f"policies/zone_{i}.toml": (
        f'name = "zone_{i}"\nbase_cents = {31 + i * 7}\nunit_cents = {9 + i * 3 + variant}\n'
        f"cap_cents = {700 + i * 83}\n"
        + "".join(f"# policy evidence slot {i}-{j}: reserved catalog annotation\n" for j in range(8)))
        for i in range(6)}
    files["pricing.py"] = (
        "def charge(policy, units):\n"
        "    subtotal = policy['base_cents'] + units * policy['unit_cents']\n"
        "    return min(subtotal, policy['cap_cents'])\n")
    def needs(contents, paths):
        return [ReadEvidence(path=p, sha256=hashlib.sha256(contents[p].encode()).hexdigest(),
                             offset=1, returned_lines=len(contents[p].splitlines())).model_dump() for p in sorted(paths)]
    def episode(contents, expected, initial, required, question, goal):
        followup = ("Question replaces the acknowledgement. Write answer.json as a JSON object with exactly "
                    + ", ".join(expected) + ". " + question + " Only answer.json may change. Request independent verification.")
        return {"family": case, "variant": variant, "files": contents, "target": "pricing.py", "expected": expected,
                "goal": goal + " Checkpoint learned evidence using working notes when available, otherwise normal PTC/artifacts. "
                        "Print LEARNING_COMPLETE; await a fresh acknowledgement and worker-loss context cut before answering. "
                        "Do not write answer.json early or inspect oracle code.",
                "followup": followup, "source_requirements": required, "permitted_paths": ["answer.json"],
                "answers": [{"path": "answer.json", "expected": expected, "required": required, "checkpoint": 0}],
                "stages": [{"learning_requirements": initial, "checkpoint_marker": "LEARNING_COMPLETE",
                            "worker_loss": True, "followup": followup}],
                "final_source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in contents.items()},
                "max_model_calls": 24, "input_budget": 350_000}
    required = needs(files, files)
    catalog_required = [{**r, "returned_lines": 3} for r in needs(files, [p for p in files if p.endswith(".toml")])]
    producer = episode(dict(files), {"regions": 6, "max_unit_cents": 24 + variant}, required, catalog_required,
                       "Report the number of catalog regions and the greatest unit_cents across them, both integers.",
                       "Audit all six shipping policies under policies/ and pricing.py. Read them with agent.fs.read. "
                       "Preserve useful rates, caps, base charges, and pricing behavior with evidence for later related work.")
    producer["family"] = case + "_producer"
    zone, units = 1 + variant, 11 + variant * 3
    selected = f"policies/zone_{zone}.toml"
    if variant == 2:
        files[selected] = files[selected].replace(f"unit_cents = {9 + zone * 3 + variant}", "unit_cents = 67")
    files["shipment.toml"] = f'policy = "{selected}"\nunits = {units}\n'
    rate = 67 if variant == 2 else 9 + zone * 3 + variant
    expected = {"charge_cents": min(31 + zone * 7 + units * rate, 700 + zone * 83), "zone": f"zone_{zone}"}
    shipment_required = [{**r, "returned_lines": 4} if r["path"] == selected else r
                         for r in needs(files, ["shipment.toml", selected, "pricing.py"])]
    consumer = episode(files, expected, needs(files, ["shipment.toml"]), shipment_required,
                       "Calculate charge_cents (integer) and zone (policy name string) for shipment.toml using pricing.py. "
                       "The owned completed catalog audit is a different task, not this shipment's answer. "
                       "Reuse applicable prior findings or captured content when available. Establish the current full-file hash "
                       "of reused sources with a bounded agent.fs.read (one line is sufficient for identity); acquire missing or "
                       "changed decisive ranges. Metadata-only lookup is not source content or a learned finding.",
                       "Prepare a new shipment task. Read only shipment.toml before the question; do not inspect policies or pricing.py yet. "
                       "An owned completed prior task, fixture-prior, audited this catalog. Its source versions may differ now. "
                       "Prior recall, when enabled, uses memory query --program working_set --tasks fixture-prior.")
    consumer["initial_ranges"] = {p: (1, 0) for p in files if p != "shipment.toml"}
    consumer["producer"] = producer
    return consumer


def _validation_fixture(case: str, variant: int) -> dict[str, Any]:
    command = "python -B -m unittest check_batch"
    failed = variant in {2, 4}
    units, price, correction = 6 + variant, 17 + 2 * variant, 1 + variant
    total = units * price - correction * 13
    files = {
        "records.csv": ("id,units,cents_per_unit,checksum\n"
                        f"sale,{units},{price},{units * price + int(failed)}\n"
                        f"adjustment,{-correction},13,{-correction * 13}\n"),
        "rules.md": (
            "Net cents is the sum of signed units times cents_per_unit across all records; negative adjustments reduce it. "
            "Submit only after capturing records.csv and observing a successful complete check_batch unittest run. "
            "An expected result, test source, pending result or failed run is not successful execution evidence.\n"),
        "check_batch.py": (
            "import csv\nimport unittest\nfrom pathlib import Path\n\nclass Check(unittest.TestCase):\n"
            "    def test_receipt_integrity(self):\n"
            "        rows = list(csv.DictReader(Path('records.csv').read_text().splitlines()))\n"
            "        self.assertEqual(len({r['id'] for r in rows}), len(rows))\n"
            "        for row in rows:\n"
            "            self.assertEqual(int(row['checksum']), int(row['units']) * int(row['cents_per_unit']))\n"),
    }

    def requirements(paths: list[str]) -> list[dict[str, Any]]:
        return [ReadEvidence(path=p, sha256=hashlib.sha256(files[p].encode()).hexdigest(), offset=1,
                             returned_lines=len(files[p].splitlines())).model_dump() for p in paths]

    initial = requirements(["rules.md", "check_batch.py"])
    required = requirements(sorted(files))
    final = (
        "Write a JSON object at workspace-relative answer.json with exactly net_cents (signed integer) and rows (integer count). "
        "Use the signed-record rule and completed successful check, then request independent verification. "
        "Do not rerun an applicable completed check merely because live Python state was lost. "
        "If required evidence is unavailable or the check failed, report blocked without writing answer.json.")
    execution = (
        f"Read records.csv with agent.fs.read and run {command!r} through agent.shell.run. "
        "Inspect the actual completed result; only status ok and exit_code 0 count as success. "
        "If it fails or is incomplete, report blocked without writing answer.json. Do not change sources or substitute a different check. ")
    execution += (final if failed else
                  "After success, checkpoint the learned signed total, source evidence and completed validation receipt using working notes "
                  "when available, otherwise PTC artifacts. Do not write answer.json yet. Print VALIDATION_COMPLETE and await a fresh "
                  "acknowledgement and another worker-loss context cut before the final question.")
    stages = [{"learning_requirements": initial, "checkpoint_marker": "LEARNING_COMPLETE", "worker_loss": True,
               "followup": "Validation request replaces the acknowledgement. " + execution}]
    if not failed:
        stages.append({"learning_requirements": required, "checkpoint_marker": "VALIDATION_COMPLETE", "worker_loss": True,
                       "followup": "Final question replaces the acknowledgement. " + final})
    expected = {"net_cents": total, "rows": 2}
    return {"family": case, "variant": variant, "files": files, "target": "records.csv", "expected": expected,
            "goal": "Read rules.md and check_batch.py; do not inspect records.csv or run its check during preparation. "
                    "Checkpoint the rule and evidence using working notes when available, otherwise normal PTC artifacts. "
                    "Print LEARNING_COMPLETE, then await the fresh acknowledgement and idle-worker-loss context cut. "
                    "Only answer.json may change. Never modify inputs or the check, and do not inspect oracle code.",
            "followup": stages[-1]["followup"], "expect_abstention": failed,
            "validation_control": "failed" if failed else "complete", "validation_command": command,
            "source_requirements": required, "permitted_paths": ["answer.json"],
            "answers": [{"path": "answer.json", "expected": expected, "required": required,
                         "checkpoint": len(stages) - 1, "validations": [command]}],
            "stages": stages, "initial_ranges": {"records.csv": (1, 0)},
            "final_source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in files.items()},
            "max_model_calls": 24, "input_budget": 350_000}


def _revision_or_conflict_fixture(case: str, family: str, variant: int) -> dict[str, Any]:
    revision: dict[str, str] = {}
    initial_ranges: dict[str, tuple[int, int]] = {}
    initial_paths: list[str]
    final_paths: list[str]
    if family == "changed":
        files = {
            "rates.csv": f"zone,cents_per_unit\neast,{37 + variant * 7}\nwest,{61 + variant * 9}\n",
            "shipment.toml": f'zone = "east"\nunits = {6 + variant}\ncredit_cents = {17 + variant}\nwaive_base = false\n',
            "pricing.toml": f"base_cents = {110 + variant * 13}\n",
            "rules.md": "Charge = units times the zone rate plus base_cents unless waive_base is true, minus credit_cents. Zero credit is explicit, not an inherited prior credit.\n",
        }
        revision = {"shipment.toml": f'zone = "west"\nunits = {9 + variant}\ncredit_cents = 0\nwaive_base = true\n'}
        expected = {"charge_cents": (61 + variant * 9) * (9 + variant), "zone": "west"}
        acquisition = "Preserve the pricing relationship, rates, current shipment and its derived charge, with source dependencies."
        intermediate = (
            "Apply this authorized revision to shipment.toml using a current-version guard. Exact UTF-8 bytes as a JSON string: "
            + json.dumps(revision["shipment.toml"]) + ". Read its completed new version; correct shipment-dependent findings, "
            "including the explicit zero and true values. Retain the unchanged rates and pricing rule without rereading them merely to confirm. "
            "Checkpoint the revision without writing answer.json, then print REVISION_COMPLETE and await the next acknowledgement.")
        question = "Compute the current shipment's charge_cents (integer) and zone (string), using the revised shipment, not the historical snapshot."
        target = "shipment.toml"
        initial_paths = sorted(files)
        final_paths = sorted(files)
    else:
        # Two evidence-backed but incompatible candidates. Neither recency nor
        # enumeration order supplies authority; the delayed receipt does.
        selected = "amber" if variant == 1 else "violet"
        candidates = {
            "amber": {"limit": 23 + variant * 11, "enabled": False},
            "violet": {"limit": 71 + variant * 13, "enabled": True},
        }
        files = {f"claims/{name}.json": json.dumps(value, sort_keys=True) + "\n" for name, value in candidates.items()}
        hashes = {name: hashlib.sha256(files[f"claims/{name}.json"].encode()).hexdigest() for name in candidates}
        rejected = next(name for name in candidates if name != selected)
        receipts = [
            {"sequence": 90 + variant, "state": "pending", "claim": rejected, "sha256": hashes[rejected]},
            {"sequence": 12 + variant, "state": "completed", "claim": selected, "sha256": hashes[selected]},
            {"sequence": 70 + variant, "state": "failed", "claim": rejected, "sha256": hashes[rejected]},
        ]
        if variant == 2:
            receipts.append({"sequence": 80 + variant, "state": "completed", "claim": rejected, "sha256": "0" * 64})
            receipts.reverse()
        files["activation.json"] = json.dumps(receipts, indent=2) + "\n"
        files["rules.md"] = (
            "Candidate documents disagree about the same service. Neither filename nor row order implies authority. "
            "Use the greatest-sequence completed activation whose sha256 matches its candidate bytes. Pending and failed "
            "activations do not establish the live value. Return that candidate's fields and the completed sequence.\n")
        expected = {**candidates[selected], "sequence": 12 + variant}
        acquisition = (
            "Capture both competing claims and the rule without choosing a winner. Preserve their disagreement and evidence, "
            "using explicit conflicts_with links when working-note entries are available. Do not inspect activation.json during preparation.")
        intermediate = (
            "Now read activation.json with agent.fs.read. Resolve the competing claims from completed, hash-matching activation evidence; "
            "a higher pending/failed sequence cannot supersede it. Preserve a corrected evidence-backed checkpoint, using explicit "
            "supersession of the conflicting entries when available. Do not write answer.json yet. Print REVISION_COMPLETE, "
            "then await the next acknowledgement.")
        question = "Return the live service's limit (integer), enabled (boolean), and completed activation sequence (integer)."
        target = "activation.json"
        initial_ranges[target] = (1, 0)  # No initial source range is authorized for this delayed receipt.
        initial_paths = sorted(p for p in files if p != target)
        final_paths = sorted(("rules.md", target, f"claims/{selected}.json"))
    final_files = {**files, **revision}

    def requirements(contents: dict[str, str], paths: list[str]) -> list[dict[str, Any]]:
        return [ReadEvidence(path=p, sha256=hashlib.sha256(contents[p].encode()).hexdigest(), offset=1,
                             returned_lines=len(contents[p].splitlines())).model_dump() for p in paths]

    final = requirements(final_files, final_paths)
    followup = (
        "Final question replaces the acknowledgement request. Write a JSON object at workspace-relative answer.json "
        f"with exactly these top-level keys: {', '.join(expected)}. {question} "
        "Reuse applicable completed evidence; recover only what is needed. Then request independent verification.")
    stages = [
        {"learning_requirements": requirements(files, initial_paths), "checkpoint_marker": "LEARNING_COMPLETE",
         "worker_loss": True, "followup": intermediate},
        {"learning_requirements": requirements(final_files, sorted(final_files)), "checkpoint_marker": "REVISION_COMPLETE",
         "worker_loss": True, "required_changes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in revision.items()},
         "followup": followup},
    ]
    goal = (
        f"Read these preparation sources with agent.fs.read: {', '.join(initial_paths)}. {acquisition} "
        "Checkpoint useful findings in working notes when available, otherwise normal PTC artifacts. Do not write answer.json yet. "
        "Print LEARNING_COMPLETE after preparation. Each new acknowledgement precedes a context cut and idle-worker loss; "
        "durable evidence remains available. Only answer.json and subsequently authorized source revisions may change. "
        "Do not inspect oracle code. Completion is independently source- and value-verified.")
    return {"family": case, "variant": variant, "files": files, "target": target, "goal": goal,
            "followup": followup, "expected": expected, "expect_abstention": False,
            "answers": [{"path": "answer.json", "expected": expected, "checkpoint": 1, "required": final}],
            "stages": stages, "source_requirements": final, "initial_ranges": initial_ranges,
            "revision_files": revision, "permitted_paths": ["answer.json", *revision],
            "final_source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in final_files.items()},
            "max_model_calls": 24, "input_budget": 350_000}
