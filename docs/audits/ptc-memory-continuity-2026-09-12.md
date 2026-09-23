# PTC and memory continuity: implementation and live checks

## Scope and implementation

The current [context and memory ADR](../adr/context-and-memory.md) records the resulting
continuity contract. This audit originally staged S0–S5, which were implemented. The
live evaluation is S6, not evidence of default-promotion approval.

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
unreported cost of v2's failed provider response. This was the total before the
evidence-placement diagnostic below.

## Evidence placement and lost progress diagnostic

`.artifacts/evidence-placement-live-20260912-v1/`, revision `3bbddb2`, completed nine
trials: three repetitions each of visible exact evidence, recoverable exact evidence,
and a partial capture requiring previously unread evidence. These are first-proposal
diagnostics through real PTC/memory, **not** the outer verification loop or held-out
generalization evidence. All use Luna/max and the same task/answer oracle.

| Evidence placement | Correct | Calls | Reported cost |
| --- | --- | --- | --- |
| Visible after the cut | 3/3 | 7 | $0.01450180 |
| Recoverable through memory/live bindings | 3/3 | 11 | $0.01597319 |
| Not in the captured range | 2/3 | 29 | $0.05771905 |

All usage/cost was accounted; total **$0.08819404**. The known cumulative total is
**$1.03106715**, plus the earlier unreported failed-response cost. Visible/recoverable
successes needed no additional source-file reads. Correct unread trials used a new
range or current search, so zero `fs.read` overlap alone must not be labelled memory
benefit. This diagnostic does not establish a cost-matched reread reduction.

The failed `repeat-1-unread` trial is a stronger continuity counterexample than the
earlier wrong-symbol guesses. Canonical sequence 95 exposed `SAFE_VALUE = 11178`
through completed search; sequence 108 wrote the correct answer. A self-authored
shell check was blocked, and sequence 131 read the correct answer back. Cuts at
137, 168, and 184 all reused note version 1 (the original route-only finding).
After the newer evidence disappeared, the model recovered the old ten-line capture
and overwrote the correct answer with `11101` at sequence 178. The task still asked
for `SAFE_VALUE`. This is loss of learned progress plus inappropriate evidence use,
not a worker restart or inability to find the missing value.

The next implementation gives one note-refresh opportunity before an advancing soft
cut, retains the exact suffix during that opportunity, and explicitly marks fallback
notes stale. Hard limits still take precedence; an ignored request cannot create an
unbounded checkpoint loop. It does not assume a refreshed summary is true. A fresh
run of this same nine-trial diagnostic will test consumption/retention before any
larger cohort, and will remain development evidence even if it passes.

Separately, `3e44987` fixes missing-exit success, incomplete baseline comparisons,
and shifted criterion-validation references. `8c72acf` exercises the real PTC/outer
workflow with scripted proposals: independent verification rejects a wrong
self-checked answer, stops an unchanged repeated proposal, and accepts a repair
using only the necessary one-line range. These deterministic checks do not prove
live-model reliability or the semantic sufficiency of generic discovered tests.

### Checkpoint-refresh rerun

`.artifacts/evidence-placement-live-20260912-v2/`, revision `a1c0a44`, also ran all
nine trials. **9/9 final artifacts were correct, but only 8/9 were normal completions**:
`repeat-2-unread` reached the unchanged twelve-call limit with a correct artifact.
Normal completions therefore did not increase. No infrastructure/measurement
failures or unaccounted calls were reported.

| Placement | Correct artifacts | Normal correct completions | Calls | Cost |
| --- | --- | --- | --- | --- |
| Visible | 3/3 | 3/3 | 6 | $0.01441797 |
| Recoverable | 3/3 | 3/3 | 10 | $0.02037552 |
| Unread | 3/3 | 2/3 | 25 | $0.06112764 |

Total calls fell **47→41**; total reported cost rose **$0.08819404→$0.09592113**
(8.8%). These tiny development cohorts do not identify a causal score or cost effect.
The two long unread trials did exercise the new checkpoint mechanism. Repetition 1
first ignored two refresh opportunities (cuts explicitly marked stale), then recorded
five finding entries and the exact `SAFE_VALUE` at note version 2 before its next cut.
Repetition 2 recorded four findings at version 2, including the incorrect answer and
remaining correction, and refreshed again at version 3. It retained the correction
but did not return a normal terminal response within budget. This is evidence of
model-written checkpoint use, not a passed reliability/cost gate or demonstrated
reduction in avoidable rereads.

The traces also repeatedly treated managed `memory query` results like subprocess
stdout, and state descriptors like status/data envelopes. PTC help/instructions now
document the existing native shapes explicitly; deterministic tests exercise those
exact access paths. This subsequent help correction is not part of either live cohort.

Known provider cost for these two new diagnostics is **$0.18411517**; cumulative
known cost across all six campaigns is **$1.12698828**, plus the earlier unreported
failed-response cost. No DeepSWE expansion or default promotion was launched.

## First real-workflow development canary

`verified-continuity-live-20260912-v1` (d82242c) uses the actual outer review and
independent verifier, unlike the previous artifact-only continuation loops.

| Case | Metadata | Findings |
| --- | --- | --- |
| TOML routing | Verified, 7 calls, $0.01618780 | Correct artifact, call limit 12, $0.03716562 |
| Missing TOML fields | Missing artifact, call limit 12, $0.01598180 | Correct artifact, call limit 12, $0.02765120 |

One of four accepted completions; no false acceptance. Both findings trials proposed
verification at call 11 but used the last call for notes during counterexample review.
Routing reread all 13 previously captured lines in both arms. Missing/findings fetched
the needed ten-line continuation twice (ten avoidable overlapping lines); metadata
fetched it once but never produced the answer. Unequal terminals prohibit an efficiency
claim. Submitted public programs show no oracle or parent-directory access. All 43
provider requests were accounted for; known cost $0.09698642 (cumulative $1.22397470,
plus the previously unreported failed-response cost).

The 6k immediate-cut configuration produced 5–8 cuts per task, repeatedly prompting
note refresh. Some post-cut requests already exceeded 6k. This inherited stress
setting confounds the intended checkpoint-recovery screen. Evaluator v2 separates the
initial forced checkpoint from phase-boundary continuation, without changing production
defaults, oracle, arms, task definitions, or budgets. It must not be presented as proof
that repeated-cut reliability or rereads have been solved.

## Single-checkpoint real-workflow rerun

`verified-continuity-live-20260912-v2` (adaa8bb) restores phase-boundary timing after
the initial forced checkpoint. All four trials had exactly one cut.

| Case | Metadata | Findings |
| --- | --- | --- |
| TOML routing | Verified, 10 calls, $0.01454095 | Verified, 7 calls, $0.01351970 |
| Missing TOML fields | Correct artifact, call limit 12, $0.01595852 | Verified, 6 calls, $0.01461652 |

All four final artifacts are correct; three reached canonical accepted completion.
Routing source reads fell from 13 previously captured lines to zero; missing/findings
read the necessary ten missing lines once, whereas metadata repeated a four-line range
twice to locate citation metadata. Both findings tasks passed the independent verifier.
Metadata/missing ran the required external unittest oracle successfully through the
broker, then spent its remaining calls on review/notes instead of returning to the
outer verifier. That is not an accepted completion. Invoking the declared verifier is
allowed; it did not inspect the oracle source or expose its expected answer.

Cost was $0.05863569 for 35 accounted calls (cumulative known $1.28261039, plus the
earlier unreported failed-response cost). Findings cost $0.02813622 versus metadata
$0.03049947; different stopping points and two development pairs preclude promotion.
The routing pair is encouraging evidence of retained-finding use, not a held-out
generalization result or repeated-cut qualification.

The next PTC correction documents `result['read_reference']['artifact_uri']` in the
exact help contract and stable instructions. The source hash is not a receipt URI.
Its real-broker regression obtains the citation from the saved result and descriptor
with exactly one original source read. This correction is not part of v2's results.

## Citation-help canary and lifecycle screen

`verified-continuity-live-20260912-v3` (ba00099) reached 4/4 independently verified
completions. Both arms used 17 calls and neither made an avoidable source reread.
Metadata cost $0.02948028; findings cost $0.03008602 (+2.1%). The previously observed
citation-location rereads did not recur, but these two pairs do not establish a
memory advantage. Total cost was $0.05956630.

The eight remaining development lifecycle trials at the same revision are in
`verified-continuity-lifecycle-20260912-v1`. Restart and freshness passed in both arms.
Correction failed in both; the findings arm wrote an extra `route` key and exhausted
input budget, while metadata inspected the external oracle source and tried candidate
answers against its hash. **The correction pair is contaminated and invalid for memory
quality conclusions.** The model cannot normally edit that file via fs, but the local
shell adapter can read host paths. A hash of a low-entropy expected answer is not a
sealed oracle. The join metadata arm left a correct call-limited artifact; findings
exhausted input budget without an answer. Overall: 4/8 accepted, 5/8 correct artifacts,
no false acceptance, and $0.11366931 known cost. Cumulative known spend is $1.45584600,
plus the previously unreported failed-response cost. No further paid cohort used the
exposed-file oracle after this failure was observed.

The evaluator correction uses a host-owned virtual test target through the existing
execution-runtime factory. Both PTC shell calls and independent verification pass through
the same managed command policy and adapter. The expected value stays in host memory;
no oracle source or answer hash is published. Live shell commands use an explicitly
preflighted, cached Docker image with only the fixture workspace mounted. The guarded
PTC worker remains local, as required by its current implementation. Record that runtime
override separately from the composition. This is not a claim of an adversarially secure
Python runtime. The fixture now states exact output keys, without revealing values.

The real Docker preflight also exposed an invalid shared `--mount` argument (`rw`
instead of the default writable mount). The adapter now uses the valid bind argument
and forbids implicit image pulls. Colima's unshared macOS temporary directory cannot
host the bind-mounted preflight; the passing test uses a fresh repository-local
directory and trusts only `/workspace` for Git ownership checks. These are evaluator/
environment corrections, not evidence of improved memory quality.

## Host-owned oracle rerun and rejected-cell recovery

`verified-continuity-isolated-20260912-v1` (ef88f33) reran correction and join with
explicit output keys and Docker-isolated commands. All four final artifacts are
correct; findings accepted both (7 and 9 calls), metadata accepted join (9 calls),
while metadata/correction stopped at the 200k input reservation gate after 11 calls.
No false accepted completion occurred. Costs: metadata $0.02915833, findings
$0.02954896, total $0.05870729. Cumulative known spend is $1.51455329 plus the earlier
unreported failed-response cost. The host-owned checker did not publish oracle code
or an expected-answer hash.

Reread behavior remains mixed: correction source overlap was metadata 3 lines versus
findings 0; join was metadata 0 versus findings 9. Do not claim a memory read/cost win.
The stopped correction trial proposed verification at call 6. In review, call 7's
read assignment was rejected before execution because a later exception handler used
`type(exc).__name__`. The entire source is validated first, including dead branches.
The model then treated an old `r` binding (a memory-note result) as the newly read
answer, spent more calls diagnosing its shape, and hit its budget after finally
submitting a valid fresh read. The worker had correctly preserved the old heap.

The next PTC fix exposes `execution_started` in canonical and compact results and
explicitly says that rejected cells ran no lines, assignments, or capability calls.
Existing bindings are unchanged, not outputs of the rejected program. Stable guidance
uses `str(exc)` instead of forbidden dunder introspection. A regression reproduces a
rejected late/dead-branch dunder access, verifies zero reads and the old binding, then
requires an explicitly corrected read to obtain new evidence. Runtime failures with
heap rollback remain distinguishable: they can have completed capability calls.
Security validation is not relaxed, and this fix is not part of the preceding cohort.

## Rejected-cell guidance rerun

`verified-continuity-isolated-20260912-v2` (2dee395) reached 4/4 independently verified
completions, with exactly one cut per task and no failed PTC cells. Both arms used the
same fixed tasks, host-owned checker, Docker image, output schema, and spending limits.

| Case | Metadata calls / cost | Findings calls / cost | Source reread lines, metadata → findings |
| --- | --- | --- | --- |
| Correction | 8 / $0.01360863 | 6 / $0.01232471 | 0 → 0 |
| Multi-file join | 9 / $0.01826913 | 6 / $0.01187403 | 9 → 0 |

Aggregate: 17→12 calls, $0.03187776→$0.02419874 (24.1% lower provider cost), and nine→zero
same-version source reread lines. All 29 requests have complete cost/usage accounting,
with no hidden wire retries. Public submitted programs show no host-path/oracle-source
inspection. No rejected cell occurred, so this live run exercises the revised guidance
but does not directly test model recovery after rejection; the forced regression does.

This is a positive **two-pair development pilot**, not a demonstrated generalization
effect. Both arms include working notes and retrieval; the comparison isolates richer
continuity representation, not memory on/off. Prior cost and reread reversals remain in
the record. Total cost was $0.05607650; cumulative known spend is $1.57062979, plus the
earlier unreported failed-response cost. Nothing was promoted or expanded to DeepSWE.

## Decisive-source timing audit (no new provider calls)

The new `answer-source-availability-v1` audit compares host-frozen source path,
version, and decisive line ranges with successful task-local reads before each managed
answer write request. It retains first and repaired answers separately. Later reads,
failed/pending reads, and another source version do not fill earlier coverage. Shell,
artifact, and prior-run routes without an exact mapping remain unknown, not silently
counted as missing or as evidence. This measures durable historical availability, not
model exposure, semantic use, or continuous workspace freshness. Arbitrary Python
value-construction time is not observable; the measured boundary is write dispatch.

Retrospective results for all four trials in
`.artifacts/verified-continuity-isolated-20260912-v2`: decisive ranges were available
before the first and only managed answer write in every trial. Correction requires
two lines (route identity and safe limit); join requires four lines across two files
(identity, policy link, attempts, pause). The source ranges were already seeded, so
this cannot establish autonomous finding construction or recovery use.

The separate sidecar
`.artifacts/verified-continuity-isolated-20260912-v2-answer-evidence.json` preserves
measurement, requirement, and input-event hashes plus source receipt identities.
Original cohort results are unchanged. No provider spend was added.

A real-workflow deterministic negative demonstrates the remaining authority gap:
a correct guessed answer to the missing-fields case passes the independent artifact
oracle despite never acquiring the decisive fields. The new availability measurement
does not pass that trial. Artifact correctness alone therefore cannot establish the
user's completed-evidence goal. The measurement is not a new production completion
policy; task-specific evidence enforcement and model-written checkpoint trials remain
next-stage work. Full suite: 738 passed, two skipped; Ruff clean; Pyright zero errors
and the existing `__all__` warning.

## Source-gated live diagnostic (1602a2f)

`.artifacts/verified-continuity-evidence-gated-20260912-v1` ran the frozen four trials
under evaluator v4. All four completed with correct artifacts, first-verification
success, and decisive source availability before their first answer. Both missing-field
arms read the ten previously uncaptured lines once; both freshness arms read the new
seven-line version once. No avoidable same-version source overlap occurred in either
arm, so this is not evidence of a reread benefit.

| Case | Metadata calls / cost | Findings calls / cost |
| --- | --- | --- |
| Missing evidence | 11 / $0.01567863 | 8 / $0.01509387 |
| Changed evidence | 6 / $0.00819488 | 7 / $0.01461355 |

Findings used 15 calls versus 17, but cost $0.02970742 versus $0.02387351 (+24.4%).
Input tokens were 247,243 versus 190,903. All 32 requests had complete usage/cost
accounting with no extra wire attempts. A metadata cell using `type(v).__name__`
was rejected before execution; the next cell safely inspected its already-existing
note binding. No stale-source or unsupported answer was accepted. Total spend:
$0.05358093; cumulative known spend $1.62421072 plus the older unreported failure cost.

Actual first-request input revealed a duplicated handoff: the context plugin supplied
the current handoff and the root workflow independently included the persisted summary
in its work packet. In freshness/findings the same historical route finding appeared
twice; the injected message was 10,369 characters and the workflow packet 12,665.
Metadata had the same duplicate delivery path with a smaller handoff. This is a concrete
input duplication, not proof that removing it will improve model quality or paid cost.

The fix assigns handoff delivery to the installed context plugin; off/shadow profiles
retain workflow-owned summaries. The workflow retains a conservative input reservation,
and existing plugin ordering lets inner metrics inspect the reconstructed request.
The real-workflow regression failed with two handoffs before the change and passes with
one while all six source-derived development answers still verify. The next four-trial
rerun uses the same frozen cases/budgets to check the common harness change. It is still
a development diagnostic; no promotion or broader paid expansion follows automatically.

## Single-owner handoff live check (39b6684)

`.artifacts/verified-continuity-evidence-gated-20260912-v2` reran the same four trials.
Actual first provider requests contain one handoff in every trial. The findings packet
fell from about 12.7k to 2.9k characters while the separate 10.3k-character handoff
remained intact. Provider-reported first input fell 11,057→7,221 tokens for freshness
and 11,051→7,251 for missing evidence; this confirms removal at the paid request boundary.

| Case | Metadata terminal / calls / cost | Findings terminal / calls / cost |
| --- | --- | --- |
| Missing evidence | Correct artifact, call limit / 12 / $0.01371733 | Verified / 6 / $0.01081826 |
| Changed evidence | Verified / 8 / $0.00993836 | Verified / 7 / $0.01249980 |

Findings accepted 2/2; metadata accepted 1/2. All four first answers had the decisive
source ranges available beforehand, and every final artifact was correct. Both arms
again had zero avoidable source overlap. Metadata split the missing ten lines into two
new-range reads; that is not an avoidable reread. Findings had 13 calls/$0.02331806/
144,166 input tokens versus metadata's 20 calls/$0.02365569/201,145 input tokens.
The unequal terminal makes aggregate efficiency a diagnostic, not a promotion result.

Relative to its prior run, findings input decreased 41.7% and cost decreased 21.5%,
with both tasks still accepted. However, overall acceptance fell 4/4→3/4 and total
calls rose 32→33; do not describe this as an established overall quality improvement.
The failed metadata trial spent a call correcting unsupported finding kind `plan`,
split its tail acquisition, recovered the retained identity header, and made several
note-management calls. It wrote the correct source-backed artifact at call 9, passed
the model-requested host check at call 10, updated notes at call 11, and proposed done
at call 12. No outer verification report completed before the call ceiling. Preserve
that outcome rather than upgrading it from correct bytes or the model-requested check.

All 33 provider calls have complete accounting and no extra wire attempts. Cost was
$0.04697375; this goal turn's two cohorts cost $0.10055468. Cumulative known spend is
$1.67118447 plus the older unreported failed-response cost. The process is terminal;
no live jobs remain. Full deterministic suite: 744 passed, two skipped; Ruff clean,
Pyright zero errors and the existing warning. No profile was promoted.

Next: model-written checkpoint development cases and note-management overhead, then
genuinely new held-out variants and repeated cuts. The current pairs have no avoidable
baseline rereads, so they cannot demonstrate the requested reread reduction regardless
of how often they are rerun. Avoid another repeat of these cases without a new,
trace-supported intervention.

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
