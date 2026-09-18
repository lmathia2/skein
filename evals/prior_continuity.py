"""Two distinct verified tasks sharing owned catalog evidence; no seeded notes."""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.learned_continuity import LearnedContinuation
from evals.memory_audit import merged
from evals.prior_evidence import PriorRun
from evals.runner import _atomic_write
from harness.evidence.ledger import LedgerEvent
from harness.evidence.memory.models import ReadEvidence
from harness.evidence.state.receipts import ToolReceiptStore

COST_FIELDS = ("model_calls", "tool_cells", "input_tokens", "cached_input_tokens", "uncached_input_tokens",
               "output_tokens", "reasoning_tokens", "provider_cost_usd", "usage_missing_calls", "cost_missing_calls",
               "unaccounted_model_calls", "wire_attempts", "extra_wire_attempts")


def audit_cross_run_reads(producer: list[LedgerEvent], consumer: list[LedgerEvent]) -> dict[str, Any]:
    """Compare explicit source ranges across tasks without merging ledger clocks."""
    prior: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    reads = []
    for events, previous in ((producer, True), (consumer, False)):
        if len(events) > 100_000 or len({e.task_id for e in events}) != 1:
            raise ValueError("cross-run read audit requires bounded single-task streams")
        for event in events:
            p = event.payload
            if (event.kind not in {"capability.completed", "read.observed"}
                    or p.get("operation") != "fs.read" or p.get("status") != "ok"):
                continue
            if not p.get("read_evidence"):
                counts["unmapped_producer_reads" if previous else "unmapped_consumer_reads"] += 1
                continue
            read = ReadEvidence.model_validate(p["read_evidence"])
            key = read.path, read.sha256
            lo, hi = read.offset, read.offset + read.returned_lines
            if previous:
                prior[key] = merged([*prior[key], (lo, hi)])
                continue
            overlap = sum(max(0, min(hi, end) - max(lo, start)) for start, end in prior[key])
            counts["same_version_refetched_lines"] += overlap
            counts["same_version_reads"] += overlap > 0
            counts["one_line_identity_candidates"] += overlap == read.returned_lines == 1
            reads.append({"consumer_event_id": event.event_id, **read.model_dump(), "prior_overlap_lines": overlap})
    return {"version": "owned-cross-run-reads-v1", "producer_task": producer[0].task_id,
            "consumer_task": consumer[0].task_id, "counts": dict(counts), "reads": reads,
            "scope": "completed same-version captured interval overlap; not semantic consumption or automatic waste",
            "limitations": ["one-line identity checks are necessary, not avoidable rereads",
                            "shell and arbitrary artifact routes remain unmapped; inspect per-episode exposure too"]}


async def run_owned_prior(root: Path, case: str, arm: str, sandbox_image: str | None = None) -> dict[str, Any]:
    from evals.verified_continuity import run_verified_case

    if root.exists():
        raise ValueError("refusing to overwrite an owned-prior pair")
    started = time.monotonic()
    consumer = LearnedContinuation(root / "consumer", case, arm)
    if "producer" not in consumer.fixture:
        raise ValueError("owned prior pair requires a declared producer fixture")
    producer = LearnedContinuation(root / "producer", case, arm)
    producer.fixture = consumer.fixture["producer"]
    producer.task_id = "fixture-prior"
    producer.source_requirements = [ReadEvidence.model_validate(r) for r in producer.fixture["source_requirements"]]
    producer.workspace = consumer.workspace = root / "workspace"
    producer.command_image = consumer.command_image = sandbox_image

    async def episode(trial):
        try:
            async with asyncio.timeout(900):
                return await run_verified_case(trial)
        except Exception as exc:
            progress = trial.root / "progress.json"
            result = json.loads(progress.read_text()) if progress.exists() else {}
            result.update(family=trial.fixture["family"], arm=arm, accepted=False, passed=False,
                          terminal="wall_time_limit" if isinstance(exc, TimeoutError) else "harness_or_fixture_error",
                          error=type(exc).__name__)
            _atomic_write(trial.root / "result.json", json.dumps(result, indent=2))
            return result

    first = await episode(producer)
    eligible = (first.get("terminal") == "verified_completion" and first.get("first_verification_passed") is True
                and first.get("checkpoint_exercised") is True and not first.get("unresolved_execution")
                and "measurement_error" not in first
                and first.get("measurement", {}).get("answer_contracts", {}).get("all_submissions_supported") is True
                and not any(first.get(key) for key in ("unaccounted_model_calls", "usage_missing_calls", "cost_missing_calls", "extra_wire_attempts")))
    second: dict[str, Any] = {"family": case, "arm": arm, "accepted": False, "passed": False,
                              "terminal": "producer_not_qualified"}
    # Retain charged preparation even if admission or the consumer later fails.
    _atomic_write(root / "progress.json", json.dumps({**second, **{k: first.get(k, 0) for k in COST_FIELDS}}, indent=2))
    if eligible:
        producer_events = tuple(producer.ledger.read(producer.task_id))
        consumer.prior_root = producer.state
        consumer.prior_run = PriorRun(producer.bindings, producer_events,
                                     tuple(ToolReceiptStore(producer.state / "managed-tools.db").for_task(producer.task_id)))
        # Preserve the completed answer outside the next task's workspace. Its
        # recorded verdict and immutable read artifacts remain in the producer run.
        (producer.workspace / "answer.json").replace(producer.root / "completed-answer.json")
        second = await episode(consumer)
    result = {**second, "family": case, "arm": arm, "producer_qualified": eligible,
              "episodes": {"producer": first, "consumer": second},
              "episode_paths": {"producer": str(producer.root), "consumer": str(consumer.root)},
              **{k: first.get(k, 0) + second.get(k, 0) for k in COST_FIELDS},
              "max_model_calls": producer.fixture["max_model_calls"] + consumer.fixture["max_model_calls"],
              "input_budget": producer.fixture["input_budget"] + consumer.fixture["input_budget"],
              "wall_seconds": time.monotonic() - started}
    if eligible and hasattr(consumer, "ledger"):
        assert consumer.prior_run is not None
        try:
            result["cross_run_reads"] = audit_cross_run_reads(list(consumer.prior_run.events), consumer.ledger.read(consumer.task_id))
        except (ValueError, KeyError, OSError) as exc:
            result["measurement_error"] = str(exc)[:1000]
    _atomic_write(root / "result.json", json.dumps(result, indent=2))
    return result
