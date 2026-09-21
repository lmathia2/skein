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
  --config harness/core/config/profiles/four-tool.yaml \
  --concurrency 2
```

Suites are `smoke` (6 tasks), `broader` (10 tasks), and `full` (105 tasks).
Use `--benchmark`, `--task-id`, or `--limit` to narrow a run. Each trial retains
its Pier job, Skein events, traces, verification output, and metrics. Reusing the
jobs directory resumes incomplete Pier jobs and skips completed task keys.

Trackio is installed by default. Log one live dashboard run per campaign:

```sh
uv sync
scripts/run_e13_muse_20.sh pi
scripts/run_e13_muse_20.sh ptc
scripts/run_e13_muse_20.sh pi-compatible
scripts/show_e13_trackio.sh
```

Add `--trackio-space-id USER/SPACE` to sync to a Hugging Face Space. Trackio logs
campaign progress, pass rate, cost, tokens, and latency; `runs.jsonl` remains the
task-level source of truth. Incomplete trials, timeouts, nonzero exits, and runner
exceptions also create Trackio error alerts. Set `TRACKIO_WEBHOOK_URL` to forward
those alerts to Slack or Discord.

To resume after an interruption, rerun the exact command with the same `--jobs-dir`.
The wrapper skips completed task keys, recovers results already written to disk, and
calls `pier job resume` for unfinished Pier jobs. Pier 0.3.1 resumes at the job/trial
boundary; the Skein Pier adapter does not currently resume a model midway through an
interrupted trial, so that one active task may restart while completed tasks do not.

## Modes

The profile passed to `--config` selects the model interface and memory policy:

| Profile | Model-facing execution | Memory |
|---|---|---|
| `four-tool.yaml` | `read`, `bash`, `edit`, `write` | bounded task log |
| `notebook-ptc-jsonl.yaml` | persistent Skein `execute_code` notebook | canonical JSONL |

Provider, model, reasoning effort, token limits, concurrency, and attempts are
runner flags. Tool topology, safety, verification, and evidence authority remain
code-owned so benchmark modes stay comparable.

The notebook profile has two workflow policies. `structured` uses Skein's phased
work packets, counterexample review, and bounded verification retries.
`pi_compatible` keeps the same PTC worker, broker, verifier, and canonical trace but
gives the model Pi's compact direct-helper contract and continuous repair loop.
`thin` remains an experiment-compatible alias for that lightweight path. Select a
policy with `--workflow-mode structured|pi_compatible`.

In `pi_compatible` mode the model receives concise Markdown and readable helper
results; the trace still records complete typed capability receipts and artifacts.
Repeated verification failures return to the model until the task or execution budget
ends. Only a genuine human dependency should produce `blocked`.

### Pi + Skein PTC v4.1

The E13 comparison runner also provides `PiSkeinPtcPierAgent`, a Pi extension backed
by Skein's persistent CPython worker. This is separate from the ADK notebook profile:
Pi owns the model loop, while Skein supplies the `code` tool, worker, Harbor bridge,
and workspace helpers.

One model call can submit a Python cell that calls synchronous `read`, `write`,
`edit`, `bash`, and `verify` helpers. Python variables and functions persist between
cells, so batching is ordinary Python composition—loops, filtering, and several
helper calls in one cell—rather than a second batch API. `json`, `math`, and `re` are
preloaded. The worker blocks direct host I/O and routes workspace effects through the
confined broker.

V4.1 provides:

- conservative AST preflight for undefined names, helper signatures and arguments,
  known result keys, simple literal types, and invalid operators;
- readable text projections with merged shell diagnostics and exit status, plus
  50 KiB model observations and pageable retained results;
- rollback of supported in-memory values after a Python exception without rolling
  back external effects;
- a bounded JSON-only checkpoint after each successful cell, restored after worker
  timeout or transport loss without replaying the transcript; and
- a separate `verify` helper plus one completion-review follow-up when the last
  possible mutation is not covered by successful verification.

The general Skein default remains the four-tool profile. V4.1 is the default only for
the Pi + Skein PTC evaluation arm; v4.2 remains an archived experiment because it
reduced `verify()` calls without reducing total interactions, cost, or latency. See
[the PTC architecture decision](docs/adr/programmatic-tool-calling.md)
and [the v4.2 comparison](docs/experiments/e13-pi-skein-v4.2-comparison.md).

## Develop

```sh
uv run --extra eval pytest tests/unit/test_harbor_adapter.py tests/unit/test_harbor_eval_runner.py
uv run --extra eval ruff check app harness scripts tests/unit/test_harbor_adapter.py tests/unit/test_harbor_eval_runner.py
uv run --extra eval pyright app harness
```

The source tree separates product code from benchmark support:

| Path | Responsibility |
|---|---|
| `app/agent/` | Concrete ADK worker and composition root |
| `harness/core/` | Task contracts, configuration, context, and orchestration |
| `harness/execution/` | Brokered tools, policy, approvals, and workspace runtimes |
| `harness/evidence/` | Append-only state, ledger projections, traces, and metrics |
| `harness/ptc/` | Persistent Python worker and notebook projection |
| `harness/verification/` | Independent completion checks |
| `harness/adapters/` | ADK, provider, and Pier integration code |
| `evals/` | Harbor manifests, campaign runner, and result analysis—not harness core |

See `docs/evaluation-harbor.md` for Pier details and `docs/architecture.md` for
the component wiring. See `docs/package-layout.md` for dependency rules and the
contents of each package.
