"""Offline bounded-scan benchmark; never opens an existing application ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from harness.ledger import JsonlLedgerStore, LedgerEvent
from harness.memory import MemoryProgramRuntime, ViewRequest


def benchmark_scan(size: int, attempts: int = 5) -> dict[str, object]:
    if not 1 <= size <= 100000 or not 1 <= attempts <= 100:
        raise ValueError("size must be 1..100000 and attempts 1..100")
    with tempfile.TemporaryDirectory(prefix="skein-context-scan-") as temporary:
        path = Path(temporary) / "ledger.jsonl"
        now = datetime(2025, 1, 1, tzinfo=UTC)
        # Fixture construction is deliberately not an append-throughput benchmark.
        with path.open("w") as stream:
            for index in range(size):
                key = f"context:{index}"
                row = LedgerEvent(
                    event_id=hashlib.sha256(f"fixture\0{key}".encode()).hexdigest(),
                    task_id="fixture", sequence=index + 1, source="context", source_id=str(index),
                    kind="context.history", observed_at=now, recorded_at=now, idempotency_key=key,
                    payload={"role": "user", "parts": [{"text": f"verification result {index}"}]},
                )
                stream.write(row.model_dump_json() + "\n")
        runtime = MemoryProgramRuntime(JsonlLedgerStore(path), authorized_tasks=("fixture",))
        durations, statuses, exposed = [], [], []
        for _ in range(attempts):
            started = time.perf_counter()
            result = runtime.compute(ViewRequest(task_id="fixture", program="events.count",
                                                 max_scan_events=size, timeout_seconds=60))
            durations.append((time.perf_counter() - started) * 1000)
            statuses.append(result.status)
            exposed.append(len(result.model_dump_json().encode()))
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"events": size, "attempts": attempts, "ledger_bytes": path.stat().st_size,
                "p50_ms": statistics.median(durations),
                "p95_ms": sorted(durations)[math.ceil(attempts * .95) - 1],
                "statuses": statuses, "complete": all(status == "ok" for status in statuses),
                "max_result_bytes": max(exposed),
                "process_high_water_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
                "cache": "first read then warm filesystem; no provider calls"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--attempts", type=int, default=5)
    args = parser.parse_args()
    for size in args.sizes:
        print(json.dumps(benchmark_scan(size, args.attempts), sort_keys=True))


if __name__ == "__main__":
    main()
