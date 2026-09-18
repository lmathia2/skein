# E13 six-task code-mode comparison

The Luna/max comparison uses six frozen DeepSWE 1.1 tasks, one attempt per arm, three concurrent Pier jobs per arm, the same task artifact hashes and official verifier images. The clean PTC arm ran from detached Skein commit `80e300d` with `notebook-ptc-jsonl.yaml`; the two Pi arms used the local extensions at their pinned revisions. Scores below come from official Pier verifier rewards, not agent completion claims. The earlier interrupted PTC control is excluded.

| Luna arm | Official passes | Model cost | Cumulative input | Output | Sum active agent time | Median task end-to-end | Three-way run span |
|---|---:|---:|---:|---:|---:|---:|---:|
| `pi-code-tool` (Python/Monty) | **4/6** | $2.716 | 98.1M | 399k | 112.1 min | 18.3 min | 44.8 min |
| `pi-codemode` (TypeScript/QuickJS) | 3/6 | $3.191 | 111.5M | 559k | 154.0 min | 28.8 min | 59.2 min |
| Skein notebook PTC (resident CPython) | 3/6 | **$1.571** | **45.4M** | **335k** | **96.7 min** | **17.6 min** | **38.2 min** |

The sum of active time measures work across six agents; the run span measures elapsed time with concurrency. End-to-end task time includes container setup, collection, and verification. These are single runs, and the PTC run overlapped other provider and Docker work, so latency differences are descriptive rather than a controlled throughput benchmark.

| Task | `pi-code-tool` | `pi-codemode` | PTC |
|---|---:|---:|---:|
| Arcane | pass / $0.542 | pass / $0.790 | pass / **$0.270** |
| Cliffy | fail / $0.364 | fail / $0.683 | fail / $0.328 |
| Dasel | **pass** / $0.198 | pass / $0.333 | fail / $0.185 |
| KaTeX | **pass** / $0.999 | fail / $0.770 | pass / **$0.294** |
| Pest | fail / $0.289 | fail / $0.297 | fail / $0.256 |
| Returns | pass / $0.324 | pass / $0.319 | pass / **$0.238** |

The quality gap is narrow on most tasks. All arms passed Arcane and Returns; all failed Cliffy by one new test (36/37), and Pest remained near completion (PTC and `pi-code-tool`: 101/104 new, 249/250 existing). PTC's Dasel patch passed 144/146 new tests, whereas both Pi arms passed 146/146. `pi-codemode` collected an empty committed KaTeX patch and passed 0/94 new tests; its trace contains successful edits, but its last model response stopped for length before a commit. The official collector uses `git diff` from the task base to `HEAD`, so uncommitted work did not enter the verifier. The other two arms committed KaTeX patches and passed 94/94 new tests.

The code execution surfaces explain much of the measured token and latency spread, without proving that any one feature caused the one-task quality difference:

- Skein PTC exposes one Python notebook tool backed by a resident CPython worker. Across six tasks, 421 submitted cells composed 858 confined file and shell requests; on Arcane, 32 cells made 100 inner requests. The work-batch cap is 48 completed cells, while the live heap persists across batches. This control profile has `context.window_management: false`, so the `work_batch_handoff` plugin is inactive and the model receives no automatic live binding inventory at batch boundaries. On KaTeX, 102 submitted cells made 134 inner requests across seven batch IDs and produced a 20 KB committed patch; `pi-code-tool` used 179 code calls and a 46 KB patch for the same pass. PTC achieved the lowest cumulative tokens and cost, but failed Dasel's last two new tests.
- `pi-code-tool` exposes Python through Monty. Its Session replays prior code to reconstruct variables and caches prior host effects so replay does not repeat file or shell mutations. Variables are reusable, although its prompt does not list live bindings. Across the six tasks it made 665 outer code calls and 1,064 recorded inner host calls. Its short first-turn prefix ranged from about 1.1k to 2.2k cache-write tokens. It achieved the highest official score, but its KaTeX and Arcane explorations were substantially more expensive than PTC's passing paths.
- `pi-codemode` exposes TypeScript in a fresh QuickJS execution context on every `codemode` call, so code-call-local variables do not survive the next call. It discovers the four remote MCP tools and validates TypeScript against generated declarations. Its first-turn prefix ranged from about 3.3k to 4.5k cache-write tokens, and it used 758 outer code calls; 70 of those contained `Promise.all`. Two Arcane calls failed schema validation because the model supplied `strings` without the required `code` argument. Despite parallel calls, it used 13.7% more cumulative input, 40% more output, 17.5% more model spend, and 37% more summed active time than `pi-code-tool`. KaTeX's missing commit is the concrete source of their official quality gap in this run.

The prompts ask the models to work differently. `pi-code-tool` describes four Python helper functions, persistent state, and printing only selected results; its Harbor wrapper disables local Pi tools, so those helpers are the whole route. `pi-codemode` adds a much longer TypeScript/API guide with generated MCP declarations, discovery examples, type-error guidance, `π` string arguments, and `Promise.all` examples. Its upstream normal-mode text also describes native patch tools; the Harbor wrapper appends a correction because only remote `mcp.harbor` tools are available. Skein's stable instruction asks for small code changes and an `AgentStep` with criterion evidence, while the outer workflow owns verification. That profile keeps exact PTC history but disables the optional context handoff plugin. These prompt differences are part of the arms, so this run does not isolate execution language from prompt design.

Trace storage is another material efficiency difference: the six `pi-code-tool` Pi event files total **5.90 GB**, versus **103 MB** for `pi-codemode` and **17 MB** for PTC's event JSONL. In `pi-code-tool` Arcane, full message snapshots recur in `message_start`, `message_end`, `turn_end`, and `agent_end`, each contributing about 239 MB to one 1.22 GB trace. This duplication does not directly imply extra model context tokens, but it increases disk and trace-processing work and could add host-side latency. A future adapter can keep one canonical message body and small references in the repeated events.

The most useful next Skein ablation is a small, read-only binding-name hint at each PTC work-batch boundary, measured against this control for added prompt tokens, repeat reads, and verifier quality. A larger memory subsystem is not needed to test the specific variable-awareness concern. The most useful `pi-codemode` follow-up is to detect a length-stopped model turn with uncommitted changes and give the agent a bounded finalization turn before collection; this run's KaTeX failure otherwise discards a substantial amount of paid work.

Result ledgers: `.artifacts/live-e13-pi-code-tool-six-detached-20260915/runs.jsonl`, `.artifacts/live-e13-pi-codemode-six-detached-concurrent-20260915/runs.jsonl`, and `.artifacts/live-e13-ptc-clean-worktree-v2-20260915/runs.jsonl`. Each result root retains per-task `result.json`, `agent/pi-events.jsonl` or `agent/skein-state/runs/*/events/*.jsonl`, and collected `artifacts/model.patch`.

## Muse Spark 1.3 Contributor on the winning arm

The `pi-code-tool` Muse rerun also scored **4/6**, but with a different pass set: Muse fixed Cliffy (37/37 new tests) and regressed Dasel (139/146), while Arcane, KaTeX, and Returns passed and Pest matched Luna's 101/104 new plus 249/250 existing tests.

| `pi-code-tool` model | Passes | Repriced cost | Cumulative input | Output | Sum active time | Sum end-to-end | Three-way run span | Code calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Luna/max | 4/6 | $2.716 | 98.1M | 399k | 112.1 min | 124.3 min | 44.8 min | 665 |
| Muse Spark 1.3 Contributor/max | 4/6 | **$0.552** | **45.9M** | **333k** | **75.1 min** | **86.5 min** | **31.0 min** | **506** |

Muse kept the same binary quality while using 53% fewer cumulative input tokens, 16.6% fewer output tokens, 24% fewer code calls, 33% less summed active time, and 79.7% less model spend. Every Muse task was faster than its Luna counterpart. This is one attempt per model, and the swapped Cliffy/Dasel outcomes show that equal aggregate quality does not mean equivalent behavior.

The non-binary verifier evidence makes this look like a quality tie, not merely a pass-rate tie. Luna averaged **99.778%** partial credit and Muse **99.711%**, a Muse-minus-Luna difference of **-0.067 percentage points**. Luna passed 618/622 feature tests and Muse 612/622; both passed 2,374/2,375 preservation tests. Four tasks had identical partial scores. Muse gained 0.205 points of partial credit on Cliffy and lost 0.604 points on Dasel. With only six paired tasks and one rollout each, that tiny aggregate difference is not evidence of a real quality ordering.

DeepSWE's own methodology argues for reporting output tokens, wall-clock duration, and cost alongside accuracy, and its qualitative study uses three rollouts per task because trajectories vary. For the next comparison, use **20 diverse tasks × 3 paired rollouts per configuration** (60 trials per arm) as the minimum decision-quality experiment. Twenty tasks with one rollout is useful only as a screening run: at pass rates near 50–70%, its unpaired 95% sampling error is roughly ±20 percentage points. Sixty trials reduces that to roughly ±12 points before benefiting from pairing. Detecting or proving equivalence within about five pass-rate points would require hundreds of trials; the practical decision should instead use paired bootstrap intervals over task-level pass rate and partial credit, plus cost and latency frontiers. A stronger publication-style study would follow DataCurve's qualitative scale of **30 tasks × 3 rollouts** per arm.

Pi resolves this new model through its custom OpenRouter fallback, which inherits a different model's price table. The Muse `cost_usd` fields in Pier artifacts are wrong; the table reprices each trace's uncached input at $0.10/M, cache reads at $0.002/M, and output at $0.20/M using [OpenRouter's published Muse Contributor rates](https://openrouter.ai/meta/muse-spark-1.3-contributor). The model behavior and official verifier results are unaffected by the accounting bug.
