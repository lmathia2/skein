#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
export UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/skein-uv-cache}
arm=${1:-}
if [[ "$arm" != "pi" && "$arm" != "ptc" ]]; then
  echo "usage: $0 pi|ptc [extra run_harbor_eval.py arguments]" >&2
  exit 2
fi
shift

manifest="$root/tests/eval/experiments/e13-code-mode-20-v2.json"
task_args=()
while IFS= read -r task_id; do
  task_args+=(--task-id "$task_id")
done < <(python3 -c 'import json,sys; print(*(t["task_id"] for t in json.load(open(sys.argv[1]))["panel"]["tasks"]), sep="\n")' "$manifest")

common=(
  --suite confirm
  --benchmark deep_swe
  --model meta/muse-spark-1.3-contributor
  --attempts 3
  --concurrency 3
  --retries 0
  --per-trial-timeout-seconds 6900
  --max-iterations 1000
  --max-output-tokens 32768
  --max-task-input-tokens 1000000000
  --trackio-project "${TRACKIO_PROJECT:-skein-harbor}"
  --trackio-group "${TRACKIO_GROUP:-e13-muse-20-v3-pricing-fixed}"
)
if [[ -n "${TRACKIO_SPACE_ID:-}" ]]; then
  common+=(--trackio-space-id "$TRACKIO_SPACE_ID")
fi

if [[ "$arm" == "pi" ]]; then
  arm_args=(
    --reasoning xhigh
    --agent-import-path scripts.pi_code_tool_harbor:PiCodeToolPierAgent
    --config harness/core/config/profiles/four-tool.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-v3-pricing-fixed-pi-code-tool"
    --trackio-run-name pi-code-tool-xhigh-pricing-fixed
  )
else
  arm_args=(
    --reasoning xhigh
    --agent-import-path scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent
    --config harness/core/config/profiles/four-tool.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-v4.2-pi-skein-ptc"
    --trackio-run-name pi-skein-ptc-v4.2-xhigh
  )
fi

cd "$root"
exec uv run python scripts/run_harbor_eval.py \
  "${common[@]}" "${arm_args[@]}" "${task_args[@]}" "$@"
