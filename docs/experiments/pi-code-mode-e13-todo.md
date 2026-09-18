# E13 code-mode evaluation TODO

Updated 2026-09-15 (Pacific). Source of truth for scores: each result root's `runs.jsonl`.

## Complete

- [x] Freeze the six E13 DeepSWE 1.1 tasks and six additional tasks in `tests/eval/cohorts/e13-deep-swe-1-1-v1.json`; verify hashes and six/12 task selection.
- [x] Run Joseph Kern's `pi-code-tool` on the six tasks with Luna/max: 6 official results, 4 passes, $2.716. Result root: `.artifacts/live-e13-pi-code-tool-six-detached-20260915/`.
- [x] Run Boozedog's `pi-codemode` on the six tasks with Luna/max: 6 official results, 3 passes, $3.191. Result root: `.artifacts/live-e13-pi-codemode-six-detached-concurrent-20260915/`.
- [x] Run clean Skein PTC on the six tasks with Luna/max: 6 official results, 3 passes, $1.571. Result root: `.artifacts/live-e13-ptc-clean-worktree-v2-20260915/`.
- [x] Choose `pi-code-tool` as the highest-quality Luna arm (4/6 versus 3/6 for each other arm).
- [x] Run `pi-code-tool` with Muse Spark 1.3 Contributor: 4/6, $0.552 repriced spend, 75.1 summed active minutes. Result root: `.artifacts/live-e13-pi-code-tool-muse13-six-20260915/`.
- [x] Create a clean Skein Git worktree at `80e300d` and verify Pier imports ADK from the installed project environment. The failed dependency setup attempt is excluded.
- [x] Estimate a Muse Spark 1.3 Contributor six-task rerun at $0.5–$1.5 and an eventual three-arm 12-task expansion at $12.5–$23 in model spend.

## Next

- [x] Collect official Muse verifier rewards and corrected cost/latency metrics.
- [x] Compare the four valid six-task runs per task: quality, cost, tokens, active/model and end-to-end latency, code calls, parallel calls, prompt/state behavior, tool failures, and verifier time. Exclude the old interrupted PTC control from the ranking.
- [x] Report findings with trace paths and practical limits in `docs/experiments/pi-code-mode-e13-results.md`.

## Waiting for user confirmation

- [ ] Decide whether and how to run the expanded 12-task cohort (`--cohort-group all`), including concurrency and arm selection. Do not launch it before confirmation.
