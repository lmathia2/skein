# Pi + Skein PTC v3: stopped-campaign comparison

**Status: 23 verified trials; one Scriggo trial interrupted by the 7,200-second job budget.** No automatic retry was launched. There are no remaining agent/controller processes. This is not a completed 24-trial comparison.

V3 has 19 verified passes, four verified failures and one unknown result: 19/23 among verified trials, or 19/24 scheduled trials with a verified success. Its eventual pass count could have been 19 or 20; do not impute the missing verifier outcome. Code Tool scored 21/24 and v2 18/24.

## Fair comparison and aggregate metrics

All arms use the same eight task artifacts, Muse Spark 1.3 contributor, xhigh and 32,768 output-token reserve. V3 used six concurrent task jobs with three serial trials each, matching v2. The completion checklist was disabled. Contract/prompt/preflight changes are a bundle, so this experiment cannot attribute quality changes to one component. Historical arms are independent samples, not paired random seeds.

| Arm | Verified passes | Mean F2P | Mean P2P | Median agent min | Uncached input M | Cached input M | Output M | Repriced USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Code Tool | 21/24 | 95.50% | 99.992% | 17.75 | 35.51 | 388.88 | 1.98 | $4.726 |
| v2 | 18/24 | 97.52% | 100.000% | 14.96 | 33.63 | 345.09 | 1.93 | $4.439 |
| v3 | 19/23 | 96.48% | 100.000% | 16.63 | 34.45 | 269.13 | 1.89 | $4.362 |

**V3 totals omit the interrupted trial’s tokens, latency and provider usage**, because its adapter never flushed the buffered event stream to disk. Its account billing is included in the account debit below. Therefore the lower raw token/cost totals are not evidence of a cheaper completed campaign.

The seven fully completed tasks provide the cleanest available comparison (21 trials per arm):

| Arm | Passes | Mean F2P | Median min | Uncached input M | Repriced USD |
|---|---:|---:|---:|---:|---:|
| Code Tool | 19/21 | 94.86% | 15.63 | 26.08 | $3.479 |
| v2 | 18/21 | 99.75% | 14.32 | 23.76 | $3.086 |
| v3 | 18/21 | 96.55% | 16.08 | 27.94 | $3.496 |

On these seven tasks, v3 ties v2 on exact passes but lowers mean F2P from 99.75% to 96.55%; uncached input rises 17.6%, repriced cost 13.3%, and median latency 12.3%. Against Code Tool, v3 has one fewer pass, higher mean F2P, similar repriced cost (+0.5%), and slightly higher median latency (+2.8%). These measures disagree because the failures differ in severity. V3 is not an across-the-board efficiency or quality win.

## Per-task outcomes

Each complete entry represents three fresh trials. Scriggo v3 has only two verified outcomes. Latencies are medians of verified trials.

| Task | Code Tool passes | v2 passes | v3 passes | Median seconds: Code / v2 / v3 |
|---|---:|---:|---:|---|
| ink-grid-box-layout | 2/3 | 3/3 | 2/3 | 1261 / 1180 / 1785 |
| koota-pair-relation-tracking | 3/3 | 2/3 | 3/3 | 1364 / 1454 / 1398 |
| obsidian-linter-scoped-ignore-markers | 2/3 | 3/3 | 2/3 | 1281 / 857 / 1068 |
| query-persist-restored-query-state | 3/3 | 3/3 | 3/3 | 616 / 548 / 644 |
| scriggo-method-declarations | 2/3 | 0/3 | 1/2 | 1900 / 2302 / 2264 |
| tengo-destructuring-bindings | 3/3 | 2/3 | 3/3 | 930 / 954 / 904 |
| testem-per-launcher-reports | 3/3 | 2/3 | 3/3 | 782 / 611 / 673 |
| textual-richlog-follow-state | 3/3 | 3/3 | 2/3 | 808 / 830 / 815 |

## Trace and implementation evidence

**Timeout/state contract: demonstrated improvement.** V2 has two hard worker timeouts in its completed traces. V3 has zero hard worker timeouts in its 23 saved traces and one handled shell timeout. Ink `ubUbPeo` runs `FORCE_COLOR=true npx ava 2>&1 | grep -E "✘|failed|passed" | tail -n 20`; GNU timeout reports exit 124, while the Python cell status remains `ok` and the trial eventually passes. The deterministic Docker reproduction additionally confirmed process termination and a surviving sentinel. This supports the timeout fix, not the claim that it causes higher overall reward.

**Shell evidence: delivered, with a backend caveat.** V3 records 1,491 shell calls and supplies 38 diagnostic notices; one notice supplies an exit status absent from selected output. Installed Pier Docker exec redirects stderr to stdout before the adapter sees it. Consequently, historical stdout-only cells are a risk indicator for other backends, not proof that these particular runs hid stderr. V3 still exposes nonzero exit status regardless of the model’s projection.

**Preflight: useful local prevention, little measured effect on model accuracy.** Two structured gate errors are visible: Obsidian `kHdbMoF` tries `write(..., content="PLACEHOLDER")["data"]["output"]`, and Tengo `hzpt5fs` passes `arguments=` to `agent.fs.read`. Both are genuine contract errors. The first is rejected before the placeholder write occurs, a concrete benefit over a runtime KeyError after mutation. No identified false positives in saved traces. Offline replay covered 3,463 v2 execution cells and flagged 16 already-failed cells, mostly syntax errors. This does not justify a new mypy dependency.

**Batching did not materially change.** Static source counting finds multiple direct helper calls in 389/3,463 v2 cells (11.2%) and 341/2,948 saved v3 cells (11.6%). This measures syntactic call sites, not executed loop iterations. The wording change alone did not create a major batching shift.

**Persistent reuse remains limited in the measured straight-line cases.** V3 records 18 cells reading a previous binding before reassignment. The metric intentionally misses functions/branches/aliases; it must not be interpreted as total dynamic reuse. In particular, `b = bash(...); print(b)` is not reuse. Raw v2 overlap counts are not comparable.

**Projection and retrieval:** saved v3 observations total 7.04 MB across 2,948 execution cells, versus v2 8.24 MB across 3,463. Per-cell averages are nearly unchanged (about 2.39 KB versus 2.38 KB). V3 never pages retained records. Its 386 `output_truncated` flags include intentionally omitted diffs/diagnostics, whereas v2’s 62 flags mean truncation; comparing those counts as cap hits would be incorrect. V2 has one retrieval call in this parser’s 3,464 tool-call total. Compact receipts preserve a route to details, but the model does not use that route in v3.

**Compaction is not a proven cause.** Saved v3 traces contain two compaction completions, versus four in v2. Prior Code Tool analysis found compaction in all three Scriggo trials, including two passes. Count differences and occurrence alone do not establish causal damage.

## Remaining failures: what the evidence actually shows

- **Obsidian `kHdbMoF`: 16/33 F2P, 1,133/1,133 P2P.** All 17 failing cases are scoped `no-bare-urls` interactions: named enables/disables, next-line/next-N, nesting and alias normalization. The final response reports 1,177 local tests passing. The patch routes new protection through `Rule.apply` after existing `ignoreListOfTypes`, filters supplied aliases against `rulesDict`, and catches all protection errors by applying the unprotected rule. Those are specific integration boundaries to inspect; the verifier proves named-rule integration fails, but the retained evidence here does not isolate which boundary caused it. Do not label this a stderr or preflight failure. The broad fallback is especially capable of concealing a protection-path error.

- **Ink `Wce9nfw`: 21/25 F2P, 49/49 P2P.** Failures cover column span, explicit column placement, explicit row/column placement, and 2fr/1fr distribution (observed widths/positions differ from expected values). The implementation uses absolute positioning with a two-pass Yoga layout. The final response claims typecheck and existing flex/position tests pass. This points to insufficient interaction tests for new layout arithmetic, not proof of hidden diagnostics. The passing handled-timeout trial is a different Ink trial.

- **Textual `oAviU22`: 19/20 F2P, 6/6 P2P.** The example integration test successfully appends, then times out waiting for positive viewport height. The added example places six buttons vertically plus an eight-line event pane and two `1fr` logs in a 28-row screen. This is direct code evidence for a layout-space problem, rather than a general follow-state failure. Test the example at its required terminal size; the other two v3 trials pass.

- **Scriggo `dtmsWPj`: 44/48 F2P, 1,049/1,049 P2P.** The three substantive failed interface cases return empty strings for error-interface, repeated interface calls, and interface passed to a function; the fourth reported failure is their parent test. The final answer claims `go test ./...` passes and interface support works. The patch adds proxy/method-value dispatch. This resembles v2’s `Fhrskru` empty-string failures and warrants focused interface return-value/register tests. One different v3 trial passes 48/48, demonstrating capability but not consistency. The third trial is unverified, not another known implementation failure.

Koota, Tengo and Testem improve from 2/3 in v2 to 3/3 in v3; Query remains 3/3. The small samples and bundled changes do not identify which prompt/runtime change produced those gains. All four verified v3 failures preserve baseline tests, supporting an interaction-coverage gap rather than broad breakage.

## Budget interruption and cost

The Scriggo controller budget is 7,200 seconds for the entire three-trial task job, not for each trial. The first two agent executions take 2,280.8 and 2,246.3 seconds, plus setup/verification. The last trial was killed when the shared job budget expired (controller return code 124), before a verifier result. Artifact collection then failed because the expected model.patch was absent. This is an evaluation-budget interruption, distinct from a shell timeout inside PTC.

The adapter buffers `process.communicate()` and writes pi-events.jsonl only after normal return, so the interrupted trial has no saved Pi event trace or token accounting. Preserve this as missing data. For a future campaign, stream events incrementally and apply comparable per-trial deadlines in all arms; do not change deadlines mid-run.

Account credit moved from $8.594547312 before launch to $3.728729466 after stop: **$4.865817846 account debit**. This includes interrupted work and any unrelated account usage during the interval. The verified-trial repriced estimate is **$4.361939546**, using $0.10/M uncached input, $0.002/M cache reads and $0.20/M output. Provider-reported trace cost is a different measure and is retained in the metrics JSON; it must not be described as actual debit.

## Recommended next steps

1. Keep the demonstrated timeout/exit-status safeguards and zero-effect signature/key preflight. They fix concrete contract defects without transcript replay.
2. Investigate the four failed patches with focused reproductions at the boundaries above. The next quality treatment should add the already-separated completion/requirement-evidence checklist, emphasizing interactions and runnable example behavior. It is still untested; do not claim it closes the gap.
3. Address per-trial deadlines and incremental event persistence before further campaigns. Recovering this interrupted sample requires a separately recorded fresh trial or an explicitly justified artifact-only verification; no automatic retry was performed.
4. Keep checkpoint recovery/full-file acquisition deferred until needed by evidence. No hard worker discard occurred in saved v3 traces, so checkpointing cannot explain their four verified failures.
5. Re-evaluate the next isolated treatment on a balanced completed manifest. Eight development tasks × three trials cannot establish a general accuracy ranking, and raw totals from 23 vs 24 verified trials cannot establish cost superiority.

## Reproduction and evidence

- Implementation revision: `a4effbf9ec5373a99b3bbaa5a942b225609e8734`. Frozen source hashes and settings: [manifest](e13-pi-skein-v3-manifest.json).
- V3 run ledger: `.artifacts/e13-pi-skein-v3/runs.jsonl`; controller timeout: `controller.log`; final balance: `ending-credit-snapshot.json`.
- Regenerate metrics: `.venv/bin/python scripts/analyze_e13_pi_skein_v3.py`. The collector asserts 24 Code Tool, 24 v2 and 23 v3 verified results.
- [Machine-readable per-trial metrics, trace counts and source rejections](e13-pi-skein-v3-comparison-metrics.json). Every trial includes its exact result path.
- Code Tool detailed historical trace analysis: [comparison](e13-pi-code-vs-pi-skein-deep-dive.md). V2 failure context: [regression analysis](e13-pi-skein-v2-regression-analysis.md).
