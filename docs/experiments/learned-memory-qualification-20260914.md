# Learned memory qualification

## Frozen question

Does the implemented checkpoint/findings loop reliably reuse completed evidence on
fresh continuations, reduce avoidable same-version rereads, remain fresh after source
changes, and withhold answers when completed evidence is insufficient?

## Cohort

- Arms: `no_recall` and `findings`, one fresh-state attempt per case.
- Model: OpenRouter `openai/gpt-5.6-luna`, reasoning `max`.
- Concurrency: six; paired arms share fixture bytes, budgets and command image.
- Limits: 24 calls, 350,000 cumulative input tokens, 8,192 output tokens and 900
  seconds per trial; no retry or selective replacement.
- Cached command image:
  `sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
- Output: `.artifacts/learned-memory-qualification-live-v1`.

The six fixture variants have no prior live result directories:

| Risk | Cases | Required behavior |
| --- | --- | --- |
| Unchanged evidence | `qualification_routes_3`, `qualification_partial_3` | Reuse captured relationships; acquire only missing ranges. |
| Changed evidence | `qualification_changed_3`, `qualification_changed_4` | Correct stale findings after the authorized revision while retaining unchanged evidence. |
| Conflict/insufficient evidence | `qualification_conflict_3`, `qualification_validation_4` | Resolve only completed hash-matching authority; withhold after a failed required validation. |

## Gates

- Both arms produce independently verified outcomes or correct evidence-based
  abstentions; report harness/provider failures separately.
- Findings has no quality regression and no stale-memory answer.
- Findings produces and consumes applicable learned state in at least five of six
  eligible cases.
- Findings reduces avoidable same-version reread lines by at least 50% in aggregate.
- Findings does not increase median model calls or provider cost.
- Every answer write is preceded by its required completed source/validation evidence;
  correct unsupported guesses do not pass.
- Report calls, cells, verification rounds, input/cached/uncached/output/reasoning
  tokens, provider cost, wall time, checkpoint/note lifecycle, retained-handle
  consumption, actual catalog reuse and interval-level new/reread lines.

Promotion remains manual and held unless every gate passes. Stop the cohort on the
existing infrastructure/accounting fail-fast conditions. Do not expand to DeepSWE or
change defaults from this run automatically.

## Cache interpretation fixed before dispatch

The preceding consumed-case pair had the same three phase-boundary cache resets and
normal append-only reuse within each epoch. Findings' 6,557 additional uncached tokens
came from larger memory-bearing packets at cut boundaries plus a cold first-call cache
miss, not continual stable-prefix displacement. This cohort remeasures the cost; it
does not add another cache mechanism.

## Result

The twelve trials ran concurrently at clean revision `5879471`. There were no
provider, infrastructure, accounting, false-acceptance or harness failures. Both arms
passed first independent verification on the partial, two changed-source and conflict
cases. Both produced correct routes artifacts but stopped before verification: control
at its 24-call ceiling and findings before a projected request crossed 350,000 input
tokens. Both withheld the answer after the required validation failed; the failed shell
effect remains conservatively unresolved, so this is `workflow_blocked`, not a qualified
safe abstention.

| Arm | Verified | Calls | Input | Uncached | Output | Reasoning | Cost | Median calls | Median cost | Median wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_recall` | 4/6 | 71 | 725,486 | 153,836 | 55,937 | 36,554 | $0.11700575 | 11 | $0.01826180 | 120.2s |
| `findings` | 4/6 | 73 | 845,092 | 181,880 | 54,782 | 32,317 | $0.12446169 | 12.5 | $0.02027868 | 131.2s |

Findings reduced aggregate post-cut same-version overlap from 82 to 40 lines (51.2%).
On the five non-routes cases it reduced overlap from 18 lines to zero. Every findings
trial completed its expected checkpoint protocol and published a cited note; the five
answering cases reused learned relationships while acquiring required new, changed or
previously missing evidence. Both changed-source cases and the conflict case passed,
with no stale-memory answer or unsupported acceptance.

The quality/no-stale and reread gates pass. The terminal, median-calls and median-cost
gates fail. Findings used two more calls overall, 16.5% more input, 18.2% more uncached
input and 6.4% more cost. It used 11.6% fewer reasoning tokens and 1.7% less aggregate
wall time, but median wall time was higher. Promotion therefore remains **hold**.

Trace diagnosis: the first model verification request in both routes arms entered the
required counterexample review. `learned_checkpoint_request@1` then asked findings to
checkpoint again solely because phase became `review`. Citation repair produced several
note-update cells; control instead reread sources. This is not a missing-memory problem:
the durable learned evidence already existed, and failed verification would create a real
future continuation. The follow-up correction excludes terminal review entry while
retaining plan/implementation and failed-verification requests.

Artifacts: `.artifacts/learned-memory-qualification-live-v1/results.json`,
`summary.json`, and each arm's `result.json`, `responses/`, `wire/`, and
`state/ledger.jsonl`. No paid run remains active.

## Frozen review-checkpoint diagnostic

Rerun only consumed `qualification_routes_3` at the tested correction revision, with
the same two arms, model, reasoning, limits and immutable command image. Dispatch the
pair concurrently to `.artifacts/review-checkpoint-diagnostic-live-v1`, label it
diagnostic, and do not substitute or retry. The canary succeeds only if both arms reach
independent verification, findings emits no review-entry checkpoint request, and the
previous post-review note-repair loop disappears. This can test the diagnosed mechanism;
it cannot add held-out reliability evidence or reverse the full cohort's promotion hold.

### Diagnostic result

The frozen pair ran once at commit `7a3a9d9`. Both arms wrote all three correct answer
artifacts, but neither reached independent verification: control hit the 24-call limit
and findings exhausted the projected 350k task-input budget after 21 calls. Findings
reduced summed post-cut same-version overlap from 30 to 20 lines (33.3%) and used three
fewer calls, but input rose from 283,951 to 326,477 tokens, uncached input from 52,180
to 57,316, cost from $0.03544162 to $0.03897027, and wall time from 176.7s to 181.2s.

The narrow checkpoint hypothesis was confirmed: findings emitted no review-entry
checkpoint and no post-review note-repair loop. The terminal hypothesis failed. Trace
inspection found a separate protocol problem. Findings requested verification with the
correct answer and learned transformation but truncated the only criterion ID by one
character, so the fail-closed admission logic rejected it. Control used the exact ID.
Both then spent multiple calls inside the model-owned counterexample review; findings
reread the final branch and wandered into an unrelated branch, while control reread and
rechecked all answers. No verifier report was produced in either arm.

The correction bounds counterexample review to one PTC cell before a mandatory fresh
structured decision. It carries exact valid claim evidence into an ID scaffold; malformed
IDs remain rejected and receive placeholders rather than fuzzy repair. A review mutation
or discovered defect must be reported by the model before verification. This preserves
the independent verifier and unresolved-effect gates while preventing open-ended review
reacquisition. A new fresh-case canary is required; this failed frozen pair will not be
retried. Artifacts: `.artifacts/review-checkpoint-diagnostic-live-v1/`.

## Frozen bounded-review fresh canary

Run previously unused `qualification_routes_5` once at the correction revision with
the same `no_recall` and `findings` arms, OpenRouter model/reasoning, 24-call limit,
350k task-input budget, 8,192-token output limit, 900-second wall limit, concurrency two,
and immutable sandbox image. Write to
`.artifacts/bounded-review-fresh-canary-live-v1`; do not substitute or retry.

This mechanism canary succeeds only if both arms reach independent verification with
correct source-supported artifacts and no unsupported acceptance. A model-owned review
work batch may contain at most one PTC cell. After the final answer, findings must not
read sources outside the requested branch and its summed same-version overlap must not
exceed control. Report calls, total/cached/uncached input, output/reasoning tokens, cost,
wall time, rejected claims and every terminal reason. Calls and cost are diagnostic, not
promotion gates. This single fresh pair cannot reverse the six-pair promotion hold.

### Fresh canary result

The pair ran once at commit `28fbca7`. Control reached first-pass independent
verification in 22 calls. Findings wrote all three correct, source-supported artifacts
but stopped at the projected input budget after 23 calls and 308,979 actual input tokens;
no verifier ran. The bounded review mechanism itself fired exactly once per arm. Control
returned the required structured decision and verified. Findings completed its one review
cell using only the three answer files—no post-answer source reacquisition—but the metrics
preflight ran before the host-authored boundary and stopped the run.

Findings reduced summed same-version overlap from 34 to 30 lines and uncached input from
60,882 to 60,194; reasoning fell from 8,305 to 7,493 tokens. Total input rose from 257,022
to 308,979, calls from 22 to 23, cost from $0.03519840 to $0.03618355, and wall time from
149.7s to 158.1s. The terminal gate therefore fails despite correct artifacts and modest
reread/reasoning improvements. Artifacts:
`.artifacts/bounded-review-fresh-canary-live-v1/`.

Trace inspection found that learned findings were never usable at any question boundary.
The preparation note used reserved `memory query` and `memory note write` commands through
`agent.shell.run`. Their *requested* capability receipts inherited generic shell's
`workspace_may_have_changed=true`, even though completed managed-memory receipts correctly
reported no workspace effect. Those requests advanced the unknown-mutation watermark past
the source captures, so the memory view labeled every transformation
`revalidation_required` and `usable_as_current_fact:false`. The model correctly reread each
branch. This is self-invalidation at the PTC/memory provenance boundary, not retrieval
noncompliance.

The follow-up classifies reserved managed-memory requests as workspace effect `none`,
allows `agent.state.describe('read:N')` directly, warns against duplicate aggregate note
citations, and exempts a pending host-authored work-batch yield from provider input-budget
preflight. Deterministic tests cover all four contracts. A new fresh pair is required to
show that findings remain usable and actually replace source reacquisition.

## Frozen managed-memory freshness canary

Run previously unused `qualification_routes_7` once at the tested provenance-fix
revision, pairing `no_recall` and `findings` at concurrency two with the same OpenRouter
model/reasoning, immutable image, 24-call, 350k-input, 8,192-output and 900-second limits.
Write to `.artifacts/managed-memory-freshness-canary-live-v1`; do not retry or substitute.

Both arms must reach first-pass independent verification with correct source-supported
artifacts and no unsupported acceptance. At all three post-cut question boundaries,
findings must expose the requested branch transformation as usable—not
`invalidated_findings` or `revalidation_required`—and must perform zero same-version
source reread lines for those branches. Its counterexample review must remain bounded and
must not read source outside the final requested branch. Report calls, total/cached/
uncached input, output/reasoning tokens, cost and wall time. Findings calls and cost must
not exceed control for this canary to demonstrate net efficiency, but this single pair is
still insufficient for default promotion.

### Managed-memory freshness canary result

Both arms reached first-pass independent verification at commit `f8d634b`, so quality
was 2/2. The efficiency and reread gates failed. Control used 22 calls, 261,162 input
tokens (57,519 uncached), 11,546 output tokens, 7,064 reasoning tokens, $0.03230451 and
120.9 seconds. Findings used 24 calls, 320,333 input tokens (67,184 uncached), 13,286
output tokens, 7,398 reasoning tokens, $0.03779858 and 131.3 seconds. Summing each
post-cut audit, control reread 50 same-version source lines and findings reread 60.

Reserved memory commands no longer self-invalidated findings, and retained-handle
description worked. Two later ordinary shell checks nevertheless made all source-backed
findings unusable. Their canonical `tool.bash` receipts proved successful complete
execution with identical before/after workspace fingerprints, but the capability events
did not carry the receipt identity and the freshness projection ignored those receipt
rows. The model therefore correctly reacquired source. Artifacts:
`.artifacts/managed-memory-freshness-canary-live-v1/`.

The correction joins each completed shell capability to its same-task canonical receipt.
Only a completed bash receipt with the exact identity, successful integer-zero exit,
complete output and equal valid workspace fingerprints retracts that operation's
provisional uncertainty. Earlier or intervening unknown effects, changed fingerprints,
failed/truncated output, missing/mismatched identities and malformed receipts remain
unknown. This affects advisory source freshness only; it does not admit execution or
completion.

## Frozen receipt-backed freshness canary

Run previously unused `qualification_routes_9` once at the receipt-join revision, pairing
`no_recall` and `findings` at concurrency two with the same model, maximum reasoning,
immutable image, 24-call, 350k-input, 8,192-output and 900-second limits. Write to
`.artifacts/receipt-backed-freshness-canary-live-v1`; do not retry or substitute.

Both arms must reach first-pass independent verification. At every post-cut question,
findings must remain usable after successful no-change shell checks and avoid rereading
the requested branch source. Report all quality, call, token, cost, wall, freshness and
read-overlap metrics. Findings calls and cost must not exceed control. This single
mechanism pair cannot establish broad reliability or change defaults.

### Receipt-backed freshness canary result

Findings reached first-pass independent verification in 16 calls. Control wrote passing
artifacts but hit the 24-call ceiling before verification, so accepted quality was 1/1
versus 0/1. Findings used 214,804 input tokens (57,616 uncached), 7,741 output tokens,
4,792 reasoning tokens, $0.02683456 and 89.9 seconds. Control used 299,274 input tokens
(46,503 uncached), 13,065 output tokens, 6,216 reasoning tokens, $0.03235557 and 134.4
seconds. Findings therefore reduced calls 33.3%, total input 28.2%, output 40.7%,
reasoning 22.9%, cost 17.1% and wall time 33.1%; uncached input rose 23.9%.

All three answer writes used historical-snapshot findings without source reacquisition.
The final mandatory counterexample review then read 14 same-version lines: all ten lines
of unrelated branch 0 and four lines of the requested branch 1. Counting each actual
post-first-cut read once, findings reread 14 lines versus control's 20. The existing
sum-over-each-cut projection reports 42 versus 30 because the one final findings read is
downstream of all three cuts and is counted three times. Both measurements are retained;
neither is semantic exposure.

This pair demonstrates usable learned transformations and a material quality/cost/call
gain, but it fails the zero-reread review gate. It also did not exercise the receipt join:
the model issued only the reserved memory shell command and no ordinary shell check.
The result cannot validate the diagnosed no-change-receipt mechanism.

The common review instruction now says to challenge completed evidence in place and to
acquire source only for a specifically identified missing, stale or contradictory fact
within the cited criterion scope. It keeps the one-cell bound and all independent
verification gates.

## Frozen targeted receipt-and-review canary

Run previously unused `qualification_routes_11` once, pairing `no_recall` and `findings`
at concurrency two under the unchanged model, reasoning, image and resource ceilings.
Both arms must run exactly `git status --short` once after the durable preparation
checkpoint and before `LEARNING_COMPLETE`; the completed receipt must show no workspace
change. Write to `.artifacts/targeted-receipt-review-canary-live-v1`; do not retry.

The pair succeeds only if both arms have correct source-supported artifacts, findings
reach first-pass independent verification, the findings visible at all three questions
remain historical snapshots after the shell check, and findings performs zero
same-version source reads after the first cut, including counterexample review. Report
calls, tokens, cost and wall time, but do not treat a control resource stop as evidence
of memory reliability. One targeted pair cannot promote defaults.

### Targeted receipt-and-review canary result

Both arms wrote correct source-supported artifacts but missed terminal acceptance:
control hit 24 calls and findings exhausted the 350k projected input budget after 21
calls. Control used 306,962 input tokens (51,191 uncached), 18,740 output tokens, 9,962
reasoning tokens, $0.04039757 and 171.3 seconds. Findings used 312,875 input tokens
(50,300 uncached), 13,060 output tokens, 6,447 reasoning tokens, $0.03349535 and 124.2
seconds. Findings reread 20 same-version source lines after the first cut, equal to
control, so the gate failed despite lower cost and latency.

Both arms ran the required no-change command after their checkpoint. The findings shell
capability carried its exact completed receipt, whose workspace fingerprints matched.
The receipt join itself worked, but it could not preserve dependencies that were absent
from the note. Trace analysis found a last-write-wins bug in note construction: after the
model recovered read-result artifacts with `artifacts.load`, those later artifact-use
rows shadowed the original `fs.read` rows under the same content URI. The note retained
the correct text and citations but stored empty `source_dependencies`. It therefore
could not demonstrate source-backed freshness after the shell check.

Note construction now keeps source-bearing read identity across later references to the
same artifact URI. If one URI maps to conflicting read evidence, the note fails closed
instead of selecting either. This is a provenance-construction correction, not a prompt
change.

## Frozen source-identity canary

Run previously unused `qualification_routes_13` once with the same targeted
post-checkpoint no-change command, paired arms, model, maximum reasoning, immutable image
and resource ceilings. Write to `.artifacts/source-identity-canary-live-v1`; do not retry.
Require source dependencies on all four learned transformations, historical-snapshot
freshness after the shell receipt, correct source-supported answers, first-pass
verification for findings, and zero post-cut source rereads through review. Report the
full quality/efficiency metrics; one targeted pair remains insufficient for promotion.

### Source-identity canary result

Findings reached first-pass independent verification in 23 calls; control wrote passing
artifacts but hit the 24-call ceiling. Source construction passed: each of the four
learned branch entries carried exactly three dependencies, and the post-note shell had a
successful complete receipt with identical workspace fingerprints. Nevertheless,
findings reread all 40 source lines after the first cut versus ten for control. It used
315,622 input tokens (65,391 uncached), 15,269 output tokens, 9,364 reasoning tokens,
$0.03967172 and 151.0 seconds, versus control's 282,082 input (41,366 uncached), 8,618
output, 4,843 reasoning, $0.02549382 and 98.8 seconds.

The shell receipt preserved the initial note correctly. Each later answer-file write,
however, began with a conservative `fs.write` request that set task-wide uncertainty.
Its successful terminal receipt scoped the actual change to `answers/use_1.json`,
`answers/use_2.json`, or `answer.json`, but freshness never retracted the request-wide
watermark. The following question therefore saw unrelated pipeline dependencies as
`revalidation_required` and rationally reread them. The review prompt also permitted its
final reread because the harness itself had mislabeled the evidence stale.

Successful `fs.write`/`fs.edit` terminals now replace only their matching request's
provisional task-wide uncertainty with exact path-specific observations. A source edit
still invalidates that source; failed, malformed, unscoped, unmatched or intervening
unknown effects remain task-wide unknown.

## Frozen final scoped-write canary

Run previously unused `qualification_routes_15` once with the same paired arms,
post-checkpoint no-change shell, model, reasoning, image and ceilings. Write to
`.artifacts/scoped-write-canary-live-v1`; do not retry. Require four source-dependent
findings, successful receipt-backed shell freshness, correct completed evidence before
each answer, first-pass verification for findings, and zero same-version source reads
after the first cut through review. Report all quality and efficiency metrics. Stop and
analyze after this fourth mechanism iteration rather than adding another canary.

### Final scoped-write canary result

The mechanism gate passed. Findings reached first-pass independent verification in 17
calls. All four transformations retained three exact source dependencies each; the
post-note `git status --short` receipt completed with equal workspace fingerprints;
the final task-wide unknown watermark was zero; and every dependency remained a
historical snapshot across all three cuts and answer writes. There were zero post-cut
pipeline source reads. The only post-cut read was the newly written one-line
`answer.json` during review. This is the first live run in the sequence to demonstrate
source-backed learned-memory use through shell validation, repeated worker loss, scoped
answer writes and terminal review without source reacquisition.

Control exhausted the input budget after 21 calls, completed only two cuts, failed the
answer outcome, and reread ten same-version source lines. Findings used 302,226 input
tokens (71,137 uncached), 16,759 output tokens, 9,846 reasoning tokens, $0.04251428 and
155.9 seconds. Control used 326,196 input (43,749 uncached), 17,269 output, 7,007
reasoning, $0.03730584 and 152.3 seconds. Findings reduced calls 19.0%, total input 7.3%,
output 3.0%, and source rereads from ten to zero, while uncached input rose 62.6%,
reasoning rose 40.5%, cost rose 14.0%, and wall time rose 2.4%. Since control did not
reach the same terminal stage, these efficiency deltas are descriptive rather than a
clean paired estimate.

Across the four fresh mechanism iterations (`routes_9`, `routes_11`, `routes_13`, and
`routes_15`), findings independently verified twice; the other two wrote correct
artifacts but stopped at the input budget. The iterations successively exposed review
reacquisition, artifact-reference source shadowing, and unretired file-write request
uncertainty. The final run closes all three on one targeted case. It demonstrates that
the current implementation can use learned memory reliably and eliminate rereads under
this protocol, but it does not establish broad reliability or a cost win. Expansion
should use a frozen diverse cohort rather than further routes tuning.

Artifacts: `.artifacts/receipt-backed-freshness-canary-live-v1/`,
`.artifacts/targeted-receipt-review-canary-live-v1/`,
`.artifacts/source-identity-canary-live-v1/`, and
`.artifacts/scoped-write-canary-live-v1/`.
