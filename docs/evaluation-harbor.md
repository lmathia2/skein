# Pier evaluation adapter

Skein runs as a host-side Pier custom agent against Harbor-compatible tasks. The ADK/model loop and provider
credential stay in the host process; `read`, `bash`, `edit`, `write`, repository
inspection, and verification execute through Pier's task environment. Pier
does not receive the provider credential through agent or task environment variables.
For this adapter only, dependency, network, unknown-command, and Git authority
is delegated to Harbor's disposable task environment; its task policy and
container boundary remain the enforcement layer. Local `skein eval-run` keeps
the standard restrictive approval policy.

## Pinned contract

- Python 3.12
- Skein: the Git revision recorded by the job
- Harbor 0.22.0 (`uv.lock` and the `eval` extra, used for the frozen task cache)
- Pier 0.3.1 for DeepSWE v1.1
- concurrency: 1
- retries: 0 for ordinary agent failures
- task state: a fresh `agent/skein-state` directory per Harbor trial

Install Harbor without changing the normal Skein runtime:

```bash
uv sync --extra eval
```

Pier is the benchmark runner, matching DeepSWE's mini-SWE-agent interface:

```bash
uv tool install datacurve-pier==0.3.1
```

## Resumable suite runner

The checked-in CLI runs the frozen samples sequentially and keeps the complete
Pier job tree, Skein traces, events, metrics, verifier results, command
artifacts, stdout/stderr, run metadata, and SHA-256 file inventory:

```bash
python scripts/run_harbor_eval.py --suite smoke --plan
python scripts/run_harbor_eval.py --suite smoke --task-id modernize-scientific-stack
python scripts/run_harbor_eval.py --suite smoke
python scripts/run_harbor_eval.py --suite broader
python scripts/run_harbor_eval.py --suite full
```

For every DeepSWE task, this runner builds the task's frozen `tests/` context
once as `linux/amd64`, resolves the resulting immutable `sha256:` image ID, and
passes that ID through Pier's supported `verifier.environment.docker_image`
field. Later trials start fresh verifier containers from the same image instead
of rebuilding it. The official cached task is never modified, and each trial's
`task.json` records the resolved verifier image ID. Do not replace this runner
with a raw `pier run` for scored DeepSWE campaigns unless the equivalent pinned
image preparation and provenance recording are preserved.

The cache changes startup cost only. A new host or Docker backend must pass an
official oracle task with the prebuilt path before scored runs; the Wazero
oracle gate passed with reward `1`, full F2P/P2P, and no exception on the local
Rosetta-backed Docker environment.

Rerun the same command after an interruption. Completed task keys are skipped,
an incomplete Pier job is resumed with `pier job resume`, and a finished
infrastructure error gets a separate attempt directory. A result written before
a wrapper crash is recovered from disk without rerunning the task.
Ordinary verifier failures are completed results and are not retried. The CLI
rejects a jobs directory whose fixed model, reasoning, sample, configuration,
attempt count, or Git revision differs from its original run contract.

## Local Pier run

Use the checked-in external-agent import path and pass no provider secret to
`--agent-env`, the task, or the container. For an authorized Codex workspace:

```bash
pier run \
  --path "$HOME/.cache/harbor/tasks/packages/terminal-bench/TASK_ID/TASK_ARTIFACT_SHA256" \
  --agent-import-path harness.evals.harbor:SkeinPierAgent \
  --model gpt-5.6-luna \
  --agent-kwarg provider=openai_codex \
  --agent-kwarg reasoning=max \
  --n-concurrent 1 \
  --n-attempts 1 \
  --max-retries 0 \
  --yes
```

For a fixed-model OpenRouter trial, load `OPENROUTER_API_KEY` into the host shell
and reference its name only:

```bash
pier run \
  --path "$HOME/.cache/harbor/tasks/packages/terminal-bench/TASK_ID/TASK_ARTIFACT_SHA256" \
  --agent-import-path harness.evals.harbor:SkeinPierAgent \
  --model meta/muse-spark-1.2-contributor \
  --agent-kwarg provider=openrouter \
  --agent-kwarg reasoning=xhigh \
  --agent-kwarg api_key_env=OPENROUTER_API_KEY \
  --n-concurrent 1 \
  --n-attempts 1 \
  --max-retries 0 \
  --yes
```

This is the same `pier run -p ... --model ...` interface used by mini-SWE-agent;
only the built-in `--agent mini-swe-agent` is replaced by Skein's
`--agent-import-path`.
Do not append benchmark-specific instructions. The adapter forwards Harbor's
instruction unchanged.

Each trial writes `skein-state/evaluation/result.json` plus durable events,
metrics, traces, command artifacts, the model/config identity, and a redacted
error classification under Harbor's agent log directory. Harbor `AgentContext`
receives token/cache/cost totals and the versioned Skein result metadata.

The adapter supports Linux tasks only. It fails rather than copying credentials,
exposing `/tests` or `/solution`, retrying around provider limits, or converting a
missing/unverified Skein result into a successful agent result.
