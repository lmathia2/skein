# Skein

Skein is a minimal Google ADK coding harness for evaluating a fixed model in
Harbor tasks through Pier. It keeps the model-facing interface small, records
append-only execution evidence, and requires deterministic verification before a
coding task can complete.

## Setup

Requirements: Python 3.12+, `uv`, Docker with Compose and Buildx, and Pier 0.3.1.

```sh
uv tool install datacurve-pier==0.3.1
./install.sh
```

Set the provider credential used by your selected mode, for example
`OPENROUTER_API_KEY`. The runner can also read it from a mode-`0600` `.env` file.

## Run Harbor evaluations

Inspect a frozen suite without starting Docker or calling a model:

```sh
.venv/bin/python scripts/run_harbor_eval.py --suite smoke --plan
```

Run the smoke suite with two isolated Pier trials at a time:

```sh
.venv/bin/python scripts/run_harbor_eval.py \
  --suite smoke \
  --config harness/config/profiles/four-tool.yaml \
  --concurrency 2
```

Suites are `smoke` (6 tasks), `broader` (10 tasks), and `full` (105 tasks).
Use `--benchmark`, `--task-id`, or `--limit` to narrow a run. Each trial retains
its Pier job, Skein events, traces, verification output, and metrics. Reusing the
jobs directory resumes incomplete Pier jobs and skips completed task keys.

## Modes

The profile passed to `--config` selects the model interface and memory policy:

| Profile | Model-facing execution | Memory |
|---|---|---|
| `four-tool.yaml` | `read`, `bash`, `edit`, `write` | bounded task log |
| `notebook-ptc-jsonl.yaml` | persistent Skein `execute_code` notebook | canonical JSONL |

Provider, model, reasoning effort, token limits, concurrency, and attempts are
runner flags. Tool topology, safety, verification, and evidence authority remain
code-owned so benchmark modes stay comparable.

## Develop

```sh
uv run --extra eval pytest tests/unit/test_harbor_adapter.py tests/unit/test_harbor_eval_runner.py
uv run --extra eval ruff check app harness scripts tests/unit/test_harbor_adapter.py tests/unit/test_harbor_eval_runner.py
uv run --extra eval pyright app harness
```

See `docs/evaluation-harbor.md` for Pier details and `docs/architecture.md` for
the component wiring.
