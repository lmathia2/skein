#!/usr/bin/env python3
"""Build Skein's public benchmark manifests from materialized Harbor metadata."""

from __future__ import annotations

import argparse
import json
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.manifests import (
    BENCHMARKS,
    Benchmark,
    EvaluationManifest,
    manifest_sha256,
    seeded_rank,
)

SEED = "skein-fixed-intelligence-2026-v1"
DATASETS: dict[Benchmark, tuple[str, str, str]] = {
    "deep_swe": ("deep-swe-1-1", "datacurve", "1.1"),
    "terminal_bench": ("terminal-bench-2-1", "terminal-bench", "2.1"),
    "swe_atlas_qna": ("swe-atlas-qna", "scale-ai", "1.0"),
}
DEEP_QUOTAS = {
    "ablation": {"typescript": 2, "go": 2, "python": 2, "javascript": 1, "rust": 1},
    "pilot": {"typescript": 6, "go": 5, "python": 5, "javascript": 1, "rust": 1},
    "confirm": {"typescript": 15, "go": 13, "python": 13, "javascript": 2, "rust": 2},
}
ATLAS_QUOTAS = {
    "ablation": {
        "Architecture & system design": 1,
        "Root-cause analysis": 1,
        "Code Onboarding": 1,
        "Security": 1,
        "API & library usage / integration": 1,
    },
    "pilot": {
        "Architecture & system design": 4,
        "Root-cause analysis": 4,
        "Code Onboarding": 2,
        "Security": 1,
        "API & library usage / integration": 1,
    },
    "confirm": {
        "Architecture & system design": 11,
        "Root-cause analysis": 9,
        "Code Onboarding": 7,
        "Security": 2,
        "API & library usage / integration": 1,
    },
}
TERMINAL_COUNTS = {"ablation": 5, "pilot": 12, "confirm": 30}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--deep-trials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _pass_rates(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    passed: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    peak: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row["source"] != "deep-swe" or row["eval_scope"] != "full" or not row["included_in_score"]:
            continue
        task_id = row["task_name"]
        totals[task_id] += 1
        passed[task_id] += bool(row["passed"])
        if row["peak_context_tokens"] is not None:
            peak[task_id].append(int(row["peak_context_tokens"]))
    rates = {task_id: passed[task_id] / count for task_id, count in totals.items()}
    medians = {
        task_id: float(sorted(values)[len(values) // 2])
        for task_id, values in peak.items()
        if values
    }
    return rates, medians


def _difficulty(pass_rate: float | None) -> str | None:
    if pass_rate is None:
        return None
    if pass_rate < 1 / 3:
        return "hard"
    if pass_rate < 2 / 3:
        return "medium"
    return "easy"


def _digest(cache_root: Path, org: str, task_id: str) -> str:
    candidates = sorted(path.name for path in (cache_root / org / task_id).iterdir() if path.is_dir())
    if len(candidates) != 1 or len(candidates[0]) != 64:
        raise ValueError(f"expected one cached digest for {org}/{task_id}, got {candidates}")
    return candidates[0]


def _runtime_band(seconds: int) -> str:
    if seconds <= 900:
        return "short"
    if seconds <= 1800:
        return "medium"
    return "long"


def load_inventory(
    datasets_root: Path,
    cache_root: Path,
    deep_trials: Path,
) -> dict[Benchmark, list[dict[str, Any]]]:
    pass_rates, peak_context = _pass_rates(deep_trials)
    inventory: dict[Benchmark, list[dict[str, Any]]] = {benchmark: [] for benchmark in BENCHMARKS}
    verifier_contract = {
        "deep_swe": "binary_behavioral",
        "terminal_bench": "binary_terminal_state",
        "swe_atlas_qna": "all_rubric_items_and_clean_source",
    }
    for benchmark, (directory, org, _) in DATASETS.items():
        for path in sorted((datasets_root / directory).glob("*/task.toml")):
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            metadata = raw.get("metadata", {})
            environment = raw["environment"]
            task_id = str(metadata.get("task_id") or path.parent.name)
            language = metadata.get("language")
            if language == "ts":
                language = "typescript"
            runtime = int(raw["agent"]["timeout_sec"])
            rate = pass_rates.get(task_id) if benchmark == "deep_swe" else None
            repository = metadata.get("repository") or metadata.get("repository_url")
            inventory[benchmark].append(
                {
                    "benchmark": benchmark,
                    "task_id": task_id,
                    "harbor_task": f"{org}/{task_id}",
                    "artifact_sha256": _digest(cache_root, org, task_id),
                    "repository": repository,
                    "language": language,
                    "category": metadata.get("category"),
                    "difficulty": metadata.get("difficulty") or _difficulty(rate),
                    "published_pass_rate": rate,
                    "expected_runtime_seconds": runtime,
                    "internet_policy": (
                        "allowed"
                        if environment.get("allow_internet", environment.get("network_mode") != "no-network")
                        else "disabled"
                    ),
                    "verifier_contract": verifier_contract[benchmark],
                    "verifier_isolated": True,
                    "resources": {
                        "cpus": environment["cpus"],
                        "memory_mb": environment["memory_mb"],
                        "storage_mb": environment["storage_mb"],
                        "gpus": environment.get("gpus", 0),
                    },
                    "selection_tags": [],
                    "selection_rank": seeded_rank(SEED, benchmark, task_id),
                    "_peak_context": peak_context.get(task_id, 0),
                    "_runtime_band": _runtime_band(runtime),
                }
            )
    return inventory


def _diverse(candidates: list[dict[str, Any]], count: int, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    usage = {field: Counter() for field in fields}
    remaining = list(candidates)
    while len(selected) < count:
        if not remaining:
            raise ValueError(f"cannot select {count} diverse tasks")
        best = min(
            remaining,
            key=lambda task: (
                *(usage[field][str(task.get(field))] for field in fields),
                task["selection_rank"],
            ),
        )
        remaining.remove(best)
        selected.append(best)
        for field in fields:
            usage[field][str(best.get(field))] += 1
    return selected


def _deep(tasks: list[dict[str, Any]], quotas: dict[str, int]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    repositories: Counter[str] = Counter()
    difficulties: Counter[str] = Counter()
    for language, count in quotas.items():
        candidates = [task for task in tasks if task["language"] == language]
        for _ in range(count):
            best = min(
                candidates,
                key=lambda task: (
                    repositories[str(task["repository"])],
                    difficulties[str(task["difficulty"])],
                    task["selection_rank"],
                ),
            )
            candidates.remove(best)
            selected.append(best)
            repositories[str(best["repository"])] += 1
            difficulties[str(best["difficulty"])] += 1
    return selected


def _atlas(tasks: list[dict[str, Any]], quotas: dict[str, int]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    languages: Counter[str] = Counter()
    repositories: Counter[str] = Counter()
    for category, count in quotas.items():
        candidates = [task for task in tasks if task["category"] == category]
        for _ in range(count):
            best = min(
                candidates,
                key=lambda task: (
                    repositories[str(task["repository"])],
                    languages[str(task["language"])],
                    task["selection_rank"],
                ),
            )
            candidates.remove(best)
            selected.append(best)
            repositories[str(best["repository"])] += 1
            languages[str(best["language"])] += 1
    return selected


def _public(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if not key.startswith("_")}


def _manifest(name: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "skein-evaluation-manifest-v1",
        "name": f"evaluation-{name}-v1",
        "seed": SEED,
        "frozen": True,
        "sources": {
            "deep_swe": {
                "harbor_dataset": "datacurve/deep-swe-1-1",
                "version": "1.1",
                "expected_tasks": 113,
                "metadata_url": "https://deepswe.datacurve.ai/artifacts/v1.1/tasks.json",
            },
            "terminal_bench": {
                "harbor_dataset": "terminal-bench/terminal-bench-2-1",
                "version": "2.1",
                "expected_tasks": 89,
                "metadata_url": None,
            },
            "swe_atlas_qna": {
                "harbor_dataset": "scale-ai/swe-atlas-qna",
                "version": "1.0",
                "expected_tasks": 124,
                "metadata_url": None,
            },
        },
        "tasks": sorted((_public(task) for task in tasks), key=lambda task: (task["benchmark"], task["task_id"])),
        "exclusions": [],
        "score": {
            "official_reward": "binary",
            "task_attempt_aggregation": "mean",
            "benchmark_aggregation": "task_mean",
            "composite_aggregation": "equal_weight_mean",
            "benchmark_weights": {benchmark: 1 / 3 for benchmark in BENCHMARKS},
        },
    }
    payload["manifest_sha256"] = manifest_sha256(payload)
    EvaluationManifest.model_validate(payload)
    return payload


def build(inventory: dict[Benchmark, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    available = {benchmark: list(tasks) for benchmark, tasks in inventory.items()}
    smoke_deep = _diverse(inventory["deep_swe"], 2, ("language", "repository", "difficulty"))
    smoke_terminal = sorted(
        inventory["terminal_bench"], key=lambda task: (task["expected_runtime_seconds"], task["selection_rank"])
    )[:2]
    smoke_atlas = _diverse(inventory["swe_atlas_qna"], 2, ("language", "repository", "category"))
    result = {"smoke": _manifest("smoke", smoke_deep + smoke_terminal + smoke_atlas)}

    for split in ("ablation", "pilot", "confirm"):
        deep = _deep(available["deep_swe"], DEEP_QUOTAS[split])
        if split == "ablation":
            for task in sorted(deep, key=lambda item: (-item["_peak_context"], item["selection_rank"]))[:2]:
                task["selection_tags"] = ["compaction_pressure"]
        terminal = _diverse(
            available["terminal_bench"],
            TERMINAL_COUNTS[split],
            ("category", "difficulty", "_runtime_band", "resources"),
        )
        atlas = _atlas(available["swe_atlas_qna"], ATLAS_QUOTAS[split])
        chosen = deep + terminal + atlas
        result[split] = _manifest(split, chosen)
        for benchmark in BENCHMARKS:
            used = {task["task_id"] for task in chosen if task["benchmark"] == benchmark}
            available[benchmark] = [task for task in available[benchmark] if task["task_id"] not in used]
    return result


def main() -> None:
    args = _args()
    inventory = load_inventory(args.datasets_root, args.cache_root, args.deep_trials)
    if {benchmark: len(tasks) for benchmark, tasks in inventory.items()} != {
        "deep_swe": 113,
        "terminal_bench": 89,
        "swe_atlas_qna": 124,
    }:
        raise ValueError("source task counts do not match the frozen versions")
    manifests = build(inventory)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, manifest in manifests.items():
        path = args.output / f"evaluation-{name}-v1.json"
        path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
