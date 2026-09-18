# Pi + Skein PTC v3: completion checklist comparison

All 24 fresh trials finished on September 17, 2026, with no terminal infrastructure errors, missing results or hard worker timeouts. **Keep the checklist optional: this experiment does not justify enabling it by default.** Checklist off passed 9/12; on passed 8/12. On saved uncached tokens and aggregate cost, but lost two Scriggo passes while gaining one Textual pass. These four tasks were selected from earlier failures; three trials each cannot establish a general causal effect or an overall benchmark ranking.

## Configuration and accounting

Four pinned DeepSWE tasks × three trials × two arms, Muse Spark 1.3 Contributor through OpenRouter, xhigh reasoning, 32,768 output limit, identical v3 helpers/preflight/runtime. Checklist-off/on was the only prompt difference. Maximum six concurrent evaluations; zero campaign retries. Pi did retry transient model requests inside existing trials. Per-trial execution budget 6,900 seconds; three-attempt outer watchdog 26,100 seconds. Trace output was saved continuously.

All six runtime/launcher source hashes still match the launch manifest. Metadata records checklist 0 versus 1 and identical model settings. Exact tasks, artifact hashes and verifier images are in [the manifest](e13-pi-skein-v3-checklist-manifest.json). Recompute result and trace metrics with `python3 scripts/analyze_e13_v3_checklist.py`; the script asserts 24 results, three per task/arm, and no terminal exceptions. Raw per-trial results, verifier failures and final model messages are retained in [metrics](e13-pi-skein-v3-checklist-metrics.json).

| Metric | Checklist off | Checklist on | On versus off |
|---|---:|---:|---:|
| Passes | 9/12 | 8/12 | −1 trial |
| Mean F2P fraction | 98.25% | 96.93% | −1.32 percentage points |
| Mean P2P fraction | 100% | 97.55% | −2.45 percentage points |
| Median agent execution | 27.81 min | 21.56 min | −22.5% |
| Median end-to-end trial | 28.69 min | 22.26 min | −22.4% |
| Uncached input | 26.237M | 22.550M | −14.1% |
| Cached input | 204.483M | 218.195M | +6.7% |
| Output | 1.288M | 1.281M | −0.6% |
| Repriced cost | $3.2903 | $2.9476 | −10.4% |
| Repriced cost / successful trial | $0.3656 | $0.3684 | essentially unchanged |

F2P/P2P means weight trials equally; with three trials per task, tasks are equally weighted too. A crash that prevents tests producing results counts against verifier P2P but is not hundreds of independently observed assertion failures.

Account total usage rose from $201.271270534 to $207.554786538: **$6.283516 actual account debit**, leaving $47.445213462. This is account-wide, not individually attributed by arm. Token repricing totals $6.237906 at $0.10/M uncached, $0.002/M cached and $0.20/M output. Provider-reported trace costs total $124.253183 and must not be presented as actual billing. Observed debit stayed within the $5–8 estimate.

## Task-level results

| Task | Passes off → on | Mean F2P off → on | Median end-to-end off → on | Repriced cost off → on |
|---|---|---|---|---|
| Ink | 1/3 → 1/3 | 94.67% → 94.67% | 30.35 → 26.77 min | $0.878 → $0.536 |
| Obsidian | 3/3 → 3/3 | 100% → 100% | 22.34 → 17.33 min | $0.692 → $0.412 |
| Scriggo | 3/3 → 1/3 | 100% → 93.06% | 36.66 → 44.82 min | $1.140 → $1.600 |
| Textual | 2/3 → 3/3 | 98.33% → 100% | 16.62 → 17.06 min | $0.580 → $0.399 |

### Ink: numeric semantics, not absence of manual checking

Off DXdSmmH and WoBhZhF and on JLi5e95 all fail fixed-maximum `minmax` (width 5 instead of 15) and row `minmax`. On zTUm5bZ fails automatic track sizing (14 instead of 5) and fixed-maximum `minmax` (18 instead of 15). All retain 49/49 existing tests. Neither arm eliminated the same numeric boundary errors.

The traces contain manual checks and final claims that `minmax` was verified; a generic reminder to check requirements did not turn those into correct numeric assertions. Passing on rayx9wM explicitly exercises `minmax(2, 5) 3` and adjusts fixed-maximum column and row base allocation in `src/grid.ts`. This links the outcome to track sizing behavior, not merely to whether tests were run. It does not prove the checklist caused the successful implementation: one off trial also passed.

### Obsidian: less work, same verified quality

Both arms pass every new and existing test. On uses 233 cells versus 339 off, 15.5M cache tokens versus 35.3M, and 197K output versus 283K. This is consistent with a shorter implementation/debugging path rather than a batching improvement: multi-helper cells are 18 versus 20. Off had two network interruptions in eX42nFy, while on had one in ZSqpJNE. Consequently the wall-clock difference includes provider effects. No evidence here that scoped-ignore correctness specifically requires the checklist.

### Scriggo: explicit noncompliance plus insufficient behavioral assertions

Off passes all three trials. On fails two distinct contracts:

* **9aH2ysZ:** five method-expression cases panic on `reflect.Value.Interface on zero Value`, plus their parent suite. These cover expressions assigned to variables, parameters, pointer receiver expressions, return values and mixed value/pointer methods. An existing template test also panics: two explicit failure entries plus 306 missing test results explain the 741/1,049 P2P score. The final model response explicitly says `Known gaps: f := T.M; f(x) as variable + pointer-interface runtime dispatch still panic with zero-Value`. That directly violates the enabled completion component. This was a declared unfinished solution, not a concealed gap.
* **ggBn2jZ:** three interface-return cases emit empty strings: `error: \n` rather than `error: something failed\n`, `\n\n` rather than `first\nsecond\n`, and `label:\n` rather than `label:hello\n`; their parent suite makes four failed F2P entries. All existing tests pass. The submitted `internal/runtime/methods.go` creates a bound `reflect.MakeFunc`, runs a new VM, and marshals registers back into return values. That adapter is the relevant implementation boundary to investigate; an exact register-allocation root cause still needs a counterfactual fix/retest. The final message claims interface dispatch works and all `go test ./...` pass. Existing-suite success did not establish newly requested return-value behavior.

On Scriggo performs **1,053 cells and 670 shell calls**, versus **820 and 439 off**. It uses 12.24M versus 8.70M uncached tokens and costs 40% more. Thus “the checklist saves tokens” is not consistent across tasks. All three successful off Scriggo trials compact once; failing on 9aH2ysZ compacts twice, failed ggBn2jZ once, and successful on TZwqMuH once. Compaction alone does not separate success from failure. The known-gap stopping defect persists even with explicit prompt wording.

### Textual: example layout, not the follow-state core

Off rx4S7E3 fails only the example integration check: after append actions, every log must have a positive viewport. Its patch gives three logs `height: 1fr` but yields controls vertically. Passing on Fw2gcg2 groups buttons into two `Horizontal` containers with `height: auto`; the saved patch provides a concrete layout difference consistent with the viewport failure. The remaining two off trials and all on trials pass. A one-change reproduction would be needed to call that layout difference a proven causal fix.

On performs more cells (474 versus 395) and shell calls (300 versus 209) yet uses fewer uncached tokens and slightly more cached tokens. More tool calls need not imply more billed input. Median latency is slightly worse on, not better. This is the strongest task-level quality improvement but only one additional passing trial.

## Runtime and prompt evidence

The optional component reads: “Before finishing, check requirements against evidence, including interactions/negative cases. Do not finalize with required behavior still a known gap or delete valid failing probes to get green tests. Distinguish baseline failures; preserve unresolved gaps and exact failing commands across compaction.” It is appended to the code-tool description; it is advice, not an executable completion gate.

| Trace metric | Off | On |
|---|---:|---:|
| Code cells | 2,008 | 2,123 |
| Cells with multiple syntactic helper calls | 145 (7.2%) | 122 (5.7%) |
| Prior bindings read before assignment | 33 (1.6%) | 46 (2.2%) |
| Shell calls | 1,003 | 1,282 |
| Source-validation rejections | 21 | 25 |
| Cell errors | 30 | 46 |
| Shell timeouts | 1 | 1 |
| Hard worker timeouts | 0 | 0 |
| Operational diagnostic notices | 29 | 45 |
| Model-visible result text | 4.55 MB | 4.16 MB |
| Output-truncation flags | 381 | 338 |
| Retained-result paging calls | 0 | 0 |
| Compactions | 3 | 4 |
| Model request errors | 8 | 3 |

Batching counts are static syntax counts, not the number of calls executed by loops. Binding-use counts are conservative worker telemetry, not semantic value-reuse measurements. Truncation flags can reflect paged helper results; they do not establish that a decisive test failure was hidden. Model-visible bytes are tool observations, not full transcript tokens.

Both arms retain the v3 contract fixes: shell operational notices are surfaced, blocking preflight catches invalid cells before effects, and the two shell timeouts did not cause hard worker loss. Since both arms use the same runtime, none of their quality difference can be attributed to a runtime change made for this experiment. There were six upstream 429s and two network errors off, versus two 429s and one network error on. Raw latency includes these asymmetric delays. Do not label the 22% median difference as an isolated prompt speedup; no trustworthy provider-wait-adjusted estimate is established here.

## Recommended next steps

1. **Keep the base v3 prompt and checklist arm separate.** No default promotion: the off arm has better observed quality, and cost per success is essentially equal. Three trials on four failure-selected tasks are insufficient for significance claims.
2. **Change the evidence requirement, not simply prompt length.** Ask for exact expected-versus-actual output for new behavior: numeric grid allocation, interface return strings, and example geometry. Existing-suite success and informal “manual checks” are inadequate. Use general requirement-level probes, not these held-out verifier cases in the agent prompt.
3. **Investigate termination before adding more reminders.** 9aH2ysZ obeyed neither “no known gaps” nor complete method-expression semantics. A bounded finalization review with explicit unresolved requirements is a candidate separate experiment; it should not silently change the completed arm or be represented as already validated.
4. **Prioritize the shared VM return-value adapter in Scriggo diagnosis.** Reproduce ggBn2jZ's empty outputs against a passing patch and validate any register-marshalling hypothesis with a minimal failing program. Keep that task-solution investigation distinct from changes to the PTC execution contract.
5. **Measure provider waiting and check result access in the next run.** Record request-level start/end/retry delays for useful latency decomposition. Zero retained-result retrievals deserve a targeted usability probe; first establish whether omitted evidence matters before changing caps or introducing transcript replay.

No additional paid experiment was launched. The 24-trial run is complete; future broader validation should use the separate diverse-task manifest and hold model/runtime settings fixed.
