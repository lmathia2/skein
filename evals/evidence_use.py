"""Diagnostic of visible, recoverable, and missing evidence; not a promotion gate.

Reuses the production PTC/memory continuation driver. This tests first proposals,
not the outer workflow's verification/re-entry loop. No provider calls without --live.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from evals.continuity import INPUT_BUDGET, MAX_CALLS, MODEL, Continuation, run_case
from evals.runner import _atomic_write
from scripts.run_harbor_eval import dotenv_value

PLACEMENTS = ("visible", "recoverable", "unread")


class EvidenceContinuation(Continuation):
    def __init__(self, root: Path, placement: str):
        if placement not in PLACEMENTS:
            raise ValueError("unknown evidence placement")
        super().__init__(root, "partial", 1, "findings")
        self.placement = placement
        self.seed_read_limit = 10 if placement == "unread" else 24

    async def prepare(self) -> None:
        await super().prepare()
        await self.request()  # Fix the cut before the diagnostic intervention.
        if self.placement == "visible":
            # Expose already-captured source through the real PTC result path.
            # No new source read, invented summary, or hidden oracle information.
            await self.cell(
                f"print(sources[{self.fixture['target']!r}]['data']['text'])", seed=True)


async def campaign(output: Path, repetitions: int, concurrency: int) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)
    failed = asyncio.Event()

    async def bounded(repetition: int, placement: str) -> dict:
        async with semaphore:
            root = output / f"repeat-{repetition}-{placement}"
            if failed.is_set():
                result = {"passed": False, "terminal": "not_started_infrastructure_gate"}
            else:
                try:
                    async with asyncio.timeout(900):
                        result = await run_case(root, "partial", 1, "findings",
                                                trial=EvidenceContinuation(root, placement))
                except TimeoutError:
                    progress = root / "progress.json"
                    result = json.loads(progress.read_text()) if progress.exists() else {}
                    result.update(passed=False, terminal="wall_time_limit")
                if result["terminal"] in {
                    "provider_error", "harness_or_fixture_error", "wall_time_limit"
                } or "measurement_error" in result:
                    failed.set()
            result.update(repetition=repetition, placement=placement)
            _atomic_write(root / "result.json", json.dumps(result, indent=2))
            return result

    results = await asyncio.gather(*(bounded(repetition, placement)
        for repetition in range(repetitions) for placement in PLACEMENTS))
    _atomic_write(output / "results.json", json.dumps(results, indent=2))
    totals = {}
    for placement in PLACEMENTS:
        rows = [r for r in results if r["placement"] == placement]
        totals[placement] = {
            "correct_artifacts": sum(r["passed"] for r in rows),
            "correct_normal_completions": sum(r["passed"] and r["terminal"] == "fixture_completed" for r in rows),
            "planned": len(rows),
            **{key: sum(r.get(key, 0) for r in rows) for key in (
                "model_calls", "input_tokens", "uncached_input_tokens", "output_tokens",
                "provider_cost_usd", "cost_missing_calls", "unaccounted_model_calls")},
        }
    _atomic_write(output / "summary.json", json.dumps({"placements": totals,
        "deep_swe_gate": "hold", "limitations": [
            "One diagnostic problem repeated; not held-out reliability evidence",
            "Visible/recoverable capture 24 lines; unread captures 10",
            "Visible adds a completed PTC output after the cut",
            "No outer workflow verification or feedback; first proposals only",
        ]}, indent=2))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--concurrency", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("use a fresh campaign output directory")
    manifest = {
        "version": "evidence-placement-diagnostic-v1", "model": MODEL, "reasoning": "max",
        "placements": PLACEMENTS, "repetitions": args.repetitions, "concurrency": args.concurrency,
        "max_model_calls": MAX_CALLS, "input_budget": INPUT_BUDGET, "max_output_tokens": 8192,
        "wall_seconds_per_case": 900, "promotion": False,
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"])).hexdigest(),
        "driver_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (Path(__file__), Path(__file__).with_name("continuity.py"))},
    }
    _atomic_write(args.output / "manifest.json", json.dumps(manifest, indent=2))
    if not args.live:
        print(json.dumps({"manifest": str(args.output / "manifest.json"), "trials": args.repetitions * 3, "live": False}))
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_value(args.dotenv, "OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is unavailable")
    os.environ["OPENROUTER_API_KEY"] = key
    asyncio.run(campaign(args.output, args.repetitions, args.concurrency))


if __name__ == "__main__":
    main()
