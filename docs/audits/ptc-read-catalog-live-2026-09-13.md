# PTC automatic read-catalog live audit

Status: complete, negative quality result with positive mechanism evidence. No retry or
replacement trial was run.

## Frozen run

- Revision: `8293c64836eb55176f50d0acef45dc02d926fc93`
- DeepSWE 1.1 tasks: Kombu, Koota, Ofetch, Testem, Textual, and Wazero from
  `evaluation-ablation-v1.json` (`ae5b84f0...5153563`).
- OpenRouter `meta/muse-spark-1.3-contributor`, reasoning `xhigh`, concurrency six.
- One attempt, no retries, 2,000,000 task-input tokens, 16,384 output tokens, and
  7,200-second Harbor timeout per task.
- Treatment results: `/Users/mathiasl/skein-eval-results/memory-read-catalog-auto-6-20260913`.
- Immediate pre-fix comparison:
  `/Users/mathiasl/skein-eval-results/memory-read-catalog-6-20260913`.

## Result

| Aggregate | Before | Automatic catalog | Change |
| --- | ---: | ---: | ---: |
| Official passes | 1/6 | 0/6 | -1 |
| Model calls | 192 | 210 | +9.4% |
| Input tokens | 9.79M | 10.88M | +11.1% |
| Uncached input tokens | 513k | 526k | +2.6% |
| Output tokens | 205k | 192k | -6.3% |
| Cost | $0.110735 | $0.111644 | +0.8% |
| Active wall | 1,931s | 1,876s | -2.8% |
| Logical read receipts | 191 | 126 | -34.0% |
| Logical selected lines | 28,803 | 20,394 | -29.2% |
| Newly selected source lines | 28,803 | 15,523 | -46.1% |

The aggregate read-count comparison includes different model trajectories and therefore
does not by itself establish a causal savings rate. The treatment's own receipts do:
42 reads used current-version catalog coverage and reused 4,871 lines while selecting
15,523 new lines. They also recorded 42 one-line identity probes. The probe invokes the
current read implementation, which hashes the full file; these counters describe selected
source lines and model-facing content, not filesystem bytes read.

Five tasks reused every same-version interval overlap observed after an earlier completed
read: Kombu 2,182 lines, Koota 1,413, Testem 828, Textual 407, and Wazero 41. Ofetch
reused zero of 1,769 overlapping lines. Its trace kept one worker epoch, but submitted
absolute paths such as `/app/src/fetch.ts`; completed read evidence normalized these to
`src/fetch.ts`. The broker's pre-probe lookup compared the request string to the normalized
catalog key and therefore bypassed the catalog. Normalize the requested path through the
existing workspace boundary before lookup, then rerun a bounded Ofetch canary.

## Quality and memory uptake

All six Harbor jobs and verifiers completed. Five Skein runs stopped at the task-input
budget; Koota stopped on provider `max_output_tokens`. No treatment task produced a
model patch. Wazero regressed from the pre-fix run's 78/78 feature-to-pass and 2/2
pass-to-pass checks to 0/78 and 2/2. The other five verifier outcomes were identical to
the pre-fix cohort. One sample per task cannot attribute the Wazero trajectory to catalog
reuse, but the cohort fails the quality gate.

No context compaction occurred: peak contexts stayed far below the configured 80% of the
1,048,576-token window. Two working-note updates were written. Testem contained only an
unsupported hypothesis and next action with empty evidence references; Textual's note had
no entries. Submitted cells used neither `agent.state.cite` nor `read_handle`. Thus this
run demonstrates automatic PTC range reuse, not reliable learned-memory use.

## Disposition

The root read-catalog mechanism is useful but incomplete. Fix request-path normalization,
retain the same freshness and completion rules, and use a small Ofetch regression canary
before another six-task run. Do not promote memory defaults or claim a quality/cost win
from this cohort. A separate forced-cut lifecycle test is still required for working-note
and prior-recall uptake because this DeepSWE panel did not exercise compaction.

## Path-normalization follow-up

Revision `d3f2e8e` normalizes catalog request keys through the configured workspace
boundary. The focused PTC suites passed 90 tests, with an absolute-path regression using
the same prior relative-path receipt.

One no-retry Ofetch canary then ran at
`/Users/mathiasl/skein-eval-results/memory-read-catalog-ofetch-path-canary-20260913`.
It produced 11 catalog-routed receipts, 10 with reused content, and reused 1,047 of
3,159 logically selected lines (33.1%); 872 reused lines came from `src/fetch.ts`.
Fresh selected lines were 2,112 versus 3,638 in the preceding Ofetch trajectory.
The model happened to request relative paths in this sample, so the live result confirms
Ofetch catalog uptake while the deterministic regression confirms the absolute-path fix.

The canary still failed quality: 0/47 feature-to-pass and 13/13 pass-to-pass tests, with
no patch before task-input-budget exhaustion. It used 36 model calls, 1.902M input tokens,
87k uncached input tokens, $0.018424, and 317 seconds active wall. No compaction occurred.
The separate deterministic forced-cut screen passed six lifecycle cases covering read
handoff, repeated epochs, refreshed findings, source-derived answers, and independent
completion evidence. This qualifies the deterministic lifecycle only, not live learned-
memory use or a default change.

## Official-compatible limit follow-up — 2026-09-14

Commit `29f5da4` changed the Pier campaign runner to use each frozen task's declared
agent timeout, raised the practical workflow ceiling from 24 to 1,000 iterations, and
raised the cumulative input ceiling from 2M to 1B tokens. The same six tasks were run
once with Muse Spark 1.3/xhigh and concurrency six at
`/Users/mathiasl/skein-eval-results/memory-official-compatible-6-20260914`.

| Aggregate | Pre-catalog baseline | Automatic catalog | Official-compatible latest |
| --- | ---: | ---: | ---: |
| Official passes | 1/6 | 0/6 | 1/6 |
| Model calls | 192 | 210 | 255 |
| Input / uncached input | 9.79M / 513k | 10.88M / 526k | 16.08M / 638k |
| Output / reasoning tokens | 205k / 157k | 192k / 137k | 216k / 150k |
| Cost | $0.110735 | $0.111644 | $0.137773 |
| Active wall | 1,931s | 1,876s | 2,351s |
| Logical selected / source-read lines | 28,803 / 28,803 | 20,394 / 15,523 | 27,613 / 20,131 |
| Catalog-reused lines | 0 | 4,871 | 7,482 |

Wazero again passed the official verifier with a 15,532-byte committed patch. The
other five produced empty collected patches; Koota ended on provider
`max_output_tokens`, while four models declared themselves blocked. No task ended on
the cumulative input budget. The catalog reused 7,482 selected lines across 61 reads,
including 2,622 on Koota and 2,396 on Testem; Ofetch had no eligible repeated range in
this trajectory.

This run removes the task-input stop as a quality confound and restores 1/6, but does
not improve quality over the pre-catalog 1/6 baseline. Relative to that baseline it
uses 32.8% more calls, 64.3% more input, 24.4% more uncached input and cost, and 21.8%
more active wall time. The 30.1% reduction in physically selected source lines and
7,482 directly reused lines demonstrate catalog operation, not a causal aggregate
reread improvement, because the model trajectories and stopping points differ.

## Decision-guidance canary — 2026-09-14

Commit `0d1460c` temporarily added automatic path/range descriptions and binding hints,
front-loaded instructions to inspect/annotate retained reads, and strengthened the
no-progress checkpoint toward edit/test/commit. The deterministic path passed 194
focused tests. A single official-compatible Testem trial ran at
`/Users/mathiasl/skein-eval-results/memory-decision-reuse-testem-canary-20260914`.

| Testem | Previous latest | Decision guidance | Change |
| --- | ---: | ---: | ---: |
| Official reward | 0 | 0 | equal |
| Model calls | 46 | 63 | +37.0% |
| Input / uncached input | 3.34M / 135k | 4.67M / 119k | +39.6% / -11.4% |
| Cost | $0.027222 | $0.027357 | +0.5% |
| Active wall | 392s | 377s | -3.6% |
| Logical selected / source-read lines | 7,855 / 5,459 | 1,557 / 989 | -80.2% / -81.9% |
| Catalog-reused lines | 2,396 | 568 | trajectory differs |

The model continued through two work batches rather than stopping after the first, but
again returned `blocked`, produced no committed patch, and scored 0/90 feature tests
with 489/489 regressions passing. It never called `agent.state.annotate`. The treatment
therefore reduced source acquisition and uncached input but failed quality, total-call,
and total-cost gates. The remaining five paid trials were not dispatched, and the
default prompt/descriptor changes were reverted. Automatic broker-level catalog reuse
remains enabled.

## Call-control and submission follow-up — 2026-09-14

The next implementation kept exact repeated reads on their original live-epoch handle,
routed unsupported `blocked` results with changed work to verification, and enabled
deterministic DeepSWE workspace commit packaging. A first matched Testem pair exposed a
separate inner-loop problem and was stopped after its efficiency gate had already failed:
Muse reached 122 model calls/$0.114864 and Luna reached 104/$0.439501 without finishing.
The retained traces are at
`/Users/mathiasl/skein-eval-results/memory-call-control-testem-{muse,luna}-20260914`.
Luna nevertheless reused 4,317 selected lines across 72 reads while acquiring 5,714,
showing that source reuse and repeated model actions are independent.

The memory profile then reduced read-only and hard work-batch ceilings from 24/48 to
12/24 cells, made a changed hard-limit yield request verification, and added a three-
attempt verification budget. A scripted real-workflow test proves changing failed
workspaces stop at that absolute limit. The first bounded Muse run stopped at 43 calls,
$0.025975, and 406 seconds, and Harbor graded its packaged seven-file patch at 39/90
feature tests and 489/489 regressions. It then exposed a reducer crash on late advisory
criterion proposals; those proposals are now rejected without changing the frozen
criterion contract. Preserve this diagnostic at
`/Users/mathiasl/skein-eval-results/memory-bounded-control-testem-muse-20260914`.

The fixed replacement at
`/Users/mathiasl/skein-eval-results/memory-bounded-control-v2-testem-muse-20260914`
terminated after three failed independent verifications and produced a graded patch:

| Testem | Official-compatible baseline | Fixed bounded replacement |
| --- | ---: | ---: |
| Official reward | 0 | 0 |
| Feature / regression tests | 0/90 / 489/489 | 86/90 / 489/489 |
| Model calls | 46 | 96 |
| Input / uncached input | 3.34M / 134,528 | 10.89M / 197,164 |
| Cost | $0.027222 | $0.051553 |
| Active wall | 390s | 1,074s |
| Selected / newly read lines | 7,855 / 5,459 | 7,390 / 4,990 |
| Catalog-reused lines | 2,396 | 2,400 |

The final run demonstrates nonempty submission, bounded verification re-entry, completed-
evidence gating, and 469 fewer newly selected source lines. It does not pass the binary
quality or efficiency gates: the patch missed four feature tests, calls doubled, and
three `npm run test` attempts dominated latency. The initial baseline verification had
timed out, so the model's claim that two failures were pre-existing was not completed
evidence; Skein correctly withheld completion. A partial Luna rerun reached one forced
verification at 73 calls/$0.279520 and was stopped because the efficiency gate was already
lost; retain it at
`/Users/mathiasl/skein-eval-results/memory-bounded-control-testem-luna-20260914`.

Do not expand to six tasks from these canaries. The next experiment must separately test
targeted validation selection/caching and semantic progress control; reread suppression
is operating, but it cannot by itself prevent broad edit/test loops.
