"""Deterministic fixed-intelligence experiment planning and paired analysis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .manifests import (
    BENCHMARKS,
    Benchmark,
    BenchmarkTask,
    EvaluationManifest,
    load_evaluation_manifest,
)

EXPERIMENT_SCHEMA_VERSION = "skein-fixed-intelligence-experiment-v1"
MATRIX_SCHEMA_VERSION = "skein-evaluation-matrix-v1"


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class FixedModelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai_codex"] = "openai_codex"
    model: Literal["gpt-5.6-luna"] = "gpt-5.6-luna"
    reasoning: Literal["max"] = "max"
    auth_mode: Literal["chatgpt_subscription"] = "chatgpt_subscription"
    authorization: Literal["pending", "approved"] = "pending"
    account_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    client_version: str | None = None
    model_snapshot: str | None = None
    max_iterations: int = Field(default=24, ge=1)
    max_task_input_tokens: int = Field(default=2_000_000, ge=8_000)
    wall_time_seconds: int = Field(default=1_800, gt=0)
    concurrency: Literal[1] = 1
    stable_local_host: Literal[True] = True
    browser_bridge: Literal[False] = False
    account_or_network_rotation: Literal[False] = False
    usage_limit_bypass: Literal[False] = False
    distillation: Literal[False] = False

    @property
    def contract_sha256(self) -> str:
        return _hash(self.model_dump(mode="json"))

    @property
    def blockers(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.authorization != "approved":
            missing.append("subscription authorization is pending")
        if self.account_fingerprint is None:
            missing.append("redacted account/workspace fingerprint is not frozen")
        if self.client_version is None:
            missing.append("Codex client version is not frozen")
        if self.model_snapshot is None:
            missing.append("account-enabled model snapshot is not frozen")
        return tuple(missing)


class HarnessCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    adapter: str = Field(min_length=1)
    revision: str = Field(pattern=r"^(PENDING|[0-9a-f]{40})$")
    config_path: str | None = None
    config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    behavior_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    agent_kwargs: dict[str, str | int] = Field(default_factory=dict)

    @property
    def blockers(self) -> tuple[str, ...]:
        return (f"{self.id} revision is not frozen",) if self.revision == "PENDING" else ()


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-fixed-intelligence-experiment-v1"] = EXPERIMENT_SCHEMA_VERSION
    name: str = Field(pattern=r"^evaluation-(ablation|pilot|confirm)-v[0-9]+$")
    status: Literal["planning", "frozen"] = "planning"
    manifest_path: Path
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: str = Field(min_length=8)
    attempts: Literal[1, 2]
    concurrency: Literal[1] = 1
    model: FixedModelContract
    candidates: tuple[HarnessCandidate, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_phase(self) -> ExperimentSpec:
        expected_attempts = 2 if "confirm" in self.name else 1
        if self.attempts != expected_attempts:
            raise ValueError(f"{self.name} requires {expected_attempts} attempt(s)")
        if len({candidate.id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        return self

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers = list(self.model.blockers)
        for candidate in self.candidates:
            blockers.extend(candidate.blockers)
        if self.status != "frozen":
            blockers.append("experiment is not frozen")
        return tuple(blockers)


class TrialAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    order: int = Field(ge=1)
    block: int = Field(ge=1)
    benchmark: Benchmark
    task_id: str
    task_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    harbor_task: str
    attempt: int = Field(ge=1)
    candidate_id: str
    candidate_revision: str
    adapter: str
    agent_kwargs: dict[str, str | int]
    model_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-evaluation-matrix-v1"] = MATRIX_SCHEMA_VERSION
    experiment: str
    experiment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: FixedModelContract
    live_ready: bool
    blockers: tuple[str, ...]
    assignments: tuple[TrialAssignment, ...]


def load_experiment(path: Path) -> ExperimentSpec:
    return ExperimentSpec.model_validate_json(path.read_text(encoding="utf-8"))


def build_matrix(spec: ExperimentSpec, manifest: EvaluationManifest) -> ExperimentMatrix:
    if manifest.name != spec.name:
        raise ValueError(f"experiment {spec.name} cannot use {manifest.name}")
    if manifest.manifest_sha256 != spec.manifest_sha256:
        raise ValueError("experiment manifest hash is stale")
    model_hash = spec.model.contract_sha256
    spec_hash = _hash(spec.model_dump(mode="json"))
    blocks: list[tuple[str, int, BenchmarkTask]] = []
    for attempt in range(1, spec.attempts + 1):
        for task in manifest.tasks:
            rank = _hash([spec.seed, task.benchmark, task.task_id, attempt])
            blocks.append((rank, attempt, task))
    assignments: list[TrialAssignment] = []
    for block_index, (_, attempt, raw_task) in enumerate(sorted(blocks), start=1):
        task = raw_task
        candidates = sorted(
            spec.candidates,
            key=lambda candidate: _hash(
                [spec.seed, task.benchmark, task.task_id, attempt, candidate.id]
            ),
        )
        for candidate in candidates:
            identity = {
                "manifest": manifest.manifest_sha256,
                "benchmark": task.benchmark,
                "task": task.task_id,
                "artifact": task.artifact_sha256,
                "attempt": attempt,
                "candidate": candidate.id,
                "revision": candidate.revision,
                "model": model_hash,
            }
            assignments.append(
                TrialAssignment(
                    trial_key=_hash(identity),
                    order=len(assignments) + 1,
                    block=block_index,
                    benchmark=task.benchmark,
                    task_id=task.task_id,
                    task_artifact_sha256=task.artifact_sha256,
                    harbor_task=task.harbor_task,
                    attempt=attempt,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    adapter=candidate.adapter,
                    agent_kwargs=candidate.agent_kwargs,
                    model_contract_sha256=model_hash,
                )
            )
    blockers = spec.blockers
    return ExperimentMatrix(
        experiment=spec.name,
        experiment_sha256=spec_hash,
        manifest_sha256=manifest.manifest_sha256,
        model=spec.model,
        live_ready=not blockers,
        blockers=blockers,
        assignments=tuple(assignments),
    )


def build_matrix_from_file(path: Path) -> ExperimentMatrix:
    spec = load_experiment(path)
    manifest_path = spec.manifest_path
    if not manifest_path.is_absolute():
        manifest_path = (path.parent / manifest_path).resolve()
    return build_matrix(spec, load_evaluation_manifest(manifest_path))


TrialStatus = Literal[
    "pass",
    "task_failure",
    "agent_timeout",
    "provider_interruption",
    "subscription_interruption",
    "verifier_error",
    "infrastructure_error",
    "safety_failure",
]
INFRASTRUCTURE_STATUSES = {
    "provider_interruption",
    "subscription_interruption",
    "verifier_error",
    "infrastructure_error",
}


class TrialMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_wall_time_seconds: float | None = Field(default=None, ge=0)
    end_to_end_wall_time_seconds: float | None = Field(default=None, ge=0)
    api_equivalent_cost_usd: float | None = Field(default=None, ge=0)
    uncached_input_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    model_turns: int | None = Field(default=None, ge=0)
    steps: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    shell_calls: int | None = Field(default=None, ge=0)
    verification_runs: int | None = Field(default=None, ge=0)
    changed_files: int | None = Field(default=None, ge=0)
    compactions: int | None = Field(default=None, ge=0)
    duplicate_actions: int | None = Field(default=None, ge=0)
    operator_interventions: int | None = Field(default=None, ge=0)
    subscription_limit_interruptions: int | None = Field(default=None, ge=0)


class TrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark: Benchmark
    task_id: str
    attempt: int = Field(ge=1)
    candidate_id: str
    status: TrialStatus
    official_reward: Literal[0, 1] | None
    metrics: TrialMetrics | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_reward(self) -> TrialRecord:
        if self.status in INFRASTRUCTURE_STATUSES:
            if self.official_reward is not None:
                raise ValueError("infrastructure interruptions do not receive capability rewards")
        elif self.official_reward is None:
            raise ValueError("capability outcomes require an official binary reward")
        return self


class _Timing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    started_at: datetime | None = None
    finished_at: datetime | None = None


class _AgentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n_input_tokens: int | None = None
    n_cache_tokens: int | None = None
    n_output_tokens: int | None = None
    cost_usd: float | None = None
    metadata: dict[str, object] | None = None


class _VerifierResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rewards: dict[str, float | int] | None = None


class _ExceptionInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exception_type: str
    exception_message: str


class _HarborResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_name: str
    agent_result: _AgentResult | None = None
    verifier_result: _VerifierResult | None = None
    exception_info: _ExceptionInfo | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    agent_execution: _Timing | None = None


class MetricSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    median: float
    p90: float


class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    assigned: int
    capability_trials: int
    infrastructure_interruptions: int
    clean_completion_rate: float
    intent_to_run_benchmark_scores: dict[Benchmark, float]
    intent_to_run_composite: float
    capability_benchmark_scores: dict[Benchmark, float]
    capability_composite: float
    metric_summaries: dict[str, MetricSummary]
    metric_coverage: dict[str, float]
    api_equivalent_cost_per_pass: float | None
    active_seconds_per_pass: float | None


class PairedComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_a: str
    candidate_b: str
    difference: float
    bootstrap_95: tuple[float, float]
    wins: int
    losses: int
    ties: int
    mcnemar_p: float


class ExperimentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[CandidateSummary, ...]
    comparisons: tuple[PairedComparison, ...]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _metric(values: list[float]) -> MetricSummary | None:
    return MetricSummary(median=median(values), p90=_percentile(values, 0.9)) if values else None


def _duration(timing: _Timing | None) -> float | None:
    if timing is None or timing.started_at is None or timing.finished_at is None:
        return None
    return max(0.0, (timing.finished_at - timing.started_at).total_seconds())


def _number(mapping: Mapping[str, object], name: str) -> int | None:
    value = mapping.get(name)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def trial_record_from_harbor_result(
    assignment: TrialAssignment,
    payload: str | bytes | Mapping[str, object],
) -> TrialRecord:
    """Normalize Harbor's result without trusting agent-authored score metadata."""

    result = (
        _HarborResult.model_validate_json(payload)
        if isinstance(payload, (str, bytes))
        else _HarborResult.model_validate(payload)
    )
    if result.task_name not in {assignment.task_id, assignment.harbor_task.split("/", 1)[-1]}:
        raise ValueError("Harbor result task does not match the assigned task")
    agent = result.agent_result or _AgentResult()
    metadata = agent.metadata or {}
    skein = metadata.get("skein")
    skein_result = skein if isinstance(skein, Mapping) else {}
    raw_metrics = skein_result.get("metrics")
    metrics = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    subscription = skein_result.get("subscription_limit")
    subscription_interrupted = bool(
        isinstance(subscription, Mapping) and subscription.get("interrupted") is True
    )
    error = skein_result.get("error")
    error_data = error if isinstance(error, Mapping) else {}
    error_code = str(error_data.get("code") or "")
    exception_message = (
        f"{result.exception_info.exception_type}: {result.exception_info.exception_message}"
        if result.exception_info is not None
        else ""
    )
    diagnostic = f"{error_code} {exception_message}".lower()

    reward_value = (
        result.verifier_result.rewards.get("reward")
        if result.verifier_result is not None and result.verifier_result.rewards is not None
        else None
    )
    reward: Literal[0, 1] | None = None
    if (
        isinstance(reward_value, (int, float))
        and not isinstance(reward_value, bool)
        and reward_value in (0, 1)
    ):
        reward = 0 if reward_value == 0 else 1
    if subscription_interrupted or any(
        marker in diagnostic for marker in ("429", "rate limit", "usage limit", "quota")
    ):
        status: TrialStatus = "subscription_interruption"
        reward = None
    elif any(marker in diagnostic for marker in ("workspace_violation", "secret leak")):
        status = "safety_failure"
        reward = 0
    elif "timeout" in diagnostic:
        status = "agent_timeout"
        reward = 0
    elif result.exception_info is not None:
        status = "infrastructure_error"
        reward = None
    elif reward is None:
        status = "verifier_error"
    else:
        status = "pass" if reward == 1 else "task_failure"

    total_input = _number(metrics, "input_tokens")
    cache_read = _number(metrics, "cache_read_tokens")
    total_input = total_input if total_input is not None else agent.n_input_tokens
    cache_read = cache_read if cache_read is not None else agent.n_cache_tokens
    changed = skein_result.get("changed_paths")
    trial_metrics = TrialMetrics(
        active_wall_time_seconds=_duration(result.agent_execution),
        end_to_end_wall_time_seconds=(
            max(0.0, (result.finished_at - result.started_at).total_seconds())
            if result.started_at is not None and result.finished_at is not None
            else None
        ),
        api_equivalent_cost_usd=(
            float(skein_result["api_equivalent_cost_usd"])
            if isinstance(skein_result.get("api_equivalent_cost_usd"), (int, float))
            else agent.cost_usd
        ),
        uncached_input_tokens=(
            max(total_input - (cache_read or 0), 0) if total_input is not None else None
        ),
        cache_read_tokens=cache_read,
        cache_write_tokens=_number(metrics, "cache_write_tokens"),
        output_tokens=_number(metrics, "output_tokens") or agent.n_output_tokens,
        reasoning_tokens=_number(metrics, "reasoning_tokens"),
        model_turns=_number(metrics, "model_calls"),
        steps=_number(metrics, "outcome_iterations"),
        tool_calls=_number(metrics, "tool_calls"),
        verification_runs=(1 if skein_result.get("verification") is not None else None),
        changed_files=(len(changed) if isinstance(changed, list) else None),
        compactions=_number(metrics, "outcome_compactions"),
        operator_interventions=_number(metrics, "outcome_user_interventions"),
        subscription_limit_interruptions=int(subscription_interrupted),
    )
    return TrialRecord(
        trial_key=assignment.trial_key,
        benchmark=assignment.benchmark,
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        candidate_id=assignment.candidate_id,
        status=status,
        official_reward=reward,
        metrics=trial_metrics,
        error_code=error_code
        or (result.exception_info.exception_type if result.exception_info else None),
    )


def append_trial_record(path: Path, record: TrialRecord) -> bool:
    """Append one result exactly once; a conflicting retry must use a new trial key."""

    existing = load_trial_records(path) if path.exists() else ()
    prior = next((item for item in existing if item.trial_key == record.trial_key), None)
    if prior is not None:
        if prior != record:
            raise ValueError("trial key already has a different result")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(record.model_dump_json() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _scores(records: list[TrialRecord]) -> tuple[dict[Benchmark, float], float]:
    by_task: dict[tuple[Benchmark, str], list[int]] = defaultdict(list)
    for record in records:
        if record.official_reward is not None:
            by_task[(record.benchmark, record.task_id)].append(record.official_reward)
    scores: dict[Benchmark, float] = {}
    for benchmark in BENCHMARKS:
        tasks = [
            sum(values) / len(values) for (kind, _), values in by_task.items() if kind == benchmark
        ]
        if not tasks:
            raise ValueError(f"no scored {benchmark} tasks")
        scores[benchmark] = sum(tasks) / len(tasks)
    return scores, sum(scores.values()) / len(BENCHMARKS)


def _mcnemar(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2 * tail / (2**discordant))


def _paired_task_values(
    records: list[TrialRecord], candidate_a: str, candidate_b: str
) -> dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]]:
    values: dict[tuple[str, Benchmark, str], dict[int, int]] = defaultdict(dict)
    for record in records:
        if record.official_reward is not None:
            values[(record.candidate_id, record.benchmark, record.task_id)][record.attempt] = (
                record.official_reward
            )
    paired: dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]] = defaultdict(dict)
    for benchmark in BENCHMARKS:
        task_ids = {
            task_id
            for candidate, kind, task_id in values
            if candidate == candidate_a and kind == benchmark
        } & {
            task_id
            for candidate, kind, task_id in values
            if candidate == candidate_b and kind == benchmark
        }
        for task_id in task_ids:
            left = values[(candidate_a, benchmark, task_id)]
            right = values[(candidate_b, benchmark, task_id)]
            attempts = sorted(set(left) & set(right))
            if attempts:
                paired[benchmark][task_id] = tuple(
                    (left[attempt], right[attempt]) for attempt in attempts
                )
    return paired


def _bootstrap(
    paired: dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]],
    *,
    seed: str,
    samples: int,
) -> tuple[float, float]:
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    differences: list[float] = []
    for _ in range(samples):
        benchmark_differences: list[float] = []
        for benchmark in BENCHMARKS:
            tasks = sorted(paired[benchmark])
            if not tasks:
                raise ValueError(f"no paired {benchmark} tasks")
            sampled: list[float] = []
            for _ in tasks:
                task_id = rng.choice(tasks)
                attempts = paired[benchmark][task_id]
                resampled = [rng.choice(attempts) for _ in attempts]
                sampled.append(sum(left - right for left, right in resampled) / len(resampled))
            benchmark_differences.append(sum(sampled) / len(sampled))
        differences.append(sum(benchmark_differences) / len(BENCHMARKS))
    return (_percentile(differences, 0.025), _percentile(differences, 0.975))


def analyze_trials(
    matrix: ExperimentMatrix,
    records: tuple[TrialRecord, ...],
    *,
    bootstrap_samples: int = 10_000,
) -> ExperimentAnalysis:
    assignments = {assignment.trial_key: assignment for assignment in matrix.assignments}
    if len({record.trial_key for record in records}) != len(records):
        raise ValueError("duplicate trial record")
    unknown = {record.trial_key for record in records} - set(assignments)
    if unknown:
        raise ValueError(f"results contain unknown trial keys: {sorted(unknown)}")
    missing = set(assignments) - {record.trial_key for record in records}
    if missing:
        raise ValueError(f"results are incomplete: {len(missing)} assigned trials are missing")
    by_candidate: dict[str, list[TrialRecord]] = defaultdict(list)
    for record in records:
        assignment = assignments[record.trial_key]
        if (
            assignment.candidate_id != record.candidate_id
            or assignment.benchmark != record.benchmark
            or assignment.task_id != record.task_id
            or assignment.attempt != record.attempt
        ):
            raise ValueError(f"trial identity mismatch: {record.trial_key}")
        by_candidate[record.candidate_id].append(record)
    candidate_ids = sorted({assignment.candidate_id for assignment in matrix.assignments})
    summaries: list[CandidateSummary] = []
    for candidate_id in candidate_ids:
        candidate_records = by_candidate[candidate_id]
        assigned = sum(a.candidate_id == candidate_id for a in matrix.assignments)
        capability = [r for r in candidate_records if r.official_reward is not None]
        capability_scores, capability_composite = _scores(capability)
        intent_records = [
            record
            if record.official_reward is not None
            else record.model_copy(update={"official_reward": 0})
            for record in candidate_records
        ]
        intent_scores, intent_composite = _scores(intent_records)
        metrics = [r.metrics for r in candidate_records if r.metrics is not None]
        clean = sum(r.status in {"pass", "task_failure"} for r in candidate_records)
        passed = sum(r.official_reward == 1 for r in capability)
        metric_values: dict[str, list[float]] = defaultdict(list)
        for item in metrics:
            for name, value in item.model_dump().items():
                if value is not None:
                    metric_values[name].append(float(value))
        complete_cost = len(metric_values["api_equivalent_cost_usd"]) == assigned
        complete_active_time = len(metric_values["active_wall_time_seconds"]) == assigned
        summaries.append(
            CandidateSummary(
                candidate_id=candidate_id,
                assigned=assigned,
                capability_trials=len(capability),
                infrastructure_interruptions=sum(
                    r.status in INFRASTRUCTURE_STATUSES for r in candidate_records
                ),
                clean_completion_rate=clean / assigned,
                intent_to_run_benchmark_scores=intent_scores,
                intent_to_run_composite=intent_composite,
                capability_benchmark_scores=capability_scores,
                capability_composite=capability_composite,
                metric_summaries={
                    name: summary
                    for name, values in sorted(metric_values.items())
                    if (summary := _metric(values)) is not None
                },
                metric_coverage={
                    name: len(metric_values[name]) / assigned
                    for name in sorted(TrialMetrics.model_fields)
                },
                api_equivalent_cost_per_pass=(
                    sum(metric_values["api_equivalent_cost_usd"]) / passed
                    if passed and complete_cost
                    else None
                ),
                active_seconds_per_pass=(
                    sum(metric_values["active_wall_time_seconds"]) / passed
                    if passed and complete_active_time
                    else None
                ),
            )
        )
    comparisons: list[PairedComparison] = []
    summary_by_id = {summary.candidate_id: summary for summary in summaries}
    for candidate_a, candidate_b in combinations(candidate_ids, 2):
        paired = _paired_task_values(list(records), candidate_a, candidate_b)
        wins = losses = ties = 0
        for tasks in paired.values():
            for attempts in tasks.values():
                left_mean = sum(left for left, _ in attempts) / len(attempts)
                right_mean = sum(right for _, right in attempts) / len(attempts)
                if left_mean > right_mean:
                    wins += 1
                elif left_mean < right_mean:
                    losses += 1
                else:
                    ties += 1
        comparisons.append(
            PairedComparison(
                candidate_a=candidate_a,
                candidate_b=candidate_b,
                difference=(
                    summary_by_id[candidate_a].capability_composite
                    - summary_by_id[candidate_b].capability_composite
                ),
                bootstrap_95=_bootstrap(
                    paired,
                    seed=f"{matrix.experiment_sha256}:{candidate_a}:{candidate_b}",
                    samples=bootstrap_samples,
                ),
                wins=wins,
                losses=losses,
                ties=ties,
                mcnemar_p=_mcnemar(wins, losses),
            )
        )
    return ExperimentAnalysis(candidates=tuple(summaries), comparisons=tuple(comparisons))


def load_trial_records(path: Path) -> tuple[TrialRecord, ...]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                records.append(TrialRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(f"invalid trial record on line {line_number}: {error}") from error
    return tuple(records)


def next_assignment(
    matrix: ExperimentMatrix, records: tuple[TrialRecord, ...]
) -> TrialAssignment | None:
    completed = {record.trial_key for record in records}
    return next(
        (assignment for assignment in matrix.assignments if assignment.trial_key not in completed),
        None,
    )


def harbor_task_path(
    harbor_task: str, artifact_sha256: str, cache_root: Path | None = None
) -> Path:
    """Resolve a manifest digest to Harbor's content-addressed task cache."""

    org, name = harbor_task.split("/", 1)
    root = cache_root or Path.home() / ".cache/harbor/tasks/packages"
    return root / org / name / artifact_sha256


def harbor_command(assignment: TrialAssignment, model: FixedModelContract) -> tuple[str, ...]:
    """Return argv for one trial; credentials stay in the host's normal Skein state."""

    command = [
        "harbor",
        "run",
        "--path",
        str(harbor_task_path(assignment.harbor_task, assignment.task_artifact_sha256)),
        "--agent",
        assignment.adapter,
        "--model",
        model.model,
        "--agent-kwarg",
        f"provider={model.provider}",
        "--agent-kwarg",
        f"reasoning={model.reasoning}",
        "--n-attempts",
        "1",
        "--n-concurrent",
        "1",
        "--job-name",
        assignment.trial_key,
        "--yes",
    ]
    for name, value in sorted(assignment.agent_kwargs.items()):
        command.extend(("--agent-kwarg", f"{name}={value}"))
    return tuple(command)
