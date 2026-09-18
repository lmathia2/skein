#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
# Two jobs with three workers each: six evaluations total. Run once on an idle host.
pids=()
trap 'for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done' INT TERM
for checklist in 0 1; do
  arm=checklist-off
  if [[ "$checklist" == 1 ]]; then arm=checklist-on; fi
  PTC_COMPLETION_CHECKLIST=$checklist uv run python scripts/run_harbor_eval.py \
    --suite confirm --benchmark deep_swe --provider openrouter \
    --model meta/muse-spark-1.3-contributor --reasoning xhigh \
    --attempts 3 --concurrency 3 --retries 0 --per-trial-timeout-seconds 6900 \
    --max-iterations 1000 --max-output-tokens 32768 --max-task-input-tokens 1000000000 \
    --agent-import-path scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent \
    --config harness/core/config/profiles/four-tool.yaml \
    --jobs-dir "$root/.artifacts/e13-v3-checklist/$arm" \
    --trackio-project skein-harbor --trackio-group e13-v3-checklist --trackio-run-name "$arm" \
    --task-id ink-grid-box-layout --task-id obsidian-linter-scoped-ignore-markers \
    --task-id scriggo-method-declarations --task-id textual-richlog-follow-state "$@" &
  pids+=("$!")
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
exit "$status"
