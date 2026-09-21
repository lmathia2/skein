#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
export UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/skein-uv-cache}
arm=${1:-}
if [[ "$arm" != "pi" && "$arm" != "ptc" && "$arm" != "structured" && "$arm" != "thin" && "$arm" != "pi-compatible" && "$arm" != "pi-parity" ]]; then
  echo "usage: $0 pi|ptc|structured|thin|pi-compatible|pi-parity [extra run_harbor_eval.py arguments]" >&2
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
  --concurrency 8
  --retries 0
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
  common+=(--per-trial-timeout-seconds 6900)
  arm_args=(
    --reasoning xhigh
    --agent-import-path scripts.pi_code_tool_harbor:PiCodeToolPierAgent
    --config harness/core/config/profiles/four-tool.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-v3-pricing-fixed-pi-code-tool"
    --trackio-run-name pi-code-tool-xhigh-pricing-fixed
  )
elif [[ "$arm" == "ptc" ]]; then
  common+=(--per-trial-timeout-seconds 6900)
  arm_args=(
    --reasoning xhigh
    --agent-import-path scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent
    --config harness/core/config/profiles/four-tool.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-v4.1-pi-skein-ptc"
    --trackio-run-name pi-skein-ptc-v4.1-xhigh
  )
elif [[ "$arm" == "pi-compatible" || "$arm" == "pi-parity" ]]; then
  common+=(--per-trial-timeout-seconds 6900)
  parity_agent=PiParityPierAgent
  [[ "$arm" == "pi-compatible" ]] && parity_agent=SkeinParityPierAgent
  arm_args=(
    --reasoning xhigh
    --agent-import-path "scripts.pi_code_tool_harbor:$parity_agent"
    --config harness/core/config/profiles/notebook-ptc-jsonl.yaml
    --jobs-dir "$root/.artifacts/e13-muse-20-strict-$arm-v1"
    --trackio-run-name "strict-$arm-v1-xhigh"
  )
else
  jobs_name="e13-muse-20-skein-$arm"
  [[ "$arm" == "thin" ]] && jobs_name+="-fixed2"
  workflow_mode=${arm//-/_}
  arm_args=(
    --reasoning xhigh
    --agent-import-path harness.adapters.pier:SkeinPierAgent
    --config harness/core/config/profiles/notebook-ptc-jsonl.yaml
    --workflow-mode "$workflow_mode"
    --timeout-seconds 7200
    --jobs-dir "$root/.artifacts/$jobs_name"
    --trackio-run-name "skein-$arm-xhigh"
  )
fi

cd "$root"
exec uv run python scripts/run_harbor_eval.py \
  "${common[@]}" "${arm_args[@]}" "${task_args[@]}" "$@"
