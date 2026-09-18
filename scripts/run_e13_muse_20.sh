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

manifest="$root/tests/eval/experiments/e13-code-mode-20-v1.json"
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
  --timeout-seconds 7200
  --max-iterations 1000
  --max-output-tokens 16384
  --max-task-input-tokens 1000000000
  --trackio-project "${TRACKIO_PROJECT:-skein-harbor}"
  --trackio-group "${TRACKIO_GROUP:-e13-muse-20}"
)
if [[ -n "${TRACKIO_SPACE_ID:-}" ]]; then
  common+=(--trackio-space-id "$TRACKIO_SPACE_ID")
fi

if [[ "$arm" == "pi" ]]; then
  arm_args=(
    --reasoning xhigh
    --agent-import-path scripts.pi_code_tool_harbor:PiCodeToolPierAgent
    --config harness/core/config/profiles/four-tool.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-xhigh-pi-code-tool"
    --trackio-run-name pi-code-tool-xhigh
  )
else
  arm_args=(
    --reasoning xhigh
    --agent-import-path harness.adapters.pier:SkeinPierAgent
    --config harness/core/config/profiles/notebook-ptc-jsonl.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-xhigh-skein-ptc"
    --trackio-run-name skein-ptc-xhigh
  )
fi

cd "$root"
exec uv run python scripts/run_harbor_eval.py \
  "${common[@]}" "${arm_args[@]}" "${task_args[@]}" "$@"
