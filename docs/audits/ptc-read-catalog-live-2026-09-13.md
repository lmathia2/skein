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
