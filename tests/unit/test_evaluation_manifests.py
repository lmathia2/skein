from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.manifests import (
    BENCHMARKS,
    TrialReward,
    load_evaluation_manifest,
    score_trials,
    validate_disjointness,
)

MANIFEST_ROOT = Path("tests/eval/manifests")


def test_checked_in_evaluation_manifests_are_frozen_and_disjoint() -> None:
    manifests = tuple(
        load_evaluation_manifest(path) for path in sorted(MANIFEST_ROOT.glob("*.json"))
    )

    assert [len(manifest.tasks) for manifest in manifests] == [18, 105, 42, 6]
    assert all(manifest.frozen for manifest in manifests)
    validate_disjointness(manifests)


def test_manifest_hash_rejects_task_substitution() -> None:
    path = MANIFEST_ROOT / "evaluation-smoke-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tasks"][0]["task_id"] = "post-freeze-substitution"

    with pytest.raises(ValueError, match=r"selection rank|manifest_sha256"):
        type(load_evaluation_manifest(path)).model_validate(payload)


def test_score_contract_equal_weights_task_normalized_benchmarks() -> None:
    trials = (
        *(
            TrialReward(
                benchmark=benchmark,
                task_id=f"{benchmark}-1",
                attempt=1,
                reward=reward,
            )
            for benchmark, reward in zip(BENCHMARKS, (1, 0, 1), strict=True)
        ),
        TrialReward(benchmark="deep_swe", task_id="deep_swe-1", attempt=2, reward=0),
    )

    result = score_trials(trials)

    assert result.benchmark_scores == {
        "deep_swe": 0.5,
        "terminal_bench": 0.0,
        "swe_atlas_qna": 1.0,
    }
    assert result.composite == 0.5
