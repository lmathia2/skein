# E13 Muse 20-task analysis: Pi Code Tool vs Skein PTC

## Protocol and validity

- 20 DeepSWE tasks, 3 fresh trials per task and arm (60 trials per arm).
- Model: `meta/muse-spark-1.3-contributor` through OpenRouter, requested reasoning `xhigh`.
- Pi Code Tool: 40/60 passes. Skein PTC: 19/60 passes.
- Task-clustered paired bootstrap (100,000 samples over the 20 tasks): Pi's pass-rate advantage is 35 percentage points, with a 95% interval of 20 to 50 points. Pi wins 13 task-level matchups, 6 tie, and PTC wins 1.
- The arms were not perfectly matched on response limits. The runner passed `max_output_tokens=16384` only to Skein. Pi used its model-catalog limit; 13 Pi responses exceeded 16,384 output tokens and the largest was 28,359. Treat this as a material confound and fix it before attributing the whole gap to the agent design.

## Aggregate results

| Metric | Pi Code Tool | Skein PTC | Interpretation |
|---|---:|---:|---|
| Binary pass rate | 40/60 (66.7%) | 19/60 (31.7%) | Pi +35.0 pp |
| Mean task partial credit | 97.59% | 85.31% | Pi's failures are usually closer to complete |
| F2P (new feature tests) | 93.35% | 54.73% | Main quality gap |
| P2P (existing tests) | 98.28% | 98.25% | Essentially tied |
| Repriced total cost | $7.833 | $2.313 | Pi costs 3.39x per trial set |
| Cost per passing rollout | $0.196 | $0.122 | PTC remains cheaper per pass |
| Recorded tokens per trial | 10.39M | 5.38M | Pi uses 1.93x as many |
| Recorded tokens per pass | 15.59M | 16.99M | Pi is 8.2% better after quality adjustment |
| Cache-read share of input | 90.55% | 96.48% | PTC has better cache reuse, but lower task completion |
| Median active latency | 14.22 min | 9.65 min | Pi is 47% slower per trial |
| P95 active latency | 37.97 min | 21.99 min | Pi has a longer tail |
| Active time per passing rollout | 23.95 min | 34.35 min | Pi is 30% faster after quality adjustment |

Costs use the experiment's Muse rates: $0.10/M uncached input, $0.002/M cache read, and $0.20/M output. Pi's recorded provider prices were stale, so both arms were repriced from token counts.

The P2P/F2P split is the most useful result. Both arms preserve old behavior equally well. Skein fails to finish the requested feature behavior. Its lower raw cost and latency largely reflect less work and early termination, rather than equivalent work performed more efficiently.

## Trace-level differences

| Trace measure (60 trials) | Pi Code Tool | Skein PTC | Ratio / note |
|---|---:|---:|---|
| Code calls / cells | 5,957 | 3,400 | Pi 1.75x |
| Nested helper / capability calls | 7,167 | 4,038 | Pi 1.78x |
| Shell calls | 4,465 | 1,565 | Pi 2.85x |
| Edit + write calls | 1,036 | 578 | Pi 1.79x |
| Cross-cell binding reuse heuristic | 507 (8.5%) | 2,622 (77.1%) | State persistence is working in PTC |
| Repeated reads | 1,225/1,642 (74.6%) | 1,544/1,895 (81.5%) | PTC still rereads slightly more |
| Failed PTC cells | n/a | 96/3,400 (2.8%) | Ordinary cell errors are not dominant |
| Stored trace bytes | 32.6 GB | 105.5 MB | Pi JSON events duplicate growing message snapshots; this is storage overhead, not model input |

Skein exposes a persistent CPython worker, explicitly tells the model to retain values, and provides `agent.state.list/describe/reuse`. The model is not automatically given a fresh binding inventory on every turn, but the traces do not support missing variable awareness as the primary cause: the model references prior bindings in most PTC cells. The higher repeated-read rate is a smaller efficiency defect worth fixing after termination reliability.

Pi's dominant behavioral difference is persistence at the task level: more tool rounds, much more test execution, and more edits. Skein uses structured work batches, criterion transitions, a bounded review cell, and a terminal `AgentStep` schema. Those controls introduce additional ways to stop or fail before the feature is complete.

Fifteen Skein trials ended in runtime-level failures: 12 `max_output_tokens`, one reused tool-call ID with different content, one criterion probe-row transition failure, and one DNS failure. They account for 15 guaranteed losses. Skein still passes only 19/45 (42.2%) when those trials are excluded, so runtime reliability is material but cannot explain the whole 35-point gap.

## Per-task comparison

`pass` is successful trials out of 3. F2P is the mean new-feature test score. Calls are Pi code calls versus PTC cells across three trials.

| Task | Pass Pi/PTC | F2P Pi/PTC | Calls Pi/PTC | Main trace difference |
|---|---:|---:|---:|---|
| Anko default arguments | 3/1 | 1.00/0.50 | 364/158 | Pi performs over twice the iteration; one PTC output-limit termination. |
| Bandit structured nosec | 0/0 | 0.976/0.333 | 184/112 | Both miss binary success; Pi is close, while two PTC trials hit the output cap. |
| Clack async autocomplete | 1/0 | 0.984/0.980 | 168/147 | Near-identical partial behavior; likely rollout variance around a narrow verifier edge. |
| Claude recursive delegation | 2/2 | 0.762/0.952 | 157/179 | PTC does more work and has better partial credit; no Pi advantage here. |
| Happy DOM observer | 2/2 | 0.976/0.976 | 289/214 | Quality tie. PTC costs about half, with similar total latency. |
| Ink grid layout | 3/1 | 1.00/0.653 | 223/242 | PTC works longer and calls more capabilities, but one tool-ID runtime failure and incomplete feature paths erase the effort. |
| Koota relation tracking | 3/0 | 1.00/0.00 | 428/99 | Largest under-investment: Pi uses 4.3x the calls; two PTC trials hit the output cap. |
| LangChain coalescing | 2/0 | 0.993/0.647 | 277/211 | PTC leaves feature cases incomplete; one output-cap failure. |
| Numba stencil modes | 2/1 | 0.989/0.598 | 333/215 | PTC covers the core path but misses boundary-mode cases. |
| Obsidian ignore markers | 3/0 | 1.00/0.00 | 273/47 | All three PTC trials hit the output cap; this result is mostly a response-limit artifact. |
| Oxvg selector preservation | 0/0 | 0.556/0.00 | 576/244 | Hard for both; Pi spends much more effort and gains partial feature coverage only. |
| PSD Tools blend range | 3/2 | 1.00/0.948 | 274/158 | Narrow one-rollout gap; PTC is efficient when it converges. |
| Pwntools multiplexing | 2/1 | 0.986/0.333 | 162/89 | Two PTC output-cap failures dominate the mean. |
| Query restored state | 2/3 | 0.958/1.00 | 249/243 | PTC's only win; it spends about twice the latency and completes all paths. |
| Scriggo methods | 1/0 | 0.625/0.028 | 874/171 | Pi uses five times the calls to earn one pass; PTC also has a criterion-transition runtime failure. |
| Tengo destructuring | 3/1 | 1.00/0.341 | 328/173 | PTC stops with broad feature semantics still missing. |
| Testem reports | 3/3 | 1.00/1.00 | 204/206 | Clean equal-quality case; PTC is cheaper and faster. |
| Textual follow state | 2/0 | 0.983/0.167 | 287/178 | PTC implements only a subset of state transitions. |
| Updo alerting | 0/0 | 0.882/0.824 | 149/171 | Near tie among failures; one PTC DNS failure. |
| YTT JSONPath | 3/2 | 1.00/0.667 | 158/143 | Similar calls; one PTC output-limit termination causes most of the gap. |

The per-task causal labels above are trace-supported diagnoses, not proof that a single mechanism caused every failed assertion. Exact semantic root-cause work would require comparing the three patches and verifier failures for each task.

## Prompt and orchestration differences

Pi uses its ordinary coding-agent loop. The extension adds a short rule: use sandboxed Python for multi-step workflows, host tools are Python functions, variables persist, and only print useful observations. It does not require a task-state schema or criterion evidence before stopping.

Skein's stable prompt is substantially more procedural. It tells the model to make a small change, let the outer workflow verify it, own a bounded model/tool loop, and finish with every field of `AgentStep`, completion claims, criterion IDs, and evidence. Dynamic work packets then carry phase, ledger, criterion, and review state. This can improve auditability, but here it competes with implementation and creates hard transition failures. The output-cap failures are especially damaging with `xhigh`, because a long reasoning response can terminate an entire work batch before another code cell is issued.

## Recommended next experiment

Moving the PTC executor into Pi as an extension is the best next isolation test, but only the executor should move initially. Reuse Skein's existing persistent CPython worker and expose the same four Harbor capabilities under Pi's ordinary loop and prompt. This tests whether the loss comes from the execution substrate or Skein's orchestration without rewriting the whole system.

Run a three-arm comparison:

1. Pi loop + Pi Code Tool (current control).
2. Pi loop + Skein persistent CPython PTC (new minimal extension).
3. Skein loop + Skein PTC (current treatment).

Before running it, enforce the same explicit per-response token cap in both adapters. A 32,768 cap is a practical first choice because it includes every response observed in this run while remaining bounded. Also use the same task timeout and exact `xhigh` mapping. Start with the eight most diagnostic tasks: Obsidian, Koota, Scriggo, Tengo, Textual, Ink, Query, and Testem, with three trials per arm. They cover cap failures, under-iteration, a PTC win, and an equal-quality efficiency win. Expand to the full 20 only if all three arms complete without harness-level failures.

This design gives direct answers:

- If Pi + Skein PTC matches Pi + Pi Code Tool, Skein's outer prompt/workflow is the main problem.
- If Pi + Skein PTC remains near Skein + Skein PTC, the CPython PTC interface or its result contract is the main problem.
- If it lands between them, ablate Skein's structured completion/review boundary next; automatic binding inventory is a lower-priority ablation.

