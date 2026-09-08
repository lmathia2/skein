# PTC six-task trace comparison and Pareto plan

> Evidence date: 2026-09-07  
> Model: `meta/muse-spark-1.3-contributor`  
> Status: one-attempt diagnosis and staged experiment plan; not a promotion result

Implementation update: incremental bounded history, criterion-gap review/stopping,
phase-aware tunable batching, batching telemetry, and complete PTC validation receipt
reuse are implemented on `main`. Conditional pristine baselines remain pending because
the evaluation runtime does not yet provide an isolated workspace with the task's
installed dependencies. The next evidence is one combined six-task run rather than an
ablation after every slice.

## Scope and method

This report compares the same six DeepSWE tasks across three harness setups:

- corrected four-tool Skein:
  `/Users/mathiasl/skein-eval-results/skein-muse-spark-1.3-contributor-deepswe-remaining-6-trace-v3-20260907`
- notebook PTC with canonical JSONL, v4:
  `/Users/mathiasl/skein-eval-results/skein-muse-spark-1.3-contributor-deepswe-remaining-6-ptc-jsonl-v4-20260907`
- the matching six-task subset of the eight-task mini-swe-agent run:
  `/Users/mathiasl/skein-eval-results/mini-swe-agent-muse-spark-1.3-contributor-deepswe-8-sequential-20260905-uncapped`

`boa` and `helm` are excluded from mini-swe totals. All retained tasks used one
attempt, no harness retry, sequential execution, and the same model. Official
quality comes from each trial's `verifier/reward.json`. Skein resource and timing
metrics come from `evaluation/result.json` and `metrics.db`; its action sequence
comes from ADK session events and task events. Mini-swe metrics come from its trial
`result.json` and `mini-swe-agent.trajectory.json`.

Mini-swe model time is estimated by summing provider response timestamp deltas; it
is not the same native timer used by Skein. Counts of test-bearing model actions are
a deterministic keyword classification of recorded shell commands, so they are
diagnostic rather than a contractual metric. This is a paired trace diagnosis over
six single samples, not a variance-controlled quality conclusion.

## Aggregate result

| Measure | Four-tool Skein | PTC v4 | mini-swe, same six |
| --- | ---: | ---: | ---: |
| Official reward | **4/6** | 2/6 | 2/6 |
| Model calls | 479 | 429 | **409** |
| Harness/model-visible tool calls | 533 | 442 | **409** |
| Input tokens | **29.145M** | 43.169M | 40.462M |
| Uncached input tokens | **0.926M** | 1.655M | 4.211M |
| Cache-read ratio | **96.82%** | 96.17% | 89.59% |
| Output tokens | 305k | **301k** | 315k |
| Provider cost | **$0.210** | $0.309 | $0.557 |
| Agent wall time | 5,180 s | 4,921 s | **3,471 s** |
| Model time | 3,108 s | 2,994 s | about 2,848 s |
| Maximum per-task context | 174k | 228k | 261k |
| Cost per official pass | **$0.053** | $0.154 | $0.278 |
| Calls per official pass | **120** | 215 | 205 |

PTC reduced model calls by 10.4% and wall time by 5.0% relative to four-tool
Skein, but quality halved while input rose 48.1% and cost rose 47.0%. Relative to
mini-swe, PTC had the same reward and 4.9% more calls. Its better cache reuse made
it 44.5% cheaper, but it was 41.7% slower.

Strictly, no single setup dominates every other setup: Skein owns quality and cost,
mini-swe owns latency, and PTC trades between them. PTC is not at the desired corner
of the frontier because it improves neither baseline's strongest dimension.

## What the harness traces have in common

All three setups:

- found the correct production area on every task;
- produced substantive patches rather than terminating on a harness failure;
- preserved all pre-existing tests except mini-swe on Wazero;
- worked against the same task repositories with the same model and relied
  primarily on shell-driven inspection and validation;
- spent most wall time in model generation and commands/tests, not orchestration;
- failed on behavioral edge conditions, not syntax or basic repository discovery.

The changed production paths substantially overlap on Kombu, Ofetch, Testem,
Textual, and Wazero. Koota has the widest valid design surface and therefore the
largest patch-set variation.

## Where the harness setups differ

| Concern | Four-tool Skein | PTC v4 | mini-swe |
| --- | --- | --- | --- |
| Model surface | `read`, `bash`, `edit`, `write` | one persistent `python` tool | one `bash` tool |
| History behavior | bounded work packet; outer work batches do not load prior ADK contents | full ADK contents retained; no compaction occurred | append-oriented trajectory |
| Underlying actions | 510 model-requested direct capabilities; 23 additional verifier records | 413 Python cells containing 563 broker capabilities; 29 additional verifier records | 409 Bash actions |
| Verification | baseline plus deterministic in-harness verification | same deterministic verifier and baseline | model-selected checks only; official verifier runs afterward |
| Durable evidence | task JSONL, receipts, checkpoints, metrics, ADK session | those plus canonical JSONL, cell/capability lifecycle, notebook, snapshot | trajectory and final artifacts |
| Internal terminal state | 3 complete, 3 blocked | 5 complete, 1 blocked | no equivalent completion gate |

The event counts themselves are not comparable: PTC records the submitted,
materialized, executed, and terminal state of every cell and each nested capability.
That produced 2,968 task events versus 189 four-tool task events, but the richer
trace was not the principal latency cost.

### PTC composition did not yet produce enough batching

PTC submitted 413 cells and invoked 563 broker capabilities:

| Cell shape | Count | Share |
| --- | ---: | ---: |
| One capability | 309 | 74.8% |
| Two or more capabilities | 97 | 23.5% |
| No capability | 7 | 1.7% |

The nested operations were 222 shell runs, 184 reads, 129 edits, and 28 writes.
Thus the one-tool surface reduced model-visible tool calls, but it did not reduce
underlying operations: PTC performed about 10% more broker operations than the 510
direct model actions in four-tool Skein. Most cells still reproduced an
action-observation-model rhythm.

### PTC retained too much inner history

PTC returned fewer model-visible tool-result bytes than four-tool Skein
(1.649 MB versus 1.755 MB), yet consumed 48% more input. Its mean request context
was larger than four-tool Skein on all six tasks and larger than mini-swe on five:

| Task | Four-tool mean | PTC mean | mini-swe mean |
| --- | ---: | ---: | ---: |
| Kombu | 46.5k | **113.5k** | 65.5k |
| Koota | 96.8k | 137.4k | **152.7k** |
| Ofetch | 26.9k | **81.2k** | 45.8k |
| Testem | 57.6k | **91.1k** | 85.8k |
| Textual | 48.5k | **90.5k** | 56.4k |
| Wazero | 23.9k | **51.4k** | 29.9k |

For every PTC task, the final request was also its largest request, at 81k-228k
tokens. The run recorded zero compactions. The cost problem is therefore repeated
retention of old Python call/response pairs, not excessive selected output from an
individual cell.

### Trace and notebook persistence are no longer the bottleneck

PTC wall time decomposes as follows:

| Component | Six-task time | Share of PTC wall |
| --- | ---: | ---: |
| Model generation | 2,994 s | 60.8% |
| Python tool calls | 1,118 s | 22.7% |
| Internal baseline and verification | 704 s | 14.3% |
| Other orchestration | about 105 s | 2.1% |

Nested capabilities account for 1,075 of the 1,118 Python-tool seconds. Python,
cell framing, and the remaining notebook work account for only about 43 seconds,
or 104 ms per cell. Separately measured late notebook materialization was 20-49 ms
on average after the v4 read-repair fix. Asynchronous ledger or notebook writes
cannot recover the 1,449-second gap to mini-swe and would weaken durability.

Skein's deterministic safety checks are material but useful. PTC spent about 474
seconds establishing cold baselines, including a 300-second Textual timeout. That
cost prevented unverifiable no-regression claims; mini-swe skipped the equivalent
gate and its Wazero patch missed both pre-existing integration tests.

## Task-grouped trace findings

Triples below are ordered **four-tool / PTC / mini-swe**.

### Kombu

- Quality: 67/76, 73/76, and 75/76 new tests; all preserved 1412/1412 old tests.
- Calls and wall: `109 / 98 / 65` calls and `659 / 910 / 477` seconds.
- PTC used 94 cells for 102 capabilities, only 1.09 capabilities per cell, and 11
  test-bearing shell actions.
- PTC added a large self-authored test file and changed +959/-14 lines versus
  +571/-11 for four-tool Skein and +594/-11 for mini-swe.
- PTC implemented more of the task than four-tool Skein, but its completion review
  missed all three `x-death`/first-death header cases. Mini-swe missed only queue TTL
  persistence. The remaining defect was explicit task semantics, not missing code
  location or unavailable tests.

### Koota

- Quality: 51/51, 50/51, and 51/51 new tests; all old tests passed.
- Calls and wall: `143 / 92 / 162` calls and `1,245 / 1,035 / 1,270` seconds.
- PTC was the fastest harness and almost matched four-tool cost ($0.068 versus
  $0.067), with 1.46 capabilities per cell.
- PTC ran only four test-bearing model actions versus 13 and 10. Its single miss was
  the negative-history case where `Removed` must not match an entity that never had
  all aspect constituents.
- This is the clearest near-Pareto PTC case: compact implementation and batching
  worked, but the final counterexample review did not cover one stated boundary.

### Ofetch

- All three harnesses passed 47/47 new and 13/13 old tests.
- Calls and wall: `37 / 74 / 43` calls and `593 / 746 / 467` seconds.
- PTC ran 15 test-bearing model actions versus two and five, and changed tests plus
  `.gitignore`; the two baselines changed only `src/fetch.ts` and `src/types.ts`.
- PTC's patch was +752/-108 lines versus +317/-106 and +320/-63. This is the main
  over-execution case: quality was available much earlier, but test and patch churn
  doubled calls and tripled mean context relative to four-tool Skein.

### Testem

- Quality: 90/90, 86/90, and 83/90 new tests; all preserved 489/489 old tests.
- Calls and wall: `102 / 54 / 76` calls and `1,419 / 900 / 493` seconds.
- PTC achieved the highest batching rate, 2.16 capabilities per cell, and cut output
  to 33k tokens. This proves PTC composition can reduce calls materially.
- PTC still missed propagation of `testsRanBeforeBail` into the app error, reporter
  query, dot summary, and TAP summary. Mini-swe also missed reset and suppression
  behavior; PTC covered those additional cases but not the central count path.
- The cold baseline and final baseline-relative test consumed about 241 seconds.
  Four-tool Skein spent more time and code to cover the full reporting path.

### Textual

- Quality: 22/23, 22/23, and 15/23 new tests; all preserved 57/57 old tests.
- Calls and wall: `51 / 71 / 41` calls and `821 / 832 / 425` seconds.
- Four-tool Skein and PTC missed the identical requirement: shifted alternate-key
  data must remain available for shortcut matching. PTC additionally modified
  `_kitty_keys.py` without resolving that edge.
- PTC was neither faster nor cheaper than four-tool Skein. A 300-second cold baseline
  timeout accounts for much of both Skein harnesses' latency disadvantage to mini.
- Mini-swe was fast but lost seven more printable/modified-key behaviors, showing why
  removing no-regression verification is not an acceptable latency optimization.

### Wazero

- Four-tool Skein and PTC passed 78/78 new plus 2/2 old tests. Mini-swe passed all new
  tests but produced no passing result for either old integration test.
- Calls and wall: `37 / 40 / 22` calls and `443 / 497 / 339` seconds.
- PTC cost less than mini-swe ($0.024 versus $0.028) but was slower and slightly behind
  four-tool Skein. It used 1.11 capabilities per cell.
- Later generic batching-pressure repeats reduced PTC to 26 and 32 calls, but both
  scored zero after their old integration-test package failed to build, so the two
  tests never ran. The runs do not isolate batching as the cause. Read-only
  exploration and already-selected validation remain the conservative batching
  targets.

## Cross-task diagnosis

1. **The largest PTC tax is history replay.** Smaller selected output did not prevent
   monotonic 81k-228k requests. This is the first treatment because it can improve
   cost and model latency without changing the model, tools, or authority.
2. **One Python tool is not automatically fewer actions.** Three quarters of cells
   invoked exactly one capability. Python needs phase-aware composition, not stronger
   generic pressure to do more per cell.
3. **PTC's misses are lost explicit requirements.** Koota, Testem, Textual, and
   Kombu failed named edge conditions from the task. Five internal PTC completions
   produced only two official passes, so the existing generic counterexample pass is
   not sufficiently criterion-focused.
4. **More tests are not automatically better.** PTC over-tested Ofetch and wrote a
   large Kombu test file while missing explicit semantics. Test selection and a
   requirement-to-evidence map matter more than raw test count.
5. **Verification is buying real quality but is poorly scheduled.** Mini's fastest
   result also contains the only missing old-test results. Skein must retain deterministic
   verification while avoiding unconditional slow baselines and duplicate PTC/verifier
   executions.
6. **Persistence optimization is complete enough for this gate.** The remaining
   wrapper cost is under one percent of wall time. Do not weaken write-ahead events,
   fsync, receipts, or notebook snapshots for marginal savings.

## Desired Pareto envelope

The current best observed coordinates are 4/6 quality and $0.210 cost from
four-tool Skein, and 3,471 seconds from mini-swe. A final PTC candidate should meet
all of these in the same controlled run rather than winning separate metrics on
different runs.

| Promotion dimension | Development gate | Final confirmation gate |
| --- | ---: | ---: |
| Official reward | at least 4/6 | median at least 5/6; neither repeat below 4/6 |
| Old-test regressions | zero | zero in both repeats |
| Total provider cost | at most $0.210 | below $0.210 median |
| Agent wall time | at most 3,471 s | below 3,471 s median |
| Model calls | at most 409 | below 409 median; target 350 |
| Input tokens | at most 29.1M | below 29.1M median; target 25M |
| Uncached input | at most 0.926M | below 0.926M median |
| Maximum request context | at most 64k | at most 64k each task |
| Effect/replay safety | no unknown or duplicate effects | same in both repeats |

Five passes makes quality strictly better than the observed Skein baseline. The
development gate permits a 4/6 intermediate candidate so treatments can be composed
without accepting a quality regression.

## Staged PTC-only improvement plan

Each treatment changes one independent variable. Four-tool Skein stays frozen as
the quality/cost control and mini-swe stays frozen as the latency control.

### P0: finish request-level measurement before changing behavior

Capture the actual serialized provider request size and content hash for each PTC
model call, split into stable prefix, current work packet, recent exact pairs, older
retained pairs, and latest tool result. Record PTC phase, cell capability count,
selected egress bytes, and actual versus cached validation wall time.

This completes the existing provider-request-capture TODO and proves whether the
observed token growth is retained ADK history rather than nested values or the live
heap. Store hashes and bounded/redacted diagnostics, not unredacted prompts.

Gate: reported region totals reconcile with provider input within a documented
tokenizer tolerance, and enabling measurement does not change provider request
bytes or deterministic tests.

### P1: keep deterministic history windows experimental for PTC

Do not enable this in the standard PTC profiles yet. A live Kombu screen on
2026-09-07 reached 106 model calls, 28 compactions, and no edit while batching
2.05 capabilities per completed cell. The matching unwindowed configuration had
completed useful work, so the screen failed on call count and progress before a
quality comparison was warranted.

Retain the opt-in context experiment for later tuning:

```yaml
memory:
  enabled: true
  context_programs:
    mode: off
  working_notes: false
context:
  window_management: true
  reconstruction: handoff_tail
```

This reuses `ContextWindowPlugin`; it adds no model summarizer, retrieval, semantic
index, or new tool. It keeps the latest complete Python call/response interaction,
publishes an explicit context epoch, and replaces older history with the deterministic
ledger handoff. Do not enable the broad `context-ptc.yaml` bundle.

If revisited, first widen the recent exact tail and screen Kombu, Ofetch, and Textual.
Continue only if input falls at least 30%, call count does not rise, the latest tool
result remains exact, and no task loses old tests. The six-task target remains at
most 25M input and 64k peak context with no loss of the PTC v4 passes.

### P2: turn the existing counterexample iteration into a criterion-gap audit

Do not add a reviewer agent or another standing model call. Reuse the one review
iteration the workflow already schedules. Its bounded packet should put these items
in order:

1. the complete original requirement;
2. one row per explicit behavior with implementation and test evidence;
3. the current changed-file/diff summary;
4. the latest validation result;
5. unresolved or weakly evidenced rows.

The model should try to falsify only the weak rows, then update the evidence map.
The map is advisory; deterministic tests and the normal verifier remain completion
authority. This directly targets the four PTC misses: first-death headers, negative
Removed history, bail-count propagation, and shifted alternate-key metadata.

Run Kombu, Koota, Testem, and Textual as the quality screen. Promote only if PTC
recovers both Koota and Testem or produces another 4/6 combination without losing
Ofetch/Wazero or any old tests.

### P3: let deterministic verification reuse exact PTC test receipts

When `agent.shell.run` executes a command classified as build or test, append a
bounded validation-observation event containing the exact command hash, result,
workspace-before/after fingerprints, and capability receipt ID. At verification,
reuse it only when command, environment, and current workspace identity match.

This uses the existing ledger and broker; it must not create another cache or trust
notebook output. Failed, timed-out, blocked, mutated-workspace, and effect-unknown
commands remain non-reusable unless the existing baseline-relative rules explicitly
handle them.

Screen Ofetch and Testem. Gate on identical verification reports and official
rewards with fewer executed validation seconds. This should remove exact model/
verifier test duplication without weakening the completion contract.

### P4: batch only independent discovery and already-decided checks

Keep Python as the composition layer; do not add a batch tool. Adjust the PTC
instruction so one cell groups:

- independent bounded reads or searches whose paths are already known;
- formatter, type, and targeted-test commands whose selection is already decided;
- local deterministic projections that print only a compact result.

Do not encourage batching speculative edits, unrelated repairs, or completion
decisions. The Wazero repeats show that generic call-count pressure sacrifices
regression reasoning.

Screen Koota and Wazero, then the full set. Target at least 1.6 capabilities per
cell and at most 350 model calls without losing any P1/P2 pass. Call reduction is a
secondary gate; quality remains primary.

### P5: make no-regression baselines conditional and reusable

The cold baseline cost was about 474 seconds, dominated by Textual's unused
300-second timeout. Preserve baseline-relative verification but avoid paying for a
full baseline that final validation never needs:

- reuse a prior baseline only when base revision, command, dependency/lock state,
  execution image, and relevant tool configuration all match;
- on a cold task, run the modified-workspace validation first and request a pristine
  base comparison only when that command fails or times out;
- execute a required cold comparison in an isolated pristine worktree so the active
  workspace is never reset or raced.

This is a separate treatment because it changes validation scheduling. Testem is the
failure-relative safety case; Textual is the latency case; Wazero is the regression
case. Gate on byte-equivalent baseline evidence and zero old-test regressions.

### P6: combine only promoted treatments and confirm twice

Run the same six tasks sequentially with the contributor model, one attempt per task,
zero harness retries, and the frozen provider defaults. Then repeat once. Report
paired task-level reward, cost, wall/model/tool/verification time, model/capability
calls, context-region tokens, compaction epochs, cache ratio, validation reuse,
terminal reason, and effect reconciliation.

Do not promote PTC by averaging away a lost Wazero regression or by comparing its
six tasks with mini-swe's eight-task total. PTC becomes the default only after the
final confirmation gate above is met.

## 7. First combined live run (v7)

Commit `25babd1` ran the same six tasks with Muse Spark 1.3 Contributor,
provider defaults, one attempt, and zero retries. An earlier screen was stopped
after bounded history caused Kombu to reach 106 calls and 28 compactions without
an edit; v7 kept exact history and retained P2-P4.

| Measure | PTC v4 | PTC v7 | mini-swe, matching six |
| --- | ---: | ---: | ---: |
| Official passes | 2/6 | 2/6 | 2/6 |
| Agent wall time | 4,926 s | 5,409 s | 3,471 s |
| Model calls | 429 | 542 | 409 |
| Input tokens | 43.2M | 63.6M | 40.5M |
| Uncached input tokens | 1.66M | 1.47M | 4.21M |
| Output tokens | 301k | 292k | 315k |
| Provider cost | $0.309 | $0.330 | $0.557 |
| Peak context | 228k | 307k | 261k |

The pass sets changed: v4 passed Ofetch and Wazero; v7 passed Koota and Ofetch;
mini-swe passed Koota and Ofetch. Wazero's v7 verifier failed before any test ran
because the Go assembler segfaulted while obtaining a build ID. Its 0/80 result is
an evaluator failure, not evidence that the produced patch regressed. Koota's new
official pass is real, but one unseeded sample cannot attribute it to P2 or P4.

| Task | v4 calls | v7 calls | Delta | v7 reward |
| --- | ---: | ---: | ---: | ---: |
| Kombu | 98 | 67 | -31 | 0 |
| Koota | 92 | 221 | +129 | 1 |
| Ofetch | 74 | 49 | -25 | 1 |
| Testem | 54 | 53 | -1 | 0 |
| Textual | 71 | 112 | +41 | 0 |
| Wazero | 40 | 40 | 0 | 0 (verifier infrastructure) |

P4 did not meet its promotion gate: 577 capabilities across 520 completed cells
is 1.11 capabilities per cell, and 542 calls exceeds both v4 and the 350-call
target. It helped bounded discovery on Kombu and Ofetch, but dependent edit/test
repair on Koota and Textual dominated the total. Keep the instruction as a
learnable policy, not as evidence of a global speedup.

P3 recorded 22 complete validation receipts and reused eight exact command,
environment, and workspace matches. Total deterministic validation time was
essentially unchanged (203 s in both runs) because the expensive checks did not
match and common `pnpm -F ... test` / `npm run test` forms were not recognized.
The command classifier now covers those forms. No verifier rule was weakened.

The combined treatment is therefore not promoted as a latency win. Exact history
stays enabled in the standard PTC profile, bounded history stays experimental, and
the next optimization should target runaway dependent edit/test repair without
generic batching pressure.

## Expected order of impact

1. **P2 criterion audit:** highest quality leverage without an additional review
   call.
2. **P3 receipt reuse:** safe removal of duplicated test execution where commands match.
3. **P4 phase-aware composition:** retain as a tunable policy; v7 did not promote it.
4. **P5 conditional baseline:** closes the remaining latency gap without adopting
   mini-swe's regression risk.
5. **P1 history window:** experimental only until a wider exact tail avoids the
   observed exploration loop.

Further trace/notebook storage optimization is not on the critical path.

## 8. Focused work-batch screen (v9)

Commit `55ec40d` screened Koota and Ofetch with the same Contributor model,
provider defaults, one attempt, and zero retries. A preceding v8 attempt exposed
and fixed an ADK state-propagation error: the workflow now identifies synthetic
batch yields from the authoritative event stream. V9 proved two 48-cell Koota
boundaries resume through the same durable notebook without becoming blockers or
counting synthetic responses as provider calls.

| Task | v7 reward/calls/input/cost/wall | v9 reward/calls/input/cost/wall |
| --- | --- | --- |
| Koota | 1 / 221 / 38.85M / $0.157 / 2,253 s | 0 / 104 / 13.06M / $0.074 / 925 s |
| Ofetch | 1 / 49 / 2.45M / $0.018 / 476 s | 1 / 59 / 3.58M / $0.027 / 852 s |

Koota recovered about 53% of calls, 66% of input tokens, 53% of cost, and 59%
of wall time, but failed one of 51 new tests: `Removed` incorrectly matched an
entity that had never held all aspect constituents. All 172 existing tests passed
and the partial score was 0.9955. Ofetch reached a verified harness completion
instead of v7's false-negative block, but used ten more calls and had a much slower
provider sample. The two-task quality gate therefore did not promote this treatment.

Metadata-only traces captured hashed canonical ADK request regions without prompt
text. The final Koota request was 712,610 bytes, including 699,636 bytes of retained
history; Ofetch was 358,552 and 340,792 bytes respectively. This confirms trace
writes are not the growth source. Commit `60cdced` subsequently makes every forced
boundary a review phase and adds the generic temporal negative-history check that
the Koota review missed; it has deterministic coverage but was not re-run live to
avoid turning a two-task screen into another costly ablation.
