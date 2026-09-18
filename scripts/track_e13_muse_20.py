#!/usr/bin/env python3
"""Backfill and follow the two live E13 Harbor ledgers in Trackio."""

from __future__ import annotations

import argparse
import time

from scripts.run_harbor_eval import ROOT, TrackioRun, ledger_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=("pi", "ptc"))
    args = parser.parse_args()
    roots = {
        "pi-code-tool": ROOT / ".artifacts/e13-muse-20-pi-code-tool",
        "skein-ptc": ROOT / ".artifacts/e13-muse-20-skein-ptc",
    }
    name = "pi-code-tool" if args.arm == "pi" else "skein-ptc"
    root = roots[name]
    tracker = TrackioRun(
        project="skein-harbor",
        name=name,
        group="e13-muse-20",
        space_id=None,
        config={"source": "runs.jsonl", "result_root": str(root)},
    )
    seen = 0
    try:
        while seen < 20:
            rows = ledger_rows(root / "runs.jsonl")
            for row in rows[seen:]:
                tracker.log(row)
            seen = len(rows)
            time.sleep(10)
    finally:
        tracker.finish()


if __name__ == "__main__":
    main()
