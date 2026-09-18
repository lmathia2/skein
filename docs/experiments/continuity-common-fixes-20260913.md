# Common-boundary continuity diagnostic

Status: closed at clean `d333c6a`; supervisor exited zero. Both arms independently
verify, but findings has equal tracked rereads (171) and costs 147.2% more. The memory
efficiency gate fails. This is a consumed-case diagnostic, not held-out qualification.
Report: `.artifacts/continuity-common-fixes-live-v1/analysis.md`.

## Question and treatment

Do findings remain usable and reduce tracked same-version rereads after fixing the
three deterministic failures in the previous routing pair: disappearing checkpoint
reminders, incidental Docker bytecode, and oversized malformed tool-call arguments?

Apply all common fixes to both arms. Preserve the existing `no_recall` versus
`findings` distinction; do not change the fixture, evaluator, instructions, oracle,
budgets or stopping rules during the run. The prior result remains negative evidence.
The common-fix bundle is not an ablation identifying each fix's live effect.

## Frozen scope

- One consumed `qualification_ordered_rules` case, one fresh-state repetition per
  arm, two trials in parallel. No adaptive retries or selection of a better attempt.
- OpenRouter `openai/gpt-5.6-luna`, reasoning `max`.
- Per trial: 24 model calls, 350,000 cumulative input tokens (including conservative
  pre-dispatch reserves), 8,192 output tokens per call, 900 seconds active wall.
- Aggregate ceilings: 48 model calls and 700,000 input tokens. All acquisition,
  checkpoint, recovery, repair and final-verification work is charged.
- Cached Docker image only:
  `sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
- Use `evals.verified_continuity --diagnostic`; store new results under
  `.artifacts/continuity-common-fixes-live-v1`. Never overwrite the old cohort.
- Record a clean committed revision. Compare every fixture/driver hash and shared
  manifest contract to `.artifacts/quoted-command-policy-live-diagnostic-v1/manifest.json`
  before launch; only runtime revision and diagnostic output location should differ.

## Required readout and stopping gates

1. Both trials must be terminal; progress files are not proof of completion. Report
   all calls/tokens/cost and all terminal reasons, including budget stops separately.
2. All six requested answers must be correct, with completed required source evidence
   before their managed writes; both trials must independently verify. Do not equate
   an answer-only oracle pass or source availability with accepted completion or
   demonstrated semantic dependence on memory.
3. All six planned cuts must be exercised. Audit worker epochs, memory/artifact
   recovery, learned findings, and the timing of repeated reads around each cut.
4. Audit every same-epoch outgoing request prefix, not just local prompt hashes.
   Report malformed calls, rejected versus submitted cells, retained diagnostic
   artifacts, verification repair rounds, and incidental bytecode. A naturally absent
   malformed call does not live-test that recovery path; deterministic tests remain
   its direct evidence.
5. No false acceptance, unknown effects, unaccounted calls, missing cost records or
   unreported wire attempts. Any such failure holds further paid expansion.
6. Compare paired rereads, cost, calls, input/uncached/output/reasoning tokens and wall
   time only with stopping conditions visible. Filesystem same-version overlap is a
   tracked metric, not proof every overlap is avoidable; shell/artifact exposure must
   be measured or explicitly marked unknown. Do not relabel unknown coverage as zero.

A successful diagnostic requires both accepted completions, exercised lifecycles,
sound accounting/cache behavior, lower tracked rereads and no paired cost increase.
Even then it permits only consideration of a separately frozen, bounded fresh diverse
screen. It cannot promote defaults or establish the broader goal. A failure instead
returns to its trace-backed mechanism; do not increase budgets or run a broad DeepSWE
campaign to obscure it. Natural compaction, authorized prior recall and effectful coding
remain independent unqualified gates.
