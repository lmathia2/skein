#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
arm=${1:-}
case "$arm" in
  pi-code) agent=scripts.pi_code_tool_harbor:PiCodeToolPierAgent ;;
  pi-skein) agent=scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent ;;
  skein) agent=harness.adapters.pier:SkeinPierAgent ;;
  *) echo "usage: $0 pi-code|pi-skein|skein" >&2; exit 2 ;;
esac

tasks=(
  obsidian-linter-scoped-ignore-markers
  koota-pair-relation-tracking
  scriggo-method-declarations
  tengo-destructuring-bindings
  textual-richlog-follow-state
  ink-grid-box-layout
  query-persist-restored-query-state
  testem-per-launcher-reports
)
task_args=()
for task in "${tasks[@]}"; do task_args+=(--task-id "$task"); done

extra=()
if [[ "$arm" == skein ]]; then
  extra+=(--config harness/core/config/profiles/notebook-ptc-jsonl.yaml)
else
  extra+=(--config harness/core/config/profiles/four-tool.yaml)
fi

cd "$root"
exec uv run python scripts/run_harbor_eval.py \
  --suite confirm --benchmark deep_swe \
  --model meta/muse-spark-1.3-contributor --reasoning xhigh \
  --attempts 3 --concurrency 2 --retries 0 --timeout-seconds 7200 \
  --max-iterations 1000 --max-output-tokens 32768 --max-task-input-tokens 1000000000 \
  --agent-import-path "$agent" "${extra[@]}" \
  --jobs-dir "$root/.artifacts/e13-ptc-isolation-$arm" \
  --trackio-project "${TRACKIO_PROJECT:-skein-harbor}" \
  --trackio-group e13-ptc-isolation --trackio-run-name "$arm" \
  "${task_args[@]}"
