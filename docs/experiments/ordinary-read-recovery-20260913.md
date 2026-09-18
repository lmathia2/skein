# Ordinary-cut recovery diagnostic

Status: closed on clean `08df057`; supervisor exited zero and both arms pass first
independent verification. Findings costs 13.23% less but rereads 285 tracked source
lines versus control's 284; the joint gate fails. The control makes six artifact
loads without avoiding subsequent filesystem reads; findings makes none. All six
handoffs contain the recipe, and all 21 same-epoch prefix checks pass. Report:
`.artifacts/ordinary-read-recovery-live-v1/analysis.md`. No held-out or default-promotion
claim. Runtime changes are `1c7f4b7` and `aeb4ba5`; their full regression passed
1,247 tests with three skips before dispatch.

## Question

Will the live model use the completed-read load/decoding instructions now included
in ordinary handoffs, and will findings reduce tracked rereads without increasing
cost or weakening completed-evidence verification?

Apply ordinary recovery and accurate citation-rejection guidance to both arms.
Retain the existing `no_recall` versus `findings` distinction. This is a common-fix
diagnostic, not an individual causal ablation of those two changes. The prior
common-fix pair remains negative evidence: equal 171-line rereads, with findings
costing 147.2% more.

## Frozen scope and budgets

- One consumed `qualification_ordered_rules` case, one fresh-state repetition per
  arm, two trials in parallel; no selective retries or better-attempt selection.
- OpenRouter `openai/gpt-5.6-luna`, reasoning `max`.
- Per trial: 24 calls, 350,000 cumulative input tokens including pre-dispatch
  reserves, 8,192 output tokens per call, 900 seconds active wall.
- Aggregate ceilings: 48 model calls and 700,000 input tokens. Include acquisition,
  note construction, recovery, repair, review and verification in all accounting.
- Cached Docker only, no image pulls:
  `sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
- Run the existing `evals.verified_continuity --diagnostic`; new output root
  `.artifacts/ordinary-read-recovery-live-v1`, with a separate dry manifest under
  `.artifacts/ordinary-read-recovery-dry-v1`. Never overwrite an earlier cohort.
- Compare the frozen manifest with
  `.artifacts/continuity-common-fixes-live-v1/manifest.json`. Apart from the clean
  runtime revision, fixture/driver/model/budget contracts must be identical.

## Evidence and stopping gates

1. Both results terminal; six source-supported correct answer submissions; both
   independent verifications accepted; all six planned worker-loss cuts exercised.
2. No false acceptance, unknown effects, missing usage/cost, unaccounted model calls,
   extra wire attempts or measurement errors. Any such outcome holds paid expansion.
3. Inspect actual post-cut submitted code and broker receipts for artifact recovery,
   exact-citation use and source recomputation. Prompt availability alone is not use.
4. Compare same-version filesystem read intervals at each cut and label shell or
   artifact exposure unknown where unmapped. Artifact loading can avoid a filesystem
   reread without reducing source bytes reacquired or emitted; distinguish those.
5. Report calls, input/uncached/output/reasoning tokens, cost, active wall, all
   terminal reasons, verification rounds, source writes and repeated preparation
   acknowledgements. Do not strip required checks to save calls.
6. Verify every outgoing same-epoch input prefix. Preserve all failed attempts and
   budget stops; do not infer a regression improvement from different stopping points.

A positive diagnostic requires accepted completions, exercised lifecycles, clean
accounting/cache behavior, fewer tracked rereads and no paired cost increase. Only
then consider a separately frozen fresh diverse screen. A negative or unexercised
result returns to its concrete mechanism without increasing budgets or expanding
DeepSWE. Natural compaction, authorized prior recall and effectful coding retain
their independent held-out qualification gates.

Phase delivery is unchanged: the saved common-fix final review already included
current steering and the instruction not to repeat satisfied preparation. See
`.artifacts/continuity-common-fixes-live-v1/phase-review-audit.md`. This diagnostic
does not silently change the fixture's inferred criterion or remove review.
