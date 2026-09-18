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
