# Skein

Skein is a small coding-agent harness built on Google ADK. It gives the model one
programmable tool, keeps effects behind a controlled host boundary, and verifies the
workspace before declaring a coding task complete.

A skein is a length of thread gathered into a usable form. The name reflects the
design: model conversation, Python cells, file and shell effects, verification, and
trace evidence are separate threads joined into one inspectable run.

## The idea

Skein keeps the coding loop deliberately narrow:

```text
task -> ADK coding agent -> code -> read/write/edit/bash/verify
                              |
                              v
                       effect broker
                              |
                              v
                     trace + host verifier
```

Google ADK owns model calls, native tool continuation, streaming, sessions,
cancellation, and resume. The model sees PTC v4.1's single persistent `code` tool.
Inside it, synchronous `read`, `write`, `edit`, `bash`, and `verify` helpers
compose ordinary Python work.

Skein adds three boundaries around that simple loop:

- Every workspace effect passes through the same broker and produces a typed receipt.
- Model context and tool output are bounded projections; complete evidence stays in an
  append-only trace and content-addressed artifacts.
- The model can request completion, but only the host verifier can complete the task.

This is the main innovation: programmatic tool use, context, recovery, tracing, and
verification share one causal record without replacing ADK's event loop or expanding
the model's tool surface.

## Install

Requirements:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose v2 and Buildx for Harbor tasks

Run:

```sh
./install.sh
```

The script creates `.venv`, installs Skein and pinned Harbor dependencies from
`uv.lock`, and installs Pier 0.3.1 in uv's isolated tool environment. A successful
run ends with:

```text
Skein Harbor/Pier environment ready.
```

Set the provider credential used by the evaluation runner. Its default provider is
OpenRouter:

```sh
export OPENROUTER_API_KEY=...
```

The runner also reads `~/.env` by default. Keep that file mode `0600`.

## Run coding evaluations

First inspect the frozen smoke suite without starting Docker or calling a model:

```sh
.venv/bin/python scripts/run_harbor_eval.py --suite smoke --plan
```

Run one smoke task through the default ADK + PTC v4.1 harness:

```sh
.venv/bin/python scripts/run_harbor_eval.py \
  --suite smoke \
  --task-id modernize-scientific-stack
```

Run the complete six-task smoke suite with two isolated Pier trials at a time:

```sh
.venv/bin/python scripts/run_harbor_eval.py \
  --suite smoke \
  --concurrency 2 \
  --jobs-dir "$HOME/skein-runs/smoke"
```

Suites are `smoke` (6 tasks), `broader` (10 tasks), and `full` (105 tasks).
Use `--benchmark`, `--task-id`, or `--limit` to narrow a run. Every trial keeps
its Pier job, model and tool trace, workspace evidence, verification output, and
metrics below the jobs directory.

Rerun the same command with the same `--jobs-dir` to skip completed task keys and
resume incomplete Pier jobs. To rebuild a missing campaign ledger without launching
work:

```sh
.venv/bin/python scripts/run_harbor_eval.py \
  --recover-only \
  --jobs-dir "$HOME/skein-runs/smoke"
```

The default configuration is [`harness/core/config/default.yaml`](harness/core/config/default.yaml).
It exposes PTC v4.1 `code`. The
[`four-tool.yaml`](harness/core/config/profiles/four-tool.yaml) profile is retained
only for controlled tool-surface comparisons:

```sh
.venv/bin/python scripts/run_harbor_eval.py \
  --suite smoke \
  --config harness/core/config/profiles/four-tool.yaml
```

## Develop

```sh
make test
make lint
```

The code is organized by authority:

| Path | Owns |
| --- | --- |
| `app/agent/` | ADK worker and the coding/verification loop |
| `harness/core/` | Configuration, task contracts, and bounded context |
| `harness/ptc/` | Persistent Python execution and recovery |
| `harness/execution/` | Brokered filesystem and command effects |
| `harness/evidence/` | Trace, artifacts, projections, and learning episodes |
| `harness/verification/` | Independent completion checks |
| `harness/adapters/` | ADK, provider, and Pier boundaries |
| `evals/` | Frozen Harbor task manifests and campaign analysis |

For the precise runtime contract, read
[`docs/architecture.md`](docs/architecture.md) and
[`docs/adr/adk-native-ptc-v4.1-core.md`](docs/adr/adk-native-ptc-v4.1-core.md).
