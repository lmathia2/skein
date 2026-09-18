# PTC and memory continuity: implementation and live checks

## Scope and implementation

The grouped [continuity plan](../design/ptc-memory-continuity-plan.md) stages S0–S5
are implemented. The live evaluation is S6, not evidence of default-promotion approval.

| Component | Delivered contract |
| --- | --- |
| PTC | Evidence-linked live values, bounded advisory descriptions, nested access/inspection recipes, actual worker epochs, changed-only notices, redacted artifacts and non-replayable redacted source |
| Memory | Authorized addressed read recovery; typed, cited findings with dependencies and corrections; focus-ranked whole-entry working sets; explicitly authorized prior sources |
| Context | Bounded whole-entry handoffs, frozen compaction epochs, preserved unconsumed results, version/range-specific read index, stable model/tool prefix |
| Safety and runtime | Freshness uncertainty, current-version guards, conservative worker-loss/unknown-effect handling, independent input-budget terminal category |
| Evaluation | Production-path controlled continuations, canonical read coverage, selected-output and provider-wire exposure, paired correctness/cost gates, preserved stopped-run accounting |

The component ADRs and repository `coding-harness-development` skill were updated.
No new top-level tool, alternate effect broker, automatic effect replay, or blanket
default promotion was introduced. Correctness takes priority over minimizing reads.

Validation at candidate revision `acb7044`: 680 unit tests passed, one skipped;
seven integration tests passed. Ruff, compileall, and Pyright passed (Pyright retains
one existing `__all__` warning). These are code-contract checks, not model-quality proof.
After the coverage correction (`528f8d4`, diagnostic frozen at `812c5b6`), the full
suite passes **695 tests, one skipped**; lint, compilation, and type checks remain clean
apart from that existing warning.

## Frozen experiment

Candidate cohort: `.artifacts/continuity-live-20260912-v3/`, revision `acb7044`.
OpenRouter model `openai/gpt-5.6-luna`, reasoning `max`, concurrency six.
Six fixture families × two variants × four arms = 48 planned continuations.
Each case is bounded at 12 calls, 200k cumulative input, 8192 output tokens, 900 seconds.

The arms are full history, compacted metadata, described values, and described values
plus findings. Each uses the same static instruction and managed capability APIs;
compacted arms differ in representation, not authority. Findings are seeded through
the public note writer. This evaluates recovery/use, not autonomous note-writing or
official DeepSWE reward. Full history is intentionally not context-size matched.

Families cover old-path navigation, covered/missing ranges, managed/external mutation,
live/restarted workers, rejected approaches, and authorized prior runs with changed
current content. Necessary fresh reads are not counted as avoidable rereads.

The frozen advancement gate requires all 12 findings outcomes correct and normally
terminated, seven measured reusable pairs with at least 25% fewer duplicate emitted
source lines, no aggregate provider-cost increase versus metadata, a complete cohort,
and separate deterministic safety checks. Exact-line exposure v2 is a lower bound:
transformed, ambiguous, and unrecognized encodings remain unmapped, not proven absent.

## Earlier stopped cohorts (not pooled)

| Cohort | Started / planned | Correct artifacts | Stop / failures | Known cost |
| --- | --- | --- | --- | --- |
| v1 (`a6bb458`) | 28 / 48 | 27 | Same-cut compaction publication collision; 20 not started | $0.21267189 |
| v2 (`0fd6396`) | 33 / 48 | 28 | Provider server error; three wrong artifacts; one input-budget terminal; 15 not started | $0.24561903 |

The v1 bug was deterministically reproduced: an oversized unconsumed result forced a
second publication at the same cut. The fix keeps that epoch until the cut can advance,
without dropping the unconsumed result or bypassing the hard context ceiling. Nested
descriptors also gained explicit parent/selected types and access recipes.

In v2, all three completed missing-range arms returned the visible `VALUE` instead of
the requested `SAFE_VALUE`, which was outside the captured ten lines. The grader was
correct. The common instruction now explicitly requires obtaining missing coverage
and forbids substituting another symbol merely to avoid a read. Fixtures, budgets,
and advancement thresholds were not weakened. The provider `server_error` had been
misclassified as a harness/fixture error; the driver now recognizes its typed exception
and retains any reported usage. Missing usage/cost remains unknown. The v2 amount is
a lower bound because that failed response was unaccounted.

Measurement v2 additionally decodes the known structured read-recovery JSON route;
v1's exact-line decoder missed that escaped source text. The version is recorded in
each measurement, and old inputs remain available for uniform reanalysis.

## Completed 48-case cohort

All 48 cases started and finished; no provider, harness, or measurement error stopped
v3. Total reported provider cost was **$0.40246332**.

| Arm | Correct artifacts | Calls | Input | Uncached input | Output / reasoning | Cost | Sum active wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Full history | 10/12 | 68 | 1,254,097 | 216,598 | 79,319 / 67,471 | $0.17007 | 674.7s |
| Metadata | 11/12 | 57 | 296,967 | 58,014 | 43,530 / 36,014 | $0.07151 | 377.4s |
| Described | 11/12 | 65 | 393,917 | 81,925 | 44,446 / 36,723 | $0.08005 | 404.1s |
| Findings | 11/12 | 50 | 308,209 | 71,487 | 48,530 / 41,078 | $0.08083 | 411.3s |

Artifact correctness is separate from termination. There were 41 normal correct
completions, four wrong-artifact completions (every missing-range arm), two input-budget
terminals (full-history navigation-0 and prior-0), and one call-limit terminal
(described prior-0). The latter two prior-0 outputs were already correct when stopped.
Reasoning is included in output, not an additional token charge. Wall totals sum active
trial time; they are not elapsed campaign duration at concurrency six.

| Pair | Metadata → findings calls | Duplicate emitted source lines | Correctness |
| --- | --- | --- | --- |
| Navigation 0 | 4 → 3 | 24 → 0 | Both correct |
| Navigation 1 | 5 → 3 | 22 → 0 | Both correct |
| Covered range | 4 → 4 | 10 → 0 | Both correct |
| Missing range | 5 → 5 | 10 → 0 | Both wrong; excluded from reusable gate |
| Managed mutation | 4 → 3 | 0 → 0 | Both correct |
| External mutation | 4 → 4 | 0 → 0 | Both correct |
| Live worker | 6 → 5 | 0 → 0 | Both correct |
| Restarted worker | 7 → 5 | 0 → 0 | Both correct |
| Rejected approach 0 | 5 → 4 | 0 → 0 | Both correct |
| Rejected approach 1 | 5 → 4 | 22 → 0 | Both correct |
| Prior run 0 | 4 → 6 | 0 → 0 | Both correct |
| Prior run 1 | 4 → 4 | 0 → 0 | Both correct |

On the seven reusable pairs, mapped duplicate emissions fell **78 → 0**; across all
pairs, **88 → 0**. Mapped provider-transmitted source lines fell **209 → 2**. These
are exact-line lower-bound counters, not proof of zero repeated semantic content.
Both arms fetched **17 post-cut reads / 132 lines**, including **one fully covered
24-line reread** each. Metadata reread navigation-0; findings reread restarted-worker-1.
Therefore this cohort does **not** demonstrate reduced filesystem rereading.

Findings used 12.3% fewer calls but cost **13.0% more** than metadata; input increased
3.8%, uncached input 23.2%, and output 11.5%. All 240 recorded wire attempts retained
one common static model/instruction/tool/reasoning/output-limit identity, stable both
between calls and across arms. Dynamic handoffs remain separate epoch content.

The cost increase is not explained by calls alone. Findings prior-0 costs $0.00739
versus $0.00290 for metadata: it separately checks for the absent output, writes it,
and reads it back, where metadata batches write/read. The failed missing-range pair
costs $0.01209 versus $0.00812. These are concrete next diagnostics for unnecessary
PTC turn boundaries and unsupported inference; they are not proof of a general causal
effect from one model sample. Conversely, restarted-worker findings costs less but
rereads 24 source lines, illustrating why cost, exposure, and fetches need separate gates.

**Decision: hold.** The frozen correctness and no-cost-increase gates failed. The
positive exposure result does not qualify a DeepSWE expansion or production defaults.

## Coverage correction after the full cohort

The failed recovery trace labelled its ten-line capture `complete: true`, meaning
the recovery page, while the file had 24 lines. One submitted cell explicitly called
this a complete historical capture and substituted the visible wrong symbol. This
exposes a representation ambiguity in addition to the model's requirement violation;
the preceding prompt-only change was insufficient.

Recovery now declares `complete_scope=selected_capture_page` and separately reports
historical `source_coverage` (`total_lines`, `whole_file`, `next_unread_offset`). The
same host-derived coverage is retained in PTC references/descriptors, direct-read
receipts, and handoff indexes. Unknown legacy coverage stays unknown; contradictory
line counts fail closed. A source offset is explicitly not a recovery-page offset.
This does not silently fetch missing lines or authorize a stale write.

The focused follow-up keeps both original partial-read variants and all four arms
(eight cases), with unchanged model, budgets, grading, and concurrency. It is a
diagnostic of this correction, not a replacement 48-case cohort or a promotion gate.

Completed diagnostic: `.artifacts/continuity-coverage-live-20260912-v1/`, revision
`812c5b6`. All eight cases ran, six produced correct artifacts; no infrastructure or
measurement failure. Reported provider cost: **$0.08211887**.

| Arm | Covered range | Missing range | Total calls | Cost |
| --- | --- | --- | --- | --- |
| Full history | Correct | Wrong | 10 | $0.03101 |
| Metadata | Correct | Correct | 16 | $0.01850 |
| Described | Correct | Correct | 12 | $0.01930 |
| Findings | Correct | Wrong | 7 | $0.01332 |

Metadata and described actually fetched the previously unseen range starting at line
11 and produced `11178`. Findings' recovery output explicitly contained
`whole_file: false`, `total_lines: 24`, and `next_unread_offset: 11`, but the model still
wrote the visible wrong value `11101` without fetching the missing source. This is
remaining model/evidence-use failure, not a missing metadata field or a successful
read-avoidance optimization. It also invented a `not_found` read status and reset the
worker after the actual `error` response, then recovered execution but not correctness.
Full history also failed, so this small diagnostic does not isolate a causal penalty
from findings. The correction fixes the representation contract; it does not establish
behavioral reliability. The summary's `campaign_complete: false` denotes the unmet
full 48-case gate, not unfinished execution of these eight requested cases.

Known reported cost across the four separate campaigns is **$0.94287311**, plus any
unreported cost of v2's failed provider response. No further paid campaign was launched.

## What remains

The requested implementation is delivered; empirical promotion is **not**. Keep the
DeepSWE confirmation, twenty-task expansion, and feature-default promotion held.
The next bounded work should test evidence-use correctness with new, held-out missing-
coverage and contradictory-finding variants, then independently measure model-written
checkpoints. Pair those with a capable-model control before attributing failures to
memory alone. Diagnose unnecessary PTC turn boundaries and incorrect result-shape/
status assumptions; never fix them by weakening source coverage, freshness, or grading.
Do not reuse this tuned diagnostic panel as independent generalization evidence.

## DeepSWE preflight

The six-task DeepSWE 1.1 panel was frozen before controlled results: Koota, Obsidian
linter, Bandit, Tengo, HTTPX, and Pest (exact identities/hashes in the plan). Its
24-trial confirmation is conditional, not automatically launched by the evaluator.
Docker 29.5.2, Compose 5.5.0, and Buildx 0.37.0 are available. All task packages and
five base images are cached; HTTPX needs its declared image pulled if the gate clears.
Image presence alone does not establish verifier viability.

## Result artifacts

Each cohort retains `manifest.json`, `results.json`, `summary.json`, and per-case
`config.json`, `fixture.json`, `result.json`, `exposure.json`, public `wire/*.json`,
workspace output, and canonical state/read artifacts. Credentials, HTTP authorization
headers, and private reasoning are not retained. Keep stopped cohorts separate from
the fresh candidate; do not fill missing pairs from an earlier run.
