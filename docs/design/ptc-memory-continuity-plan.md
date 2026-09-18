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
