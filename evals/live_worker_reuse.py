"""Live-worker reuse fixtures: no notes, context cuts, worker loss, or prior recall."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from evals.learned_continuity import LearnedContinuation
from evals.qualification_continuity import qualification_fixture
from evals.repeated_continuity import repeated_fixture
from harness.core.config import parse_harness_composition

LIVE_WORKER_CASES = (
    "live_worker_repository",
    "live_worker_config",
    "live_worker_reconcile",
    "live_worker_missing_range",
    "live_worker_holdout_routes",
    "live_worker_holdout_changed",
    "live_worker_holdout_validation",
    "live_worker_holdout_missing",
)
LIVE_WORKER_ARMS = ("baseline", "on_demand", "locator")


def _source_case(case: str) -> str:
    if case not in LIVE_WORKER_CASES:
        raise ValueError("unknown live-worker fixture")
    holdout = {
        "live_worker_holdout_routes": "qualification_routes_2",
        "live_worker_holdout_changed": "qualification_changed_2",
        "live_worker_holdout_validation": "qualification_validation_1",
        "live_worker_holdout_missing": "qualification_partial_2",
    }
    if case in holdout:
        return holdout[case]
    return ("qualification_partial_1" if case == "live_worker_missing_range"
            else "reuse_" + case.removeprefix("live_worker_") + "_3")


def live_worker_fixture(case: str) -> dict:
    source = _source_case(case)
    fixture = deepcopy(qualification_fixture(source) if source.startswith("qualification_") else repeated_fixture(source))
    fixture["family"] = case
    fixture["checkpoint_mode"] = "live_worker"
    fixture["stages"] = [{**stage, "worker_loss": False, "reuse_checkpoint": False}
                         for stage in fixture["stages"]]
    for stage in fixture["stages"]:
        stage["followup"] = stage["followup"].replace(
            "worker-loss context cut", "acknowledgement in the same live worker"
        ).replace("context cut", "same-worker boundary")
    prefix = fixture["goal"].split("Preserve useful public findings", 1)[0].split(
        "Checkpoint learned evidence", 1)[0]
    fixture["goal"] = (
        prefix + "Retain completed source results or text under meaningful Python names; do not write working notes. "
        "Do not create answer files before their question. Print LEARNING_COMPLETE after acquisition. "
        "Each question follows a fresh acknowledgement in the same live Python worker, without a context cut or worker stop. "
        "Reuse applicable retained values instead of reacquiring unchanged source content. Acquire a missing range or a newly changed "
        "source version when required. Answer files are independently source- and value-verified. Only requested answer paths and "
        "explicitly instructed source revisions may change. Do not inspect oracle code."
    )
    return fixture


class LiveWorkerContinuation(LearnedContinuation):
    def __init__(self, root: Path, case: str, arm: str):
        if arm not in LIVE_WORKER_ARMS:
            raise ValueError("unknown live-worker arm")
        super().__init__(root, _source_case(case), "no_recall")
        self.arm = arm
        self.fixture = live_worker_fixture(case)
        self.task_id = f"{case}-{arm}"
        payload = self.composition.model_dump(mode="json")
        if arm != "on_demand":
            payload["harness"]["config"]["notebook_ptc"]["emit_state_updates"] = arm == "locator"
        payload["harness"]["config"]["memory"].update(
            working_notes=False, prior_runs=False, context_programs={"mode": "off"})
        self.composition = parse_harness_composition(payload)
