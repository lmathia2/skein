# Context, versioned memory programs, and long sessions

> Status: deterministic programs implemented; active retrieval and long-context
> treatments remain opt-in with live quality gates pending
>
> Updated: 2026-09-12

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. A prompt is a bounded, deterministic projection of retained evidence.
2. Stable instructions and tool declarations precede volatile task context so the
   provider can reuse a byte-stable prefix.
3. Memory is a versioned computation over authorized ledger evidence, not prose
   inserted into the system prompt and not a second database of truth.
4. Exact history, counts, failures, tool usage, and semantic ranking share one
   request/result contract. Working notes and context handoffs are separate bounded
   control views over the same evidence.
5. Stored projections and caches are disposable, watermark-bound optimizations.
6. Long context is handled by bounded views, artifact indirection, explicit
   compaction epochs, and a recent exact tail—not by replaying everything forever.
7. Continuity must preserve useful findings, their supporting evidence, an authorized
   recovery path, and applicability—not merely proof that a tool was called. The
   [continuity plan](../design/ptc-memory-continuity-plan.md) records the accepted
   implementation direction and staged gates; it is not a claim of delivered behavior.
8. Evidence availability, source freshness, and confidence in a finding are separate
   axes. A live value can be stale; an artifact can outlive its binding; an agent's
   interpretation remains advisory even when it cites a valid receipt.
9. Evaluation oracle data must not be available as a memory or repository source.
   A readable hash of a low-entropy answer is not isolation. Controlled continuity
   evaluations use a host-owned checker behind the normal managed command boundary,
   with ordinary live shell commands isolated from host files. A contaminated trial
   cannot qualify memory, even if its final artifact is correct. Seeded evidence
   availability, model-written learning, and independently verified completion are
   distinct gates.

## How a prompt is constructed

`memory note schema` exposes the same typed update schema used by note writes,
plus the configured canonical-byte budget and runtime evidence/merge/idempotency
rules. It is read-only, on demand, and available only when working notes are active.
It is not a historical memory program or a new model-facing tool: the existing
reserved command broker records its result. Schema responses are deterministic and
hashed; if the whole contract cannot fit the result budget, it is unavailable rather
than silently partial. Static guidance names this discovery path without embedding
the full schema in every prompt. Live efficiency improvement remains an eval gate.

Note writes return a versioned compact commit receipt (`receipt_version: 1`), not
the full note. It carries the committed event ID, note `version`, canonical ledger
`payload_hash`, retained entry count and enabled recovery commands. `version` is the
committed version even when an identical retry occurs after later writes; it is not
a claim about the latest note. The hash identifies the stored payload, including
internal request identity, not source freshness or the truth of a finding.
`memory note read` still returns the full latest note; enabled `event.read` retrieves
the exact committed event, with normal byte paging for large records. No new tool or
memory program is introduced. Note-schema contract version 2 documents the output.

Canonical commit and full-note publication remain ordered before a successful
acknowledgement. Publication failure remains unknown and retries repair the same sink
event without appending another note. Legacy notes and content-sensitive idempotency,
CAS conflicts, merge/supersession, evidence authorization, redaction and budgets retain
their existing semantics. Internal note observers still receive the full public note.
PTC and four-tool bash share the compact return path; callers needing content must
explicitly retrieve it rather than depending on a write echo. This reduces bounded
output size, not the amount of source evidence required for completion.

The checked-in worker has one byte-stable provider prefix: model identity, stable
instruction, tool declarations, and tool configuration. Before every model call it
compares those canonical bytes with the first call and fails closed if they changed.
Task, session, progress, time, and steering remain in the dynamic request.

The workflow budgets its work packet in this fixed order:

```text
TASK
CONVERSATION
SELECTED SKILLS
REPOSITORY MANIFEST
COMPACTED HISTORY
RECENT EVENTS
USER STEERING
```

Skills and project instructions enter only after trust checks. Every section has a
configured token/byte limit. Oversized tool bodies stay in artifacts and context
contains a bounded excerpt/reference.

With context windows off, the direct-tool worker receives the current work packet and
PTC profiles retain normal ADK history. With trace-backed windows on,
`ContextWindowPlugin` captures the public request history into canonical evidence,
builds one bounded deterministic handoff, and asks the pure `select_context_cut`
function for a complete-interaction suffix boundary. Only after the compaction epoch is
durable does the plugin replace the provider contents with the handoff header, retained
exact tail, and transient steering. The removed P0-P3 prompt-program layer is not a
second live compiler.

The timing policy is independently selectable. `immediate` allows an epoch at the
soft work-packet limit. `phase_boundary` defers until a sufficiently large semantic
task-phase transition (or a pending checkpoint); provider-calibrated pressure forces
a safety cut independently. The pressure estimate uses the last provider-reported
input plus the estimated new-request delta, not the size of all captured history.
The safety threshold applies `compaction_threshold_ratio` to `max_context_tokens`
minus reserved output. A model accepting one large request establishes a lower bound
on its window, not its configured size. `max_task_input_tokens` remains a separate
cumulative spending ceiling checked before dispatch.

When working notes are required, soft transitions request a populated note and defer.
At hard pressure the plugin can use the previous note marked stale, or an evidence-only
handoff; missing optional prose does not itself throw away the run. Corrupt history,
identity mismatch, or required continuation metadata that cannot fit still fails
closed without silently replacing history. The initial hint and each epoch header remain frozen.
Committed workspace mutations advance the implementation phase, while read-only
batch exhaustion returns to planning rather than claiming a completed review.

### Example from trace to prompt

Assume the ledger contains this abbreviated sequence:

```text
41 task.created                 goal="fix flaky retry"
42 message.recorded             user request
43 tool.read                    completed, artifact=sha256:aa...
44 tool.bash                    failed, result_hash=bb...
45 memory.note                 "failure occurs after timeout"
46 tool.edit                    completed, path=harness/retry.py
47 verification.completed      passed=false
48 steering.received           "preserve backoff behavior"
```

The harness does not paste all eight raw records into a rewritten static instruction.
At a context transition it derives a bounded handoff such as:

```text
Required continuation metadata:
{"history_boundary":48,"unresolved_effects":{"count":0}}

Advisory memory:
{"note":{"version":1,"evidence_event_ids":[44]},
 "note_excerpt":"failure occurs after timeout",
 "retrieval":"memory history; memory query --program tools.usage"}

Recent exact ADK tail:
[complete tool-call/result interactions after the selected cut]
```

The compaction event records the history watermark, selected cut, input hash,
reconstruction strategy, token counts, frozen handoff, and note metadata. When active,
the model can request exact supporting evidence with `memory history`, `memory query`,
or `memory event`; it never receives private reasoning or unbounded raw traces.

## Memory-program contract

```text
request = program name/version
        + typed parameters
        + authorized task scope
        + watermark/time boundary
        + scan/time/output budgets

result  = bounded data
        + program/execution/result hashes
        + source manifest and evidence IDs
        + applied watermark
        + complete | partial | unavailable | denied | timeout
```

Three identities must remain separate:

- **Program identity:** exact reviewed code source, version, contracts, dependencies,
  and exposure-policy version.
- **Execution identity:** program hash, canonical parameters, authorized scope,
  evidence manifest/watermark, clocks, and budgets.
- **Result identity:** canonical returned bytes and provenance. Runtime duration is
  telemetry, not part of deterministic content identity.

### Configuration and registry

Trace-native memory programs are selected separately from the PTC implementation and
its native persistence pairing. Configuration uses one activation mode, shared budgets,
and an optional exact name-to-version allowlist:

```yaml
memory:
  enabled: true
  ledger: jsonl
  context_programs:
    mode: active
    programs:
      history.page: 1
      tools.usage: 1
    max_result_bytes: 16000
    max_scan_events: 10000
    timeout_seconds: 2
```

The loader resolves each exact `(name, version)` through a finite code-owned registry.
The current `MemoryProgramSpec` declares name, version, reuse eligibility, and
model-visible eligibility. All programs share the typed `ViewRequest`/`ViewResult`
contract; the executor owns the exposure allowlist, source authorization, temporal
filtering, resource bounds, evidence manifests, and computed implementation hash. YAML
cannot provide per-program parameters, an import path, callable, SQL body, or Python
source.

Assembly fails before model execution when a configured program/version is unavailable
or disallowed by its reuse/model-visible gate. Pydantic rejects invalid shared budgets,
and backend assembly rejects unavailable ledger or embedding capabilities. The mapping
shape makes duplicate program names impossible; configured insertion order is retained,
while each program owns deterministic result ordering. Query-specific parameters are
validated when `ViewRequest` is built.

At runtime, `shadow` executes the fixed `events.count@1` probe against the same frozen
evidence boundary and records a receipt but contributes no prompt bytes. `active`
enables the selected programs through the reserved `memory` command on Bash and
brokered PTC implementations. Program results are fetched on demand; merely activating
a program does not inject it into every prompt. Promotion changes configuration and
therefore the behavior hash; it never occurs implicitly because a shadow result looked
useful.

The existing `pi` option is a context-compaction strategy over ADK session history, not
a versioned program over canonical ledger evidence. It remains separately configurable
and cannot be selected in `memory.context_programs.programs`. Factory assembly installs
its summarizer only for `memory.enabled` with `implementation: pi`. The evaluated
`trace_native` profiles do not install it: their cuts come from `ContextWindowPlugin`,
not a second Pi summarizer running alongside or overwriting that plugin.

## Types of memory

| Need | Implemented representation |
| --- | --- |
| Exact episodic history | `history.page`, `event.read`, and artifact byte ranges |
| Addressed source snapshots | `reads.lookup@1` by path/version; `read.recover@1` by evidence event |
| Full-set facts | `events.count`; reviewed `failures.by_kind` |
| Query-relevant task memory | filtered lexical, semantic, or hybrid `history.page` |
| Tool accounting | top-level/nested `tools.usage` aggregate |
| Working intent | bounded, optimistic-concurrency `memory.note` event |
| Learned task records | typed advisory entries in versioned notes; `working_set@1` selection |
| Long-context handoff | deterministic control state + note metadata/excerpt + recent tail |
| Narrative compression | optional evidence-bound model summary, always advisory |

Semantic similarity ranks candidates; it does not establish truth. Lance rows retain
canonical event IDs, embedding version, and projection identity. Results are hydrated
from canonical evidence before exposure. No embedding model is selected or downloaded
implicitly, and task erasure invalidates the corresponding projection.

### Delivered read-evidence recovery seam

The Stage 1 implementation adds reviewed `reads.lookup@1` and `read.recover@1`
programs to the existing registry, not a new model-facing tool or historical store.
The public projection preserves `read_evidence` and `result_artifact_uri` from PTC
capability receipts. With canonical memory wired, successful direct reads also append
`read.observed` with normalized range metadata and a redacted result artifact. Existing
histories are not rewritten: lookup cannot infer metadata or content never captured.

When active and enabled by the profile's program allowlist, the reserved memory
command supports this sequence through the existing Bash/PTC broker:

```text
memory query --program reads.lookup --path src/example.py
memory query --program read.recover --event-id EVENT_ID --offset 1 --limit 20
```

Lookup can also filter `--source-sha256`; its bounded, paginated results identify
available receipts and ranges rather than synthesizing a whole file. Recovery selects
one authorized event and its linked artifact, checks the artifact's content hash and
read metadata, and returns source text marked `freshness: historical_snapshot`.
It does not consult the current workspace or assert that the snapshot remains fresh.
An already known event ID needs no lookup before recovery.

Recovery offsets are one-based **within the captured range**, not absolute file line
numbers. Returned `source_offset` identifies the corresponding original file line;
`next_offset` continues the captured range. `--byte-offset`/`--byte-limit` page the
selected text, and returned byte continuations preserve UTF-8 boundaries. Completion
of a selected page does not mean the complete original file was captured.

Input artifact reading and JSON decoding are capped at 16,000,000 source bytes;
selected output is additionally bounded by request/profile budgets and a maximum
16,000-byte text page, with scan/deadline limits. This bounds decoding but is not
streaming extraction from arbitrarily large JSON artifacts. Oversized, missing,
corrupt, or mismatched evidence is not silently served; results distinguish partial,
unavailable, denied, and timeout outcomes as applicable.

Prior-run recovery requires an authorized source task/ledger and explicitly supplied
prior artifact roots. Shared repository paths or a guessed content hash do not grant
access. Stage 2 also delivers bounded live-value descriptions and read associations
through PTC's state catalog; see the
[PTC working-value contract](trace-native-harness.md#working-values-and-durable-memory).
These seams do not themselves create semantic findings. The Stage 3 note contract
below persists explicit findings; the subsequent prompt/freshness integration is
described separately and has not cleared live promotion gates.

### Delivered finding lifecycle and working-set program

Stage 3 extends the existing `memory.note` writer with optional typed `--entries`
alongside free-text notes. It preserves optimistic note versions, content-sensitive
operation identities, redaction, and the canonical append-only history. Later text-only
updates retain existing entries. Each finding has a stable task-local ID, revision,
kind, public statement, evidence references, task/path links, and explicit status.
Kinds distinguish observations, hypotheses, decisions, rejected approaches, open
questions, and next actions; these are not private reasoning traces.

Observations require references to available public current-task events or their
addressed artifacts. Other kinds can remain unsupported hypotheses or proposals;
citations establish provenance, not correctness. The writer derives source-version
dependencies where a cited read receipt establishes them. It rejects private, forged,
or foreign-task references rather than importing authority from a path or an artifact
name. Prior-run findings are retrieved under separate source authorization, not silently
rewritten as current-task observations.

Explicit conflict links mark both entries disputed; explicit supersession hides old
entries from the current working set without deleting their historical note versions.
Subsequent checkpoints retire already-superseded entries from the bounded current set.
The harness does not infer or resolve semantic contradictions by text similarity.
Updates are bounded to 64 retained entries and the existing note payload budget;
oversized or invalid updates leave the last checkpoint intact. This is a bounded
checkpoint, not an unbounded knowledge graph or another historical database.

Reserved memory commands parse quoted multiline text as data; they never dispatch
to a shell. NUL, shell composition, extra commands, duplicate/unknown flags, and
missing required note fields remain rejected. Finding text is bounded to 1000
characters/2000 UTF-8 bytes. Note-budget failures expose required and allowed serialized
bytes, including derived source dependencies, and retain the previous checkpoint.
The model should keep the note heading short and cite receipts instead of repeating
their hashes/ranges inside every finding. These usability changes do not relax source
authorization, freshness, note identities, or completion verification.

`working_set@1` first selects the latest note per authorized source task at the requested
evidence boundary, then ranks non-superseded entries deterministically: current task,
explicit `--focus` task/path matches, disputes, open questions/actions, revision, and
stable identity. History filters that could revive an older note are rejected for this
program. It retains whole entries, reports omissions, and supplies note/evidence recovery
addresses within its byte and count budgets. Results distinguish unsupported, available,
and unavailable provenance, and retain source-task attribution and historical freshness.

The handoff service supplies this view when active and allowed, without duplicating
the full entries in note metadata. Findings survive service/worker loss through the
ledger, but no live binding, current workspace validity, or verification success is
implied by their recovery.

### Prompt and freshness integration boundary

The Stage 4–5 implementation connects the finding view to context construction and
adds observed-version invalidation. The unit/integration suite passed with one skip,
and follow-up focused continuity checks passed. These establish the tested code
contracts; live qualification and feature-promotion gates remain pending.

The factory supplies the current step, criterion IDs, and recorded modified paths as
bounded focus links. The renderer reserves required history/kernel/effect/retrieval
metadata and working-set identity before selecting complete advisory entries: findings,
note metadata/text, eligible live bindings, touched/modified paths, validation receipts,
and read recovery handles. Oversized entries are omitted whole, not head/tail-spliced
into invalid JSON; both rendering omissions and upstream selection omissions are counted.
The selected header remains frozen for its context epoch and complete call/result
interactions remain together in the exact tail.

`continuation@3` factors exact repeated finding-context and source-dependency objects
into handoff-local tables. References are explicitly local labels, not broker arguments;
the tables retain full recovery identities. Unique objects remain inline, and factoring
is used only when it saves serialized bytes including its explanatory legend. Selection
budgets the complete projected result, with no dangling references or partial findings.
Compact canonical JSON removes separator whitespace from advisory data. Required
kernel/effect metadata remains separate. Canonical notes, `working_set@1`, source
freshness semantics and memory tool results are unchanged; expanding the selected
prompt entries reconstructs their original objects exactly.

Recorded next-actions are explicitly historical advisory proposals, not pending user
requests or proof that a phase remains unfinished. Current task and latest steering
govern applicability. The renderer does not infer semantic completion, delete note
entries, acknowledge messages, or silently expire actions on phase changes. Historical
source versions and conflicts remain available. Deterministic checks establish exact
reconstruction, bounded output, scope/version preservation and replay stability; live
efficiency and reliable reuse still require the controlled evaluation gate.

`continuation@4` supersedes the lossless default projection for invalidated findings.
The live revised-config diagnostic showed that factored freshness labels did not stop
the model from using salient obsolete values. Freshness now remains inline. Findings
whose dependencies changed or need revalidation become explicit non-current entries;
their conclusion text and misleading active status are withheld from the default
handoff, while identity, dependencies, scope and historical recovery remain. Matching
newer recorded captures are attached only for current-task changed dependencies, never
inferred as current filesystem truth or mapped across prior-run scope. If invalidated
findings exist, unstructured note excerpts are also withheld to avoid repeating the
same obsolete conclusion without its dependency label. Full notes remain recoverable.
Canonical notes, historical views and verification authority are unchanged. Other
selected findings retain the exact factoring/reconstruction contract. The initial
offline regression uses the actual third-cut failure plus deterministic scope and
uncertainty cases; live qualification of this change is still required.

The subsequent six-trial canary passed first verification in both arms on all three
cases. The changed-source findings trial actually received two invalidated entries,
read the updated note, and submitted the correct revised answer without a source
reread. This qualifies one regression path only. It does not meet the overall
efficiency gate: that trial cost 48.3% more than control because setup and checkpoint
updating outweighed final-continuation savings. Hard note budgets and historical
provenance remain intact; defaults and broad benchmark expansion stay held.

The next efficiency change corrects note lifecycle guidance, not storage semantics.
Use the newest observed committed version from task metadata or a receipt, and read
only for needed content/version or a conflict. CAS remains mandatory; an idempotent
retry can identify an older commit and must not replace newer observed state. A new
task does not require an empty-note read or plan-only write. Checkpoint learned
evidence at useful boundaries, updating existing IDs for revised findings rather
than appending parallel `_current` entries. The model still decides semantic identity;
the host never merges claims by text similarity. Supersession/history, source evidence,
unknown-effect handling and the 8,000-byte canonical bound are unchanged.

This is note-schema guidance version 3 and `continuation@5` initial guidance; the v4
invalidation representation is retained. The PTC static instruction also says to
render selected data or model_text, not both copies. Replaying the actual rejected
update with only its two revised IDs reused reduced 10,088 bytes to 7,649 bytes,
retaining all evidence references. That demonstrates an available lower-cost path,
not that a live model will use it reliably. Live cost qualification remains required.

The installed context plugin owns handoff delivery. The workflow omits its duplicate
persisted summary from the model packet in that configuration, while retaining a
conservative pre-dispatch token reservation. Without that plugin (including shadow),
the workflow still supplies its persisted summary. Ownership derives from actual
factory wiring, not another user option. Metrics continue to run after reconstruction,
and current kernel/effect state remains in the plugin's handoff. A real-workflow test
guards against injecting the same handoff both beside and inside the initial packet.

`continuation@6` supplies unresolved capability, cell, validation and tool-receipt
records from the shared execution-admission projection, not only started SQLite
receipts. A completed Python cell may contain an unknown nested effect. The count
is unresolved records (a cell and its capability can describe the same uncertainty),
with at most 16 addressed records inline; it is not a count of distinct side effects.
This metadata cannot reconcile operations or authorize completion by itself.
When an unconsumed result exceeds the soft packet target but fits the hard window,
the plugin retains that published epoch and interaction. It cannot publish revised
content under the same cut identity. Compaction resumes once a complete boundary can
advance; no unseen tool result is discarded to meet a soft target.

`context.continuity_representation` separates metadata, described-value, and findings
representations for controlled ablation. Metadata retains the compact eight-read view
without described bindings/findings; the richer representations admit up to 32 read
entries with recovery URIs and prioritize relevant paths before recency. These are
representation choices, not different evidence or execution authorities. No mode makes
an omitted read unavailable in the canonical evidence store.

PTC can append bounded changed descriptions/read associations and explicit invalidation
notices through `notebook_ptc.emit_state_updates`; its result separately reports observed
kernel liveness and epoch. It does not rewrite prior messages or the static prefix.
The stable instruction explains selective reuse, advisory annotation,
typed finding checkpoints, recovery, and conditions requiring a fresh read. Four-tool
mode shares durable notes/findings without claiming a live Python binding.

PTC terminal receipts and direct-tool `workspace.effect_observed` events retain touched
paths, returned content hashes, and conservative workspace-change uncertainty. The
finding view compares those observations with read dependencies and marks them
`historical_snapshot`, `changed_since_capture`, or `revalidation_required`. A new read
can establish another observed version; it never upgrades historical evidence to an
unqualified claim of current filesystem freshness. Unobserved external changes remain
outside this observation log and require a new workspace check. Prior-run findings keep
their source scope and must not authorize a current-task mutation without fresh evidence.

### Successful validation and advisory source freshness

A completed PTC shell call provisionally marks workspace-dependent findings for
revalidation. When the same operation subsequently has a host-observed successful
validation with an explicit zero exit and equal nonempty before/after workspace
fingerprints, the source-observation reducer retracts only that call's provisional
invalidation. It preserves any earlier or intervening unknown observation. Missing,
failed, malformed, truncated, differently owned or mismatched-operation observations
do not clear it. This prevents unchanged successful checks from needlessly making
all retained findings stale. Program source hashes include the changed reducer.

This is an advisory source-freshness correction, not execution reconciliation.
Unknown capability/cell effects, pending operations and the completion/recovery fence
are untouched. Equal workspace fingerprints cannot reconcile an earlier unknown
effect. Actual worker-loss tests and fault-boundary checks cover the distinction;
live reread/cost improvement remains to be measured.

## Repeated-use evaluation

Repeated-use evaluation must verify each requested answer artifact independently,
not just the last answer. Host-only contracts bind expected values, decisive source
versions/ranges and an assigned checkpoint window. Completed source evidence must
precede that artifact's managed write request; later reads cannot justify it retroactively.
The coordinator checks intermediate artifacts before and after the acknowledgement
cell, and final verification checks all artifact values and current-byte/receipt hashes.
Corrections require a fresh write in the still-open window; earlier unsupported
submissions remain visible in qualification metrics. This is an evaluation policy,
not a claim that production memory proves arbitrary semantic derivations. Memory
quality/default promotion still requires fresh live paired results with all overhead
charged; deterministic gate tests alone do not satisfy it.

In controlled repeated-use fixtures, an unchanged-source question may close its
answer window with a completed marker while retaining the existing historical note
or artifact. The evaluator records that note identity but does not manufacture a new
note version or freshness claim. Source-revision stages cannot use this shortcut:
they still require completed new-version capture and an updated checkpoint. The
production compactor's stale-note and bounded hard-pressure fallback semantics are
unchanged. One-use controls remain in the comparison so note preparation overhead
cannot be hidden by selecting only repeated-use workloads.

## Python, SQL, and caching

[`PROGRAM_REGISTRY`](../../harness/evidence/memory/programs.py) is the only code-owned
`(name, version)` catalog used by configuration, execution, and model exposure.
Reusable logic must be reviewed, typed, tested, and version-pinned before entering
that finite library. Notebook code, YAML callables, and arbitrary SQL are never
auto-promoted or executed as memory programs.

The registry is deliberately small: it identifies reviewed programs, while the common
executor supplies their request validation, bounds, provenance, and hashes. Move
program-specific parameter schemas or source policies into the registry only when two
programs actually require different configuration-time contracts.

Program storage is distinct from result caching:

- ordinary deterministic views currently recompute;
- evidence-bound model summaries have an explicit optional cache keyed by source
  view, prompt, model, and settings;
- any future deterministic result cache must key on full program and execution
  identity and revalidate source availability before reuse.

## Incremental projections

DuckDB currently maintains the measured hot aggregate in the event append
transaction:

```text
BEGIN
  insert canonical event
  upsert (task, source, kind, status) count
  advance task watermark and stream hash
COMMIT
```

Current unfiltered `events.count@1` and `failures.by_kind@1` requests use this
projection without loading event payloads into Python. Temporal, keyword, filtered,
cross-ledger, and historical-watermark requests retain the exact bounded evidence
path. Startup deterministically rebuilds mismatched projections from `ledger_events`.
No generic materialization framework is justified yet.

## Long-context lifecycle

```text
append exact turns and effects
          |
          v
request approaches context budget
          |
          v
persist compaction epoch + structured handoff
          |
          +--> retain required task/effect/verification state
          +--> retain bounded working note and recent exact tail
          `--> leave full evidence in ledger/artifacts
          |
          v
continue with a smaller dynamic suffix
```

Window management, model-visible retrieval, working notes, fresh reconstruction,
prior-run recall, and notebook continuity are independent opt-in controls. Fresh
context does not create a new task or permission scope. Prior-run access requires an
owned or explicitly authorized source manifest; sharing a repository path is not
authorization. Factory assembly additionally gates prior task IDs, source ledgers,
and artifact roots on `memory.prior_runs`: supplying prior-run bindings alone does
not enable recall when that control is off. Notebook continuity does not bypass this
independent memory-access gate.

### Continuity boundary and accepted next direction

Read recovery pagination and source coverage are distinct. Recovery `complete`
describes the selected captured page (`complete_scope=selected_capture_page`), not
the whole source file. `source_coverage` separately carries the historical file line
count, whole-file flag, and next unread source offset, derived from the captured read.
PTC read references, descriptors, direct-read receipts, and handoff indexes retain
that distinction. Older artifacts without a file line count report unknown whole-file
coverage; contradictory counts fail closed. Recovery never fetches uncovered lines.

A nonempty note is not automatically a checkpoint for every later context cut. With
working notes enabled, a new soft cut that would advance the history boundary offers
one note-refresh opportunity if its version has not advanced since the previous cut.
The old exact suffix remains visible for that opportunity. The request asks for
newly learned findings, completed changes, actual verification outcomes, and remaining
unknowns; notes remain advisory. Initial note prompting remains shared with the
non-compacting control. An unconsumed result that prevents advancing the cut does
not trigger an unnecessary refresh. At the hard threshold, or after an ignored
opportunity, compaction can use the available checkpoint with explicit `note_stale`
metadata rather than looping on a missing note. This means not refreshed for this
cut, not that every cited source changed. Published headers remain frozen per epoch.
The live quality/cost benefit of this refresh policy still requires measurement.

PTC help and instructions distinguish native managed-command results from process
results. For `memory query`, the capability's `data` contains the view envelope and
its nested `data` contains the program body. Missing process stdout is not evidence
that a memory view is empty. `state.describe` returns a descriptor directly, not a
status/data envelope, and an unavailable binding raises `KeyError`. These document
existing result shapes, rather than introducing another tool or envelope migration.

The current handoff includes note metadata/excerpt, required kernel/effect state, and
a bounded recent-read/validation manifest. A read entry identifies path, full-file
hash, offset, and returned lines; it is not a finding, a whole-file snapshot, or proof
that the model saw those bytes. The PTC catalog now supports advisory descriptions,
opt-in previews, and bounded broker-established read associations. These describe live
values, not durable learned source structure. In-loop `touched_paths` now reflects
receipt-confirmed paths independently of `modified_paths`, which still derives from
task-ledger state refreshed later by workspace observation. Touched paths are not a
verified current diff or proof that a later shell command left those files unchanged.

The accepted continuity design assigns responsibilities rather than adding a second
memory store:

| Component | Continuity responsibility |
| --- | --- |
| Execution/evidence | Address receipts and immutable result bytes; observe source versions |
| PTC | Describe selected live values and validate their binding/epoch availability |
| Memory | Retain evidence-linked findings, corrections, and direct recovery handles |
| Context/messages | Select a bounded task-relevant working set and freeze it per epoch |
| Recovery | Reconcile effects and distinguish historical evidence from current workspace state |

The delivered finding view uses explicit focus links and stable priorities rather than
only the newest reads. Whole entries retain evidence and retrieval handles when the
budget excludes full content. Agent-authored findings are concise public task records,
not private reasoning. Structural provenance is derived only where supported; arbitrary
Python transformations do not acquire lineage by inference. Delivered runtime
descriptions are scoped to the observed worker epoch and validated supported values;
annotations are not automatically promoted to findings. Integrated focus selection,
observed-version annotations, and bounded state messages do not by themselves prove
reduced rediscovery or improved quality; all features remain subject to the plan's
live promotion gates and declared lifecycle coverage limits.

PTC fetches, selected emitted content, and provider-request exposure must be measured
separately. Same-version interval overlap identifies reuse candidates, not automatically
wasted context. Evaluation includes filesystem, artifact, and shell retrieval routes,
and counts checkpoint/retrieval overhead. A reduction in filesystem calls alone cannot
justify promotion. Compare paired quality, calls, tokens, cost, and terminal reasons;
budget exits, harness failures, provider failures, and verifier outcomes are not
interchangeable. Compaction, notes, prior-run recall, and PTC earn activation separately.

Known single-file no-ops retain their narrow scope: a successful observed managed
write/edit with no changed paths and one valid content hash must not invalidate
unrelated findings. An empty changed-path list alone does not establish this; failed
or unknown effects and missing identity remain conservative. This is an advisory
source-observation rule, not reconciliation of an earlier unknown operation.

## Rejected alternatives

- Mutable “memory” inside `static_instruction`: destroys prefix stability and hides
  evidence boundaries.
- Automatic RAG on every turn: changes context unpredictably and spends retrieval
  cost without demonstrated need.
- Unbounded Python/SQL supplied by the model: violates authorization and resource
  contracts.
- Treating summaries or vector results as facts: both are derived and fallible.
- Precomputing every aggregate: write amplification and invalidation cost must be
  earned by a measured repeated query.
