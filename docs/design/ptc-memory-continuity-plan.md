# PTC and memory continuity implementation plan

Status: proposed implementation; no runtime changes or new paid runs authorized by
this document. Prepared 2026-09-12.

## Outcome

After exploration, compaction, a file change, or worker loss, the agent should know
what it learned, what supports it, where the details are available, and whether they
remain applicable. Reduce unnecessary rediscovery and repeated model-visible content
without discouraging necessary fresh reads or weakening verification.

Correctness of the whole lifecycle is the goal. Stages provide independently testable
boundaries, not permission to call an incomplete retrieval or continuity path finished.

## Current evidence and implementation gaps

The latest Koota/Oxvg compaction runs made 47 post-cut file reads. Thirty-three were
fully covered by pre-cut reads of the same file version; 3,969 of 5,203 returned lines
overlapped pre-cut evidence. These are reuse candidates, not proof of wasted model
context: PTC can fetch content without exposing it. Exact-range matching missed subset
reads, including five covered by ranges displayed in the handoff.

The eight-entry handoff covered six of 29 previously read paths in Koota and five of
18 in Oxvg. It retained identifiers rather than findings or direct recovery handles.
Both handoffs reported no modified paths despite preceding successful edits. Neither
post-cut submitted program explicitly used history retrieval or artifact loading.
Both workers survived their cuts; later timeout/reconciliation and task-input-budget
outcomes confound end-to-end comparisons.

Relevant seams:

- `harness/ptc/repl/worker.py`: `_binding_description`, `_state_manifest`,
  `_StateProxy`, cell completion, and worker lifecycle.
- `app/agent/ptc.py`: nested capability receipts, result artifacts, PTC state events,
  and bounded model-facing output.
- `harness/evidence/memory/context.py`: projected capability records currently omit
  `read_evidence` and `result_artifact_uri`; artifact reads expect a singular
  `artifact_uri`, unlike capability `artifact_refs`.
- `harness/execution/tools/memory.py`: existing versioned, evidence-linked working
  notes and generic continuation hints.
- `harness/adapters/adk/context.py`: `_evidence_manifest`, live-state handoff,
  complete-interaction cuts, and frozen epochs.
- `app/agent/config.py`: existing read-once instruction and PTC examples.

## Component ownership

| Component | Owns | Does not own |
| --- | --- | --- |
| PTC runtime | Live bindings, supported value descriptions, binding availability and changes | Historical truth or workspace freshness |
| Execution/evidence | Authorized operations, receipts, immutable result bytes, observed workspace versions | Semantic conclusions |
| Memory | Evidence-linked findings, corrections, retrieval, bounded working-set projection | Live heap restoration or completion decisions |
| Context/messages | Placement, selection budgets, deterministic serialization, cache-stable epochs | Inventing findings or promoting them to facts |
| Recovery/orchestration | Epoch transitions, effect reconciliation, workspace observations, terminal reasons | Replaying effects to recreate memory |
| Evaluation | Exposure/reuse measurements and paired quality experiments | Changing harness authority or leaking verifier answers |

The canonical ledger remains the historical authority. Reuse its artifacts, notes,
program registry, and existing broker. No second memory database, separate agent, or
additional top-level tool is required. Four-tool compatibility must remain supported.

## Shared contract

Define three linked typed records, not three independent stores:

1. **Evidence reference:** task/owner/workspace scope, receipt/event identity,
   source watermark, operation, artifact locator, source path/version/range when
   established, and the version of the exposed representation. Distinguish a raw
   source hash from the hash of redacted or transformed exposed bytes.
2. **Live binding descriptor:** kernel epoch, binding name and restricted selector,
   revision/observation cell, type/shape, bounded description, evidence links, and
   availability. A selector is validated data, not an expression evaluated with
   `eval`. It may identify a supported container entry without inspecting arbitrary
   objects. Binding names are never durable evidence IDs.
3. **Finding:** stable ID/version, kind (observation, hypothesis, decision, rejected
   approach, open question, or next action), concise public task-level statement,
   evidence links, scope/dependencies, and correction/supersession links. Model
   statements remain advisory. Evidence links establish provenance, not truth.

Keep these axes separate:

- Availability: live in the observed epoch, artifact-backed, unavailable, or pending
  reconciliation. A later reset can invalidate a previously live descriptor.
- Freshness: historical snapshot, checked against the workspace at a recorded
  boundary, changed since capture, or unknown. Absence of a managed edit does not
  prove freshness; shell commands and external changes also matter.
- Epistemic status: deterministic observation versus agent interpretation, with
  explicit uncertainty. Findings can survive binding loss while becoming stale.

All derived views record program/version/source hash, parameters, evidence IDs,
watermark, authorization scope, applied budgets, completeness, and result hash.
Use additive schema evolution or an explicit new program version; never reinterpret
old histories as if missing provenance had been recorded.

## Stage 0 — Establish the baseline and executable contracts

**Owner:** evaluation + evidence. **Dependency:** none.

- Add a reproducible analyzer over the existing read receipts, compaction events,
  selected cell output, and captured provider requests.
- Compute interval-union overlap by path and source version; distinguish pre-cut
  coverage, new post-cut coverage, partial overlap, new ranges, and changed versions.
- Separate data fetched into Python, content actually emitted, and content retained
  in the provider request. Track rediscovery searches and failed path attempts.
- Cover filesystem reads, artifact recovery, and recognizable shell excerpts. Label
  unmappable/transformed output unknown rather than claiming exhaustive provenance.
- Freeze the current code/profile/task/trace identities and the measurement version.
- Specify record schemas and lifecycle cases above before implementing consumers.

**Gate:** synthetic overlap/exposure fixtures produce exact expected counts; the
existing canary audit is reproducible with explicit coverage limitations. No model
quality conclusion is derived from a lower filesystem-call count alone.

**Likely files:** `evals/experiments.py` or a focused companion analyzer, relevant
evidence models, and corresponding deterministic tests.

## Stage 1 — Complete evidence addressing and durable retrieval

**Owner:** memory + execution/evidence. **Dependency:** Stage 0 contracts.

- Preserve read metadata and result-artifact linkage through the authorized memory
  projection. Normalize direct-tool and PTC evidence where their receipts support
  equivalent facts; retain explicit unknowns for older histories.
- Make path/version/range queries return addressed evidence and completeness, not
  require searching unstructured cell transcripts.
- Extend the existing artifact/event read path to return a selected result field or
  line range from an authorized receipt. Keep generic byte loading available, but
  do not require the model to manually page and decode an entire JSON result to get
  one source excerpt.
- Support multiple ranges and versions; do not fabricate a whole-file snapshot
  from a bounded read or merge ranges across incompatible versions.
- Return a ready-to-use retrieval recipe using the supported broker or reserved
  memory command. Prior-run retrieval must use an explicitly authorized source
  manifest, not merely the same repository path.
- Preserve limits on returned bytes, decoding/input work, latency, and allocation.
  Large artifacts need bounded extraction or a durable indexed representation;
  a small output cap alone does not make unbounded JSON decoding safe.
- Verify artifact integrity and authorization on recovery; distinguish missing,
  corrupt, denied, partial, and unavailable results. Do not recursively artifact
  artifact-loading responses.

**Gate:** exact selected bytes recover after worker loss; Unicode/pagination/range
boundaries work; cross-task access is denied; corrupt bytes fail closed; missing
historical data is unavailable; a supplied evidence handle needs no discovery round
trip. Existing artifact operations remain compatible.

**Likely files:** memory `models.py`, `context.py`, `programs.py`, `runtime.py`;
`harness/execution/tools/memory.py`; `app/agent/ptc.py`; existing artifact helpers.

## Stage 2 — Make PTC working values discoverable and safely described

**Owner:** PTC + evidence. **Dependency:** Stage 1 addressing.

- Extend the shared state-description path with bounded, safe previews of supported
  primitives/containers and known capability-result shapes, plus evidence handles.
- Add an explicit brokered annotation path under the existing `agent.*` surface
  for a selected binding, purpose, and supporting evidence. Route durable metadata
  through the same canonical memory writer; do not create a separate annotation
  database. Finalize the typed signature and help examples before wiring prompts.
- Automatically associate known returned read objects with their receipts where
  identity is established. Do not infer source provenance for arbitrary slices,
  transformations, or user-defined objects without explicit supporting metadata.
- Track reassignment, deletion, aliases, in-place mutation, and epoch changes for
  registered values. For bounded supported values, compare validated content at
  successful cell boundaries; if validation exceeds its budget or is unsupported,
  invalidate the association rather than claim unchanged state. Object identity or
  the most recent assignment alone is not a mutation detector.
- Make descriptors snapshots at an identified cell boundary. Persist committed
  descriptors before exposing them; annotations made during a subsequently failed
  cell must not advertise its values as successfully committed/live.
- Keep original read artifacts immutable even if the corresponding Python object
  is changed. Deleting a binding removes its live location, not historical evidence.
- Keep catalog scans/previews bounded and progressively disclosed. Do not call
  arbitrary `repr`, properties, iterators, or serialization hooks.

**Gate:** tests cover meaningful bindings, nested entries, overwritten names, aliases,
same-size in-place changes, cyclic/large/unsupported values, secret redaction, failure
boundaries, and epoch loss. No false live/provenance claim is emitted. Bulk results
still stay out of the prompt unless selected.

**Likely files:** `harness/ptc/repl/worker.py`, `app/agent/ptc.py`, notebook event
projection where necessary; `test_repl.py`, `test_notebook_ptc_integration.py`.

## Stage 3 — Retain learned findings and construct the task working set

**Owner:** memory. **Dependency:** Stage 1; integrate Stage 2 live locations.

- Extend the existing versioned note contract with typed evidence-linked entries
  for findings, decisions, rejected approaches, open questions, and next actions.
  Preserve compatibility with existing free-text notes. Canonical events record
  revisions and supersession; the current working set is a rebuildable projection.
- The coding model records concise conclusions after seeing the evidence, within
  its normal work cycle. Do not invent conclusions from variable names or collect
  hidden chain-of-thought. No independent summarizer call is required by this design.
- Require observation claims to name available evidence. Permit explicitly marked
  hypotheses or proposed actions without pretending they have observational proof.
- Associate findings with unfinished task/criterion IDs and source dependencies.
  New contradictory observations must not silently erase old claims or automatically
  resolve a semantic conflict. Surface unresolved conflicts for the coding model.
- Build one versioned bounded working-set view: current goal/constraints, relevant
  findings, source/symbol locations, changes, validation state, open questions, live
  descriptors, and durable retrieval handles.
- Start with explicit relevance links and deterministic priority tiers: required
  safety/current intent, unfinished-work findings and dependencies, outstanding
  validation/failed approaches, then other useful evidence. Use stable tie-breaks
  and deduplicate by evidence identity/compatible coverage, not just newest eight.
- If the selected set exceeds budget, retain whole entries with useful recovery
  handles and report omissions. Do not globally truncate serialized JSON into an
  invalid or misleading fragment.

**Gate:** replay at the same watermark gives identical bytes; corrections supersede
without deleting history; findings survive binding loss; unsupported conclusions stay
advisory; old relevant evidence survives recency pressure; oversized views preserve
valid structure, required state, and recovery paths.

**Likely files:** `harness/execution/tools/memory.py`, memory models/programs/runtime,
context projection; `test_context_programs.py`, `test_context_windows.py`.

## Stage 4 — Integrate the working set into PTC messages and context epochs

**Owner:** context/messages + PTC integration. **Dependencies:** Stages 2 and 3.

- Update invariant PTC instructions to prefer meaningful retained values and
  evidence-backed findings; reuse covered versions/ranges, not an absolute ban on
  rereading a file. Explain selective inspection and explicit fresh-read conditions.
- Put only relevant new/changed descriptions in ordinary cell output under the
  existing egress cap. Do not dump the namespace or duplicate the same catalog in
  several envelope fields. Include supported, tested access/recovery examples.
- At a cut, render the working-set view as a frozen epoch handoff. Distinguish
  required continuation/safety metadata from advisory findings, runtime locations,
  and historical source content. Preserve a complete-interaction recent tail.
- Announce whether the worker is live, which epoch a binding belongs to, what was
  learned, which sources changed, and how to continue/retrieve exact evidence.
- Append later binding invalidations, corrections, and state changes. Do not
  rewrite old messages or mutate the stable provider prefix each turn.
- Prompt a bounded checkpoint update at useful work/phase boundaries, after the
  model has interpreted evidence, not after every read. Missing notes at hard
  pressure use a safe evidence-based fallback, not fabricated findings or a crash.
- Keep trigger timing, window size, and output reserves unchanged for the first
  representation comparison. Validate actual outgoing requests, not only local
  prompt hashes. Four-tool mode gets the same durable findings without live bindings.

**Gate:** provider request fixtures prove prefix/epoch stability, bounded output,
complete call/result pairs, available recovery recipes, no false live claims, no
private reasoning exposure, and correct memory-off/PTC-off combinations.

**Likely files:** `app/agent/config.py`, `app/agent/ptc.py`,
`harness/adapters/adk/context.py`, `harness/core/context`, factory integration;
existing context/PTC integration tests.

## Stage 5 — Close freshness, interruption, and prior-run lifecycle gaps

**Owner:** execution/recovery + memory invalidation. **Dependencies:** Stage 1;
complete integration with Stages 2–4 before any live promotion.

- Project receipt-confirmed touched paths immediately at in-loop cuts; keep them
  distinct from a verified current workspace diff. Do not wait for worker return
  to reflect known mutations in continuation state.
- Mark affected evidence/findings for revalidation after writes, edits, renames,
  deletions, and potentially mutating shell commands. External changes require a
  new workspace observation. A prior version can still answer historical questions.
- Require current version checks at operations that depend on freshness, including
  expected-hash guarded edits. Do not silently serve a cached snapshot as the result
  of a current filesystem read.
- On worker loss, invalidate live locations; recover addressed data on demand,
  within existing state policies. Do not replay effectful cells or promise arbitrary
  object restoration. Findings and artifact handles remain usable independently.
- Reconcile unknown effects only when receipts and workspace evidence establish the
  outcome. Otherwise block the affected continuation/verification explicitly;
  missing optional memory alone is not an unknown execution effect.
- Prior-run findings retain source task/workspace/version and authorization. Do not
  import prior live bindings, current-task decisions, or verification success as if
  they applied to a new task. Test changed-workspace and unauthorized-source cases.
- Report task-input-budget exhaustion separately from runtime bugs, provider errors,
  reconciliation blocks, and official verifier failures.

**Gate:** deterministic failure-injection tests cover cut-after-edit, shell/external
changes, restart, missing/corrupt artifacts, interrupted cells, authorization changes,
and prior-run recall. No stale-version mutation, unauthorized disclosure, false
completion, or duplicated effect is accepted.

**Likely files:** `app/agent/workflow.py`, `app/agent/ptc.py`, context plugin,
execution/workspace observations, evidence reducers, existing recovery integration
tests. Keep Harbor/provider infrastructure repairs separate and report them explicitly.

## Stage 6 — Measure mechanism and real-task quality, then decide promotion

**Owner:** evaluation. **Dependency:** deterministic gates in Stages 0–5.

### Controlled continuations

Use six case families: multi-file navigation beyond eight entries; overlapping and
partly exposed reads; source mutation; live versus restarted workers; failed approaches
and validation findings; and authorized prior-run recall with changed workspaces and
distractors. Give each family two frozen fixture variants, including negative cases
where a fresh read is the correct action.

Compare four arms at equivalent checkpoints:

1. Full-history reference, no cut.
2. Current compacted representation, with common safety/retrieval corrections.
3. Described, evidence-addressed working values and direct recovery.
4. Arm 3 plus evidence-linked findings and task-relevant working-set construction.

This is 48 short continuations, not 48 full DeepSWE runs. An initial 24-case smoke
may find contract problems before running the second fixture variant. Put needed
evidence outside the retained exact tail and cut before dependent work. Match compacted
arms' context budgets, cut positions, model, output limits, and continuation limits;
the full-history reference is intentionally not context-size matched. Disclose all
common fixes, and retain the untouched historical baseline only as observational data.

Use deterministic fixture outcomes to assess behavior; pytest asserts data and code
contracts, never natural-language model wording. Count checkpoint-writing and retrieval
overhead, including extra model turns, in arm totals.

### DeepSWE confirmation

Freeze six diverse DeepSWE 1.1 tasks before seeing new results: TypeScript navigation,
editor behavior, Python analysis, Go semantics, streaming/state handling, and Rust
optimization. Preflight verifier/runtime viability. Oxvg remains diagnostic-only if
timeout/reconciliation makes the quality comparison uninterpretable; select any
replacement before launch, never after seeing its score.

Compare the strongest qualified treatment with the common-fix compacted baseline:
six tasks × two arms × two repetitions = 24 full trials. Use Luna `max`, concurrency
six, identical task/model/provider/budget settings, and balanced scheduling. GLM stays
out of scope. Do not treat two repetitions as a precise statistical estimate.

Separately evaluate prior-run benefit with related but disjoint tasks and authorized
source manifests; same-problem solution recall is not evidence of generalization.
Freeze fresh memory stores per arm/repetition except for explicitly seeded recall
tests. Do not change defaults automatically or launch paid campaigns from this plan.

### Readouts and decision gate

- Primary mechanism metrics: same-version duplicate model-visible source content,
  post-cut recovery calls, repeated searches/failed paths, and repeated investigations.
- Report fetched-but-unexposed data separately. Include artifact and shell routes,
  coverage limitations, necessary rereads, and historical versus current evidence use.
- Cost metrics: all input, uncached input, output/reasoning, model/tool calls,
  checkpoint/retrieval overhead, active wall time, and provider cost.
- Quality: official reward/partial reward, fixture outcomes, criterion coverage,
  separate terminal categories, and every paired task-level result.
- Safety: zero accepted unauthorized retrieval, stale guarded mutation, effect
  replay, or false verification caused by continuity behavior in the tested suite.
- Freeze a practical mechanism improvement target after Stage 0 baseline calibration,
  before treatment runs. Require reduced duplication without moving it to another
  route, and no observed correctness regression on controlled fixtures. A quality or
  cost regression requires investigation, not a success claim from read counts alone.
- Advance to the twenty-task panel only if the mechanism and six-task quality gates
  support it. Report uncertainty and promote PTC descriptions, memory findings,
  compaction, and prior-run recall independently; benchmark completion is not blanket
  authorization to change production defaults.

## Delivery order and completion criteria

Implement contracts/measurement first, then evidence retrieval. PTC descriptions and
finding retention can be developed against the shared evidence contract. Freshness and
recovery work starts after addressing and must be integrated before model evaluation.
Finally wire messages, run lifecycle integration, and execute the approved experiments.

Land focused commits per tested contract, with every caller checked before changing a
shared function. Update implementation status and the relevant ADRs only when behavior
has actually changed; older ADR text about removed summarizers, recovery modes, and
hard-limit note handling must be reconciled with tested current behavior, not copied
into the implementation uncritically.

Run focused unit/integration tests for each stage, then the repository unit suite,
runnable integrations, compile checks, Ruff, Pyright, and `git diff --check`. Inspect
actual provider-request fixtures for message changes. Publish unsupported cases and
test limitations alongside results.

Complete means the evidence-to-finding-to-continuation path works through mutation and
worker loss, is bounded/replayable/authorized, and has measured quality and efficiency
results. It does not mean every Python object supports automatic lineage, every source
read is avoidable, or all memory features should become defaults.

## Implementation record

### S0 read-coverage baseline

`python -m evals.memory_audit LEDGER --task TASK --cut-sequence SEQUENCE`
emits a source hash, measurement version, per-read categories, and interval-union
counts. Use canonical-ledger sequences, not compatibility event sequences.

| Task | Canonical task / cut | Ledger SHA-256 | Fully covered / post-cut reads | Pre-cut overlap / returned lines |
| --- | --- | --- | --- | --- |
| Koota | `64b171dc4561bb1f56940e60f92cd933` / 826 | `a2f9cc6672cfdf1ca00d2052e1ba9dd3a5dfcdbe3ed4b42be20ce48a4b8e6816` | 25 / 36 | 3,099 / 4,168 |
| Oxvg | `cb4822689563826bbe2e4d70077c6c67` / 816 | `c84b4dff09e5eb77ee1d22368a82551a9acfdef174116367628c7f4c292cb765` | 8 / 11 | 870 / 1,035 |

Inputs are the per-run canonical ledgers under the retained
`/Users/mathiasl/skein-eval-results/memory-luna-koota-oxvg-manifest-compaction-20260912`
result root. Version `read-coverage-v1` reproduces these counts. Actual model exposure,
provider retention, and shell/artifact/Python-output routes remain unmeasured in these
historical canaries; fetched-line overlap is not a model-visible duplication estimate.

### Delivered integration and live experiment contract

Stages 1–5 are integrated: typed findings, explicit corrections, authorized prior
sources, described values, whole-entry epoch messages, observed source invalidation,
and independently guarded current-version mutations. Same-version contained read
ranges collapse to a recoverable superset; partial overlaps and distinct versions stay
separate. Unobserved external changes require fresh evidence. Unknown effects remain
blocked, not speculatively replayed. ADRs record bounded/unsupported cases.

`python -m evals.continuity --output PATH` freezes the 48-case manifest without
network. `--live --dotenv ~/.env --concurrency 6` executes it using Luna `max` and the
production factory, PTC broker, context plugin, and OpenRouter adapter. Each case has
at most 12 model calls, 200k cumulative input budget, 8192 output reserve, and 900s wall
limit. The fixture loop tests continuation artifacts, not outer-workflow completion
or official DeepSWE reward. Public findings are seeded identically rather than written
by a separate model; checkpoint cells and first-request overhead remain accounted for.
Only the controlled fixtures force a cut; full quality runs retain calibrated timing.

The six families have two fixed variants: old-path navigation, covered versus missing
read ranges, managed versus external mutation, live versus restarted worker, retained
rejected approaches, and authorized historical sources with changed current content.
Separate deterministic tests deny disabled/unauthorized prior access, reject stale
guarded writes, and exercise missing/corrupt artifacts and uncertain effects.

Version `source-equivalent-exposure-v2` maps unique exact nontrivial source lines to
path/hash/range, including selected shell/artifact/PTC output and decoded public wire
input captured at HTTP dispatch. Ambiguous, transformed, missing, and short content is
explicitly unmapped. Raw provider inputs, read artifacts, exposure records, hashes,
per-case configuration, usage, and deterministic artifact outcomes remain beside each
result. Headers/credentials/private reasoning are not retained. Retry wire attempts
are retained; missing provider usage/cost is not treated as evidence of zero cost.

Before paid treatment runs, freeze a practical target of at least 25% reduction in
mapped post-cut duplicate emissions versus metadata on the seven reusable fixtures,
with all treatment fixture outcomes correct and no safety-contract failures. No mapped
baseline duplication means **insufficient evidence**, not automatic success. Missing
measurements or infrastructure errors hold the DeepSWE gate. Provider/fixture
infrastructure failures stop launching queued paid cases; already running cases drain.
Full history is intentionally not size-matched. No result automatically changes defaults.

The conditional DeepSWE 1.1 panel is frozen before controlled results:
`koota-pair-relation-tracking`, `obsidian-linter-scoped-ignore-markers`,
`bandit-structured-nosec-directives`, `tengo-destructuring-bindings`,
`httpx-streaming-json-iteration`, and `pest-character-class-coalescing`.
Pest replaces diagnostic-only Oxvg for this quality panel. Exact task artifact hashes
come from `tests/eval/manifests/evaluation-confirm-v1.json` (manifest hash
`f30a3bb6245a360820be5684ed98d001cea460169168633c18487ef28d48fad4`).
Preflight verifier/runtime viability before dispatch; a preflight failure is not a
reason to substitute a task after seeing another task's reward.

The first full quality comparison keeps the earlier canary's context settings:
`phase_boundary`, 262144 configured context ceiling, 0.8 trigger ratio, 20000-token
work packet, 3000-token handoff, 32000-token exact tail, 32768 output reserve, and
8M cumulative input budget. The 262144 ceiling is an intentionally held experimental
setting, not a claim that Luna's provider window is that size. Both arms share it.
Use two repetitions, six concurrent trials total, fresh stores, and no seeded
same-problem prior answers. Provider `max` effort and the actual model identity remain
fixed. Record official reward independently of harness/provider/budget terminals.

### First live execution and pre-rerun corrections

The first campaign (`.artifacts/continuity-live-20260912-v1`, revision `a6bb458`)
started 28 cases: 27 passed, one hit a harness error, and 20 queued cases did not
start. Known provider cost was $0.21267189. This is not a completed paired quality
result. The error was a repeated compaction publication at the same cut while a
large result remained unconsumed. A deterministic regression reproduced it; the
fix keeps the published epoch until the cut can advance, subject to the hard window.

The traces also exposed a nested-value usability problem: a selected string's type
could be mistaken for its dictionary parent's type. Descriptors now include explicit
parent type and ready-to-use access/inspection expressions; typed findings and direct
recovery already allowed the affected trials to finish without rereading their sources.

The first exposure counter did not decode source text inside structured `read.recover`
JSON, incorrectly leaving that common route unmapped. Measurement v2 recognizes only
the bounded known recovery/read JSON shapes; arbitrary Python repr/transforms remain
unknown. This measurement correction precedes the fresh full rerun, applies equally
to every arm, and can also be applied to retained first-run records. The 25% target is
unchanged. Do not combine the stopped campaign with the corrected run as one cohort.

The second campaign (`.artifacts/continuity-live-20260912-v2`, revision `0fd6396`)
started 33 cases: 28 passed, three completed with incorrect artifacts, one exhausted
the 200k fixture input budget, and one stopped on a provider `server_error` (the old
driver incorrectly labelled the typed provider exception as a harness/fixture error).
Fifteen queued cases did not start. Known reported cost was $0.24561903; the failed
response was unaccounted, so this is not an exact total or a qualified cost comparison.
The same-cut compaction error did not recur.

All three completed missing-range arms substituted `VALUE` for the requested
`SAFE_VALUE`, absent from the captured first ten lines. This is a correctness failure,
not a grader defect or evidence that avoiding reads succeeded. The common PTC
instruction now explicitly prioritizes task correctness, checks partial coverage,
and forbids substituting another symbol to avoid a necessary read. Typed provider
failures retain any reported usage and receive the provider terminal category; missing
usage remains unknown. A fresh third cohort keeps the fixtures, budgets, measurement,
and qualification thresholds unchanged. Stopped cohorts remain separate diagnostics.

The third cohort completed all 48 cases: full history 10/12 correct artifacts,
metadata/described/findings each 11/12. Findings reduced mapped duplicate emissions
78→0 on reusable cases and calls 57→50 versus metadata, but source rereads were unchanged,
cost increased 13%, and missing-range correctness failed. The DeepSWE gate remains held.

After separating recovery-page completion from historical source-file coverage across
PTC, memory, and handoffs, an eight-case diagnostic completed with 6/8 correct artifacts.
Metadata/described recovered the missing range correctly; findings/full history still
failed. This closes implementation of the explicit coverage contract, not the behavioral
quality gate. All results, limitations, and next diagnostic directions are in the
[continuity audit](../audits/ptc-memory-continuity-2026-09-12.md). No paid expansion or
default promotion follows these results.

### Reliability goal: next staged work (2026-09-12)

The user authorized bounded OpenRouter use with the existing key. Keep the failed
cohorts and their gates unchanged; they are development evidence, not a held-out
set to tune repeatedly until it passes. GLM is excluded. Do not require whole-file
reads when an already completed, applicable range establishes the requested fact.

1. **Completion authority (verification):** first close deterministic defects in
   exit-status handling, incomplete baseline comparisons, and criterion-reference
   identity. Implemented in `3e44987`, with failing-before/passing-after regressions.
   General checks still do not prove arbitrary task semantics; test task-specific
   verification and bounded re-entry separately from first model proposals.
2. **Locate the evidence-use failure (evaluation):** freeze a nine-trial diagnostic,
   `python -m evals.evidence_use`, before provider dispatch. Three independent
   repetitions of the same missing-symbol task, with (a) all captured evidence
   exposed by a completed PTC cell after the cut, (b) that same capture recoverable
   through the existing memory/binding APIs, or (c) only the first ten lines
   captured, requiring a genuine new read. Model/profile, task, and oracle stay
   fixed; the visible arm is an evidence-placement intervention, not a matched-cost
   promotion arm. Luna/max, six concurrent trials, 12 calls/200k input/8192 output
   per call/900 seconds per trial. No feedback or outer verification in this
   diagnostic: report correctness of first proposals explicitly. A failure with
   visible evidence points beyond retrieval; visible success with recovery failure
   isolates the recovery interface; missing-only failure isolates evidence sufficiency.
   Stop queued dispatch on provider/infrastructure/measurement failures. No expansion
   or promotion automatically follows even a perfect nine-trial result.
3. **Improve the responsible component (PTC/memory/verification):** use stage 2
   traces to choose the next change. Preserve what was learned with bounded,
   evidence-linked findings, usable content access, and explicit remaining unknowns.
   Test completion proposals against a host-owned task-specific oracle and exercise
   production verification/re-entry; do not count reading back a model-written answer
   as independent proof. Include genuinely sufficient partial evidence and reject
   wrong-symbol, stale, unresolved, and self-confirming evidence. Keep first-pass
   failures visible even when verification subsequently enables recovery.
4. **Demonstrate benefit (evaluation):** freeze new development and held-out cases
   before selecting a treatment. Proposed confirmation: 12 diverse cases × two
   arms × three repetitions (72 bounded continuations), with discovery, partial
   evidence, worker loss, changed versions, conflicting findings, and multi-file
   conclusions. First test consumption of controlled findings; then test model-written
   findings separately. Include positive baseline reread opportunities without
   instructing the baseline to reread. Zero critical evidence/accepted-completion
   failures, no paired correctness regression, at least 25% fewer avoidable reread
   lines, and no aggregate provider-cost increase are required. Necessary new-range
   or changed-version reads are not penalties. Missing accounting and zero baseline
   rereads mean insufficient evidence. Report per-family results and uncertainty;
   a small flawless sample is not a guarantee.
5. **Coding quality (evaluation):** only after the prior gates, run the frozen
   six-task DeepSWE 1.1 comparison above, then consider twenty tasks. Official reward,
   harness acceptance, provider errors, and budget exhaustion remain separate.

The immediate nine-trial diagnostic does not authorize automatic traversal of these
stages after a failed gate; further improvements remain within the active goal, with
bounded diagnostic runs and fresh evidence before any larger paid expansion.

### Verified development continuations

`evals.verified_continuity` now runs the production root ADK workflow, effect broker,
verification, and bounded re-entry after verification failure. It wraps the existing
provider only for fixed budgets and public response/usage capture; it does not replace
the model/tool loop. `accepted` comes from `task.finished`, `passed` from the independent
final artifact oracle, and first-verification outcomes remain separate. A correct file
at a call limit is not a verified completion. No private reasoning or auth headers are
retained. An ADK-swallowed provider/limit error must retain its real terminal category.

The six new **development**, not held-out, fixtures are frozen in
`evals.continuity_cases`: routing lookup (TOML), missing fields beyond a captured header,
JSON evidence after worker restart, externally updated JSON, a recorded rejection of
a Python fast-path constant, and a JSON/TOML two-file calculation. Both arms get
identical source reads and seeded, evidence-linked findings; metadata versus findings
changes the existing representation configuration only. Seed observations are not
proof of autonomous note-writing quality. Covered reads remain optional to recover;
the baseline is never instructed to reread.

The first live step is a four-trial canary: `routing` and `missing`, each with metadata
and findings. Luna/max, 12 calls/200k cumulative input/8192 output per call/900 seconds,
up to six concurrent trials, and no blind retry after a streamed provider failure.
All six fixture hashes are recorded before dispatch, but the other four cases are not
launched automatically. Infrastructure, measurement, or false-acceptance failures stop
queued dispatch; cost/reread/quality conclusions require paired completed measurements.

The required task-specific unittest oracle is outside the model's workspace, returns
no expected answer in failure diagnostics, and stores only its expected answer hash.
Its integrity is checked after execution. The local command adapter is still not an
adversarial OS sandbox: inspect submitted programs for oracle/parent-directory access
and treat contamination or unmappable retrieval as insufficient evidence. Do not use
this development screen as the held-out reliability panel or promote defaults from it.

The first four-trial run (`verified-continuity-live-20260912-v1`, revision d82242c)
produced one verified completion, two correct-but-call-limited artifacts, and one
call-limited missing artifact. Both findings trials submitted `verify` at call 11,
then spent call 12 updating notes during the required review. The inherited immediate
6k cut setting produced 5–8 cuts per task; it is a repeated-cut stress treatment, not
a clean single-checkpoint recovery screen. Source overlap was not reduced.

Evaluator v2 forces only the seeded checkpoint at 6k, then restores a 20k work packet
and phase-boundary timing before the real workflow starts. The existing 1,050,000
ceiling is a diagnostic setting, not a verified provider-window claim. Deterministic
tests require exactly one cut for these short source-derived continuations. Keep the
old cohort intact; rerun the same four cases before expanding. Repeated-cut stress
and natural-pressure qualification remain separate required stages, not waived gates.

The citation-help canary passed all four real-workflow trials, but both arms had zero
avoidable source rereads and findings cost 2.1% more. The following eight-trial lifecycle
screen accepted only four trials. One correction trial read the external oracle and
tried its hash against candidate outputs, invalidating that pair. Stop using a readable
oracle file or a low-entropy expected-answer hash as an isolation boundary.

Before another paid trial, evaluator v3 must pass the real-workflow Docker preflight:
the ordinary command backend sees only the mounted workspace; one exact host-owned
virtual test command evaluates the answer without publishing the expected value or
oracle source. Both model-requested and outer checks use this adapter behind the same
managed policy. The local guarded Python worker is unchanged. Record the actual image
and runtime override, preflight its cached immutable ID, and forbid implicit pulls.
Exact output keys are declared in the tasks. These common evaluator changes require
fresh pairs; never splice their successes into the contaminated cohort.

The isolated correction/join rerun after explicit rejected-cell guidance (2dee395)
passed all four trials: findings 12 calls/$0.02419874/zero source reread lines versus
metadata 17 calls/$0.03187776/nine reread lines. Treat this as a two-pair development
pilot. Both arms already expose working notes and retrieval; it is not memory on/off.

The deterministic audit now reports decisive source availability before each managed
answer write dispatch, using host-frozen path/version/ranges and completed receipts.
The observable write boundary is not arbitrary Python value-construction time. It
separates first and repaired answers, rejects later/failed/wrong-version read support,
and preserves unknown shell/artifact/prior-run routes. Latest pilot: all four first
answers had their decisive ranges available, but this does not prove consumption.
A correct-guess regression passes the independent artifact oracle without acquiring
the missing fields; artifact correctness and evidence availability are separate gates.

Evaluator v4 additionally enforces this declared availability gate through the same
host-owned oracle used by model-requested and final checks. It matches current answer
bytes to the last managed write, rejects a correct guess without sufficient preceding
reads, and permits a new answer submission after bounded source recovery. Corrupt
evidence remains an infrastructure failure. Applicable seeded ranges need not be read
again. This is a necessary evidence condition, not proof of model use or a universal
production source-provenance policy.

The next paid diagnostic is frozen to four development trials: `missing` and `freshness`
with metadata and findings, Luna/max, the existing 12-call/200k-input/900-second budgets,
and cached Docker isolation. It tests required new-range and changed-version reads under
the stricter oracle; it is not a held-out benefit test. Stop on infrastructure or false
acceptance, preserve first-verification failures, and do not expand automatically.

Next work: exercise model-written
checkpoints and negative cases where missing evidence must not become a guessed answer.
The source-gated canary completed 4/4, exposed duplicate workflow/plugin handoffs,
and the single-owner rerun completed 3/4 with four correct source-backed artifacts.
One metadata case hit its call ceiling after the model-requested check but before
outer verification. Findings retained 2/2 completion with lower input/cost; neither
arm had avoidable source rereads. Do not keep reusing this zero-opportunity panel
as a reread-benefit experiment. The model-written stage must charge learning/note
creation and recovery, retain first proposals and terminal distinctions, and include
real reuse opportunities without ordering the control to reread.
Freeze genuinely new held-out variants before their first provider call, retain matched
terminal/cost accounting, and test repeated cuts separately. Hold DeepSWE until lifecycle,
model-written checkpoints, source-evidence availability, and paired quality/cost/read
gates are independently established. Seeded findings and a correct output do not prove
that the model learned the facts or obtained missing evidence before answering.

### Model-written checkpoint development screen

`evals.learned_continuity` adds three new development cases to the same verified
runner: delayed capacity lookup, a cross-file time-window calculation, and a missing
referenced policy requiring abstention. The model receives eight small shard records
and two policies, but not the final target/question until after its checkpoint.
There are no host-seeded reads, notes, bindings, or import cells. Both metadata and
findings arms retain the same broker, note/recovery APIs, source bytes and budgets.

The evaluator observes completed path/version/range receipts before a nonempty
model-authored note, then requests one bounded acknowledgement through the existing
steering queue. Only after that later completed cell does it deliver the follow-up
and apply one synthetic pressure cut through the production context plugin. This
allows the note-writing interaction to leave the exact tail without dropping an
unconsumed tool result. The hard window and packet capacities are unchanged; the
temporary trigger ratio is restored after publication. This is controlled checkpoint
stress, not natural context-pressure calibration. Learning, notes, acknowledgement,
recovery, and verification all count against the same 12-call/200k-input budget.

Positive completion requires correct bytes, decisive source receipts preceding the
last answer write, and an answer written after the measured cut. The unavailable
case has no accepted answer artifact, including JSON null: expected abstention requires
a structured blocked proposal, a blocked workflow, an exercised checkpoint, no answer
or workspace changes, and no recorded unknown effect. Budget/provider failures and
unexercised checkpoints do not count as abstentions. This grades observable outcomes,
not whether the model's explanation faithfully describes its internal reasoning.

The first live screen is six trials: three cases × metadata/findings, Luna/max,
concurrency six, existing 8192-output/900-second bounds, the cached immutable Docker
image, and the same stop-on-infrastructure/false-acceptance policy. Freeze all fixture
and driver hashes before dispatch. Report actual note construction, learning versus
continuation calls, cut publication versus exercised continuation, first verification,
source availability, rereads across routes, and terminal costs. These cases are not
held out and do not authorize a broader paid benchmark or default promotion.

#### First model-written diagnostic, 2026-09-12

`.artifacts/learned-continuity-live-20260912-v1` ran the frozen six-trial screen at
`104790b`. Metadata accepted lookup (8 calls), hit the call limit on join (12), and
hit the call limit on unavailable policy (12). Findings accepted lookup (11) and
join (8), and stopped blocked on unavailable policy (7). The latter is **not** a
strict correct-abstention pass because a rejected oversized note had been recorded
with an unknown effect. Five checkpoints were exercised; metadata join published a
cut only after using all calls, so it has no measured continuation. All three accepted
answers had decisive completed source captures before their first write. Both arms
made zero post-cut source reads, so this cohort demonstrates no reread reduction.

All 58 provider calls were accounted, with no extra wire attempts: metadata cost
$0.05351007 and findings $0.05464529, total $0.10815536. Known cumulative live spend
is $1.77933983 plus one older failed response with unavailable cost. These are
development diagnostics, not held-out qualification. Optional run-start/run-success
tracing failed while traversing the evaluator's public runtime object; canonical
receipts and wire/cost artifacts remained available, but this is not a clean screen.

The traces identify shared implementation fixes before repeating the same bounded
screen: accept quoted multiline notes without shell dispatch; expose finding and
serialized-note bounds; preserve explicit no-effect validation rejections in PTC;
keep append/publication failures unknown; and keep evaluator runtime/oracle ownership
private from context telemetry. Do not reinterpret old unknown receipts retroactively
or enlarge budgets to convert exhausted trials into successes. Re-run the same six
development trials only after deterministic rejection/publication and workflow tests
pass, then decide whether a genuinely new held-out checkpoint family is warranted.

#### Repaired model-written screen, 2026-09-12

`.artifacts/learned-continuity-live-20260912-v2` froze `f147976`, including the shared
memory/PTC correction `88d0c91`. All six trials exercised exactly one checkpoint,
without seeded source reads/findings or optional trace-observation failures. Four
answers passed the first independent verification, with decisive completed source
ranges preceding their first answer write. Both missing-policy trials ended with a
structured blocked proposal, no answer/source changes, and no unknown effect: two
strict expected abstentions, not two successful answer artifacts.

| Case | Metadata calls / cost | Findings calls / cost | Outcome in both arms |
| --- | --- | --- | --- |
| Delayed lookup | 12 / $0.02402929 | 9 / $0.01699556 | Verified answer |
| Cross-file calculation | 10 / $0.01617652 | 9 / $0.01626616 | Verified answer |
| Missing policy | 9 / $0.01505311 | 7 / $0.01391568 | Expected abstention |

Metadata used 31 calls, 257,110 input tokens, and $0.05525892; findings used 25 calls,
219,407 input tokens, and $0.04717740 (14.6% lower aggregate cost in this screen).
Acquisition/note/acknowledgement accounted for 21 versus 18 calls; continuation for
10 versus 7. Metadata explicitly recovered notes/state on the answer tasks and
notes/read lookup on the unavailable case. Findings proceeded from the delivered
checkpoint without an additional recovery tool call. All source acquisition preceded
the cut and neither arm reread sources afterward. This demonstrates usable model-written
continuity on these development cases, **not a reduction in rereads**, because the
metadata baseline already had zero. Both arms have note and recovery APIs; this is not
a memory-on/off comparison or evidence of generic semantic grounding.

Memory-command rejections dropped from 12 in the preceding diagnostic cohort to 3;
the three remaining metadata errors were two oversized findings and an invalid `plan`
kind, all recorded as no-effect rejections. Budget guidance does not guarantee perfect
note authoring. All 56 calls/costs were accounted, with no extra wire attempts. Cohort
cost was $0.10243632; both model-written cohorts together cost $0.21059168. Known
cumulative live spend is $1.88177615 plus the older response with unavailable cost.

Next qualification remains held: freeze genuinely new worker-loss, changed-source,
and repeated-checkpoint cases before dispatch, separate live-binding reuse from
durable note/artifact recovery, and report unavoidable freshness reads separately.
Do not force control rereads or count a zero-baseline comparison as improvement.
Before claiming a memory-on/off effect, specify that separate ablation explicitly;
the present metadata/findings experiment cannot establish it. No defaults, broad
DeepSWE run, or held-out reliability gate is promoted by this six-trial result.

### Frozen worker-loss qualification screen

The next six trials are three previously unrun families in
`evals/heldout_continuity.py` × `no_recall`/`findings`: signed settled-transaction
aggregation over CSV batches, alias resolution to the latest **completed** deployment
over TOML/JSON, and unit-normalized completed sensor measurements with a pending outlier.
These fixtures are frozen for qualification, not used to tune production prompts or
selection logic. A failed live case becomes diagnostic; do not repeatedly retune on
these cases and keep calling their scores held out. They are still small controlled
tasks, not DeepSWE or broad statistical reliability evidence.

Both arms acquire sources and prepare their own public checkpoint before a delayed
question. A completed `LEARNING_COMPLETE` marker plus completed source ranges opens
one acknowledgement opportunity; findings also requires a preceding nonempty note.
After that later completed cell, the evaluator records stop intent, closes the real
idle worker through its existing lifecycle callback, verifies the old epoch is no
longer live, and records the outcome **before** provider dispatch. The original ADK
runner, tool, context plugin, source artifacts, and independent verifier remain in
place. Safe data restoration remains enabled; effects are never replayed to force a
test outcome. One synthetic cut retains the unconsumed acknowledgement interaction.

`no_recall` disables working notes, prior-run recall, and model-visible memory programs.
It does **not** erase the canonical trace or disable PTC's artifacts, safe restoration,
state APIs, or deterministic evidence index. Both arms retain the same rich read-index
capacity and PTC notices. This measures the added note/recall service, not deletion of
all historical infrastructure. Because fresh reconstruction requires active notes by
contract, both arms use the supported `handoff_tail` policy with the same zero
historical-tail target. No configuration validation is bypassed. The control may
publish or recover artifacts, retain replay-safe data, or read source files as needed;
it is not instructed or mechanically forced to reread them.

Freeze source, fixture, driver, and revision hashes before dispatch. Use Luna/max,
concurrency six, 12 model calls/200k input/8192 output/900 seconds per task and the
same cached immutable Docker image. All acquisition, preservation, acknowledgement,
recovery, and verification calls count. Stop expansion on infrastructure, false
acceptance, or measurement failure. No retries or budget enlargement convert a
failed qualification into a pass. A no-recall run that never reaches its checkpoint
is a protocol/budget failure, not an easy no-memory control win.

Report paired first verification and accepted outcomes; checkpoint publication versus
exercised continuation; actual pre/post worker epochs; post-cut source rereads versus
artifact recovery and exposure; total and continuation calls; and fully accounted cost.
The pilot gate requires all three treatment answers independently verified with
completed decisive source evidence, all six interventions exercised, no false
acceptance or unknown effects, and no aggregate cost regression. Reread benefit requires
an observed nonzero matched baseline and a reduction without worse correctness, not
merely zero treatment reads. Passing allows design of the next changed-source/repeated-
cut qualification, not default promotion or automatic DeepSWE expansion.

#### First worker-loss results and qualification limits

`.artifacts/heldout-worker-loss-live-20260912-v1` froze `7fa9944`. All six real workers
were observed live before their acknowledged stop and not live afterward; each trial
exercised one cut. Initial provider model/instruction/tool/reasoning prefixes matched
across all arms. No source acquisition or finding was host-seeded, no unknown effects
were recorded, and all submitted answers had completed decisive source coverage before
their first write. The independent verifier accepted five answers and rejected one.

| Family | No-recall result / calls / source reread lines | Findings result / calls / source reread lines |
| --- | --- | --- |
| Rollout | Verified / 11 / 36 | Verified / 6 / 0 |
| Units | Verified / 9 / 21 | Verified / 8 / 0 |
| Settlements | Verified / 12 / 13 | Verifier rejected / 12 / 0 |

The settlement comparison is **inconclusive for model quality**: the question named
`settled_rows` without defining its type or meaning. Findings returned the correct
1551-cent total and the three supporting row references; the hidden oracle expected
the integer count 3. Do not relabel that answer accepted, but do not attribute its
rejection to faulty memory either. Preserve the original fixture/results for audit;
any corrected settlement contract is a diagnostic revision, not another first held-out
attempt. Future output contracts must explicitly state field types and meanings before
dispatch. The six-trial qualification gate is therefore **not cleared**.

On the two unambiguous held-out pairs, both arms passed first independent verification.
Findings used 14 calls/102,389 input tokens/$0.02404901 versus no-recall's
20 calls/136,222 input tokens/$0.03206475: 30% fewer calls and 25% lower cost. There
were 57 same-version source lines reread in control and zero in findings. Answer-file
checks are not source rediscovery. Both findings continuations wrote directly from the
delivered note evidence without a further source/artifact recovery call. This is a
positive held-out worker-loss signal, not statistical reliability or freshness/repeated-
cut qualification. Exact-line emitted duplicates also fell 28 to zero on these pairs;
ambiguous/transformed source text remains outside that lower-bound exposure measure.

The no-recall models attempted disabled `memory` commands before falling back to source
reads, despite the context marking memory inaccessible. Artifact facilities were still
available and the scripted control proved that recovery route works. Consequently the
call/cost result includes routing/usability overhead; it does not isolate representational
compression alone. Before a broader claim, improve capability-aware recovery guidance
and check direct artifact recovery contracts without weakening the no-recall control.
The richer arm also attempted an unsupported `todo` finding kind once; it recovered
from a no-effect rejection. Both transaction models hit safely rejected `io` imports.

Report the full planned cohort alongside the qualified subset: no-recall accepted 3/3
at 32 calls/$0.05980777; findings accepted 2/3 at 26 calls/$0.04926377. All 58 calls,
usage and cost were accounted with no extra wire attempts. Total cohort cost was
$0.10907154; known cumulative live spend is $1.99084769 plus the older unpriced failed
response. Raw `results.json`, `summary.json`, per-trial `measurement`, wire captures,
and canonical ledgers remain unchanged. Defaults and paid DeepSWE expansion stay held.

#### Frozen capability-routing diagnostic after worker-loss v1

Repeat only `heldout_rollout` and `heldout_units`, both `no_recall` and `findings`,
once each, at concurrency 4. Use Luna/max, the same cached immutable Docker image,
12 total model calls, 200k task input, 8192 output, and 900 seconds per trial. All
learning, checkpoint, acknowledgement, recovery, and verification remain charged.
Output: `.artifacts/recovery-routing-diagnostic-20260912-v1`. Freeze code and the
existing fixture hashes before dispatch; do not change code during the cohort.

The intervention is exact PTC artifact byte recovery plus capability-aware guidance
and valid note-kind guidance, applied to both arms. Preserve available artifact
recovery in control; never force it to reread sources. Check independent first/final
verification, completed evidence before answers, one actual worker stop/cut, disabled
memory attempts, artifact recovery, source/exposure repeats, and complete call/cost
accounting. Existing infrastructure/false-acceptance stops remain in force.

This is **a repeated routing diagnostic**, not a new held-out sample. The driver's
`screen_scope=heldout_worker_loss` describes the reused fixture family only. Do not
pool these repeats into the original held-out result or interpret that label as fresh
qualification. The ambiguous settlement fixture/result remains untouched. A negative
or neutral routing result still gets reported; changed-source/repeated-cut gates and
DeepSWE/default promotion remain held regardless of this repeat's outcome.

#### Capability-routing diagnostic results

The four-trial repeat finished at frozen `b38cf3b` with a clean diff. All four first
independent verifications passed, all decisive source captures preceded the first
managed answer write, and every trial exercised its one acknowledged worker stop and
cut. Initial provider model/instruction/tool/reasoning prefix hashes matched
(`eb43827861e33eaf598c510874f408a5117a9c9c156221a443214a773ca3316d`). No unknown
effects, unaccounted calls, missing costs, or extra wire attempts were recorded.

| Reused case / arm | Calls | Learning calls | Source reread lines | Artifact loads after cut | Exact duplicate emitted lines | Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rollout / no recall | 9 | 3 | 0 | 2 | 32 | $0.01274170 |
| Rollout / findings | 8 | 4 | 0 | 0 | 0 | $0.01787898 |
| Units / no recall | 7 | 2 | 0 | 5 | 24 | $0.01106463 |
| Units / findings | 8 | 4 | 0 | 0 | 0 | $0.01432052 |

Both controls used the advertised artifact route, with **zero submitted memory-command
cells**. The only fresh post-cut file reads were checks of `answer.json`, not source
rediscovery. Control's prior 57 source reread lines became zero in this repeat; do not
attribute that improvement to memory. Findings continued directly from delivered note
facts and avoided control's 56 exact source-equivalent re-emitted lines. This exposure
measure is a conservative exact-line lower bound, excluding ambiguous/transformed text,
not proof of semantic dependence or all prompt duplication.

Overall calls tied at 16 per arm. Findings cost $0.03219950 versus $0.02380633:
**35.3% more**, despite lower post-cut recovery work (8 versus 11 continuation calls).
Its learning/checkpoint phase took 8 versus 5 calls; total input was 125,102 versus
102,397 and output 14,572 versus 7,793 tokens. In rollout/findings, a safely rejected
dunder expression ran no capabilities. Its replacement acquired sources but failed
note validation because finding IDs contained dots; the model then raised, losing
the heap. The next cell recovered from completed visible evidence and wrote a valid
checkpoint without another source read. The note rejection itself was correctly
`effect=none`; no unknown operation was relabeled safe. This is a schema-discoverability
and preparation-overhead finding, not a compaction failure.

The routing fix works in this diagnostic, but the no-cost-regression gate does not.
Do not pool this repeat into the original held-out sample or promote defaults.
Next: make the complete note-input contract discoverable from its validated schema
instead of chasing individual hidden field constraints in prose. Then freeze explicit
typed changed-source/repeated-cut cases with positive and negative evidence gates;
preserve artifact-capable control and charge all checkpoint work. Avoid another repeat
of these tiny fixtures as purported reliability evidence.

Cohort cost: $0.05600583. Known cumulative live spend: $2.04685352 plus the older
unpriced failed response. Process exited successfully; no live jobs remain. Regression
suite: 772 passed, two skipped (774 collected); Ruff clean, Pyright zero errors and
the existing `__all__` warning; both real Docker worker-loss preflights passed.

#### Two-cut source-revision screen

The next controlled lifecycle screen uses new `staged_dispatch` and
`staged_allocation` fixtures, once per `no_recall`/`findings` arm (four trials,
concurrency 4). Keep Luna/max, 12 total model calls, 200k task input, 8192 maximum
output, and 900 seconds per trial. Use the same cached immutable Docker command
image and existing actual ADK workflow; do not seed reads, findings, or answers.
Planned output: `.artifacts/staged-source-revision-live-20260912-v1`.

Both cases acquire source facts, publish a model-written checkpoint, acknowledge,
and lose the live worker at a context cut. The coordinator then authorizes an exact
policy-file revision through the ordinary guarded filesystem broker. A successful
post-cut change receipt, a later completed read of that new version, and a subsequent
new checkpoint must precede the second acknowledgement/worker loss/cut. Unchanged
captures remain valid; they are not forced to be reread. Only after the second cut is
the final typed-output question delivered. Dispatch tests regional deployment
selection with queued work excluded; allocation tests completed stock minus a revised
reserve, excluding pending stock. Both tasks specify exact output keys, types, and
meanings before dispatch.

The independent oracle requires both exercised checkpoints, completed decisive source
ranges before the managed answer write, matching answer bytes, and the final source
hashes. Policy edits are explicitly in the task scope from the start; no host-side
source mutation or baseline rewrite occurs. Stale answers, correct premature answers,
and correct answers followed by a policy revert are negative end-to-end tests in both
arms. Separate ordering checks reject pre-cut writes, failed writes, reads before the
change, and pending reads; later evidence cannot justify an earlier note.

Measurements retain non-overlapping post-cut intervals, including the intermediate
revision/checkpoint work, with same-version read coverage and exact-line exposure
reported separately. All learning, schema discovery, failed cells, policy edits,
checkpoint updates, recovery, and verification count against the shared budget.
No-recall retains artifact APIs and the capability-aware guidance fixed earlier.

Freeze the code and fixture hashes after deterministic/Docker preflight. The live
screen must report every planned pair, first and final verification, both actual
worker epochs/cuts, revision receipts, source freshness, rereads, exposure, calls and
cost. Stop queued dispatch on infrastructure/provider/measurement or false-acceptance
failure. A pass is a controlled two-cut lifecycle signal, not broad memory reliability;
cost regression, budget exhaustion, or unexplained reads prevent efficiency promotion.
Do not rerun the tuned routing panel or expand DeepSWE/defaults on this screen alone.

#### First staged screen: protocol did not reach the second cut

`.artifacts/staged-source-revision-live-20260912-v1` froze clean `4993810`.
All four initial worker-loss cuts occurred. Every trial completed the authorized
policy write and a later read of its expected new version, with zero same-version
source rereads in the measured first interval. No second cut occurred, no final
question was delivered, and no answer was independently accepted. **This is not a
completed two-cut memory-quality comparison.** Preserve every raw result rather than
counting successful policy updates or nonempty notes as completion.

| Case / arm | Calls | Terminal | Cost |
| --- | ---: | --- | ---: |
| Dispatch / no recall | 9 | workflow_blocked | $0.02181947 |
| Dispatch / findings | 8 | workflow_blocked | $0.02484655 |
| Allocation / no recall | 6 | workflow_blocked | $0.01163627 |
| Allocation / findings | 12 | workflow_blocked | $0.03565404 |

The coordinator delivered a second acknowledgement with exactly the same wording and
`CHECKPOINT_READY` token as the first. Its earlier policy-revision message remained
pending and said it replaced the acknowledgement request. Wire captures show both
messages together; model proposals cite the already-completed first acknowledgement
and attempt to finish preparation instead of submitting the new acknowledgement cell.
This strongly points to ambiguous stage/steering instructions, not evidence that the
revised findings were lost. The next diagnostic must retire only the coordinator's
own completed stage message, use distinct stage/token identities, and verify a fresh
acknowledgement before advancing. It must not acknowledge unrelated user messages or
weaken the source/cut/independent-verification gates. Reusing these cases after the
protocol fix is diagnostic, not a new held-out sample.

Allocation/findings also exposed a PTC result-contract problem: it checked
`result.get('exit_code') != 0` on successful reserved memory commands, then attempted
to parse their output as process stdout. Those commands return structured `data`,
not a subprocess envelope. A note revision had already committed successfully before
the model raised; later successful note reads triggered the same erroneous guard.
Three cell failures are correctly retained, with no unknown effects or duplicated
note commits inferred. Input-schema discovery alone cannot solve output-envelope
confusion. Trace the shared reserved-command/`shell.run` contract and sibling callers
before choosing normalization versus explicit branch-aware guidance; do not fabricate
a process exit or undo a committed note to satisfy generated code.

All 35 provider calls have complete usage/cost accounting and no extra wire attempts.
Cost was $0.09395633; cumulative known live spend is $2.14080985 plus the older unpriced
failed response. Process exited successfully; no live jobs remain. Latest regression
suite: 787 passed, two skipped (789 collected); Ruff clean and Pyright zero errors
with the existing warning. Both Docker staged preflights passed. The deterministic
scripts establish execution/order contracts but did not predict the model's ambiguity
around repeated acknowledgement wording. Defaults and DeepSWE expansion remain held.

#### Staged protocol/result-contract diagnostic v2

Repeat the same four pairs once at
`.artifacts/staged-source-revision-live-20260912-v2`, with the unchanged Luna/max,
12-call/200k-input/8192-output/900-second budgets, concurrency 4 and cached Docker
image. This is a protocol diagnostic on reused cases, not fresh held-out evidence.

Each staged checkpoint now names its number and a distinct `CHECKPOINT_READY_1`
or `CHECKPOINT_READY_2` token. A later completed cell must print that exact token;
an old token, failed cell or prose completion cannot advance. Once revision evidence
and its new checkpoint are complete, the coordinator acknowledges only its own
completed revision message. Unrelated user steering remains pending under normal
workflow ownership. The new instruction explicitly supersedes earlier phase requests
and says not to finish before the next stage/final question arrives.

Both arms also receive the explicit PTC `result_kind` discriminator and a native
memory-result example. Managed results preserve structured data without fabricated
process exits; process results preserve actual exit/stream evidence. This paired
bundle addresses the two observed failure mechanisms; it does not isolate their
individual causal effects. Freeze code before dispatch, preserve all first/second
checkpoint and source guards, and report all four outcomes even if the protocol is
still not exercised. No default or DeepSWE promotion follows from this repeat alone.

#### Staged v2 results: lifecycle fixed, efficiency gate remains open

The diagnostic froze clean `b88eae1` at the v2 path above. All four trials passed
their first independent verification and accepted completion after both acknowledged
worker-loss cuts. Each stop records a live worker epoch before and no live worker
after; no host-seeded cells were used. Every answer write had completed decisive
source ranges beforehand, and final revised-source hash guards passed. This proves
the exercised lifecycle and verification contracts, not semantic reliance on memory
or broad reliability. Initial model/instruction/tool/reasoning prefix hashes matched
across all four trials.

| Case / arm | Calls | First verification | Post-second-cut same-version source lines | Cost |
| --- | ---: | --- | ---: | ---: |
| Dispatch / no recall | 10 | pass | 28 | $0.01508005 |
| Dispatch / findings | 9 | pass | 27 | $0.02289624 |
| Allocation / no recall | 12 | pass | 0 | $0.01891752 |
| Allocation / findings | 11 | pass | 0 | $0.02387838 |

No recall totals 22 calls, 141,724 input tokens, 66,673 uncached input tokens and
$0.03399757. Findings totals 20 calls, 165,942 input tokens, 93,786 uncached input
tokens and $0.04677462: **37.6% higher cost**, with only one fewer same-version source
line reread. Each first interval contains one necessary new-version policy read after
its authorized write; these are not rediscovery. Answer-file verification reads are
also excluded from the source-line column. Allocation/control recovered artifacts,
while allocation/findings used memory without source rereads. Exact emitted duplicate
source lines in dispatch were 11 versus 10; this conservative metric excludes ambiguous
or transformed content and is separate from read coverage.

The dispatch/findings first post-second-cut request (`wire/005.json`) contains all
four findings, the correct revised south/s41/14 facts and source dependencies, with
zero omitted findings. It nevertheless rereads the unchanged 26-line deployment file
and one-line policy. Missing fact delivery is therefore not the explanation. The
serialized advisory block is 7,393 bytes for 670 bytes of finding text; allocation's
second-cut block is 5,193 bytes for 532 bytes of text. The remaining bytes include
necessary provenance, read-index and version metadata, not merely disposable overhead,
but repeated full evidence addresses are a concrete projection target.

Both handoffs also retain obsolete advisory next-actions: dispatch says to await the
policy revision after it has happened, and allocation says to await the final question
after delivery. Updating a fact did not supersede these separate note entries. Earlier
checkpoint instructions also remain in compiled task history despite consuming the
owned queue message. Investigate authority and representation together; do not erase
user steering, silently expire all actions at a phase boundary, or label historical
source captures current. Keep canonical evidence intact and use a deterministic,
bounded, recoverable prompt projection. Separate cheap freshness validation from full
unchanged-source rediscovery in the next diagnostic.

No native-memory-as-process failures recurred. Allocation/findings recovered from two
safely rejected dunder expressions; the safety checks remain intact. All 42 provider
calls have usage/cost accounting, with no missing costs or extra wire attempts. Cohort
cost is $0.08077219; cumulative known live spend is $2.22158204 plus the older unpriced
failed response. The process exited successfully and no live jobs remain. Latest full
regression: 787 passed, two skipped; Ruff clean, Pyright zero errors with the existing
warning. The next gate is representation/cost improvement followed by genuinely new
held-out behavior tests, not another broad DeepSWE run on the strength of this repeat.

#### Compact projection diagnostic v3: frozen scope

`continuation@3` changes only the shared model-facing handoff representation:
compact advisory JSON, exact repeated finding-context/dependency tables with full
identities, and explicit historical authority for recorded next-actions. Canonical
notes, memory program/tool contracts, selection order, phase/cut policy, source guards
and independent verification are unchanged. Selected entries must expand exactly;
tables and explanatory overhead count toward the existing budget. Do not infer that
metadata bytes are all unnecessary, or that smaller prompts imply memory consumption.

Before dispatch, deterministic context, working-set, PTC, worker-loss and staged
source-change checks must pass, including a real cached-Docker staged preflight. Freeze
the clean code revision, then run the same four staged trials once at
`.artifacts/staged-projection-live-20260912-v3`: Luna/max, concurrency 4, the same
12-call/200k-input/8192-output/900-second limits and immutable cached image. This repeat
tests live projection usability and preserves all protocol/source/verification gates;
it is not new held-out reliability. Report all planned trials, actual cuts, first/final
verification, source availability, interval rereads, exposure, calls, input/uncached
tokens and total cost. Stop queued work on provider/infrastructure/measurement or
false-acceptance failure. No automatic benchmark/default expansion follows.

Offline projection of v2's complete selected advisory blocks yields dispatch
6,444→5,498 and 7,393→6,161 bytes; allocation 4,767→4,743 and 5,193→5,144 bytes.
These figures include local-reference legends and the new advisory action notice,
but exclude unchanged control text. Action guidance is budgeted with selected advisory
entries and cannot displace required control metadata. They are a representation measurement, not a
prediction that the 37.6% cost gap is closed. A subsequent fresh held-out panel must
still demonstrate reliable evidence use and reduced rediscovery without cost regression.

#### Compact projection v3 results: reuse improves, checkpoint cost remains

The frozen four-trial cohort at the v3 path above ran clean `c76ab87`. All four passed
first independent verification and accepted completion after both real acknowledged
worker-loss cuts. Every managed answer write had completed decisive source evidence
beforehand, final source hashes passed, and no unknown effects were recorded. All 44
provider calls share the same model/instruction/tool/reasoning prefix; usage and cost
are fully accounted, with no extra wire attempts. The process exited successfully.

| Case / arm | Calls | Post-second-cut same-version source lines | Cost |
| --- | ---: | ---: | ---: |
| Dispatch / no recall | 9 | 28 | $0.01556757 |
| Dispatch / findings | 11 | 0 | $0.02688177 |
| Allocation / no recall | 12 | 0 | $0.02000551 |
| Allocation / findings | 12 | 0 | $0.02335643 |

Control totals 21 calls, 137,526 input / 69,557 uncached input / 14,023 output tokens,
and $0.03557308. Findings totals 23 calls, 192,603 input / 110,233 uncached input /
17,530 output tokens, and $0.05023820: **41.2% higher cost**. Findings source rereads
are zero versus 28 control lines; dispatch's exact emitted duplicate source lines are
zero versus 11 (conservative exact-line coverage, not exhaustive transformed lineage).
The necessary new-version policy read in each first interval and answer-file checks
are not rediscovery. Allocation/control restores artifacts rather than rereading sources.

Both findings continuations explicitly retrieve memory before answering: dispatch
queries `working_set`, allocation reads its note. Neither passes a local table label
as a broker argument or needs a source reread. All projected findings fit without
omissions, but this does **not** establish direct use of the prompt projection: the
subsequent memory query returns the full canonical representation. It also does not
isolate the representation's causal effect from sampling variation or the shared JSON
formatting change. Do not promote on the dispatch reread result alone.

Phase accounting identifies the next cost seam. Findings uses 9 acquisition calls,
5 revision calls and 9 final-continuation calls, versus control's 5, 5 and 11. Most
of the dollar gap occurs before the final continuation. Allocation/findings first
attempts an unnecessary acknowledgement note: a dunder expression is safely rejected,
then an uncited observation is rejected, then a next-action note commits before actual
source acquisition. Allocation/control also has one safely rejected dunder inspection.
These attempts remain charged; do not weaken the source validator or observation
citation requirement to make them pass.

The native note-write path returns `_public_note(event)` after publication. Submitted
PTC cells that print this result can re-emit full learned content and derived metadata:
allocation's initial source-checkpoint cell emits 4,326 stdout bytes, and dispatch's
note-submitting acquisition cell emits 6,855 bytes including its read-reference print.
Both revision cells already select small output. Inspect the shared note-write receipt
contract and its callers next: distinguish compact commit acknowledgement from full
note retrieval while preserving CAS, idempotency, publication failures, full canonical
records and explicit recovery. This is a concrete preparation/output-cost target;
another handoff wording-only rerun is not the next step.

After that contract is tested, freeze new diverse continuations with repeated evidence
use and explicit output types, including changed-source and missing/pending evidence
negatives. Charge all preparation and every query; report the cost crossover rather
than selecting only long cases where notes win. Retain capable artifact recovery in
control and keep one-shot cases as the overhead baseline. These staged cases remain
development diagnostics, not the held-out sample or grounds for broad DeepSWE expansion.

Cohort cost is $0.08581128; cumulative known live spend is $2.30739332 plus the older
unpriced failed response. Final regression: 792 passed, two skipped (794 collected),
Ruff clean and Pyright zero errors with the existing warning. Both final cached-Docker
preflights passed. The full memory reliability/efficiency goal remains open.

#### Compact note-commit receipt canary: frozen scope

The shared note-write return path now acknowledges the committed event/version/hash
and retained entry count, with enabled recovery commands. Full canonical notes and
observer publication, CAS, idempotency, source authorization and completion checks are
unchanged. `memory note read` retrieves latest content; `event.read`, when enabled,
retrieves the exact older commit with existing paging. PTC/native result guidance and
note-schema contract version 2 name this distinction; no extra API is introduced.

Offline projection of the five v3 canonical notes yields 367-byte receipt bodies
versus 564, 4,039 and 6,119 bytes for allocation, and 3,073 and 4,843 for dispatch.
These are successful result-body sizes, not totals over model requests or predictions
of saved calls/cost. Models still choose which Python values to print and may explicitly
reread notes. Invalid input and publication failure must not acquire success receipts.

After focused, full-suite and real cached-Docker checks pass, freeze clean code and run
only `staged_allocation` × `no_recall`/`findings` once at
`.artifacts/note-receipt-live-20260912-v1`, Luna/max, concurrency 2, unchanged
12-call/200k-input/8192-output/900-second trial budgets and cached immutable image.
Allocation is selected for its observed full-note echo and preparation overhead, not
for a prior memory win. This two-trial compatibility canary is not held-out evidence or
a powered cost comparison. Preserve both cut/source/final-verification gates and every
attempt. Report first verification, receipt shape, actual recovery, checkpoint/query
cost, interval rereads, input/uncached/output tokens and total cost. Do not expand the
old staged panel merely to obtain a favorable sample.

The next qualification design must exercise repeated use, not just one delayed answer:
multiple individually source-gated answer artifacts across distinct continuation
boundaries, with all setup/notes/queries charged. Include repository dependency and
call-site mapping, configuration precedence with changed sources, signed cross-file
reconciliation, and completed-versus-pending validation evidence. Keep matched one-shot
overhead cases, capable artifact recovery in control, explicit output types, and true
missing/failed/pending tool-evidence negatives. An earlier correct guess must not become
valid because evidence is read later. Intermediate artifacts require their own temporal
source checks; final acceptance must verify every requested artifact through the real
workflow, not merely the last answer. Freeze cases, repeats, budgets and stopping gates
before dispatch, and report the cost crossover across use counts without excluding
short cases where memory loses. This evaluator extension remains to be implemented.

#### Note-receipt canary results

The two-trial allocation canary froze clean `fe087e4` at the receipt path above.
Both passed first independent verification after both acknowledged worker-loss cuts.
Every answer had completed decisive source evidence beforehand; final policy/source
hashes passed and no unknown effects or failed cells were recorded. All 22 provider
calls are fully accounted with no extra wire attempts, and their actual wire prefixes
match within and across arms. The process exited successfully; no live jobs remain.

| Arm | Calls | Input / uncached input | Output | Cost | Active wall |
| --- | ---: | ---: | ---: | ---: | ---: |
| No recall | 10 | 64,426 / 38,653 | 8,492 | $0.02036761 | 85.5s |
| Findings | 12 | 85,611 / 31,087 | 7,917 | $0.01836083 | 82.4s |

Findings costs 9.9% less in this single pair despite more calls and total input; fewer
uncached and output tokens contribute to the observed price difference. This is not
a powered cost comparison or a causal attribution to the receipt change alone. Both
arms reread zero source lines. The required revised-policy read is new-version evidence,
and answer-file checks are separate. Control restores two artifacts in the first interval
and four in the second; its exact repeated emitted source-equivalent lines are 10 and 14,
versus none mapped in findings. These counts are conservative and non-overlapping by
interval, not proof of complete transformed-output lineage.

The model prints and accepts the compact receipt without attempting to access missing
write-result entries or rereading the note solely to confirm a successful write. Its
revision reads the note when merged content is needed. After the second worker loss,
findings directly writes the correct answer from the available handoff, with no memory
query, artifact load or source read before that write. The trace supports this exercised
prompt-only continuation path, not universal semantic provenance enforcement. Required
independent answer/source verification remains unchanged.

Preparation overhead still differs: findings uses 6 acquisition / 2 revision / 4 final
calls, versus control's 2 / 3 / 5. Findings starts with an unnecessary standalone
acknowledgement and separates its learning marker from the successful note-write cell.
The API no longer echoes full note content, but this result does not demonstrate fewer
setup calls. Avoid another tiny wording repair and repeat on the same allocation task;
the next implementation is the multi-answer temporal evidence gate and fresh diverse
one-shot/repeated-use fixtures specified above. Keep protocol overhead separately
measured and do not force an artificial new note version when a continuation adds no
new knowledge. Any unchanged checkpoint remains explicitly historical, never fresh by
default. Negative cases must exercise actual missing/failed/pending tool evidence, not
only input records whose business status happens to say pending.

Cost is $0.03872844 for this canary; cumulative known live spend is $2.34612176 plus the
older unpriced failed response. Full regression: 794 passed, two skipped; lint clean,
typing zero errors with the existing warning, and both cached-Docker preflights passed.
The compact receipt contract is exercised; held-out reliability, repeated-use cost
qualification and broader benchmark/default promotion remain open.

#### Per-answer temporal gate implementation

The evaluator now accepts 1–16 host-only answer contracts, each with a normalized
workspace-relative artifact path, expected JSON value, decisive source ranges/versions,
and a checkpoint index. The existing single-answer source auditor supplies the evidence
check for each artifact; no new production tool or memory store is introduced.
Every answer request must follow its assigned cut and complete before the next cut,
when present. The supplied cut list must match all canonical published cuts. Failed,
pending, late, wrong-version or opaque evidence does not become completed support.

The staged coordinator checks an intermediate artifact before closing its interval:
the expected value, managed-write hash and sufficient prior captures must match, and
the write must precede the selected checkpoint. It rechecks after the acknowledgement
cell, which can itself mutate files. A failed recheck requires a fresh checkpoint and
handshake; acknowledgement idempotency includes the checkpoint event identity so a
repaired checkpoint does not retrieve an already-consumed request. The existing
worker-loss/cut/source-revision controls remain in force.
Rejected answer checkpoints emit one bounded, idempotent repair instruction per
checkpoint identity without disclosing expected values or hashes. Only the
coordinator's own rejection message is consumed on repair; unrelated steering is
untouched. This keeps an ordinary invalid answer from becoming a silent protocol stall.

Final independent verification checks every requested artifact, including earlier
answers, using strict JSON comparison and current-byte/managed-receipt identity.
Nested symlink aliases, missing/oversized/invalid answers and a correct final answer
with a corrupt earlier artifact fail. A legitimate corrected latest submission can
pass, while `all_submissions_supported` preserves earlier unsupported submissions for
qualification. That field describes source availability and timing, not the semantic
correctness of every historical submission; final expected-value checks remain separate.
No expected value or expected-answer hash is published to the model.

Deterministic checks exercise two-answer windows, real root-workflow acceptance and
rejection, both note/recall arms, two worker losses, pre-cut guesses, post-write reads,
missing earlier artifacts, acknowledgement corruption and repair. Existing frozen live
fixtures are not rewritten; staged tests add an intermediate contract locally. The
driver manifest is `verified-continuity-v8`. These are evaluator contract tests, not
held-out model-quality evidence or general production semantic-provenance enforcement.
Fresh diverse one-shot/repeated-use fixtures and actual tool-evidence negatives are
still next. No paid run was launched for this gate implementation; cumulative known
live spend remains $2.34612176 plus the older unpriced failed response.

Final verification: 830 unit/integration tests pass, two skip; full lint is clean
and typing has zero errors with the existing warning. Four positive/repaired-checkpoint
Docker preflights pass at `.artifacts/docker-multi-answer-preflight-20260912-v3` using
the cached immutable network-disabled image. The offline v8 driver manifest at
`.artifacts/multi-answer-driver-v8-dry-20260912/manifest.json` confirms unchanged legacy
fixture hashes and decisive source requirements against the prior receipt cohort.

#### Fresh one-use/three-use fixture implementation

`evals/repeated_continuity.py` defines six previously unrun positive cases:
`reuse_repository_1/3`, `reuse_config_1/3`, and `reuse_reconcile_1/3`. The suffix
is the number of separately answered continuation questions, not a repetition count.
All source acquisition, checkpoint work, acknowledgement, recovery, answers and final
verification remain in the real ADK workflow and count toward the same arm budget.

- Repository: imported named-function call sites, an entry point's cap dependency,
  and a different entry point's path that does not invoke the cap. Full qualification
  still requires real coding tasks; these small repositories are mechanism fixtures.
- Configuration: per-key defaults/environment/service precedence, explicit false
  versus absence, and a brokered guarded environment revision before the third answer.
  The two earlier artifacts retain their historical meanings and must stay correct.
- Reconciliation: alias joins and signed settled amounts across two CSV batches,
  first per-account and then aggregate. Output types distinguish supporting row IDs
  from a row count. Pending/failed input records are distractors, **not** substitutes
  for the still-required actual pending/failed tool-operation controls.

One-use and three-use forms share initial sources, the first question's semantics
and decisive requirements, model/settings and arm capabilities. Both have a declared
20-model-call/350,000-input-token ceiling, 8192 output reserve and 900-second wall
limit. These new-case ceilings are fixed before any provider call; historical cases
retain their original 12-call/200k ceilings. The v9 manifest records per-case budgets,
and the measured wrapper and harness input gate use the same case-specific ceiling.
Configuration's three-use form includes a source revision; report that workload
separately rather than interpreting all one/three differences as pure reuse amortization.

Each answer follows an acknowledged worker-loss context cut. Before an unchanged
intermediate boundary, the coordinator accepts the successful answer/marker without
requiring a new note version. It records the reused note identity when present but
does not update the note, invent a finding, or call it fresh. The production compactor
is unchanged: synthetic hard pressure can retain that explicitly stale historical
note. This does not establish how frequently natural soft-pressure cuts should ask
for updates. Changed-source boundaries retain completed guarded writes, new-version
reads and updated checkpoints. Invalid reuse at an initial or changed-source boundary
fails closed. Final source hashes and every answer's temporal/value checks still apply.

Scripted tests derive answers from recovered source bytes, not hidden oracle values,
and exercise all six cases in both arms. The control can recover its own artifact;
it is never instructed to reread sources. Tests verify zero same-version source
rereads, exact note-write counts, stale-note labelling, three actual worker losses,
all answer/source contracts, cost accounting and matching bounded budgets. Those
scripts prove achievable execution paths, not model behavior or held-out success.

This is fixture implementation, not the final dispatch manifest or authorization to
expand the benchmark. Finish actual completed-versus-failed/pending validation and
missing-evidence controls next, then freeze repeats, interleaving, stopping gates and
all hashes before dispatch. Keep paired cost/quality/readouts at both use counts;
zero baseline duplication remains insufficient evidence of a reread reduction.
No OpenRouter calls have been made for this implementation, and known cumulative
live spend remains $2.34612176 plus the older unpriced failed response.

The repeated-use implementation passes 845 unit/integration tests with two skips,
full compile/lint checks, and typing with zero errors plus the existing warning.
All six three-use cached-Docker preflights pass at
`.artifacts/docker-repeated-use-preflight-20260912-v1`. The offline v9 manifest at
`.artifacts/repeated-use-v9-dry-20260912/manifest.json` describes twelve positive
trials, retains every legacy fixture hash, and records the new per-case budgets.
No live jobs were launched. This dry manifest is not the final qualification cohort.

#### Validation controls and frozen next screen

The v10 evaluator adds three actual-operation cases in both arms: a completed checksum
unittest, a failed checksum unittest, and a missing input. The successful case requires
both completed source evidence and the matching successful validation before writing.
Negative cases must withhold the answer. A failed command still has an unknown effect:
report this as answer withholding, not successful reconciliation or safe abstention.
Fourteen scripted local and cached-Docker checks include deliberately correct guesses
without validation and a real subprocess result held before receipt publication. The
latter tests an actual pending-publication boundary, not a live asynchronous model tool.

Next paid screen is frozen to 18 trials: these three validation cases, followed by
the six one/three-use cases, each with `no_recall` and `findings`, Luna/max and concurrency
six. One sample per case/arm is diagnostic, not reliability qualification. Existing
case budgets remain unchanged (validation 12 calls/200k input; reuse 20 calls/350k).
The driver freezes source/fixture hashes, image identity and revision before dispatch.
No provider or fixture errors, false acceptances, measurement errors or unknown cost
may be excluded from the report; infrastructure/measurement failures stop queued work.
Do not rerun a failed trial and replace its result.

Report each pair's first verification, accepted result, every answer's source/window/
validation support, actual worker-loss cuts, source reread intervals, artifact recovery,
provider-visible exposure, all-in calls/input/uncached/output/cost and terminal category.
Validation negatives are safety controls, not quality passes or efficiency samples.
For repeated-use positives, require all answers supported and no quality regression;
claim reread reduction only where control actually rereads, and require all-in cost
not to regress before efficiency promotion. A zero-reread control is a floor, not a win.
The configuration revision is a distinct workload, not pure amortization. Any success
here requires an independently frozen repeat and then real coding tasks; defaults and
the broad DeepSWE run remain held. This screen itself does not demonstrate reliability.

#### Diagnostic outcome and next bounded canary

The v10 screen completed all 18 trials for $0.35171184, with fully accounted costs,
no provider errors and no false acceptance. The detailed frozen record is
`.artifacts/diverse-continuity-live-20260912-v1/analysis.md`. Reconciliation's one/three-use
pairs passed in both arms and findings eliminated post-cut artifact loads at 28–35%
lower cost, but controls had zero source rereads. Output-schema ambiguities invalidate
several other quality comparisons; v11 now declares JSON-object keys and workspace-relative
paths explicitly. Original results are retained, not replaced or pooled with corrections.

The changed-source findings run used an obsolete derived value, failed verification,
then reread five lines to recover. `continuation@4` addresses that observed mechanism:
inline freshness, no invalidated conclusion text/active status in the default handoff,
scoped newer capture handles, and withholding of unstructured note excerpts that could
repeat stale conclusions. Canonical/historical evidence remains unchanged and recoverable.

Next paid canary is limited to six trials: `validation_complete`, `reuse_repository_1`,
`reuse_config_3`, each with no_recall/findings, Luna/max, concurrency six and unchanged
case budgets. Run only after focused tests, final regression and cached-Docker preflight.
Freeze the clean revision, fixture hashes, image and execution settings before dispatch.
Report schema compliance, first verification, acceptance, every cut and unknown outcome.
For the invalidation mechanism specifically, require observed invalidated prompt entries,
correct recomputation from applicable evidence, no same-version source reread and no
stale first answer. If the model updates all derived findings before the cut and no
invalidation entry occurs, that trial cannot qualify the new invalidation mechanism.
Any failed/inconclusive control is retained. No broad expansion or default promotion.

#### Note-cost guidance ablation

The v11 invalidation canary passed all six first verifications but findings cost
48.3% more on the three-use configuration case. The revision interval accounted for
$0.02293013 versus control's $0.00854230. Instructions required a note read before
writing and encouraged initial plan notes; the rejected update appended new IDs for
already-known derived findings. Its exact canonical merge was 10,088 bytes. Reusing
only `api_effective` and `importer_effective` IDs yields 7,649 bytes and retains the
same completed evidence, below the unchanged 8,000-byte cap.

Change only guidance: newest observed version instead of mandatory reread; learned
evidence instead of an initial plan note; existing IDs for revisions; one selected
rendering rather than data plus model_text. No automatic semantic merging, new API,
larger budget, checkpoint bypass, history deletion or verification relaxation.

After deterministic checks and cached-Docker preflight, run six trials with the same
v11 fixture definitions: completed validation, repository one-use, configuration
three-use, each no_recall/findings, Luna/max, concurrency six and existing budgets.
Freeze the new clean revision and hashes before dispatch. Compare paired all-in
cost, first verification, accepted evidence, actual cuts, rereads and artifact/query
recovery. Also report initial plan-only notes, note reads, same-ID revisions, rejected
updates and phase costs. Preserve every outcome. Older runs are diagnostic references,
not same-request randomized baselines. This single screen cannot promote defaults or
establish broad reliability; it tests whether the available efficient path is used.

The six-trial guidance screen completed for $0.11372484. Findings passed all three
first verifications without startup plan notes, note-budget retries or source rereads.
Two controls did not reach a comparable endpoint: an unexecuted marker falsely claimed
in prose, and a 20-call stop after correct artifacts but before verification. The full
record is `.artifacts/note-guidance-live-20260913-v1/analysis.md`; preserve these outcomes.

v12 adds a single pending-marker reminder per stage after prose, using existing
steering and retaining original responses, source checks, completed marker cells and
acknowledgement. The reminder is not execution evidence. It also increases both
repeated-use arms to the existing supported 24-call ceiling (one/three-use budgets
remain matched); 350k input and 900 seconds are unchanged. This addresses repeated
control stops at the original arbitrary cap, not a retrospective pass or relaxed
verification. Validation retains 12/200k. Freeze all changed hashes and limits before
the next six-trial comparison, using the same cases and arms as the guidance screen.

#### v12 outcome and frozen full-family stability repeat

The six-trial v12 screen at clean `7cbb4f8` passed every first verification and
completed-evidence gate. All ten worker-loss cuts occurred; no reminders, failed
capabilities/cells, unknown effects, missing costs or extra wire attempts occurred.
Findings cost 26.4% less for repository one-use, 14.7% less for changed configuration
three-use and 18.2% more for completed validation. Aggregate cost fell 12.1%, with
28 versus 34 model calls. Post-cut artifact loads fell 23 to zero, but both arms had
zero source rereads. A four-line reread of the generated configuration answer is
self-inspection, not independent evidence. The cohort cost $0.10507343. Full record:
`.artifacts/checkpoint-feedback-live-20260913-v1/analysis.md`.

Freeze one full-family stability repeat before further tuning: 18 trials comprising
`validation_complete`, `validation_failed`, `validation_missing`, then
`reuse_repository_1`, `reuse_repository_3`, `reuse_config_1`, `reuse_config_3`,
`reuse_reconcile_1`, `reuse_reconcile_3`, each with `findings` then `no_recall`.
Reverse the earlier arm scheduling order; keep six concurrent trials, Luna/max,
the same cached immutable image, v12 fixtures and all per-case budgets. Each arm
has fresh isolated state. No parameter or instruction change between results.
Freeze clean revision, fixture and driver hashes before dispatch. Reused cases are
a stability diagnostic, not new held-out samples. There is no seed guarantee.

Retain all outcomes and stop queued work on infrastructure/provider/measurement
failure or false acceptance. Validation negatives must withhold answers and are
reported separately from positive completion and efficiency. Failed commands retain
unknown-effect semantics; withholding is not reconciliation. Compare all seven
positive pairs' first verification, source/window/check support, cut exercise,
per-family/all-in costs, and recovery/source/exposure metrics. Missing accounting,
unequal endpoints and zero baseline duplication cannot support improvement claims.
Any failure becomes diagnostic; do not replace it with a passing retry. Even a clean
repeat only supports designing genuinely new held-out cases with positive baseline
rediscovery opportunities, not automatic DeepSWE expansion or default promotion.

#### Stability safety finding and completion correction

The frozen 18-trial screen completed at `ab3d443` for $0.30356570. All 14 positive
answer checks passed and four negatives withheld answers, but repository-three
control emitted `task.finished` after a nested shell failure with an unresolved
unknown effect. The Python cell itself completed normally. The complete record is
`.artifacts/continuity-stability-live-20260913-v1/analysis.md`; preserve the raw
accepted label as observed behavior, not a valid safety verdict. Control made 68
post-cut artifact loads and re-emitted 189 exact source-equivalent lines versus zero
with findings. Neither arm fetched unchanged sources again. This is diagnostic
mechanism evidence, not a passing cohort or general filesystem-reread reduction.

`unresolved_execution` now projects pending capability/cell/validation intents,
unknown terminal effects and unfinished tool receipts from existing evidence.
Identity errors fail closed. Known no-effect rejections remain distinguishable;
unrelated success never reconciles an earlier unknown. Completion checks the
projection before dispatching verification and again before returning its verdict.
Recovery and factory handoffs share it; `continuation@6` includes its source in the
handoff program hash. v13 evaluation reports unresolved execution independently and
labels an acceptance unsafe even if answer values pass. Safe abstention also uses
the projection instead of looking only for explicit unknown terminal events.

The full regression passed 878 tests with two skips; final targeted tests cover the
updated hash/abstention paths. Three cached, network-disabled Docker tests exercise
positive completion, the actual failed-printf operation after correct source-backed
answers, and evaluator detection with the production fence deliberately bypassed.
Handoff records match the same unresolved operations. Lint and typing pass apart
from the existing typing warning. No new top-level tool, automatic shell replay,
general reconciliation mechanism, default activation or subsequent paid run is added.

Next: confirm corrected completion behavior in a separately frozen bounded live
screen, then design genuinely new held-out continuations with distractors, partial
evidence and broader source sets. Preserve capable control recovery and count both
source fetches and repeated model-visible content. Do not repeatedly tune these
same small fixtures and rename their later results held out. The six-task DeepSWE
quality gate and eventual twenty-task expansion remain held.

#### Frozen v13 completion-fence live check

Run six bounded trials: `validation_complete`, `validation_failed` and
`reuse_repository_3`, each `no_recall` then `findings`, Luna/max, concurrency six,
fresh isolated state, existing v13 case budgets and the cached immutable Docker
image. Freeze clean revision and every driver/fixture hash before dispatch. Do not
change instructions or replace failures during the cohort. Required outcomes:
successful-check and repeated-use answers have completed support and independent
verification; failed-check cases withhold answers; no accepted task has unresolved
execution. Report whether the live model actually proposes completion after failure:
safe voluntary blocking does not exercise the production completion fence itself.
The deterministic/Docker forced-claim tests cover that exact boundary independently.

Retain first verification, all terminal categories, unknown operations, costs and
paired recovery/exposure metrics. Stop queued work on infrastructure, measurement,
provider or false-acceptance failures. This is a regression screen on reused fixtures,
not fresh held-out reliability, default promotion or DeepSWE authorization. After
this screen, leave these development fixtures frozen and move to the new held-out
contract rather than optimizing another wording change against them.

The v13 live check completed at clean `2b9703c` for $0.08703809. All four positives
passed first verification with completed support and zero unresolved execution.
Both failed-check models withheld answers and blocked; their three unresolved
records remain explicit. No forced completion claim occurred in those live negatives;
the independent Docker tests cover that exact rejection boundary. Repository findings
used 13 versus 19 calls, cost 39.0% less and eliminated 21 artifact loads/45 exact
source-line re-emissions. No source-file rereads occurred in either arm. Full record:
`.artifacts/completion-fence-live-20260913-v1/analysis.md`. This is a passing bounded
regression, not held-out qualification or authorization to expand DeepSWE.

#### New held-out contract: breadth and missing-range implementation

`evals/qualification_continuity.py` now supplies four previously unrun cases through
the existing checkpoint coordinator, real ADK workflow and host-owned oracle:

| Family | Variants | Required mechanism |
| --- | --- | --- |
| Imported transformations | 12 and 18 Python source files | Learn multi-file affine relationships, answer three delayed inputs after three actual worker losses, retain every earlier answer |
| Partial profile capture | Two distinct values and tail positions | Preserve only the observed legacy prefix and service mapping initially; acquire the selected current profile later, including explicit false |

The graphs contain several real independent branches; initial acquisition does not
disclose the selected branches or later numeric inputs. No findings are host-seeded.
Both arms retain PTC artifacts and the same read-index budget, so the control may
also compute and store a useful summary. The tests prove that strong path, rather
than deliberately forcing control rereads. Graph facts are derived from captured
source ASTs in scripted tests, not supplied expected values.

Each answer has exact output keys, source/version/range requirements and a cut window.
Partial-case decisive evidence is just the service mapping and the three-line current
profile, not the whole file. A deliberately correct guessed answer without that new
range fails verification. `initial-capture-boundary-v1` separately records out-of-range
preparation reads and opaque preparation routes; such a trial cannot qualify the
missing-range mechanism even if its final artifact is correct. A scripted full-file
early acquisition proves this distinction. Necessary new-range reads are not rereads.

All four new cases use matched 24-call/350k-input/900-second ceilings. Twelve real
workflow local checks and twelve cached-Docker checks cover both arms and negative
guesses/over-acquisition. The dry v14 manifest at
`.artifacts/qualification-v14-dry-20260913-v1/manifest.json` describes eight potential
trials, retains all 23 historical fixture hashes, and is **not** a live dispatch or
the final qualification manifest. New fixture source hashes must be frozen again
with a clean revision and complete panel before any provider calls.

The broader six-family contract remains: breadth, partial capture, changed sources,
conflicting findings, actual validation outcomes, and explicitly owned prior recall.
The last four families' new variants/protocols still need implementation. In particular,
the current task-local source auditor cannot simply treat an opaque prior-run lookup
as completed applicable evidence; owned-prior qualification needs explicit source
bindings and independent scope/version checks. Do not silently drop that requirement
or substitute same-problem solution recall. Pending-publication safety stays covered
by the real deterministic race test, not an invented asynchronous model API.

Freeze two variants per family, three fresh-state repetitions and interleaved arms
before final dispatch; execute in bounded batches with the existing stop gates.
Report negative outcomes separately from positive efficiency. Require no critical
evidence/safety failure or paired correctness regression, at least 25% reduction in
avoidable same-version duplicate source content with route coverage disclosed, and
no aggregate provider-cost increase. Report filesystem fetches separately: a zero
baseline is insufficient to claim their reduction. Any failed held-out case becomes
diagnostic; do not tune on it and keep calling it held out. Passing only these first
two families is insufficient for the full goal, DeepSWE expansion or default promotion.

Final implementation regression: 891 passed, two skipped; all 12 cached-Docker
fresh-fixture checks pass. Full lint/compile checks pass and typing has zero errors
with the existing runtime export warning. No provider calls used the new fixtures.

#### Fresh source-revision and conflict implementation

Four additional qualification fixtures reuse the existing two-checkpoint coordinator,
brokered source operations, actual worker loss and per-answer oracle:

- Two shipment revisions require a guarded source replacement, completed capture
  of its new version and recomputation across unchanged CSV/TOML pricing evidence.
  Changed fields include the selected zone, quantity, explicit zero credit and a
  false-to-true base-charge waiver.
- Two competing-candidate cases require preserving contradictory source-backed
  claims, acquiring an initially withheld activation file and resolving by completed,
  hash-matching evidence. The selected candidate and row order differ across variants;
  the second adds a newer `completed` row with the wrong content hash. These are
  domain records, not synthetic substitutes for actual failed/pending tool operations.

No findings or derived answers are host-seeded. Both arms retain ordinary artifacts.
The scripted tests exercise disputed entries and explicit supersession in findings,
and artifact recovery in control. Stale-value proposals reach independent verification
and fail before a supported repair; the first failure remains recorded. A deliberately
correct early guess is unsupported both by evidence timing and its answer window;
later capture and rewrite do not relabel that historical submission as supported.
The existing initial-capture audit marks premature activation reads ineligible even
when the answer is correct. All normal scripted paths have zero same-version overlap;
this proves a capable reuse path exists, not that live models will choose it.

All 28 qualification tests pass locally; the 16 new revision/conflict checks also
pass against the cached immutable Docker image at
`.artifacts/docker-qualification-revisions-preflight-20260913-v1`. Lint, compilation
and typing pass (the existing runtime export warning remains). The v15 dry manifest
at `.artifacts/qualification-v15-dry-20260913-v1/manifest.json` retains all 27 earlier
fixture hashes and adds four cases. It is an implementation manifest at a dirty
precommit revision, not the final freeze or a live dispatch.

Fresh actual-validation variants and explicitly owned prior-source applicability
remain before the six-family/three-repetition panel can be frozen. No production
authority, model surface, default, paid budget or promotion gate changes here.
Final full regression: 907 passed, two skipped in 239 seconds; no failures.

#### Fresh actual-validation controls and source-binding correction

The qualification panel now has a signed CSV batch family with one valid and one
checksum-invalid variant. It uses a real `check_batch` unittest subprocess, not
status words in a source fixture. Positive preparation captures the validation rule;
after the first actual worker loss the agent reads the batch and executes the check,
then preserves its finding/receipt across another worker-loss cut before answering.
The negative has one cut and requires blocking after the actual failing check.
Both arms retain normal artifacts, and preparation/checkpoint calls are charged.

The fresh controls cover skipped checks, a different command identity, a correct
answer written before successful execution, forced completion after actual failure,
and repeated successful checks without source rereads. A new counterexample found
unsafe completion in both arms: a real check ran on a temporary valid input, that
input was restored to another version, and an answer for the restored version was
accepted. `multi-answer-evidence-v3` now requires declared source versions to be
observed at validation dispatch. Later reads/restoration do not retarget a receipt.
The post-run audit independently flags accepted-but-unsupported latest answers;
a deliberately bypassed oracle evidence check proves that detection. This is an
explicit fixture-source contract, not automatic semantic dependency inference.

That investigation also found a production memory inefficiency: a successful PTC
check left a provisional workspace invalidation even after its host-observed
unchanged fingerprint. The shared source-observation reducer now retracts only that
operation's provisional invalidation. Eleven tests preserve missing/failed/malformed/
truncated/mismatched observations and older/intervening unknown state. This does not
reconcile unknown execution. Its changed code is included in memory program hashes.

All 32 validation checks pass in cached Docker, including the pending-publication
race and 18 new tests. The v16 dry manifest at
`.artifacts/qualification-v16-dry-20260913-v1/manifest.json` retains all 31 prior fixture
hashes and adds two cases. It is a dirty precommit implementation manifest, not a
final freeze or live dispatch. Owned-prior source/task/workspace/version admission
remains before the complete held-out panel; source-run preparation and cost must be
included explicitly, with disjoint source/target questions rather than solution recall.

#### Frozen bounded validation-binding live regression

After the full deterministic regression passes, run exactly six reused diagnostic
trials: `validation_complete`, `validation_failed`, `reuse_repository_3`, each with
`no_recall` then `findings`, concurrency six. Use Luna/max, unchanged case budgets
(12 calls/200k input for validation; 24/350k for repository), 8192 output tokens,
900 seconds per trial, and the existing immutable cached Docker image
`sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
Use `.artifacts/validation-binding-live-20260913-v1`; the manifest must record the
clean revision, driver/fixture hashes and budgets before the first provider request.
Do not retry failures or expand beyond six trials. Existing provider, harness,
measurement and false-acceptance gates stop queued dispatch; in-flight trials remain
bounded by their declared limits. Report all first verifications, unresolved effects,
withholding, calls/cost and source/artifact exposure separately. Prior v13's same
six cases cost $0.08703809; that is historical spend, not a new price guarantee.

Require all positive first verifications supported, negatives withholding, and no
accepted unresolved/unsupported result. Voluntary negative blocking is not evidence
of forced-claim rejection; Docker covers that boundary. This screen tests live
regression, not held-out reliability or causal reread savings. In particular, do not
claim validation-invalidation savings unless a relevant post-check cut is actually
observed. No new qualification fixture, broad DeepSWE run or default is promoted.
Pre-dispatch regression passed: 936 tests, two skipped; 32 validation Docker checks,
full lint/compile, and typing with zero errors (existing runtime warning only).

The frozen v16 regression completed at clean `9a950e3` for $0.08178951. All four
positive first verifications and completed-evidence gates pass; both failed-check
trials withhold without proposing final verification and retain three unresolved
records each. All ten worker-loss cuts occur, with no provider, measurement,
accounting or false-acceptance errors. Captured static provider-request shapes stay
unchanged within every trial. The same old v13 positive traces remain supported
under the new source-binding audit; no historical result file was rewritten.

Repository findings used 15/$0.01832458 versus control's 18/$0.02444097, 25.0%
cheaper. Across all three disjoint intervals, 15 control artifact loads and 72 exact
source-line re-emissions fell to zero. Both arms had zero source-file rereads;
their final reads inspected answer artifacts. Short validation regressed from
6/$0.00997130 control to 8/$0.01216900 findings (22.0% more), reflecting a separate
note-writing cell and split input-read/check cells. Positive aggregate cost fell
11.4% with 23 versus 24 calls. Negatives stay outside the efficiency aggregate.

No post-validation cut occurred in that live validation pair, so the source-
invalidation improvement has deterministic boundary evidence but no causal live
reread/cost attribution yet. The regression passes; five fresh families remain
unrun, owned-prior source applicability is next, and full qualification/defaults
remain held. The full record is
`.artifacts/validation-binding-live-20260913-v1/analysis.md`.

#### Owned-prior applicability audit (implemented; fresh protocol still open)

`evals/prior_evidence.py` consumes host-owned `RuntimeBindings` and frozen producer
events/receipts, reusing the existing content-addressed artifact resolver, canonical
source-manifest hashing, source observations and unresolved-execution projection.
It adds no production authorization store, model tool or orchestration loop. Both
task identities remain explicit: foreign sequence numbers are never converted into
consumer read events. Qualification producers must have completed verification with
resolved effects before consumer creation; this stronger experimental eligibility
rule does not change production's ability to inspect owned failed-run history.

Each supported contribution needs a completed managed memory capability whose
content-addressed result matches its broker hash and public-view receipt. The view's
source manifest must match the authorized canonical producer snapshot. Working-set
findings and full note history must match canonical note entries; read recovery may
contribute only its complete selected captured lines, never a wider range. Metadata
lookup alone, opaque artifact output and suffix byte pages do not count. An earlier
deduplicated public-view receipt may support a later identical query, but availability
starts at that query's own completed capability, not at the old receipt.

A consumer version observation matching the required source hash must precede each
answer dispatch. Necessary one-line identity reads do not magically capture the rest
of the file: they only establish applicability for evidence actually retrieved from
the prior run. Changed versions require fresh current ranges or another applicable
source. Current required validation receipts cannot be inherited. The existing
expected-value oracle, source/window gate and independent post-run classification
all remain required; selected advisory findings do not prove semantic use of every
cited line. Input and implementation hashes are retained in the audit.

Twenty-three actual-PTC checks cover working-set/history/event retrieval, complete read
recovery, metadata-only lookup, missing current identity, late and pending retrieval,
uncaptured ranges, changed-source repair, owner/conversation/workspace/state denial,
unfinished/failed/unresolved producers, canonical-note/source-manifest corruption,
wrong consumer identity, range expansion and altered artifacts. Their producer
completion event is host-supplied unit input, not a fresh model-quality result.
Repeated-query testing also exposed the production no-op-write invalidation fixed
in `3a2eebc`; eight reducer tests retain uncertainty on failed/unknown/malformed cases.

The dry manifest `.artifacts/qualification-v17-prior-audit-dry/manifest.json` keeps all
33 prior fixture hashes and adds the audit to driver hashes. It is a dirty development
manifest, not the full clean freeze or a provider dispatch. Next implement two related
but disjoint model-written producer/consumer variants through `run_verified_case`,
retain immutable producer state and actual host bindings, and account explicitly for
producer preparation and provider cost. Then freeze all six families, repetitions,
combined episode counts/budgets and eligibility before live dispatch. No defaults or
broad coding benchmark expansion are authorized by these deterministic checks.
Final full regression passes 965 tests with two skipped; the two subsequently added
page-boundary tests also pass in the 23-check prior suite. Lint/compile pass, and
typing reports zero errors with the existing runtime export warning. No new live
spend accompanies this implementation.

#### Fresh owned producer/consumer protocol and full-panel freeze

`qualification_prior_1` and `qualification_prior_2` now run a catalog audit and a
different shipment question through `run_verified_case`. The producer reads six
policies plus pricing code, writes its own findings/artifacts, crosses a real idle-
worker-loss cut and independently answers catalog statistics. The consumer gets a
new shipment, not the producer's question or answer. Variant 1 preserves policies;
variant 2 changes the selected unit price enough to activate the existing cap.
Its old snapshot remains immutable historical evidence, not current authority.

Producer first verification and every source submission must pass, checkpoint and
accounting must be complete, and effects must be resolved before consumer dispatch.
An ineligible producer blocks the consumer and stops queued campaign work. Each arm
has its own fresh producer and pays its full cost. The completed producer answer is
moved to `producer/completed-answer.json`, outside the shared workspace; nothing is
deleted. A new fixture task boundary installs the shipment and declared source
revision, and the consumer records actual owned bindings plus frozen canonical source
events/receipts outside its workspace. The existing memory flag still gates access
in control. No new model tool, production authorization store or model loop is added.

Consumer requirements name shipment fields, the selected policy's first four data
lines and the three-line pricing function. Comments are not decisive evidence.
The scripted unchanged pair re-fetches seven prior source lines in control versus
two necessary one-line version observations with findings. Changed policy versions
require fresh data; unchanged pricing can still be recalled. Cross-run measurement
keeps task identities/clocks separate and reports same-version fetched overlap,
one-line identity candidates and unknown routes; these are not automatically wasted
lines or proof of semantic consumption. Per-episode artifact/provider exposure is
retained alongside the cross-run fetch count. Ten scripted root-workflow controls
and three dispatch/accounting controls pass; these are not live qualification.

After final deterministic/Docker preflight and a clean focused commit, dispatch
exactly `.artifacts/qualification-v18-live-six-families-r3` using Luna/max, these cases
in the listed order, and interleaved `no_recall`, `findings` arms for repetitions 1–3:

- `qualification_routes_1`, `qualification_routes_2`
- `qualification_partial_1`, `qualification_partial_2`
- `qualification_changed_1`, `qualification_changed_2`
- `qualification_conflict_1`, `qualification_conflict_2`
- `qualification_validation_1`, `qualification_validation_2`
- `qualification_prior_1`, `qualification_prior_2`

This is **72 trials / 84 model episodes**, including twelve fresh producer episodes.
Every episode retains 24 calls, 350k task-input tokens, 8192 output tokens per request
and 900 seconds. A prior pair consequently declares 48 calls / 700k input across its
two episodes; all cost is charged. The total planned ceilings are 2016 calls and
29.4M task-input tokens, not a claim that those budgets will be consumed. Use global
concurrency six and cached immutable command image
`sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
No source/consumer episode overlaps within a pair. Fresh roots isolate repetitions;
the pre-dispatch manifest records source/fixture/driver hashes, clean revision,
repetitions, both-episode budgets and planned episode count. Development dry
manifests are not substitutes for that clean freeze.

Stop queued dispatch for provider/harness/measurement errors, false acceptance,
timeouts, producer ineligibility, missing usage/cost, unaccounted calls or extra wire
attempts. Already-running trials retain their bounds. Do not restart failures or
retune any dispatched case and continue calling it held out. Keep all terminal
outcomes, including unstarted cases, and separate the six expected failed-validation
trials from positive efficiency. Required gates remain zero critical safety/evidence
failures or paired correctness regressions, at least 25% less avoidable same-version
source-content duplication with route coverage disclosed, and no aggregate positive
provider-cost increase including producer work. Identity checks and missing/changed
ranges are necessary acquisition, not avoidable rereads. A zero filesystem baseline
cannot demonstrate filesystem savings. After this whole gate passes, and not before,
preflight the diverse six-task DeepSWE v1.1 paired confirmation. Defaults stay held.
Pre-dispatch evidence: 977 full regression checks pass with two skipped; the final
13 prior/dispatch checks pass locally and in cached Docker at
`.artifacts/docker-prior-qualification-preflight-v2`. The last focused run includes
the three new repeat/stop controls and tightened decisive ranges. Lint/compile pass;
typing has zero errors with the existing export warning. Existing 33 fixture hashes
are unchanged; only the two new prior variants are added to the held-out inventory.

#### v18 stopped result and next implementation stages

The frozen campaign ran 40 trials / 44 episodes and held 32 queued trials after a
pre-cut budget exit exposed exception-classification and zero-cut measurement bugs.
There were 33 verified completions, four workflow blocks and two call-limit exits in
addition to that budget exit. Total recorded cost was $1.12553090 including prior
producers. Findings cost 1.0% more across the 19 started positive pairs; the 14
both-verified pairs used 8.0% fewer calls but cost 6.5% more. This is failed/incomplete
qualification. Raw results stay frozen at
`.artifacts/qualification-v18-live-six-families-r3`; `analysis.md` records the final
disposition and `interim-mechanism-analysis.md` identifies exact trace sequences.

1. **Diagnostics/runtime:** v19 recognizes typed budget causes through ADK wrappers
   and measures zero-cut exits. No exception-prose classification, budget increase,
   retroactive result rewrite or resumed campaign. This correction is committed
   independently as `b357edf`.
2. **PTC/effect admission:** explicit artifact argument/access/metadata rejections
   retain known no-effect semantics and exact-URI recovery guidance. Integrity
   failures and interrupted publication remain unknown; later successful work cannot
   erase them. Test safe recovery through actual independent completion and retain
   corrupted-artifact, interrupted-publication and unknown-shell negative controls.
3. **Memory/context/review:** avoid fetching completed unchanged evidence again during
   review. For prior findings, represent applicable current-task version observations
   separately from producer provenance; source-task clocks and advisory truth stay
   distinct. Resolve foreign-citation usability through a scoped contract, not wider
   implicit authorization. Investigate note schema/budget retries and executed-marker
   discoverability separately from post-cut quality; neither warrants larger budgets.
4. **Qualification:** after deterministic and isolated regressions, use the old
   dispatched cases diagnostically and genuinely fresh cases for qualification.
   Preserve the original source/evidence/correctness/route-coverage/cost gates,
   including producer costs. No default or broad DeepSWE promotion follows merely
   from improved diagnostics or fewer control blocks.

#### Consumer-version diagnostic freeze

After the consumer-version representation's full regression, cached-Docker checks,
and clean focused commit, run only `qualification_prior_1` and
`qualification_prior_2`, interleaved `no_recall`/`findings`, one repetition, at
`.artifacts/consumer-version-prior-live-v1`. These are reused diagnostic cases, not
new held-out qualification. Use Luna/max, concurrency at most six, the same cached
image and unchanged episode limits: 24 calls, 350k task-input tokens, 8192 output
tokens per request and 900 seconds. Four pairs-of-episodes mean four trials/eight
episodes, at most 192 calls and 2.8M task-input tokens. Charge producer and consumer.

Both arms receive the same artifact-admission fixes; only the findings arm has the
active memory representation. Keep canonical source bindings, original task questions,
value/source/version requirements and stop gates. Measure actual matching observations,
prior retrieval and consumer source acquisition separately; a metadata-only identity
check is not learned content. Count necessary changed/missing ranges separately from
duplicate same-version content. Require supported first verification in all episodes
before treating efficiency differences as interpretable; do not change defaults or
launch broader tasks from this diagnostic. Retain failures, incomplete phases, costs,
and any stopped queued work without retries or retrospective relabeling.

Diagnostic completed at `f182027`: eight of eight episodes first-verified, but
findings had no aggregate prior-source reread reduction and cost 38.0% more. No
matching consumer-version result reached the model after source identity checks;
five note writes retried unavailable foreign citations. This is a failed efficiency
gate, not justification for a larger cohort. Raw results and the detailed analysis
remain at the frozen output root.

The next implementation order is (1) preserve whole active control instructions
under task/packet budgets, including an explicit outcome when required control
cannot fit; (2) append bounded completed-evidence navigation at actual review/work
boundaries without continuously rewriting cached context; (3) expose prior
applicability after authorized observations and make scoped prior-note reuse
convenient. All six inspected provider review packets in two v18 reread-heavy cases
had lost the host next_action through generic JSON truncation. Keep review and
independent verification intact. Require wire-level preservation, stale/partial/
unknown-effect negatives, deterministic bounds and source-clock checks before a
separately frozen diagnostic; new diverse held-out qualification still follows.

Stage (1) implementation is `work_packet@2`: preserve complete control JSON, supplied
skills/handoff/steering, allocate optional context afterward, and stop before dispatch
if required context exceeds the unchanged total packet ceiling. Section allocations
remain preferred targets for required sections and ceilings for optional detail.
Typed overflow has a separate runtime/eval terminal and campaign stopping gate (v20).
The real root-workflow provider-request regression and 14 cached-Docker checks pass;
full regression passes 1006 tests with two skips, and the final focused follow-up
passes 88 with one explicit Docker skip. Stages (2)/(3), live reread/cost effects and new held-out
qualification are still outstanding. Do not rerun paid tasks merely to retest syntax
or contract preservation.

Stage (2)'s PTC representation now exposes registered read values retained inside
plain containers, using bounded automatic selectors and the existing completed-cell
state updates. This closes the `reads`-is-only-a-list gap seen in v18 review cell 201.
No model-generated summary, copied provenance, new tool or automatic source fetch is
introduced. The actual broker regression preserves one completed source read while
recovering its nested citation. Full regression passes 1012 tests with two skips;
the PTC/context/qualification subset passes 167 with one explicit Docker skip.
A fresh phase/work-batch navigation snapshot is still
needed; do not equate this representation substep with completed review reuse or live
qualification. Prior applicability and citation recovery remain separate follow-ups.

The boundary-refresh follow-up must be keyed to a host-issued work batch/current
task packet, not arbitrary model prose or every inner tool call. Retain a versioned,
watermarked navigation snapshot at a stable append position so later calls do not
rewrite the old prefix. Its kernel epoch, captured source ranges and completed-check
references remain historical observations, never authority to clear an unknown effect.
Prefer useful task-relevant sources over redundant aliases under the existing budget;
do not repeat a full catalog just because the phase changed. Validate provider-visible
placement, deterministic replay, cache-prefix stability, stale/partial/worker-loss
negatives and unchanged completion gates before freezing another live diagnostic.

The boundary-refresh implementation now uses the actual appended root work packet,
not a second reinsertion store. `work_batch_navigation@1` records a bounded historical
snapshot in `context.evidence_navigation_created` before the packet is dispatched;
same task/invocation/batch replay validates identity and returns its exact bytes.
`continuation@8` shares source/worker-state assembly with this path. The packet reserves
the snapshot whole, recent events exclude its duplicate, and no inner tool call
refreshes it or forces a cut. Task-focused binding selection collapses identical read
references; check navigation includes readable commands and available recovery IDs.
The real compiled-provider regression exercises `reads[0]` recovery during review
without another source acquisition. Full regression passes 1020 checks with two skips;
35 scripted cached-Docker workflow checks and the final 91 focused checks pass.
Lint/compile pass and typing has zero errors with the existing export warning.
Live qualification remains a separate gate; prior applicability/citation usability
is still stage (3). No new paid diagnostic or default change.

Stage (3) now keeps prior findings in place instead of copying them into local notes.
Working sets carry source-scoped exact note recovery; note-schema version 4 and typed
foreign-citation rejection explain the same contract without widening admission.
After a completed PTC read cell, the active authorized-prior profile can expose a
bounded `prior_applicability@1` update in the new tool response. It preserves original
execution bytes/effects, shares the existing response ceiling, and contains identity
statuses rather than learned content. Its canonical exposure record holds source
inputs, hashes, clocks, selection, budget and replay identity. Repeated observations
of the same versions do not produce repeated notices. Full regression passes 1033
checks with two skips; 50 isolated cached-Docker prior/verification checks pass.
Lint/compile pass and typing has zero errors with the existing export warning.
The live diagnostic remains a separate gate, not a consequence of these code changes.

The next paid diagnostic is deliberately the same two prior cases used at `f182027`,
not a new held-out claim: `qualification_prior_1` and `qualification_prior_2`, paired
`no_recall`/`findings`, one repetition, Luna/max, at
`.artifacts/prior-reuse-navigation-live-v1`. Freeze a clean tested commit first. Keep
the same cached immutable image, maximum concurrency six, 24 calls/350k input/8192
output per request/900 seconds per episode. Four trials include four independent
verified producers and four consumers: at most 192 calls and 2.8M task-input tokens,
with all producer costs charged. Common boundary/PTC fixes apply to both arms.

Require first-verification support and complete accounting before interpreting paired
efficiency. Check actual provider-visible applicability after identity reads, scoped
note retry counts, necessary changed/missing ranges versus unchanged source overlap,
artifact/shell exposure coverage, calls and full cost. Metadata alone must never satisfy
missing-content evidence. Retain failures and stopped work, do not retry selectively,
and do not expand or promote from mechanism exercise alone. Review-specific diagnostics
and genuinely fresh diverse held-out qualification follow only after these gates.

#### Delivered-steering correction and diagnostic freeze

The prior-navigation diagnostic closed at `5f2f6cf`: six of eight planned episodes
ran, three independently verified, three exhausted a call/input budget, and two
consumers were not started. Total 95 calls/$0.20349057. There is no complete two-arm
consumer pair. One findings consumer reused retained bindings without review rereads,
then printed its preparation marker again. All 29 observed review-packet occurrences
omit the delivered question and USER STEERING; earlier historical request contents
may still retain it. The four rejected Python cells preserved the heap, disproving
worker-reset attribution for those failures. Keep raw results and the separate
analysis at `.artifacts/prior-reuse-navigation-live-v1`.

Fix this shared harness path before changing any evaluator protocol: retain ordered
delivered steering as required control after queue acknowledgement, avoid duplicate
recent-event excerpts, and have criterion review audit prerequisite execution from
receipts rather than reenact old instructions. Do not infer automatic semantic
supersession, rewrite criteria, relax source checks or treat delivery as completion.
Use the existing packet ceiling and typed overflow. The scripted provider regression
must see the current question at actual producer and consumer review, with unchanged
original goal/criteria and stale/guess/identity-only negatives still rejected.

After full regression, cached-Docker verification and a clean focused commit, run
the same reused `qualification_prior_1`/`qualification_prior_2` diagnostic at
`.artifacts/steering-continuity-live-v1`, no_recall/findings, one repetition, Luna/max,
concurrency at most six. Keep the cached image, all questions, criteria, source/range/
time gates and episode caps (24 calls, 350k input, 8192 output/request, 900 seconds).
Maximum four trials/eight episodes, 192 calls and 2.8M task-input tokens; charge
producer and consumer. No selective retries or budget increases. First-verification
support, completed protocol and full accounting must precede efficiency interpretation.
Measure packet-visible current instructions, repeated preparation markers, post-review
same-version reads, actual prior content retrieval, changed/missing-range acquisition,
and costs separately. Fresh diverse held-out qualification and DeepSWE stay gated.

The steering correction passes 1037 full-suite checks with two skips, 50 isolated
cached-Docker checks and 15 final serialized-provider checks. The initial Docker
attempt's unavailable host temporary mount is retained separately; the successful
run uses a fresh workspace-local directory. Lint/compile pass and typing reports
zero errors with the existing export warning. These code checks authorize only the
frozen diagnostic above, not an empirical success claim or expanded campaign.

The frozen diagnostic completed at `4609f62`: all eight episodes pass first
independent verification and all answer submissions have declared completed-source
support. All 11 observed review packets carry the delivered question; no episode
repeats preparation after review or rereads sources during review. Findings refetches
three unchanged prior-source lines, all identity candidates, versus 18 in control;
the changed-policy consumer acquires the current policy evidence. Mechanism and
completion gates pass on these two reused cases, not on fresh held-out tasks.

The cost gate fails in both pairs: aggregate findings 42 calls/$0.12438927 versus
control 37/$0.07868618 (+58.1% cost), charging producer and consumer. Producer
findings cost is lower; consumer bookkeeping and prompt input dominate the regression.
The detailed trace/action/cost analysis remains at
`.artifacts/steering-continuity-live-v1/analysis.md`; total paid cost $0.20307545.
No live process remains. Next investigate note read/schema/rewrite sequences before
terminal answers, source-local versus truly multi-source finding dependencies,
repeated prompt bodies and post-cut cache receipts. Do not assume all omitted work
is safe or that a stable routing key proves effective caching. Require measured
contract-preserving improvement before another frozen diagnostic, fresh diverse
qualification or any DeepSWE/default promotion.

#### Anchored inner-loop steering

The closed `4609f62` consumer wires identify 22 same-epoch transitions that replace
the previous last steering item with new assistant/tool work, then append steering
again. Each reports only 2747 cached tokens despite stable routing keys. Append-only
transitions reuse nearly the preceding input. The existing host-boundary fix retained
the current question at review; this is a distinct inner-loop placement defect.
The read-only audit is `.artifacts/steering-continuity-live-v1/cache-audit.md`.

Record first exposure in the existing task event stream with immutable rendered
bytes, native-history boundary/hash, source message IDs, invocation/root scope and
program/content hashes. Replay exact bytes at the original position after queue
acknowledgement; new arrivals append new exposures. Preserve newly exposed steering
and the preceding unconsumed call/result during compaction. Keep normal compaction,
required task control, all original criteria and independent verification. Queue
acknowledgement and exposure are not evidence that an action completed. Publication
or identity/content corruption must stop the request, not silently drop steering.

After full regression, cached-Docker checks and a clean focused commit, freeze the
same two reused prior cases at `.artifacts/anchored-steering-live-v1`, no_recall and
findings, one repetition, Luna/max, concurrency at most six, same cached image.
Keep every fixture, evaluator hash, question, answer/source/range/time gate and cap:
24 calls/350k input/8192 output per request/900 seconds per episode, four trials/eight
episodes at most, 192 calls/2.8M task-input tokens total, producer costs included.
No selective retries, budget increases or broadened cohort. Require first-verification
support and full accounting before interpreting cost/reread changes. Compare exact
provider input prefixes within each epoch, actual cache receipts and necessary
identity/changed-range reads. Remaining note bookkeeping and dependency granularity
must not be hidden by a cache improvement. Fresh held-out and DeepSWE gates stay open.

The anchored implementation passes 1045 full-suite checks with two skips, 116
lifecycle checks and 50 cached-Docker checks. A final integrity tightening validates
exposure hashes before scope filtering; 84 final focused checks include an added
corrupt-anchor negative. Lint/compile pass and typing has zero errors with the
existing export warning. The next run is only the frozen diagnostic above; no
note-lifecycle, fixture, criterion, provider, budget or default change is bundled.

#### Finding scope after the anchored-steering result

The `15cea79` diagnostic closed with eight first-verifications, complete answer-source
support/accounting, and all 73 within-epoch provider transitions append-only. Findings
cost $0.09347522 versus control $0.07010133 (+33.3%). Prior-source refetch is 24/21
lines; excluding three findings identity candidates yields equality. The earlier
reduction did not reproduce. Full analysis stays at
`.artifacts/anchored-steering-live-v1/analysis.md`; no live process remains.

Both producers grouped six independent file facts into a joint finding. A consumer
observed one policy version, leaving the whole entry correctly unobserved. The next
bounded change is authoring guidance only: independently reusable entries, batch
writes, every dependency retained for genuine joint conclusions, and reuse prior
findings in their own scope rather than rereading to create local citations. Do not
mechanically split prior text, widen admission or weaken current-source checks.
Deterministic tests must show separate matching/unobserved/changed statuses, joint
claims remaining unvalidated, immutable replay and the existing 8000-byte note cap.

After full regression, cached-Docker checks and a clean commit, freeze a diagnostic
at `.artifacts/finding-scope-live-v1`: the same reused prior_1/prior_2 cases,
no_recall/findings, one repetition, Luna/max, concurrency at most six, the same cached
image and unchanged driver/fixture hashes. Keep 24 calls/350k input/8192 output per
request/900 seconds per episode; at most four trials/eight episodes, 192 calls and
2.8M task-input tokens, charging all producers. No selective retries or larger limits.
Inspect actual model-authored dependency groups, scope, note retries and all answer
submissions before interpreting rereads, cache receipts or total cost. Review and
exception-induced recovery remain visible, not excluded to improve the result.
Another reused-case result cannot close fresh held-out or DeepSWE promotion gates.

Pre-dispatch checks pass: 1047 full-suite tests/two skips, 62 focused tests, 50
cached-Docker continuity/verification checks, Ruff/compile and zero typing errors
(one pre-existing runtime export warning). Test artifacts use the
`.artifacts/finding-scope-{regression,focused,docker-checks}` roots.

That diagnostic closed at `7c662db`: seven of eight episodes first-verified; all
answer submissions source-supported, but one control is blocked by an unavailable
managed-search result recorded with unknown effect. Both findings producers use seven
single-source entries and both consumers reuse prior learning before answering.
Findings refetch six unchanged-prior lines (three identity candidates), down from
24 historically; this is not a valid two-pair savings estimate. The sole complete
pair has equal identity-adjusted refetch and 62.7% higher findings cost. All 67
within-epoch prefixes remain append-only. Total cost $0.15265168, complete accounting,
no retry and no live process remaining. Report: `.artifacts/finding-scope-live-v1/analysis.md`.

Before another paid diagnostic, trace and correct two observed interface boundaries:
explicit no-effect metadata for proven pre-execution unavailable managed-search
responses (never a blanket failed-shell exemption), and schema identity separate
from note CAS version (one model used schema version 5 as expected note version 1).
Keep independent review, unknown-effect fences and byte/call limits. Actual worker
loss recovery and review rereads remain separate work; fresh held-out qualification
and DeepSWE expansion remain unearned.

The two interface corrections are implemented in separate commits. Unavailable
search backends and parser-rejected reserved commands now return explicit no-effect
metadata at their originating pre-dispatch boundary; no automatic fallback executes.
Post-dispatch backend errors and ordinary shell failures remain unknown. Actual PTC
publication/duplicate-acknowledgement and root completion/failed-shell tests pass.
The note contract now uses `schema_version=6` without a generic top-level `version`;
note CAS revision semantics and no-effect conflict rejection are unchanged.

Next address the actual worker-loss message rather than buying another run merely
to retest these two interfaces. `_state_updates` currently reduces a lost source
binding to name/selector/association_invalidated and discards its completed read
reference; its `more` hint still points at live state inspection. Preserve an explicit
historical recovery handle at that invalidation boundary, distinct from a live binding
or current source. Reuse the task-authorized artifact loader and completed read
metadata, retain bounded whole entries, and do not replay the failed cell. Test a
real exception followed by artifact-backed calculation without a new fs.read, plus
changed/missing ranges, descriptions without evidence, retained heaps, output-budget
limits, task scope, replay and unresolved-effect negatives. Historical recovery must
not support a claim that the failed computation completed. Keep this independently
committed and tested before freezing its live diagnostic; review-stage reuse remains
separate and no paid benchmark/default promotion is authorized.

Combined interface checks pass: 1055 full-suite tests/two skips and 52 isolated
cached-Docker checks, with 61 adapter/PTC, eight completion-fence and 62 memory/factory
focused checks. Ruff/compile pass and typing has zero errors with the existing export
warning. All processes are terminal. No new paid diagnostic has been started.

#### Completed-read recovery after actual binding loss

PTC now preserves an optional exact historical artifact-load expression when a
supported binding is invalidated. Required invalidation notices precede optional
whole recovery entries within the existing budget. The canonical terminal stores
`ptc_state_updates@1` with replay inputs, watermark, hashes and selection policy.
The message distinguishes a saved completed read from a live variable, current source
truth and an unfinished calculation. It reuses the existing artifact broker; unknown
effects cannot be reconciled by loading a read or by a subsequent correct answer.

Seven actual worker lifecycle checks cover complete/partial captures, changed sources,
corruption, unresolved effects, snapshot rollback and parse rejection. The real root
workflow also executes a genuine exception, recovers through the handle present in
the actual model request, writes from recovered bytes and verifies without another
source read. These are scripted protocol checks, not live-model behavior. Two new
test assumptions were corrected: this fixture uses `answer_evidence` (not the newer
multi-answer report), and its 16 seeded partial reads must be separated from the one
model-phase complete read. Neither correction changes production verification.

Final verification passes 1064 full-suite tests/two skips, all 53 cached-Docker
checks and four focused root checks. Artifacts are respectively
`.artifacts/lost-binding-regression-final`, `.artifacts/lost-binding-docker-checks-final`
and `.artifacts/lost-binding-verification-final2`; all processes are terminal.
Lint/compile pass, typing has zero errors with the existing export warning. The
earlier failing test reports are retained, not overwritten or relabeled.

Before paid execution, cover the sibling acquisition boundary found by an actual
worker probe: `captured = agent.fs.read(...); 1 / 0` preserves a completed read
artifact but produces no direct handle because there is no previous completed
binding manifest. `.artifacts/same-cell-recovery-probe/pytest.xml` confirms one
completed read, one artifact, a discarded worker and empty state updates. Derive
optional recovery entries from completed same-attempt capability receipts; never
claim a dirty binding survived or that the failed calculation completed. Test missing,
failed and unknown-effect variants as well as genuine completed-read recovery. An
initial stdin probe could not spawn Python and is not evidence for this conclusion;
the pytest probe executes the real worker and verifies the completed receipt.

Next freeze a small actual-exception recovery diagnostic before another broad memory
comparison. Require completed acquisition before a recorded, no-external-effect Python
failure; audit the next model request for the direct recovery entry and every subsequent
source/artifact read. A random run without a failure does not exercise this mechanism.
Include complete and missing-range recovery plus changed-source and unknown-effect
negatives; retain every failure and preparation cost. Do not mistake seeded diagnostic
uptake for held-out reliability. Independent review rereads are a separate hypothesis,
and both cost non-regression and fresh diverse qualification remain open.

The same-cell recovery path now uses completed capability receipts via
`ptc_state_updates@2`. The first root regression exposed a genuine projection issue:
eight older invalidation rows consumed every entry slot, leaving only an older partial
capture recoverable. The corrected view groups the retained binding invalidations,
prioritizes recent completed reads, then admits older recovery handles as space permits.
It does not raise the eight-entry/2048-byte ceiling or pretend an old binding contains
new bytes. The root test recovers the complete capture and verifies without rereading.
Receipt corruption, mismatched request identities, partial coverage, failed/missing
reads and unknown effects have separate negative checks; all remain fail-closed or
explicitly unavailable as appropriate.

After the implementation checks and focused commit, freeze a four-trial seeded
usability diagnostic: the existing missing-range development fixture, complete versus
three-line capture in an explicitly supplied read-then-fail scratch cell, each with
`emit_state_updates` on/off. Keep the memory representation, note seed, artifact
capabilities, oracle, model and all other configuration equal. This tests the state
message, not memory-on versus memory-off. Luna/max, at most six concurrent trials,
12 model calls/200k task-input/8192 output tokens per request/900 seconds per trial;
at most 48 calls and 800k task-input for the cohort. Freeze code and fixture hashes,
retain every trial, count all provider work and stop on accounting/infrastructure faults.
Verify that the supplied cell actually ran, the read completed before failure and
the expected heap loss occurred. Missing intervention means unexercised, not a pass.
Measure direct handle use, catalog/history fallback, necessary missing-range reads,
avoidable rereads, answer-time evidence, first verification, review overhead and cost.
No selective retries or hidden budget increases. Host-seeded preparation is explicitly
outside the charged live phase: the diagnostic cannot qualify amortized preparation
cost, held-out generalization or default promotion. Fresh unseeded, diverse paired
continuations remain required after this usability gate.

Same-cell recovery verification is complete: 1084 full-suite tests pass with two
skips (`.artifacts/same-cell-recovery-regression`), all 54 cached-Docker checks pass
(`.artifacts/same-cell-recovery-docker-checks`), and the focused contract/root suite
passes 113 tests with one skip plus a separately added actual missing-read negative.
Lint/compile pass; typing has zero errors with the pre-existing export warning.
The failed crowding regressions remain retained. No paid usability diagnostic has
started yet; its runner and machine-readable manifest must enforce the bounds above.

The fixed diagnostic runner is `evals/recovery_messages.py`. Its preflight exercises
the actual root workflow and serializes real request shapes: direct completed-capture
recovery, necessary partial-range acquisition, off-arm rereads, a skipped-failure
negative and a source-unsupported answer negative. Artifact acceptance does not count
as probe exercise. Accounting/audit faults stop unstarted work while preserving settled
usage and raw verifier results. All nine cached-Docker checks pass at
`.artifacts/recovery-message-docker-preflight`; lint/compile and typing pass. Only eval
code is added; the harness remains at the fully tested `f54ffd8` implementation.

Freeze the first live output at `.artifacts/recovery-message-live-v1` with a clean
committed tree, the four conditions above, Luna/max and cached image
`sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
The manifest records the actual revision, driver/fixture hashes and complete paired
configurations before dispatch. The only on/off configuration difference is
`emit_state_updates`; all live model work, including the supplied failure, is charged.

The live run closed at `0141ae1`, with all four interventions exercised once and all
four first verifications/source-support checks passing. On/off totals: 11/12 calls,
$0.01925322/$0.02101734, 149641/163898 input, 36755/35146 uncached input tokens.
All 19 within-epoch request transitions preserve prefixes. No missing accounting,
extra wire attempts or unresolved effects; total spend $0.04027056. All processes
are terminal. The complete/on model directly loaded the supplied artifact; complete/off
used lookup plus `read.recover`. Partial/on skipped lookup and read only the missing
range. Both arms have zero avoidable rereads, so this is usability evidence, not a
new reread-reduction claim. Full analysis: `.artifacts/recovery-message-live-v1/analysis.md`.

The complete/on model first treated the JSON-encoded saved result as source text,
then spent another cell parsing its envelope. Address that local representation/
instruction ambiguity with existing facilities, not a new top-level tool or evidence
store. The measured 8.39% cost reduction is one small seeded result, not cost stability.
Fresh unseeded repeated-use, prior-run, natural-compaction and coding quality gates
remain open; no default promotion or broad paid expansion follows from this pilot.

#### Saved-read decoding guidance

The implemented `ptc_state_updates@3` notice offers one shared guarded recipe beside
historical recovery handles, also exposed by existing `artifacts.load` help. It
resets its proposed output bindings, accepts only a complete first UTF-8 byte page,
checks the saved result status, and retains the original envelope alongside source
text. A fully loaded artifact may still hold only a partial source capture; loading
it proves neither current freshness nor completion of the failed calculation.
Incomplete/binary pages continue to use exact byte paging, not partial JSON parsing.
The optional recipe fits within half the existing response ceiling and is omitted
otherwise; canonical program/notice hashes make its exposure reproducible.

The actual root-workflow regression now executes the recipe from the model-visible
failure message, both for an earlier completed binding and a same-cell read before
failure. It independently verifies the recovered answer with no recovery/review
source read. Separate page/coverage cases exercise incomplete first pages, final
nonzero-offset pages, base64, blocked loads, unsuccessful saved results and partial
source captures, including stale pre-existing local variables. This is deterministic
contract evidence, not proof that a live model will choose the recipe or save a call.
Fresh unseeded paired evaluation and live uptake remain required.

Verification closed with 1100 full-suite passes/two skips at
`.artifacts/recovery-decode-regression`, 48 cached-Docker passes at
`.artifacts/recovery-decode-docker-checks`, and 121 focused passes/one skip at
`.artifacts/recovery-decode-contracts`. Lint/compile pass; typing reports zero
errors and the pre-existing runtime export warning. All processes are terminal.
No provider work has been purchased for this recipe version.

#### Next fresh repeated-use panel (pre-dispatch design, not a live freeze)

Use the existing root-workflow coordinator and independent multi-answer oracle.
Add three genuinely new source problems, rather than renaming or changing constants
in the previously dispatched fixtures:

- Ordered routing rules: first-match priority, disabled rules and terminal rejection;
  three delayed requests distinguish remembering individual rules from their order.
- SQL eligibility: NULL-sensitive anti-joins and latest completed revisions across
  two tables; delayed queries require the learned relationship, not a raw-value list.
- Build dependency selection: shared transitive dependencies and disabled edges;
  an authorized manifest change invalidates only dependent conclusions before the
  third question, while unchanged captured modules remain reusable.

All source acquisition, checkpoint authoring, acknowledgement, recovery and review
are model-executed and charged. Compare `no_recall` against `findings`, retaining
ordinary artifacts, safe restoration, read-index size, tools, oracle and budgets
in both arms. Both receive identical task instructions; no host-supplied findings
or recovery solution. Use three delayed questions per task and the existing explicit
idle-worker-loss cuts. This panel does not stand in for natural compaction, actual
exception recovery, prior-run ownership or broad coding qualification.

Before any provider dispatch, implement and independently check source-derived
answers, every answer's completed source/range/version requirements, changed-source
guards, scope, and incorrect/early/unsupported submission negatives. Freeze the
fixtures and clean driver/harness revision only after local and cached-Docker
preflight. The proposed bounded panel is three cases, two arms, two fresh-state
repetitions: twelve trials at 24 calls, 350k task-input tokens, 8192 output tokens
per request and 900 seconds each, concurrency at most six (288 calls/4.2M input
maximum). No selective retries, budget expansion or defaults.

Retain every terminal and compare matched independently verified answer windows.
Report source fetch overlap, artifact/shell and actual provider-visible repeat
exposure separately; incomplete route mapping stays unknown. Require no critical
safety/evidence failures or paired correctness regression, at least 25% less
avoidable same-version content duplication, and no aggregate positive cost increase
including preparation. A zero-duplication control cannot establish savings; a
failed gate remains a failed gate. Preserve these cases as consumed after dispatch;
subsequent tuned reruns are diagnostic, never newly held out. The remaining lifecycle
and real-coding gates are still required before claiming the overall goal achieved.

#### Transfer panel implementation and live freeze

`evals/transfer_continuity.py` adds exactly `qualification_ordered_rules`,
`qualification_sql_eligibility`, and `qualification_build_graph` to the existing
qualification registry. Ordered routing distinguishes shadowing from specificity;
the SQL view chooses completed revisions before its NULL-sensitive anti-join;
the build graph requires transitive deduplication and a guarded flag revision.
The existing coordinator owns all cuts and answer windows. No new tool, runner,
recovery authority, seeded finding or increased budget is introduced. Both arms'
configuration differs only in working notes, prior recall and model-visible memory
program activation; ordinary PTC recovery messages/artifacts remain shared.

Fifteen panel tests exercise source-derived calculations and the actual root
workflow, including missing initial evidence, early answers, scope changes, missing
revised reads, stale write guards and stale calculations from otherwise completed
sources. A stale guard exposed a real no-effect classification bug; the initial
Docker failure remains at `.artifacts/transfer-docker-preflight`. The separate
`8bda3bf` fix preserves typed precondition rejections and defers parent creation
until after guards. Post-mutation failures remain unknown. All 33 panel/file-conflict
checks pass in cached Docker at `.artifacts/transfer-guard-docker-checks`; all 61
combined focused checks pass locally. The 35 prior fixture hashes are unchanged.

After the post-fix full regression and clean evaluation commit, freeze the live
output at `.artifacts/transfer-v22-live-r2`. Run the three new cases in the order
above, interleaved `no_recall`/`findings`, two fresh-state repetitions, Luna/max,
concurrency six and cached command image
`sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
This is twelve trials, 36 required answer windows, at most 288 calls and 4.2M task-input
tokens; retain the declared per-trial/output/wall limits. Manifest v22 includes
the new fixture driver hash; clean revision and all fixture hashes are recorded
before any provider work. `.artifacts/transfer-v22-dry` is a development snapshot,
not that live freeze. No mutable source or prompt changes during dispatch.

Use the existing stop-on-infrastructure/accounting/false-acceptance gate; every
already-running trial stays bounded and its outcome is retained. Require all
interventions exercised, all three answers correct and supported at submission,
and no unknown effects for a clean trial. Record corrections/early unsupported
submissions separately even if the final artifact later verifies. Do not discard
stopped/budget-limited cases to manufacture cost or reread gains. Apply the earlier
25% duplication-reduction and non-regressing cost/correctness gates; this small
panel alone cannot qualify natural compaction, prior-run reuse or real coding.

Before dispatch, a sibling missing-empty-file probe failed in both adapters:
absence was treated as content equality, incorrectly bypassing a mismatched guard.
Existing-file identity is now required for the idempotency shortcut. Local/remote
tests cover actual empty-file creation, repeated idempotent reuse and rejected
missing-path guards with no directory mutation. Original evidence remains at
`.artifacts/missing-empty-guard-probe`; this does not change the selected fixtures,
answers or live budgets. The earlier post-conflict full suite passed, but the final
post-empty-file suite at `.artifacts/transfer-final-regression` must also close before
the clean live freeze. Retain the first failed full regression separately at
`.artifacts/transfer-regression`. No provider work has started during these corrections.

The final pre-dispatch gate closed: 1137 full-suite passes/two skips, 35 isolated
Docker checks and 67 focused checks; lint/compile pass, typing has zero errors and
the existing export warning. Exact final artifacts are
`.artifacts/transfer-final-regression` and `.artifacts/transfer-final-docker-checks`.
Runtime revision `83a37af` is fixed; the live manifest records the following clean
documentation-inclusive revision. All prior failures remain retained. Dispatch only
the twelve trials already specified, with no prompt/source changes or selective retries.

#### Transfer panel closure: gate failed

All twelve trials at `85a36ab` are terminal; report and per-trial receipts are at
`.artifacts/transfer-v22-live-r2/analysis.md`. Ten independently verified, two reached
the call limit before verification despite correct source-supported files. All 36
answers have required completed source coverage and correct values. Findings and
control each verify 5/6 on different failed pairs: do not erase the paired regression.
Calls improve 125 to 115, but source reread lines worsen 526 to 550 and aggregate cost
rises $0.20981411 to $0.21717200. All 192 observed within-epoch wire transitions are
append-only, with no accounting gaps or unknown effects. Total cost $0.42698611.

The source-emission mapping is a lower bound with formatting-dependent coverage;
custom JSON and Python dict wrappers in these traces are not decoded. Its apparent
264-to-104 reduction does not establish the required duplication gain. Any expanded
mapping must be independently versioned and rescored for both arms without replacing
the frozen results. Cost, source rereads and paired completion already fail the gate.

Next shared harness work is grounded in actual submitted cells: review navigation
already contains learned SQL/routing findings and recovery handles, yet the model
starts source exploration again; generic result bindings are overwritten between
source and answer reads. Routing also attempts blocked exec/dunder operations and
imports workspace code into the computation worker, causing avoidable recovery work.
Use existing PTC help, retained source mappings and review control before adding a
new view. Keep targeted checks, source freshness, effect fencing and independent
verification intact. Any tuned reuse of this panel is diagnostic only. Remaining
natural-compaction, prior-run and real-coding gates are unchanged; no promotion.
