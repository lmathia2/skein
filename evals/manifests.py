"""Immutable public-benchmark manifests and their deterministic score contract."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Benchmark = Literal["deep_swe", "terminal_bench", "swe_atlas_qna"]
BENCHMARKS: tuple[Benchmark, ...] = ("deep_swe", "terminal_bench", "swe_atlas_qna")
MANIFEST_SCHEMA_VERSION = "skein-evaluation-manifest-v1"


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def seeded_rank(seed: str, benchmark: Benchmark, task_id: str) -> str:
    """Return the stable, outcome-independent ordering key used for selection."""

    return hashlib.sha256(f"{seed}\0{benchmark}\0{task_id}".encode()).hexdigest()


class BenchmarkSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    harbor_dataset: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")
    version: str = Field(min_length=1)
    expected_tasks: int = Field(gt=0)
    metadata_url: str | None = None


class ResourceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpus: int = Field(gt=0)
    memory_mb: int = Field(gt=0)
    storage_mb: int = Field(gt=0)
    gpus: int = Field(ge=0)


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark: Benchmark
    task_id: str = Field(min_length=1)
    harbor_task: str = Field(pattern=r"^[a-z0-9-]+/[a-zA-Z0-9._-]+$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: str | None = None
    language: str | None = None
    category: str | None = None
    difficulty: str | None = None
    published_pass_rate: float | None = Field(default=None, ge=0, le=1)
    expected_runtime_seconds: int = Field(gt=0)
    internet_policy: Literal["disabled", "allowed"]
    verifier_contract: Literal[
        "binary_behavioral",
        "binary_terminal_state",
        "all_rubric_items_and_clean_source",
    ]
    verifier_isolated: bool
    resources: ResourceProfile
    selection_tags: tuple[str, ...] = ()
    selection_rank: str = Field(pattern=r"^[0-9a-f]{64}$")


class Exclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark: Benchmark
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=8)


class ScoreContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    official_reward: Literal["binary"] = "binary"
    task_attempt_aggregation: Literal["mean"] = "mean"
    benchmark_aggregation: Literal["task_mean"] = "task_mean"
    composite_aggregation: Literal["equal_weight_mean"] = "equal_weight_mean"
    benchmark_weights: dict[Benchmark, float] = Field(
        default_factory=lambda: {benchmark: 1 / 3 for benchmark in BENCHMARKS}
    )

    @model_validator(mode="after")
    def validate_weights(self) -> ScoreContract:
        if set(self.benchmark_weights) != set(BENCHMARKS):
            raise ValueError("score contract must include exactly the three benchmarks")
        if abs(sum(self.benchmark_weights.values()) - 1.0) > 1e-9:
            raise ValueError("benchmark weights must sum to one")
        if any(abs(weight - 1 / 3) > 1e-9 for weight in self.benchmark_weights.values()):
            raise ValueError("benchmark weights must be equal")
        return self


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-evaluation-manifest-v1"] = MANIFEST_SCHEMA_VERSION
    name: str = Field(pattern=r"^evaluation-(smoke|ablation|pilot|confirm)-v[0-9]+$")
    seed: str = Field(min_length=8)
    frozen: bool
    sources: dict[Benchmark, BenchmarkSource]
    tasks: tuple[BenchmarkTask, ...]
    exclusions: tuple[Exclusion, ...] = ()
    score: ScoreContract = Field(default_factory=ScoreContract)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_contract(self) -> EvaluationManifest:
        if set(self.sources) != set(BENCHMARKS):
            raise ValueError("manifest must pin exactly the three benchmark sources")
        identities = [(task.benchmark, task.task_id) for task in self.tasks]
        if len(identities) != len(set(identities)):
            raise ValueError("manifest contains a duplicate benchmark task")
        for task in self.tasks:
            if task.selection_rank != seeded_rank(self.seed, task.benchmark, task.task_id):
                raise ValueError(f"invalid selection rank for {task.benchmark}/{task.task_id}")
            if not task.verifier_isolated:
                raise ValueError(f"verifier must remain isolated for {task.task_id}")
        if self.manifest_sha256 != manifest_sha256(self):
            raise ValueError("manifest_sha256 does not match canonical manifest content")
        validate_quotas(self)
        return self


def manifest_sha256(manifest: EvaluationManifest | dict[str, object]) -> str:
    payload = (
        manifest.model_dump(mode="json")
        if isinstance(manifest, EvaluationManifest)
        else dict(manifest)
    )
    payload.pop("manifest_sha256", None)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


_EXPECTED_COUNTS = {
    "evaluation-smoke-v1": {"deep_swe": 2, "terminal_bench": 2, "swe_atlas_qna": 2},
    "evaluation-ablation-v1": {"deep_swe": 8, "terminal_bench": 5, "swe_atlas_qna": 5},
    "evaluation-pilot-v1": {"deep_swe": 18, "terminal_bench": 12, "swe_atlas_qna": 12},
    "evaluation-confirm-v1": {"deep_swe": 45, "terminal_bench": 30, "swe_atlas_qna": 30},
}

_DEEP_SWE_LANGUAGES = {
    "evaluation-pilot-v1": {"typescript": 6, "go": 5, "python": 5, "javascript": 1, "rust": 1},
    "evaluation-confirm-v1": {
        "typescript": 15,
        "go": 13,
        "python": 13,
        "javascript": 2,
        "rust": 2,
    },
}

_ATLAS_CATEGORIES = {
    "evaluation-pilot-v1": {
        "Architecture & system design": 4,
        "Root-cause analysis": 4,
        "Code Onboarding": 2,
        "Security": 1,
        "API & library usage / integration": 1,
    },
    "evaluation-confirm-v1": {
        "Architecture & system design": 11,
        "Root-cause analysis": 9,
        "Code Onboarding": 7,
        "Security": 2,
        "API & library usage / integration": 1,
    },
}


def validate_quotas(manifest: EvaluationManifest) -> None:
    expected = _EXPECTED_COUNTS.get(manifest.name)
    if expected is None:
        raise ValueError(f"unknown manifest contract: {manifest.name}")
    counts: dict[str, int] = defaultdict(int)
    for task in manifest.tasks:
        counts[task.benchmark] += 1
    if dict(counts) != expected:
        raise ValueError(f"benchmark counts do not match {expected}: {dict(counts)}")

    deep_languages = _DEEP_SWE_LANGUAGES.get(manifest.name)
    if deep_languages is not None:
        actual: dict[str, int] = defaultdict(int)
        for task in manifest.tasks:
            if task.benchmark == "deep_swe":
                actual[task.language or ""] += 1
        if dict(actual) != deep_languages:
            raise ValueError(f"DeepSWE language quota mismatch: {dict(actual)}")

    atlas_categories = _ATLAS_CATEGORIES.get(manifest.name)
    if atlas_categories is not None:
        actual = defaultdict(int)
        atlas = [task for task in manifest.tasks if task.benchmark == "swe_atlas_qna"]
        for task in atlas:
            actual[task.category or ""] += 1
        if dict(actual) != atlas_categories:
            raise ValueError(f"SWE-Atlas category quota mismatch: {dict(actual)}")
        if len({task.language for task in atlas}) != 4:
            raise ValueError("SWE-Atlas pilot/confirm must cover all four languages")
        minimum_repositories = 8 if manifest.name == "evaluation-pilot-v1" else 11
        if len({task.repository for task in atlas}) < minimum_repositories:
            raise ValueError("SWE-Atlas repository coverage quota is unmet")

    if manifest.name == "evaluation-ablation-v1":
        pressured = sum("compaction_pressure" in task.selection_tags for task in manifest.tasks)
        if pressured < 2:
            raise ValueError("ablation manifest needs at least two compaction-pressure tasks")


def load_evaluation_manifest(path: Path) -> EvaluationManifest:
    return EvaluationManifest.model_validate_json(path.read_text(encoding="utf-8"))


def validate_disjointness(manifests: tuple[EvaluationManifest, ...]) -> None:
    """Reject overlap among score-bearing ablation, pilot, and confirm splits."""

    seen: dict[tuple[Benchmark, str], str] = {}
    for manifest in manifests:
        if "smoke" in manifest.name:
            continue
        for task in manifest.tasks:
            identity = (task.benchmark, task.task_id)
            if identity in seen:
                raise ValueError(f"{identity} appears in {seen[identity]} and {manifest.name}")
            seen[identity] = manifest.name


class TrialReward(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark: Benchmark
    task_id: str
    attempt: int = Field(ge=1)
    reward: Literal[0, 1]


class CompositeScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_scores: dict[Benchmark, float]
    composite: float


def score_trials(trials: tuple[TrialReward, ...]) -> CompositeScore:
    by_task: dict[tuple[Benchmark, str], list[int]] = defaultdict(list)
    for trial in trials:
        by_task[(trial.benchmark, trial.task_id)].append(trial.reward)
    by_benchmark: dict[Benchmark, list[float]] = defaultdict(list)
    for (benchmark, _), rewards in by_task.items():
        by_benchmark[benchmark].append(sum(rewards) / len(rewards))
    missing = set(BENCHMARKS) - set(by_benchmark)
    if missing:
        raise ValueError(f"missing benchmark rewards: {sorted(missing)}")
    scores: dict[Benchmark, float] = {
        benchmark: sum(by_benchmark[benchmark]) / len(by_benchmark[benchmark])
        for benchmark in BENCHMARKS
    }
    return CompositeScore(
        benchmark_scores=scores,
        composite=sum(scores.values()) / len(BENCHMARKS),
    )
