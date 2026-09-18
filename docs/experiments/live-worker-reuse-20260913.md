# Live-worker reuse diagnostic

Status: complete after 3 of at most 4 iterations; iteration 4 was not needed.

## Isolated question

Can Luna/max answer successive questions by computing from applicable completed
source values retained in one unchanged PTC worker, instead of reacquiring the same
source content? This does not test semantic notes, context compaction, worker-loss
recovery, or prior-run recall.

The baseline suppresses changed-only state notices. The locator arm enables the
broker-attested `content_expression` notice from `5544174`. Both arms retain the same
worker, source receipts, notebook, static instructions, artifact access, broker,
verification, token limits and disabled memory configuration.

## Iteration 1

- Cases: repository call paths, configuration precedence with a later authorized
  source revision, signed reconciliation, and an initially missing source range.
- One fresh-state repetition per case/arm: 8 trials, concurrency 6.
- Model: `openai/gpt-5.6-luna`, reasoning `max`; 24 calls, 350,000 cumulative
  input tokens, 8,192 output tokens and 900 active seconds per trial.
- Cached Docker image only; no pull. Expected values remain in the host-owned oracle.
- Fresh outputs: `.artifacts/live-worker-reuse-iteration-1-live`; dry manifest:
  `.artifacts/live-worker-reuse-iteration-1-dry-v4`. Never overwrite or selectively
  retry a trial. Provider/infrastructure failure stops queued dispatches.

Observed live result: all eight answer oracles passed first verification, but only
seven trials satisfied the isolated live-worker contract. The repository locator
trial raised an execution-stage assertion after writing its second answer, discarded
the epoch, loaded completed source artifacts, and finished correctly. That is valid
recovery, not live-worker reuse. The two reconciliation trials had one source-
validation rejection each for a blocked `io` import; both explicitly preserved the
same epoch and subsequently used completed source reads.

Across the seven intact trials, all 17 delayed questions used retained source values
without reacquiring any already-covered source range. Both changed-configuration
trials read the new `environments.toml` version exactly once; both missing-range
trials read only the uncaptured `settings.toml` tail and retained `dispatch.toml`.
The baseline therefore already had zero avoidable source rereads, so iteration 1
cannot attribute a reread reduction to locators. All eight locator/control answers
were correct, but the all-trial locator arm cost $0.07944782 versus $0.06840826
(+16.1%), used 548,885 versus 437,062 input tokens (+25.6%), and made 44 versus 43
model calls. Excluding the disqualified repository pair, locator cost was 4.0% lower;
that post-hoc subset is diagnostic only.

The dry and live manifests differ only in `sandbox_image`: the dry manifest records
`null`, while the live invocation records the verified cached image digest. Revision,
diff hash, fixture hashes, driver hashes, model, reasoning, limits, cases, and arms
match. The evaluator now reports a live-worker integrity gate and refuses to label a
correct recovered answer as a qualifying live-worker completion. The next iteration
also tells the model to finish source-dependent calculations/assertions before a
later mutation cell; the failed trace showed that an assertion after a write both
left the external answer effect and destroyed the live epoch.

## Iteration 2

The exact eight-trial panel was repeated at clean revision `c0705cc`. All eight
trials passed first verification and the new integrity gate: one kernel epoch each,
no disruptive cell, compaction, working note, unresolved execution, missing usage or
extra provider attempt. The two reconciliation runs each had one pre-execution blocked
`io` import and explicitly preserved their epoch.

Again, neither arm reacquired any covered unchanged source. Each configuration arm
read only the authorized new `environments.toml` version after its revision, and each
missing-range arm read only the unseen `settings.toml` tail. Submitted answer cells
directly loaded retained source mappings, parsed configs, or used retained aggregate
tables. Thus the no-notice arm is 8/8 intact across two cohorts and 20/20 eligible
delayed questions without an avoidable source reread.

Locator remained more expensive: $0.07615374 versus $0.07391779 (+3.0%), 50 versus
46 model calls, and 587,740 versus 504,326 input tokens (+16.5%). Since eager notices
cannot reduce below zero rereads and regressed aggregate cost twice, they are now
disabled by default. Iteration 3 will live-test the configured on-demand default
against an explicitly enabled eager-locator arm; it is confirmation of the selected
default, not another attempt to tune notice contents.

## Iteration 3 and decision

At clean revision `18a611d`, the actual configured `on_demand` default was compared
with explicitly enabled eager locators. On-demand passed 4/4 with one kernel epoch;
eager locators passed the answer oracle in 4/4 but only 3/4 satisfied live-worker
integrity. Its repository run raised an incorrect source-dependent assertion, lost
the epoch, recovered from artifacts and was therefore classified
`live_worker_contract_violated`. This independently repeats iteration 1's failure.

Neither arm had an avoidable same-version source reread. The changed-version and
missing-range controls again performed exactly the required acquisitions. Every
answer in all three iterations had completed source evidence before its managed write,
passed first independent verification, and had no unresolved effect or unaccounted
provider attempt. Public submitted cells in the on-demand arm directly used retained
values—for example `callers_from_retained_source`, retained configuration texts and
effective maps, signed reconciliation row tables, and the captured settings/dispatch
texts—rather than relying on absence-of-read inference alone.

Iteration 3 cost $0.07074154 on-demand versus $0.09194995 eager (+30.0%), with 44
versus 55 calls and 495,855 versus 738,428 input tokens. Across all three cohorts,
on-demand/no-notice was 12/12 intact and 30/30 eligible delayed questions reused
completed values with zero avoidable reread lines. Eager locators were 10/12 intact
and 24/30 clean-window questions, also with zero avoidable reread lines, while costing
$0.24755151 versus $0.21306759 (+16.2%), making 149 versus 133 calls, and consuming
1,875,053 versus 1,437,243 input tokens (+30.5%).

Decision: keep eager per-cell binding notices opt-in and use the on-demand default for
an intact live worker. There is no reread improvement left for eager locators to earn:
the control is already at zero, while notices regress cost and worker reliability.
This closes only live-worker reuse. It does not qualify learned findings, compaction,
worker-loss recovery, working notes or prior-run recall; those require separate goals
with their own failure-inducing boundaries.

## Gates and iteration budget

For every trial, require correct artifacts, completed applicable evidence before
each write, first independent verification, no unresolved effect, no context cut,
no worker loss, no working note, complete usage/cost accounting and one kernel epoch.

For each delayed-answer window, classify every source acquisition by path, version
and range. A reuse-eligible question has complete same-version coverage available
before its boundary. A successful locator result requires at least 90% of eligible
questions to avoid reacquiring covered content, at least 50% fewer avoidable reread
lines than baseline when baseline has nonzero rereads, and no aggregate cost increase.
If baseline already has zero avoidable rereads, locator must also have zero; that
supports live-worker reliability but does not attribute an improvement to locators.

The changed-source question must acquire the new version while reusing unchanged
sources. The missing-range question must acquire the uncovered range while reusing
the fully captured dispatch source. Required final verification reads are reported
separately and are not removed to improve the metric.

Inspect public provider inputs, submitted cells, canonical read/effect receipts,
answer windows and verifier reports. A locator appearing in a prompt is not use.
After iteration 1, change only a trace-supported mechanism. Stop on success or after
four total live diagnostic iterations. Fresh held-out confirmation is required before
claiming broader reliability; no default promotion or DeepSWE expansion follows.
