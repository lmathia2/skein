# Pi Code Tool vs Pi + Skein PTC

## Scope and result

This comparison holds the Pi agent loop, task prompts, model, OpenRouter provider, `xhigh` reasoning, 32,768-token response cap, eight tasks, and three trials per task constant. The only intended treatment is the `code` executor:

- **Pi Code Tool:** Monty-based sandbox with replayed persistent state and direct `read`, `bash`, `edit`, and `write` helpers.
- **Pi + Skein PTC:** Skein's resident CPython worker with the namespaced `agent.*` capability surface.

Pi Code Tool passes 21/24 trials (87.5%); Pi + Skein passes 17/24 (70.8%). The 16.7-point observed difference has a task-cluster bootstrap 95% interval of -4.2 to +37.5 points. Eight task clusters are not enough to establish a precise population effect, but the implementation and trace differences identify concrete reliability work.

The gap is narrow in semantic quality. Mean partial credit is 0.998 versus 0.995, F2P is 0.955 versus 0.976, and P2P is 1.000 for both after rounding. Pi + Skein actually has the higher mean feature-test score; Pi Code more often crosses the strict all-tests-passing boundary. This is a rollout reliability gap rather than evidence that the Skein executor cannot solve the tasks.

## Quality by task

| Task | Pi Code | Pi + Skein | Partial Pi Code / Pi + Skein | Reading |
|---|---:|---:|---:|---|
| Ink grid layout | 2/3 | 1/3 | .991 / .986 | Small strict-verifier difference. |
| Koota relation tracking | 3/3 | 1/3 | 1.000 / .990 | Largest Code Tool reliability win despite near-complete Skein patches. |
| Obsidian ignore markers | 2/3 | 3/3 | .991 / 1.000 | Skein executor wins and uses much less time and cost. |
| Query restored state | 3/3 | 3/3 | 1.000 / 1.000 | Equivalent quality. |
| Scriggo methods | 2/3 | 1/3 | .999 / .996 | Near-complete failures; Pi + Skein pays more and runs longer. |
| Tengo destructuring | 3/3 | 3/3 | 1.000 / 1.000 | Equivalent quality. |
| Testem reports | 3/3 | 3/3 | 1.000 / 1.000 | Equivalent quality and cost. |
| Textual follow state | 3/3 | 2/3 | 1.000 / .987 | One incomplete Skein rollout. |

Pi Code's four-pass net advantage comes from Ink (+1), Koota (+2), Scriggo (+1), and Textual (+1), offset by Pi + Skein's Obsidian win (-1). Query, Tengo, and Testem tie. Koota is the strongest executor-contract diagnostic; the other differences sit near verifier boundaries.

## Token and cost efficiency

| Metric | Pi Code | Pi + Skein | Code Tool advantage |
|---|---:|---:|---:|
| Input tokens | 424.4M | 400.8M | -5.9% disadvantage |
| Cache-read tokens | 388.9M | 362.0M | -7.4% disadvantage |
| Uncached input | 35.5M | 38.8M | 8.5% lower |
| Output tokens | 1.982M | 2.099M | 5.6% lower |
| Cache-read share | 91.6% | 90.3% | +1.3 points |
| Repriced total | $4.726 | $5.026 | 6.0% lower |
| Repriced per trial | $0.197 | $0.209 | 6.0% lower |
| Repriced per pass | $0.225 | $0.296 | 24.0% lower |
| Recorded tokens per pass | 20.30M | 23.70M | 14.3% lower |
| Uncached input + output per pass | 1.79M | 2.41M | 25.8% lower |

Pi Code submits 5.9% more nominal input, but more of it is cached. Pi + Skein emits 5.6% more output despite making fewer calls. The most likely direct cause is result projection: Pi Code returns the fresh observation as plain text and keeps serialized state in non-model-facing details. The Skein bridge JSON-encodes `status`, `model_text`, `state_count`, `state_delta`, `state_preserved`, and `failure_stage` into every model-visible result. This repeats protocol keys and quoting on every cell.

The two arms have almost identical input per cell. The quality-adjusted difference comes from Pi Code needing fewer uncached/output tokens for each successful rollout and converting more of its calls into a passing patch.

## Latency

| Metric | Pi Code | Pi + Skein | Difference |
|---|---:|---:|---:|
| Median active latency | 17.75 min | 17.68 min | effectively tied |
| Mean active latency | 20.11 min | 19.85 min | Code Tool 1.3% slower |
| P90 active latency | 31.06 min | 30.77 min | effectively tied |
| Median model/tool steps | 119 | 122 | effectively tied |
| Mean model/tool steps | 152 | 145 | Code Tool 5.0% more |
| Active minutes per pass | 22.99 | 28.02 | Code Tool 17.9% lower |

The runtime choice does not materially change raw latency at this scale. Monty's transcript replay is offset by cached host calls, while the resident CPython worker avoids replay but has a more complex broker/result contract. Quality adjustment favors Pi Code because more trials pass, not because individual trials finish faster.

## Trace behavior

| Trace measure | Pi Code | Pi + Skein | Interpretation |
|---|---:|---:|---|
| Code cells | 3,597 | 3,430 | Code Tool +4.9% |
| Nested helper calls | 3,963 | 3,597 | Code Tool +10.2% |
| Shell calls | 2,505 | 2,159 | Code Tool +16.0% |
| Explicit reads | 863 | 989 | Skein +14.6% |
| Edit calls | 393 | 264 | Code Tool +48.9% |
| Write calls | 202 | 145 | Code Tool +39.3% |
| Recognized test commands | 145 | 154 | Skein +6.2% |
| Cells reusing a prior binding | 193 (5.4%) | 523 (15.2%) | Skein uses persistence more |
| Non-OK cells | 72/3,598 (2.0%) | 90/3,431 (2.6%) | Skein has a larger error tax |
| Stored Pi event bytes | 21.59 GB | 0.139 GB | Skein is about 155× smaller |

The quality difference is not caused by less testing or absent variable reuse. Pi + Skein issues slightly more recognized test commands and reuses prior Python bindings nearly three times as often. It performs more explicit reads but substantially fewer mutations. Pi Code's direct helper surface supports more granular edit/revise cycles within the same latency budget.

Cell errors behave differently. Pi Code has 72 error cells: passing trials average 1.67 errors and its three failing trials average 12.33. Pi + Skein has errors in every trial: passing trials average 3.65 and failing trials average 4.00. Its 90 non-OK cells break down into 50 execution failures, 21 source-validation failures, and 19 parse failures. The errors are background protocol friction rather than a useful signal of a hard rollout.

Pi Code's event storage is its major implementation loss. It serializes the growing replay transcript and tool-call cache into each tool result's `details.state`; Pi's JSON event stream then repeatedly snapshots those details. This does not explain model-token usage, but it makes traces operationally expensive. The resident Skein worker keeps state out of the Pi transcript and avoids this blow-up.

## Implementation differences that explain the gap

### 1. The model-facing contract is more complete in Code Tool

Code Tool generates exact Python stubs for every helper and explicitly describes calling convention, print behavior, final-expression behavior, import availability, unsupported syntax, and exception semantics. Pi + Skein's extension says only that `agent.fs.*`, `agent.shell.run`, `agent.parallel`, and `agent.state.*` exist. Exact signatures are available behind `agent.help`, but the model must spend a call discovering them.

This mismatch appears directly in the Skein errors: unsupported `workdir` arguments, blocked `pathlib` imports, malformed source, incorrect result keys, and mistaken object methods. Twenty-one source-validation failures should largely be preventable with an accurate static contract.

### 2. Code Tool returns cleaner observations

Code Tool exposes fresh stdout plus an optional `=> value`, truncates it once, and stores session machinery in tool details. Pi + Skein concatenates stdout, value, stderr, and error text, wraps that in a JSON object, serializes it again through the HTTP bridge, and returns the serialized object as text. This explains why Pi + Skein emits more output tokens with fewer cells and gives the model more envelope syntax to interpret.

### 3. Failed-cell state is safer in Code Tool

Code Tool executes the successful transcript plus the new snippet in a fresh interpreter. A failed snippet is not committed to the transcript, so Python namespace changes disappear on the next call; cached host side effects remain represented and are not blindly replayed.

The Pi + Skein adapter constructs `PersistentPythonWorker` with its default `state_recovery="replay_safe"`. The worker snapshots the namespace only in `snapshot` mode. On an execution exception it reports `state_preserved=false`, but the adapter leaves the worker running. Assignments performed before the exception can therefore remain in the resident namespace even though the result tells the model the state was not preserved. All 24 Pi + Skein trials contain at least one non-OK cell, making this more than a theoretical edge case.

### 4. The helper/result shapes encourage different behavior

Code Tool exposes four short functions. `read()` returns text directly; mutation receipts are compact. Skein uses a namespaced capability object and structured status/data envelopes. Those envelopes carry stronger provenance and concurrency guards, but the model spends more code unwrapping them and makes fewer edit/write calls. The traces suggest an evidence-to-action conversion gap rather than insufficient evidence collection.

### 5. The persistence designs trade correctness against storage

Code Tool reconstructs state by deterministic transcript replay and cached tool results. This gives clean failed-snippet rollback and serializable restart state, but its current Pi integration creates 21.6 GB of events for 24 trials.

Skein keeps a real CPython process alive. It supports richer Python values, uses prior bindings more often, and produces tiny traces. Its missing piece is a cheap, explicit commit/rollback boundary for failed cells.

## Changes to close the Code Tool advantages

Implement these as separate ablations, in this order:

1. **Match the static contract.** Expand `pi_skein_ptc_extension.mjs` with exact signatures, allowed preloaded modules, blocked direct imports/calls, final-expression behavior, timeout limits, result shapes, and two minimal examples. Generate this text from Skein's existing help catalog so it cannot drift.
2. **Project a plain model observation.** On success, return raw `model_text`; move status and state metadata into `details`. Append a one-line state delta only when names changed. On failure, return a concise stage/type/message. Remove the double JSON envelope.
3. **Make failed cells transactional.** First ablate `state_recovery="snapshot"` in the Pi adapter. If snapshot cost is material, checkpoint only validated plain bindings after successful cells and restart/restore after a failed execution cell. Never continue with `state_preserved=false` and the old worker silently alive.
4. **Add a compatibility helper surface.** Prebind `read`, `bash`, `edit`, and `write` aliases with Code Tool-like return values while retaining `agent.*` for advanced use. This tests whether shorter syntax and simpler envelopes produce the missing mutation/revision behavior.
5. **Normalize common shell arguments.** Accept `cwd`/`workdir` only when they resolve inside the workspace, or reject them in a concise pre-execution validation message. The current Python signature error wastes a full model round.
6. **Keep resident execution and compact traces.** Do not copy Code Tool's replay/state serialization into Skein. Its 155× trace-size penalty is unnecessary for closing quality, token, or latency gaps.

The first validation should reuse all eight tasks with three trials. Koota, Scriggo, Textual, and Ink measure whether reliability improves; Obsidian guards against losing Skein's existing win; Query, Tengo, and Testem guard equivalent cases. Success criteria for a Pi + Skein v2 arm are at least 20/24 passes, no worse than 0.995 partial credit, cost at or below Pi Code's $4.73, median latency within 5%, and fewer than 1.5 non-OK cells per successful trial.

The supporting machine-readable metrics are in `docs/experiments/e13-pi-code-vs-pi-skein-trace-metrics.json`.
