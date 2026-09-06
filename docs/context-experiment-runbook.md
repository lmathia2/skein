# Context-program experiment runbook

These are opt-in experimental implementations, not promoted defaults. No provider
run or quality claim follows from passing the offline checks. Keep live experiments
separate from existing servers, workspaces, state roots, and provider sessions.
Do not restart an existing server, run `uv sync` in its environment, or point a
trial at its state directory. Finish preparing dependencies before starting trials.

## Offline preflight

Implementation validation (isolated worktree, 2026-09-05): the full unit and
integration suite passed, including six real subprocess recovery checks. Eight
tests skipped: two require optional LanceDB, six require the built terminal client.
Ruff passed. Full type checking remains limited by missing optional `pier`,
`lancedb`, and `pyarrow` packages; dependencies were deliberately not changed in the
environment used by active runs. No live provider or large scan experiment ran.

Semantic retrieval requires an explicitly supplied versioned embedding/search
factory. Evidence-bound summarization is an async library API with an authorized
model callback, not an automatic summarizer or a YAML-only treatment. Configure
these adapters and their usage accounting before running those optional pairs.

From a dedicated checkout using an already installed environment:

```sh
python -m pytest -q tests/integration/test_context_experiments.py
```

This validates all treatment profiles, exercises the production factory and actual
ADK request assembly with a scripted model, compares baseline/capture/shadow
requests, runs 50 tool calls, and independently tests the pilot fixture oracle.
Scripted responses prove contracts, not recall, reasoning, or cost improvements.
Recovery and context-transition contracts have additional focused tests alongside
their implementations; run the full unit and integration suites before live work.

The dependency-free scan benchmark is also ready to run later:

```sh
python -m harness.evals.context_benchmark --sizes 1000 10000 100000 --attempts 5
```

It builds disposable fixtures, then reports scan p50/p95, bytes, result status and
completeness, and process high-water RSS. Large scans can explicitly hit physical
scan limits; a partial result is not counted as a complete aggregate. Fixture
construction is excluded from query timing, so this is not append-throughput data.
RSS is cumulative process high water, not per-query allocation. No provider runs.

## Treatments and controlled pairs

All files below are complete compositions in `harness/config/profiles/` and load
through the existing configuration parser. Model identity and authority are the
same; `eval-run` freezes the authorized provider/model into each resolved config.

| Pair | Files (`context-*.yaml`) | Question |
| --- | --- | --- |
| B0 / B1 | `baseline` / `capture` | Canonical capture parity and overhead |
| B1 / H | `capture` / `shadow` | Shadow computation preserves provider requests |
| B1 / W | `capture` / `windows` | Bounded inner-loop windows alone |
| W / R | `windows` / `retrieval` | Active retrieval with identical window management |
| R / N | `retrieval` / `notes` | Advisory notes improve continuation without stale claims |
| N / F | `notes` / `fresh` | Fresh reconstruction versus bounded retained tail |
| N / A | `notes` / `recovery` | Explicit versus safe-auto under identical faults |
| N / P | `notes` / `ptc` | Four tools versus notebook PTC (separate surface experiment) |
| P / PC | `ptc` / `continuity` | Run versus conversation PTC continuity |
| P / U | `ptc` / `reuse` | Reviewed procedures versus identical primitives without reuse |
| N / prior | `notes` / `prior-runs` | Explicit owned prior-run evidence |

Do not combine all toggles into a Cartesian product. Freeze the configuration,
provider snapshot, generation settings, fixture revision, policies, budgets, and
initial evidence for a pair. Reuse has its own gate: a cheaper lookup is not evidence
that saved programs outperform ad hoc computation after preparation/review costs.
`context.window_management` is independent of the program mode. W and R use the
same bounded window policy, isolating retrieval from reconstruction. Off/shadow
profiles leave windows disabled, preserving baseline parity. Notes, fresh selection,
prior-run scope, and reusable programs have separate one-setting comparators after R.

## Prepare pilot corpus without making a model call

```sh
python -m harness.evals.context_cases prepare full-set-aggregation \
  /tmp/skein-context-r-aggregation-01 \
  --config harness/config/profiles/context-retrieval.yaml
```

Preparation refuses an existing destination. It creates a fresh Git workspace,
frozen configuration identity, evidence hash, prompt, interaction schedule, and
independent structured-output oracle. No package installation or network access is
performed. Available cases are `early-evidence`, `temporal-correction`,
`full-set-aggregation`, `oversized-artifact`, `interrupted-mutation`, and
`later-run-recall`. Repeat preparation at a different path for every arm/attempt.

The corpus intentionally makes temporal correction, evidence IDs, full-set counts,
and exact byte recovery independently gradable. `answer.json` must match the oracle;
changing the source evidence fails verification. The mutation case additionally
checks the actual counter file. The oracle is outside the workspace and must never
be included in worker input. The local environment is not an adversarial sandbox.

```sh
python -m harness.evals.context_cases verify /tmp/skein-context-r-aggregation-01
```

The verifier returns nonzero for missing, incorrect, or tampered evidence. It is a
separate outcome check, not a substitute for Skein's completion verifier. Keep both
results, including failed trials.

## Execute only after explicit provider authorization

For single-turn transport smoke, use the existing `skein eval-run --config` with
the prepared workspace, a fresh state root, authorized frozen provider/model, and
the prompt in `trial.json`. This checks corpus/tool/grade connectivity only.

**A one-shot run over a file is not a memory experiment.** For the actual early
evidence arm, expose the source before 50+ calls and three context transitions,
then remove it from the active packet and demand exact recall. Use canonical
history/artifact references, not a permanently visible copy of the answer. For
temporal arms preserve both observed and recorded cutoffs. Count over all matching
events, including rows beyond the first retrieval page. Keep source scope identical.

The dedicated driver executes schedules through the real server coordinator, with
one newly owned subprocess per phase:

```sh
python -m harness.evals.context_driver run /tmp/skein-context-r-aggregation-01 \
  --request /tmp/authorized-context-request.json
```

The request file uses the existing `EvaluationRunRequest` JSON fields: `workspace`,
`state_root`, `auth_state_root`, `task_id`, `prompt`, `provider`, `model`,
`config_template`, and optional reasoning/budgets. Copy the prompt from `trial.json`.
Workspace and state must exactly match the prepared trial. Authentication stays
outside its workspace; request files contain credential references, never secrets.
The driver refuses an existing state root and does not signal an existing server.

Later-run recall runs a source turn, closes its process, removes the current file,
and starts another process with the same conversation and explicit owned source IDs
when `prior_runs` is enabled. Git history remains an alternate evidence route;
report its use separately rather than attributing every success to memory programs.
The mutation schedule kills only its child after the counter effect and before its
receipt publication, then opens the same state with configured recovery. A safe
unknown-effect block is reported separately from verified task completion.
No automatic reconciliation or unknown-effect retry is added by the evaluator.

Each phase retains the existing `skein-eval-run-v1` result and a process log.
`schedule-result.json` reports actual phases, context epochs, receipt counts,
schedule validity, safe recovery blocking, and independent verification. Early
evidence requires 50 calls, plus three real transitions when window management
implementation is selected. Off/shadow baselines report zero epochs rather than
being rejected for not implementing the treatment. Failure to follow a required
schedule invalidates the memory trial even if the answer is right. Choose a small
but valid packet budget in both arm profiles when deliberately inducing pressure.
The scripted integration tests prove request assembly and reset contracts without
depending on a live model following those instructions.

## Reports and promotion

Store the existing `skein-eval-run-v1` result and its config, metrics, traces, run
database, and verifier artifacts. Keep an adjacent trial manifest with source and
program versions, event IDs/watermarks, context boundaries, redacted provider-request
captures, actual fault schedule, and warm/cold-cache status. Never replace missing
provider cost with zero. No passing tasks means cost per pass is undefined.

Run six cases once per arm for integration smoke, then three attempts per case
(36 trials per pair). Confirm a candidate on at least 18 held-out tasks with two
attempts per arm. Counterbalance order; cluster confidence intervals by task. Keep
training notes/programs out of held-out evidence and include preparation cost.

Report verified success, stale/unsupported evidence, scope/duplicate-effect errors,
recovery success/time/operator interventions, cost per pass, uncached/cache-read
tokens, output/model/tool counts, context bytes, and median/p95 latency. Declare
thresholds before execution. Zero observed safety violations is necessary, not a
proof of general safety. Insufficient evidence leaves the feature opt-in.

See the [ADR](adr/long-running-context-memory-programs.md) for phase-specific
failure cases, limitations, and promotion rules. SQL/semantic/summary-cache pairs
remain separate gated extensions, not hidden dependencies of this corpus.
