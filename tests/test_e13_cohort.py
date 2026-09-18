import hashlib
import json
from collections import Counter
from pathlib import Path


def test_e13_cohort_pins_six_plus_six_frozen_deep_swe_tasks() -> None:
    source = json.loads(Path("tests/eval/manifests/evaluation-confirm-v1.json").read_text())
    cohort = json.loads(Path("tests/eval/cohorts/e13-deep-swe-1-1-v1.json").read_text())
    digest = hashlib.sha256(
        (json.dumps({k: v for k, v in cohort.items() if k != "cohort_sha256"}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    ).hexdigest()
    assert cohort["cohort_sha256"] == digest
    assert cohort["source_manifest_sha256"] == source["manifest_sha256"]
    ids = cohort["core_task_ids"] + cohort["additional_task_ids"]
    assert len(ids) == len(set(ids)) == 12
    assert len(cohort["core_task_ids"]) == len(cohort["additional_task_ids"]) == 6
    tasks = {task["task_id"]: task for task in source["tasks"]}
    assert all(tasks[task_id]["benchmark"] == "deep_swe" for task_id in ids)
    assert {task_id: tasks[task_id]["artifact_sha256"] for task_id in ids} == cohort["task_artifact_sha256"]


def test_e13_code_mode_experiment_freezes_diverse_twenty_task_panel() -> None:
    path = Path("tests/eval/experiments/e13-code-mode-20-v1.json")
    experiment = json.loads(path.read_text())
    digest = hashlib.sha256(
        (json.dumps({k: v for k, v in experiment.items() if k != "manifest_sha256"}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    ).hexdigest()
    assert experiment["manifest_sha256"] == digest
    tasks = experiment["panel"]["tasks"]
    assert len(tasks) == len({task["task_id"] for task in tasks}) == 20
    assert set(experiment["panel"]["excluded_prior_task_ids"]).isdisjoint({task["task_id"] for task in tasks})
    expected_distribution = {
        "difficulty": {"easy": 8, "hard": 5, "medium": 7},
        "language": {"go": 5, "javascript": 1, "python": 6, "rust": 1, "typescript": 7},
    }
    assert experiment["panel"]["target_distribution"] == expected_distribution
    assert dict(sorted(Counter(task["difficulty"] for task in tasks).items())) == expected_distribution["difficulty"]
    assert dict(sorted(Counter(task["language"] for task in tasks).items())) == expected_distribution["language"]
    source = json.loads(Path(experiment["source"]["manifest_path"]).read_text())
    source_tasks = {task["task_id"]: task for task in source["tasks"]}
    assert all(task["artifact_sha256"] == source_tasks[task["task_id"]]["artifact_sha256"] for task in tasks)
    assert experiment["planned_muse_cost"]["remaining_trials"]["total"] == 120
