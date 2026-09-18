# Corrected memory/PTC paired evaluation

Status: complete. The original panel, calibrated canary, same-revision twenty-pair
rerun, and three-model forced-compaction follow-up are retained below.
The first canary failed before provider dispatch because ADK's response-schema
class was not JSON serializable in the new estimator. The shared estimator now
expands Pydantic schemas and excludes host-only tool objects; both context and
telemetry paths have regression coverage. The failed run is retained at
`~/skein-eval-results/memory-fixed-canary-20260911`.
The second canary reached 25 successful cells, but the note request came too late:
it stopped at the hard context limit with no populated note. Both arms now request
a note at the soft limit, even within an unchanged phase; only the treatment
compacts at phase transitions. The second run is retained at
`~/skein-eval-results/memory-fixed-canary-v2-20260911`.
The third canary demonstrated a populated note, phase-boundary compaction
(56,251 to 2,684 estimated tokens), and continued cells, then failed when an
indivisible new call/result exceeded the soft packet target. Selection now permits
that pair up to the remaining hard window, including output reservation. The
control arm started after activation was observed; this subsequent correction
changes only the compaction path (disabled in controls). Record both revisions
rather than claiming an identical revision. No treatment trials started before
this correction. Retain the third canary at
`~/skein-eval-results/memory-fixed-canary-v3-20260911`.

## Frozen comparison

Twenty DeepSWE 1.1 tasks, one attempt per task per arm. Both arms use notebook
PTC, active lexical retrieval, working notes, and owned prior-run recall enabled.
Only context compaction differs: `notebook-ptc-memory-no-compaction.yaml` versus
`notebook-ptc-memory.yaml` (phase-boundary timing, 20k soft packet, 128k hard
context estimate). Defaults outside these experiment profiles remain unchanged.

Both use OpenRouter `meta/muse-spark-1.3-contributor`, reasoning `xhigh`, 32,768
maximum output tokens per call, 8,000,000 cumulative input tokens, 7,200 seconds
per trial, and no trial retries. Three concurrent trials per arm, six total.
These larger budgets differ from the historical six-task runs; those are diagnostic
evidence, not a matched control. Provider throttling retries retain existing policy.

Canary: `textual-kitty-key-phases` from the smoke manifest, excluded from the
twenty-task panel. Gate: populated note, successful compaction, subsequent model/tool
execution, no new deterministic harness fault. A benchmark pass is reported separately.
Do not launch the panel if this gate fails; diagnose and retain the failed canary.

## Selection, frozen before results

Source: `tests/eval/manifests/evaluation-confirm-v1.json`, manifest hash
`f30a3bb6245a360820be5684ed98d001cea460169168633c18487ef28d48fad4`.
Within each language/difficulty stratum, take ascending `selection_rank`:
Python 3 easy/2 medium/1 hard; TypeScript 2 of each; Go 2 easy/1 medium/1 hard;
Rust 2 hard; JavaScript 1 easy/1 medium. Total: 8 easy, 6 medium, 6 hard.
Existing manifest artifact hashes pin task content. Selection is broader, not an
independent holdout: Koota and Testem overlap earlier diagnostic runs.

| Task | Language | Difficulty |
| --- | --- | --- |
| happy-dom-deterministic-intersectionobserver | TypeScript | medium |
| pwntools-tube-multiplexing | Python | medium |
| tengo-destructuring-bindings | Go | medium |
| anko-default-function-arguments | Go | easy |
| langchain-request-coalescing | Python | medium |
| obsidian-linter-scoped-ignore-markers | TypeScript | easy |
| koota-pair-relation-tracking | TypeScript | hard |
| query-persist-restored-query-state | TypeScript | easy |
| textual-richlog-follow-state | Python | easy |
| ytt-jsonpath-query-api | Go | easy |
| pest-character-class-coalescing | Rust | hard |
| ink-grid-box-layout | TypeScript | hard |
| psd-tools-blend-range-api | Python | easy |
| testem-per-launcher-reports | JavaScript | easy |
| katex-multicolumn-array-spans | JavaScript | medium |
| clack-async-autocomplete-options | TypeScript | medium |
| bandit-structured-nosec-directives | Python | hard |
| oxvg-structural-selector-preservation | Rust | hard |
| returns-validated-error-accumulation | Python | easy |
| updo-policy-alerting | Go | hard |

## Readout

Retain official rewards and all failures for all 40 trials. Report paired
wins/losses/ties and pass counts, not just aggregate success. Separate harness,
provider, model-quality, and environment/verifier failures without silently
dropping any from the denominator. Report calls, input/uncached/output/reasoning
tokens, provider cost (including incomplete-response usage when supplied), active
wall time, cache ratio, prefix versions, retries, duplicate effects, verification
rounds, and terminal reasons. Inspect note writes, retrieval calls, compaction
triggers, handoff content, continued work, and PTC errors/repeated reads.

Each trial has isolated state; neither arm reads the other's history or verifier
answers. Prior-run recall is enabled but cold in isolated benchmark trials: this
panel does not validate its benefit. A future owned-history replay pair is needed
for that claim. With twenty pairs, treat quality differences as directional;
promote compaction only if activation is demonstrated without unexplained
regressions and with a useful efficiency benefit.

## Corrective execution stages

### Stage 0 — freeze the diagnostic baseline (complete)

The original panel finished 20/20 in both arms. Control passed 4/20 with 1,288
model calls, 99.78M input tokens, 2.67M uncached input tokens, $0.660 provider
cost, and 11,512 seconds active wall time. The compaction arm also passed 4/20
(two paired wins, two losses, sixteen ties), but used 2,388 calls, 74.86M input
tokens, 5.37M uncached input tokens, $0.991, and 21,192 seconds. It published
104 compaction epochs. Task-input-budget exits and plugin failures remain separate
terminal categories in every later readout.

Across the first fourteen treatment tasks, 853 of 916 `fs.read` calls after a
cut revisited a path read before that cut; 795 also matched the earlier file hash,
and 379 matched path, hash, offset, and returned line count. These are repeated
exposures, not automatically classified as unnecessary work. Capability events and
their result artifacts are the authority for notebook reads.

### Stage 1 — calibrate triggers and telemetry (complete)

- Use the previous provider-reported input count plus the estimated new-turn delta.
- Trigger at 80% of configured input capacity after reserving output tokens.
- Permit phase-boundary cuts only above half that threshold.
- Record the estimate source and threshold in each compaction event.
- Compact with deterministic evidence when a working note is missing at the threshold;
  note absence alone is recoverable pressure.

OpenRouter publishes a 1,048,576-token context window for
`meta/muse-spark-1.3-contributor`; the treatment profile records that value. The
198k request observed in control is retained only as an empirical lower bound.

### Stage 2 — preserve continuity and remove avoidable work (complete)

- Budget the exact recent tail independently from the 20k handoff-header budget and
  retain up to 32k estimated tokens.
- Incrementally capture appended ADK content rather than serializing retained objects
  on every callback.
- Report whether the PTC worker is live and its actual kernel epoch. A matching live
  epoch is `live`; a stopped/replaced worker is pending safe restoration; only an
  unknown effect requires reconciliation.
- Keep call/result pairs indivisible and retain existing artifact-backed output bounds.

### Stage 3 — mechanism canary (complete)

Run `koota-pair-relation-tracking` with a declared 262,144-token canary ceiling.
With the 32,768 output reservation, the 80% hard trigger is about 183.5k and phase
changes become eligible above 91.75k. This deliberate canary override exercises the
boundary; it is not a claim about the provider limit. The run published one phase cut
at 113,769 projected versus 112,656 provider tokens, retained the same live kernel
epoch, and continued for 27 model calls without a plugin exception. It used 65 calls
versus the original Koota control's 73. Eleven post-cut reads revisited unchanged
paths and five repeated the exact range; total reads were 73 versus control's 81.
The official reward remained 0 and the harness ended blocked, so this is continuity
evidence rather than a quality win.

### Stage 4 — checkpoint escalation (not activated)

If the canary still shows material rediscovery, add one metered, evidence-bound,
model-written checkpoint at the cut, after deterministic required metadata. It must
remain advisory, include changed-file/diff and test evidence plus the live state
manifest, be stored with its prompt/model/settings/watermark identity, and update the
working note. Do not add this model call if the larger exact tail and fresh note are
sufficient. Never replay an effect-unknown operation to recover context. The Koota
canary did not reproduce the old balloon and the exact tail kept rereading bounded,
so an extra summarizer call has not earned activation. Revisit this gate only if the
stress pair shows material rediscovery.

### Stage 5 — frozen rerun (complete)

After the canary gate, restore the published 1,048,576-token model limit, run both
20-task arms from one clean revision at three trials per arm, and retain all forty
results. Report dormant-plugin overhead separately if no task reaches the real
threshold. Add a three-task 262,144-token stress pair (Koota, Obsidian, Oxvg) only if
the full panel has no compaction activation; this is the minimal quality test of an
actual cut under the current corpus.

The published-window rerun used revision
`d75ceb2e4f6c9cbd6082c20475d1b0a186c80341`. Control passed 3/20 with 1,216
calls; treatment passed 4/20 with 1,261 calls. Pairwise treatment had two wins,
one loss, and seventeen ties. Neither arm compacted, so this measures dormant
plugin overhead and run variance rather than active compaction. Each arm had five
8M task-input-budget exits; treatment also had two criterion-decomposition harness
errors.

The forced 262,144-token Muse pair activated five phase-boundary cuts. Control
passed 0/3 with 186 calls and cost $0.106; treatment passed 1/3 with 347 calls and
cost $0.168. Of 146 post-cut reads, 130 revisited a path read before the latest cut,
122 also matched its content hash, and 61 repeated the exact range. The quality win
on Obsidian therefore came with material rediscovery and call amplification.

### Stage 6 — cross-model forced-compaction follow-up (complete)

Repeat the same Koota, Obsidian, and Oxvg pair with OpenRouter GPT-5.6 Luna and
GLM-5.3 Flash at `max` reasoning. Both models used the same revision, manifest,
task hashes, 32,768 output cap, 8M cumulative-input budget, no retries, and recorded
262,144-token treatment diff
`dfa6ae33d7a38772336f4c9e4d3a81b6e42d8f90688c7afcfa18b77ca26e182a`.

| Model | Arm | Pass | Calls | Input | Uncached | Output | Cost | Cuts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Muse Spark 1.3 Contributor | control | 0/3 | 186 | 16.96M | 428k | 151k | $0.106 | 0 |
| Muse Spark 1.3 Contributor | compaction | 1/3 | 347 | 20.26M | 820k | 233k | $0.168 | 5 |
| GPT-5.6 Luna | control | 0/3 | 220 | 21.37M | 477k | 146k | $0.713 | 0 |
| GPT-5.6 Luna | compaction | 0/3 | 281 | 20.35M | 563k | 161k | $0.730 | 2 |
| GLM-5.3 Flash | control | 0/3 | 3 | 11.8k | 11.8k | 316 | $0.0019 | 0 |
| GLM-5.3 Flash | compaction | 0/3 | 3 | 11.8k | 9.5k | 411 | $0.0017 | 0 |

Luna did not escape the official-reward floor. Its two cuts reduced aggregate input
by 4.8% but increased calls by 27.7%, uncached input by 17.9%, output by 10.2%, and
active wall time by 11.7%. Seventy-five of 85 post-cut reads revisited a prior path;
53 matched the prior content hash and 35 repeated the exact range. Koota was an
unchanged 0.819 partial score with or without a cut; Oxvg remained 0.912; Obsidian
fell from 0.999 without a cut to 0.972 and exhausted the treatment input budget.

GLM is not a compaction result. Every trial stopped after one model call without an
`execute_code` call. It returned prose about starting work or asking for tools as a
terminal step. Koota also exposed an internal-verifier false completion: unchanged
baseline tests were treated as criterion evidence despite zero changed paths, while
the official DeepSWE reward correctly remained zero. This compatibility and
verification failure must be fixed before GLM can participate in a memory ablation.

The cross-model result preserves the mechanism conclusion but not a quality-promotion
claim: deterministic phase cuts preserve kernel continuity, yet both models that
actually used PTC showed substantial post-cut rediscovery and no repeatable quality
gain. Keep the real 1,048,576-token trigger and the deterministic handoff; do not
promote forced compaction or add a model-written checkpoint until the terminal-step
guard and a smaller checkpoint/tail experiment pass.

### Stage 7 — deterministic-evidence Luna replicate (complete)

Revision `6f801c3aa8f843613905ac0bb8682b8117b25fa3` added two independently
tested guards: coding verification rejects zero changed paths, and successful PTC
reads publish bounded path/hash/range evidence that the next compaction handoff
indexes with recent validation receipts. The full deterministic unit suite passed
with 626 tests and one skip. The live run temporarily changed only the two profile
context ceilings to 262,144 tokens, recorded diff
`928ff7243c02961fd656eeab11dcde72ed53ff5a36499a44f3d090e749cdf5f1`,
then restored both production profiles to 1,048,576 tokens.

Six OpenRouter GPT-5.6 Luna `max` Obsidian trials ran concurrently: three independent
control runs and three independent compaction runs. Every run used one attempt, the
frozen confirm manifest, a 32,768 output cap, an 8M cumulative-input budget, and no
retry.

| Arm | Pass | Mean partial | Calls | Input | Uncached | Output | Cost | Active wall | Cuts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 1/3 | 0.9811 | 228 | 23.47M | 516k | 181k | $0.805 | 3,123s | 0 |
| compaction | 1/3 | 0.9903 | 218 | 22.64M | 556k | 182k | $0.799 | 3,057s | 2 |

The compaction arm reduced calls by 4.4%, aggregate input by 3.5%, active wall time
by 2.1%, and cost by 0.8%; uncached input increased by 7.7% and output was effectively
flat (+0.6%). These are descriptive replicate totals, not a powered estimate. Each arm
produced one perfect official pass. The remaining control trials both scored 0.9717;
the remaining treatment trials scored 0.9717 and 0.9991, the latter failing one of
1,133 regression tests despite passing all 33 feature tests.

Both treatment cuts occurred at the review phase, reducing estimated context from
168,014 to 33,602 tokens and from 177,836 to 34,534 tokens. Both published summaries
contained the new read-evidence manifest. All file reads had already finished, so
there were zero post-cut reads: this validates event capture and handoff wiring but
does not test whether the manifest prevents rediscovery. One treatment and all three
controls reached the 8M task-input guard; two treatment runs completed internally.
One budget-exhausted control patch nevertheless passed the official grader, confirming
that the budget exit must remain a separate terminal category from harness failure and
official task quality.

Retained result roots:

- `/Users/mathiasl/skein-eval-results/memory-luna-obsidian-manifest-control-r{1,2,3}-20260911`
- `/Users/mathiasl/skein-eval-results/memory-luna-obsidian-manifest-compaction-r{1,2,3}-20260911`

Do not expand this Obsidian replicate to twenty tasks yet. The smallest next mechanism
test is a paired Koota/Oxvg canary selected to cut before additional reads, followed by
a larger frozen panel only if post-cut repeated-path and exact-range reads fall without
quality regression. Separately, map the 8M guard to a structured task-input-budget
terminal outcome instead of the current `runtime_failed` label.

### Stage 8 — Koota/Oxvg evidence-manifest canary (complete)

At revision `3fea1dcd8a7e67966a5198a395efc6c70b6d376d`, four OpenRouter
GPT-5.6 Luna `max` trials ran concurrently: Koota and Oxvg once in each arm. The
frozen manifest, 32,768 output cap, 8M cumulative-input budget, no-retry policy,
and temporary 262,144-token context ceiling matched Stage 7. The recorded profile
diff remained `928ff7243c02961fd656eeab11dcde72ed53ff5a36499a44f3d090e749cdf5f1`;
production profiles were restored to 1,048,576 tokens afterward.

| Arm | Pass | Mean partial | Calls | Input | Uncached | Output | Cost | Active wall | Cuts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 0/2 | 0.8654 | 112 | 8.92M | 256k | 67k | $0.318 | 1,072s | 0 |
| compaction | 0/2 | 0.8654 | 191 | 11.84M | 411k | 108k | $0.460 | 1,830s | 2 |

Quality was identical task by task: Koota scored 0.8190 and Oxvg 0.9118 in both
arms. Both treatment cuts occurred during implementation and carried valid manifests:
Koota compacted 103,842 to 35,842 estimated tokens and Oxvg 157,690 to 37,264.
Koota subsequently made 36 reads; 34 revisited a prior path, 25 matched its prior
hash, and 10 repeated an exact range. Oxvg made 11 post-cut reads, all on a prior
path with the same hash and none on an exact prior range. Aggregate post-cut rates
were therefore 45/47 repeated path, 36/47 same hash, and 10/47 exact range.

The bounded manifest exposed eight recent read entries in each cut. Sixteen post-cut
reads revisited an exposed path, 15 with the same hash, but none repeated an exposed
exact range. All ten exact-range repeats referred to older ranges omitted by the
eight-entry cap. The current latest-eight representation therefore validates wiring
but fails the rediscovery gate: it preserves recent file identity, not enough range
coverage. Compared with the earlier pre-manifest Luna aggregate (75/85 repeated path,
53/85 same hash, 35/85 exact range), exact-range repetition fell descriptively while
path/hash repetition did not; single stochastic attempts cannot attribute that change
to the manifest.

Aggregate efficiency is not comparable as an optimization result because Koota control
blocked after 44 calls while treatment continued to 118 and exhausted the input budget.
On the less-confounded Oxvg pair, treatment reduced input 33.6% and cost 7.6%, but
increased calls 7.4%, uncached input 29.3%, output 41.8%, and active wall time 37.7%.
Both Oxvg runs blocked on reconciliation after a timed-out Cargo test. Koota control
also blocked on reconciliation; treatment reached the 8M task-input guard.

Retained result roots:

- `/Users/mathiasl/skein-eval-results/memory-luna-koota-oxvg-manifest-control-20260912`
- `/Users/mathiasl/skein-eval-results/memory-luna-koota-oxvg-manifest-compaction-20260912`

Do not start the twenty-task panel. First replace the latest-eight flat list with a
budgeted per-path range index so exact ranges are retained across more files, then rerun
this same four-trial canary. Independently classify task-input-budget exhaustion as a
structured terminal outcome; neither change should alter the model tool surface.
