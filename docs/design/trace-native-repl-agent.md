# Trace-Native Notebook PTC Agent

Status: target architecture with an experimental opt-in local implementation

Audience: harness, runtime, storage, safety, and evaluation implementers

Current implementation: the supported default remains the four-tool architecture
described in `docs/architecture.md`. A disabled-by-default local-only Phase 0 now
provides the persistent CPython worker, one-tool broker path, append-only lifecycle
events, deterministic notebook materialization, rich-output artifacts, transactional
failed-cell rollback, conservative self-contained-cell restoration, and metadata-only
live-state inspection. The implementation also contains a DuckDB canonical-ledger shadow writer
for task events, receipts, checkpoints, approvals, steering, metrics, public/run
events, redacted ADK session lifecycle, and traces; deterministic seeded views and
receipt-bearing prompt manifests; a gated relational-program lifecycle; and registered
MCP broker routing. Canonical memory can use dependency-free JSONL or optional DuckDB,
is disabled by default for main compatibility, and is shared across server runs when
enabled. An optional immutable LanceDB projection supplies combined
vector and keyword retrieval while preserving canonical DuckDB event provenance.
These are implemented foundations, not a completed cutover. The
remaining operational stores, DuckLake tier, live prompt-reader cutover, and model
ablation remain gated later work. The supported execution boundary is a trusted local
workspace. Production or adversarial isolation is an optional deployment profile, not
a notebook-PTC activation gate.

Historical backfill and atomic Parquet sealing are now implemented. Backfill reproduces
stable task/run/session hashes and audits recognized source counts. Parquet tests prove
hot/sealed equality at an explicit watermark. These do not satisfy semantic reader
cutover for non-task operational projections or the scale gate for adding DuckLake.
The live task-event reader does reconstruct byte-equal harness events from the ledger,
with idempotent JSONL read repair, so task replay, recent context, and compaction share
that canonical source. A separate explicit erasure path now
physically removes exact task rows and recognized operational records, JSONL, notebooks,
unshared artifacts, and manifested segments; automated retention policy remains an
operator decision rather than an implicit runtime behavior.

Standalone executable examples under `examples/notebooks/` demonstrate the
three-authority PTC contract, cache-aware compaction, and versioned database memory
programs with mocked events and standard-library Python.

## 1. Executive decision

The target agent has three primary authorities with deliberately different jobs:

1. **The notebook is the primary durable programmatic-tool-calling transcript and
   session workbench.** It contains the Python programs the model submitted,
   selected results, Markdown context, visual outputs, checkpoint references, and
   recovery notes. It is the normal artifact an agent resumes; human inspection and
   Jupyter interoperability are useful secondary properties.
2. **The append-only ledger is the primary historical database.** It is the authority
   for what happened, in what order, with which status and effect: notebook edits,
   model interactions, cells, nested capability calls, failures, timeouts, retries,
   verification, view computation, and prompt construction.
3. **The persistent CPython worker is the primary live execution environment and the
   only model-visible tool.** The model writes verifiable Python that can call a
   policy-enforcing capability broker, compose CLI or MCP operations, filter large
   results, and retain warm state across turns.

These authorities are complementary rather than interchangeable:

```text
notebook = durable executable narrative and current workbench
ledger   = immutable historical truth and recovery evidence
CPython  = disposable live heap and execution cache
```

Memory remains a catalog of versioned computations over ledger evidence and
artifacts. Those programs construct task state, prompt packets, compaction,
relevant memory, learning episodes, and notebook projections. The current notebook
is durable because it is written atomically, checkpointed by content hash, and
rebuildable from ledger events and artifacts. It does not become a second history.

The notebook does not serialize the complete Python process. Cross-process and
cross-session continuity comes from its replay-safe code, explicitly persisted
named values, the ledger, content-addressed artifacts, workspace checkpoints, and
views at an explicit trace watermark. Arbitrary heap pickling is never required for
correctness.

This architecture is an ablation-gated successor to the current harness. It is not
acceptable to replace the current four tools merely because one tool has fewer
schemas. The new design must improve the quality-latency-cost frontier while
preserving or improving deterministic correctness, safety, replay, and recovery.

## 2. Problem statement

The current harness already has useful components: append-only task events, a
structured Task Ledger, coding-aware compaction, tool receipts, checkpoints,
redacted execution traces, ADK sessions, public transcript events, steering,
approvals, and metrics. They are physically and logically split across JSONL,
multiple SQLite stores, ADK session state, and artifact files.

That split creates four problems:

- The implementation must reconcile several histories before it can answer what
  happened or what remains unfinished.
- A failure, timeout, retry, or interrupted operation can exist in one store while a
  task projection in another store lacks the same limitation.
- Prompt construction and memory generation are special-purpose pipelines rather
  than ordinary, versioned computations over one evidence substrate.
- Large tool workflows cross the model boundary repeatedly even when a short Python
  program could perform the mechanical work, retain only relevant intermediate
  state, and return a compact result.

The target design makes the trace the stable evidence substrate, the notebook the
stable working artifact, and memory an explicit set of inspectable, replayable
programs over both.

## 3. Terminology

| Term | Definition |
|---|---|
| Ledger | The canonical, append-only, totally ordered collection of recorded events. |
| Session notebook | The durable `.ipynb` workbench containing PTC cells, Markdown, selected rich outputs, and provenance links into the ledger. |
| Notebook projection | The canonical notebook state materialized from ledger events and artifacts at a watermark. |
| Notebook snapshot | A content-addressed notebook checkpoint used to accelerate restore or exchange the workbench. |
| PTC cell | One model-authored Python program submitted through the single tool and represented as a notebook code cell. |
| Kernel epoch | One lifetime of the live CPython worker; a new epoch begins after restart or replacement. |
| Stream | A logical subset of the ledger, such as a session, task, run, or branch lane. |
| Event | One immutable observation, intent, state transition, outcome, correction, or derived-result record. |
| Artifact | Large or binary content stored outside prompt context and addressed by digest. |
| Memory program | A versioned logical computation over ledger events and artifacts. |
| View | The result of a memory program at an explicit trace watermark and parameter set. |
| Seed view | A built-in memory program supplied by the harness. |
| Agent-authored view | A proposed memory program synthesized by the model and admitted through validation. |
| Materialization | A cached physical result that is rebuildable from the ledger and program definition. |
| Retrieval receipt | Evidence of which view, source events, plan, and bytes were exposed for a model request or action. |
| Context epoch | An interval during which the stable and epoch-stable prompt regions remain byte-identical. |
| Capability broker | Trusted host API used by REPL code for filesystem, shell, MCP, artifacts, views, and other effects. |
| Effect uncertainty | A state in which an operation did not return a definitive outcome and may have partially changed the environment. |

## 4. Goals and non-goals

### 4.1 Goals

- Support long-running tasks across model calls, compactions, process restarts,
  machine restarts, model changes, user steering, and session branches.
- Make every important success and limitation visible to future agent invocations.
- Let the model express high-fanout mechanical work as inspectable Python rather
  than repeated model-mediated tool calls.
- Make the notebook the default durable surface for that Python, its selected
  results, visual context, explanations, and handoff state.
- Preserve a minimal, cache-stable model interface.
- Compute task state, prompt context, compaction, and memory from one evidence log.
- Support exact `as_of` reconstruction and correction without rewriting history.
- Separate semantic planning from physical query optimization.
- Promote frequently reused computations from ad hoc execution to cached or
  incrementally maintained views based on measured workload.
- Retain deterministic completion verification and policy enforcement outside the
  model and outside arbitrary REPL code.
- Make quality, durability, cache efficiency, latency, and cost measurable.

### 4.2 Non-goals

- Retaining hidden chain-of-thought or provider-private reasoning.
- Treating the in-memory Python heap as cross-session durable state.
- Treating notebook document order, output presence, or execution count as proof of
  historical order, success, or side effects.
- Replaying an entire notebook automatically after restart.
- Requiring IPython or Jupyter Server for production correctness; `.ipynb` is the
  workbench format, while the execution environment is CPython.
- Giving arbitrary Python direct access to host credentials, unrestricted network,
  the entire filesystem, or the ledger's physical database connection.
- Making every view agent-authored. Core safety and control views are harness-owned.
- Materializing every query or embedding every event.
- Using DuckLake as the event-by-event transaction log in the local MVP.
- Supporting multiple independent writer processes in the first implementation.
- Allowing model-generated memory to declare verification success, permissions, or
  completion.
- Deleting the existing implementation before replay and ablation gates pass.

## 5. Falsifiable assumptions

Each assumption must be tested. Failure should change the architecture rather than
be explained away after implementation.

| ID | Assumption | Measurement | Falsification consequence |
|---|---|---|---|
| A1 | Strong coding models can reliably express multi-step capability use as Python. | Cell success, repair count, task pass rate, first-correct-cell latency. | Retain multiple direct tools or provide more typed broker helpers. |
| A2 | One REPL tool reduces model-facing schema and orchestration tokens enough to offset generated code tokens. | Uncached input, output tokens, cache-read ratio, cost per passed task. | Do not replace the four-tool surface. |
| A3 | Filtering intermediate results inside Python improves high-fanout workloads. | Raw bytes processed, bytes exposed to model, latency, answer correctness. | Route only selected workload classes through programmatic calls. |
| A4 | A warm CPython process materially helps multi-turn work. | Warm/cold latency, repeated import and parse cost, task wall time. | Use stateless execution plus explicit artifacts. |
| A5 | Observable events, artifacts, corrections, and outcomes are sufficient durable evidence without hidden reasoning. | Resume success, unsupported claims, missing-evidence failures. | Add new observable event types, not hidden reasoning capture. |
| A6 | Task, context, and memory views can be reconstructed from one ledger without divergent shadow state. | Full replay equality and projection hash equality. | Keep a typed state store as an explicit authority until equivalence is proven. |
| A7 | Semantic memory programs can be validated well enough to improve shifted workloads. | Program validity, oracle regret, repair calls, unsupported-claim rate. | Keep fixed seeded views and disable agent-authored activation. |
| A8 | Embedded DuckDB is adequate for the local single-writer workload. | Append p99, recovery time, database growth, view latency, checkpoint stalls. | Use SQLite/PostgreSQL for the hot ledger and keep DuckDB as the view engine. |
| A9 | Cache epochs improve provider cache reuse without starving the model of current state. | Cache-read ratio, stale-context errors, pass rate, compactions per task. | Shorten epochs or append more deterministic state deltas. |
| A10 | Cross-session memory is more accurate when computed from evidence than when maintained as independent fact records. | Temporal accuracy, correction handling, stale-memory error, evidence recall. | Retain selected typed memory records as authorities with explicit synchronization. |
| A11 | A notebook is a better durable PTC workbench than a tool-result-only transcript. | Resume success, program reuse, context reconstruction cost, visual-task pass rate, notebook repair count. | Keep notebooks as export views rather than the primary workbench. |
| A12 | Canonical notebook projections can remain byte-stable and compact enough for prompt caching. | Projection hash equality, prompt bytes, cache-read ratio, image/output expansion. | Use ledger-native prompt rendering and keep notebooks outside model context. |

## 6. Normative tenets

The words MUST, MUST NOT, SHOULD, and MAY are normative.

### T1. The ledger is the sole historical authority

All session, execution, control, memory-program, and retrieval facts MUST be
representable as ledger events. A materialized view, in-memory reducer, ADK state
object, or REPL variable MUST be rebuildable or declared disposable.

### T2. Views are computations over evidence

Episodic, semantic, procedural, preference, graph, task, prompt, and compaction
memory MUST retain source event addresses and a program version. A view result MUST
NOT silently become a second history.

### T3. One model-visible tool does not mean one unguarded capability

The model sees only `python`, but Python calls filesystem, shell, MCP, network, and
artifact operations through the trusted capability broker. The broker MUST enforce
the same confinement, approvals, redaction, idempotency, and verification boundaries
as direct tools.

### T4. Durable state is explicit

The REPL heap MAY remain warm during a session. State needed after process loss MUST
be written as a ledger event, artifact, workspace mutation, supported named value,
or checkpoint. Arbitrary heap pickling MUST NOT be a correctness dependency.

### T5. Every attempt is evidence

Started, completed, failed, blocked, cancelled, timed-out, retried, abandoned, and
effect-unknown work MUST be recorded. Absence of a completion event MUST remain
distinguishable from success and from proven no-effect failure.

### T6. Time is first-class

Events MUST carry UTC observed and recorded timestamps. Operations SHOULD carry a
monotonic duration. Time-dependent views MUST be evaluated from an explicit
`clock.observed` event so replay can reproduce their inputs.

### T7. Deterministic facts control execution

Reducers, authorization, prompt ordering, token budgets, operation recovery,
workspace reconciliation, verification, and completion MUST be deterministic.
Semantic views MAY advise them but MUST NOT override them.

### T8. Completion is independently verified

A model claim is diagnostic evidence only. The harness MUST map each acceptance
criterion to environmental evidence and MUST reject completion when required
verification, scope, workspace, or unresolved-effect checks fail.

### T9. Prompt caching is a design constraint

Stable prefix bytes MUST be canonical and versioned. Volatile state MUST NOT be
inserted ahead of an otherwise reusable transcript. Compaction MUST be an explicit
cache-epoch transition rather than continuous prompt rewriting.

### T10. Exposure is narrower than retention

The ledger may retain redacted operational evidence that is not exposed to the
model. Each prompt or action MUST have a receipt describing exactly which view
versions and source evidence were exposed.

### T11. Physical optimization follows measured reuse

Programs begin on demand. The runtime MAY cache, materialize, or incrementally
maintain them only after observing query frequency, update rate, latency, freshness,
and utility. Physical state MUST remain disposable.

### T12. Agent-authored programs earn activation

Generated programs MUST pass type, cost, temporal, policy, and historical replay
validation, then shadow execution, before becoming active. They MUST remain
versioned and retireable.

### T13. Append-only does not mean undeletable

Sensitive bodies SHOULD be stored in access-controlled artifacts rather than event
payloads. Retention, tombstoning, encryption-key destruction, and physical erasure
MUST be supported where policy requires deletion.

### T14. No invisible side effects

Every broker call capable of changing state MUST have an operation identity,
authorization result, idempotency or replay classification, and terminal or
effect-unknown event.

### T15. The notebook is the durable PTC workbench, not the historical authority

Every model-submitted Python program MUST have a stable notebook cell identity and
ledger provenance. Notebook mutation MUST be reflected as append-only events. A
notebook may be rebuilt, compacted for model exposure, or replaced by an equivalent
snapshot without changing historical truth.

### T16. Notebook persistence is not heap persistence

The notebook MUST distinguish replay-safe code from reads, writes, and unknown
effects. Restore MAY replay validated pure definitions and immutable computations;
it MUST NOT blindly execute effectful cells. State required after worker loss MUST
be explicit and portable.

### T17. Rich context is bounded and artifact-backed

Markdown and MIME outputs MAY be present in the notebook, including images. Large
or binary bodies MUST be content-addressed artifacts. A prompt view selects whether
to expose no body, a caption, a thumbnail, or the full artifact and records that
choice in its retrieval receipt.

## 7. Reference architecture

```text
User / queue / scheduler
        |
        v
ADK App and deterministic workflow
        |
        +---------------------------+--------------------------+
        |                           |                          |
        v                           v                          v
Prompt/view runtime       Notebook workbench            Verification/policy
        |                 `.ipynb` projection                   |
        v                           ^                          |
One coding model                    |                          |
        |                           |                          |
        v                           |                          |
python(code)  <-- only model tool   |                          |
        |                           |                          |
        v                           |                          |
Persistent trusted-local CPython worker+----------------------+
        |
        v
Typed capability broker
  files | shell | MCP | views | artifacts | state | clock
        |
        v
Workspace / external systems
        |
        v
Canonical append-only ledger
        |
        +--> canonical notebook materializer and snapshots
        +--> deterministic reducers and hot views
        +--> semantic/ad hoc memory programs
        +--> prompt packets, compactions, resume, learning
```

### 7.1 Authority boundaries

| Concern | Authority |
|---|---|
| Historical occurrence | Ledger event sequence and payload hash |
| Current durable PTC workbench | Canonical session notebook projection at a ledger watermark |
| Notebook history and authorship | Ledger notebook/cell events, never mutable `.ipynb` contents alone |
| Live Python values and imports | CPython worker within one kernel epoch; disposable |
| Large content | Content-addressed artifact plus ledger manifest event |
| Current task state | Deterministic reducer over ledger |
| Current workspace state | Observed workspace fingerprint event plus filesystem/Git |
| Model-visible history | `history.model` view |
| Prompt bytes | Versioned prompt compiler and prompt manifest |
| Capability permission | Host policy and approval records |
| Side-effect replay | Broker receipt and reconciliation logic |
| Completion | Deterministic verifier |
| Memory relevance | Versioned memory program; advisory unless explicitly designated otherwise |
| Cross-session Python state | Explicit named artifact plus restore receipt |

## 8. Canonical ledger

### 8.1 Event envelope

The physical schema may use columns for indexing, but the logical event contract is:

```python
class LedgerEvent(BaseModel):
    event_id: UUID                 # UUIDv7
    seq: int                       # global storage-assigned order
    stream_id: str                 # session/task/control stream
    stream_seq: int                # gap-free order within stream
    lane: str = "main"             # branch lane

    kind: str                      # namespaced discriminant
    schema_version: int
    source: str                    # user, model, repl, broker, host, verifier
    visibility: set[Literal["model", "public", "host"]]

    session_id: str | None
    task_id: str | None
    run_id: str | None
    operation_id: str | None
    parent_event_id: UUID | None
    causation_id: UUID | None
    correlation_id: str | None

    observed_at: datetime          # UTC occurrence time
    recorded_at: datetime          # UTC commit time
    duration_ns: int | None        # monotonic elapsed duration

    status: str | None
    effect: Literal["none", "observed", "changed", "unknown"] | None
    payload: dict[str, JsonValue]
    artifact_refs: list[str]

    content_hash: str
    idempotency_key: str | None
    supersedes_event_id: UUID | None
    retracts_event_id: UUID | None
```

`seq` orders the complete database. `stream_seq` gives each session or task a
gap-free replay order. Branch ancestry is represented by lane movement and parent
events rather than by copying or mutating old events.

### 8.2 Time semantics

- `observed_at` means when the underlying occurrence happened.
- `recorded_at` means when the ledger durably accepted it.
- Both are timezone-aware UTC timestamps.
- Durations use a monotonic clock and are not reconstructed by subtracting wall
  clocks.
- Late observations retain their original `observed_at` and later `recorded_at`.
- Time-sensitive view results record both the trace watermark and the
  `clock.observed` event used as now.

### 8.3 Operation status and effect

Status and effect are separate dimensions:

```text
status = started | completed | failed | blocked | cancelled |
         timeout | retrying | abandoned | unknown

effect = none | observed | changed | unknown
```

Examples:

- A rejected command is `blocked/effect=none`.
- A read failure is `failed/effect=none`.
- A local atomic edit is `completed/effect=changed`.
- A network timeout after request transmission is `timeout/effect=unknown`.
- A process killed during a shell script is `abandoned/effect=unknown` until
  reconciliation proves otherwise.

An open-operation view derives started operations without a matching terminal event.
Recovery never converts timeout into no effect by assumption.

### 8.4 Event namespaces

Initial namespaces include:

```text
session.created              session.resumed
session.branched             session.closed
notebook.created             notebook.opened
notebook.cell_added          notebook.cell_edited
notebook.cell_deleted        notebook.cell_reordered
notebook.materialized        notebook.snapshotted
notebook.external_edit_detected
user.message                 user.steering
model.requested              model.response
model.error                  model.usage
repl.started                 repl.ready
repl.cell_submitted          repl.cell_completed
repl.cell_failed             repl.cell_timeout
capability.requested         capability.authorized
capability.blocked           capability.completed
capability.failed            capability.timeout
capability.reconciled
bash.started                 bash.completed
mcp.started                  mcp.completed
workspace.observed           workspace.changed
artifact.recorded            artifact.redacted
verification.started         verification.completed
verification.failed
task.created                 task.updated
task.blocked                 task.completed
checkpoint.created           checkpoint.restored
compaction.requested         compaction.created
compaction.failed
program.proposed             program.validated
program.shadowed             program.activated
program.retired              program.rejected
view.requested               view.computed
view.materialized            view.invalidated
retrieval.served             prompt.compiled
clock.observed               budget.observed
data.retracted               data.erased
```

Notebook events describe document intent and materialization, not execution by
implication. `notebook.cell_added` does not mean a cell ran;
`repl.cell_completed` does not mean every nested operation changed nothing; and
`notebook.materialized` only proves that a projection with a particular content
hash was durably written. Views join these events using stable cell and attempt IDs.

### 8.5 Corrections and retractions

The ledger does not update an old fact in place. A correction appends an event with
`supersedes_event_id`; a retraction appends `retracts_event_id`. Views declare their
authority and temporal policy, for example latest-recorded, latest-observed,
highest-authority, or as-of-known-at-time.

### 8.6 Artifacts

Event payloads contain bounded, redacted, queryable metadata. Large outputs, source
snapshots, diffs, result sets, generated programs, and binary data are stored as
content-addressed artifacts:

```text
artifact://sha256/<digest>
```

The artifact manifest records digest, media type, byte size, redaction class,
encryption key reference, task/session ownership, creation event, retention policy,
and optional structural summary. Artifact bytes are not part of the prompt unless a
view deliberately selects and bounds them.

Notebook files are named workbench projections, not artifact bodies. Clean worker
shutdown stores the canonical notebook bytes as one immutable artifact and appends an
idempotent snapshot event; checkpoint, handoff, branch, or explicit export MAY create
additional snapshots. The corresponding event records its digest, source watermark,
notebook ID, and the last cell kernel epoch when available. Richer deployments may
also record renderer version, lane, and workspace fingerprint.

## 9. Sessions, tasks, lanes, and checkpoints

### 9.1 Session identity

A session is a durable conversational and execution stream. A task may span several
sessions, and a session may contain conversational turns before a coding task is
created. Cross-session task continuity is represented by stable `task_id` and
workspace identity, not by assuming one provider conversation survives forever.

### 9.2 Branch lanes

Each session begins with lane `main`. A branch event creates a new lane pointer at an
existing event. Appending on a lane advances only that lane. Views accept `lane` and
`as_of_seq`, making historical and counterfactual reconstruction explicit.

### 9.3 Checkpoint

A checkpoint binds:

```text
ledger watermark
session, task, run, and lane
notebook ID, projection hash, and materialized watermark
task-state view hash
workspace ID, base revision, and fingerprint
CPython environment manifest and kernel epoch
supported durable named values
replay-safe cell/helper set and restore policy version
active compaction and retained-tail cursor
open-operation reconciliation state
model and prompt-prefix versions
```

A checkpoint is a ledger event and a materialized recovery accelerator. It is not a
replacement for the events it summarizes.

### 9.4 Wake and sleep lifecycle

```text
wake
  -> append clock.observed
  -> load checkpoint and trace watermark
  -> validate or rebuild the canonical notebook projection
  -> validate workspace fingerprint
  -> derive open/effect-unknown operations
  -> reconcile safe operations
  -> start or attach CPython worker and establish kernel epoch
  -> restore supported named state and replay-safe definitions
  -> compute current views and prompt
  -> work
  -> verify or checkpoint
  -> append terminal/open limitations
  -> sleep
```

### 9.5 Notebook identity and branching

Each session lane has one canonical notebook ID. A branch starts from a notebook
snapshot at the branch event watermark and receives a new notebook ID; it does not
mutate the source lane's document. Cell IDs are stable within the notebook and map
to immutable ledger events. Reordering or editing a cell creates a new event and
revision; it never changes the recorded source of an earlier execution attempt.

The workbench path is an implementation detail and may change across machines. The
portable identity is:

```text
notebook ID + lane + source watermark + canonical content hash
```

An externally modified notebook is not silently trusted. The harness compares it
with the last materialized hash, parses the structural difference, and either:

- imports the change as explicit notebook events through a trusted operator action;
- preserves it as a separate candidate branch; or
- refuses to overwrite it and reports a conflict.

The local MVP may support only harness-owned writes plus explicit import. Real-time
collaborative editing is not required for the authority or recovery model.

## 10. Memory programs and views

### 10.1 Logical program

A memory program follows the paper's logical form `M = <Q, trigger, schema,
freshness, policy>`:

```python
class MemoryProgram(BaseModel):
    program_id: str
    name: str
    version: int
    owner: Literal["harness", "operator", "agent"]

    language: Literal["builtin", "sql", "relational_dsl", "python"]
    source_ref: str
    parameters_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]

    trigger: dict[str, JsonValue] | None
    freshness: dict[str, JsonValue]
    exposure_policy: dict[str, JsonValue]
    retention_policy: dict[str, JsonValue]
    action_policy: dict[str, JsonValue] | None

    authority: Literal["authoritative", "advisory"]
    determinism: Literal["deterministic", "semantic"]
    max_scan_events: int
    max_output_bytes: int
    max_runtime_ms: int

    status: Literal["candidate", "validated", "shadow", "active", "retired", "rejected"]
    source_event_ids: list[UUID]
    definition_hash: str
```

SQL or a restricted relational DSL is preferred for repeatable views. Sandboxed
Python is a fallback for transformations that cannot be expressed declaratively.
The model specifies logical meaning; the runtime chooses scan, index lookup, cached
result, materialized table, or incremental maintenance.

### 10.2 View request and result

```python
class ViewRequest(BaseModel):
    program_id: str
    program_version: int
    as_of_seq: int
    lane: str
    parameters: dict[str, JsonValue]
    budget_bytes: int
    clock_event_id: UUID | None


class ViewResult(BaseModel):
    view_id: str
    program_id: str
    program_version: int
    as_of_seq: int
    lane: str
    parameters_hash: str

    source_ranges: list[tuple[int, int]]
    source_event_ids: list[UUID]
    artifact_refs: list[str]
    workspace_fingerprint: str | None

    body: JsonValue | None
    body_artifact_ref: str | None
    input_hash: str
    content_hash: str
    generated_at: datetime
    valid_until: datetime | None
    warnings: list[str]
```

`generated_at` is provenance metadata and is excluded from stable model-facing
content hashes. Deterministic programs must produce the same content hash for the
same program, inputs, watermark, lane, and workspace fingerprint.

### 10.3 Retrieval receipt

Every model exposure and trigger decision records:

```python
class RetrievalReceipt(BaseModel):
    receipt_id: str
    consumer: Literal["prompt", "repl", "verification", "trigger", "public"]
    request_event_id: UUID
    program_id: str
    program_version: int
    view_id: str
    as_of_seq: int
    parameters_hash: str
    source_event_ids: list[UUID]
    physical_plan: Literal["scan", "indexed", "cached", "materialized", "incremental"]
    exposed_content_hash: str
    exposed_bytes: int
    visible_tokens: int | None
    latency_ms: int
    cost_usd: float | None
    cache_hit: bool
```

Receipts allow later diagnosis of missing evidence, bad program synthesis, stale
materialization, faulty context selection, or answer-model misuse.

### 10.4 Seed view catalog

| View | Output | Authority | Default physical path |
|---|---|---|---|
| `history.model` | Exact events visible to the model | Authoritative | Incremental |
| `history.public` | User messages and public replies | Authoritative | Incremental |
| `history.audit` | All permitted ledger metadata | Authoritative | Indexed |
| `task.contract` | Goal, acceptance, constraints, scope | Authoritative | Incremental |
| `task.state` | Phase, completed/current/pending work, blockers, next action | Authoritative | Incremental |
| `execution.open` | Open, failed, timed-out, retrying, and effect-unknown operations | Authoritative | Incremental |
| `notebook.state` | Canonical cells, revisions, execution links, projection hash, and conflicts | Authoritative | Incremental |
| `notebook.workbench` | Full durable notebook projection with bounded artifact references | Authoritative | On demand plus atomic materialization |
| `notebook.context` | Query- and phase-relevant notebook cells and rich outputs | Hybrid | On demand plus epoch cache |
| `notebook.restore` | Replay-safe definitions, named-state refs, and reconciliation warnings | Authoritative | Materialized checkpoint |
| `workspace.state` | Base revision, fingerprint, changed paths, drift | Authoritative | On demand plus cache |
| `verification.state` | Criterion evidence, checks passed/failed/not run | Authoritative | Incremental |
| `time.state` | Now, age, deadlines, backoff, expiry | Authoritative | On demand |
| `budget.state` | Token, cost, iteration, and time budgets | Authoritative | Incremental |
| `prompt.prefix` | Canonical stable instruction/tool bytes | Authoritative | Content-addressed cache |
| `context.tail` | Exact recent model-visible tail | Authoritative | Indexed |
| `context.compaction` | Structured handoff plus retained-tail cursor | Hybrid | Materialized per epoch |
| `context.phase_memory` | Evidence-backed memory for the current phase | Advisory | Cached by task/phase/query |
| `context.working_set` | Bounded files, artifacts, events, and views for next decision | Hybrid | On demand |
| `prompt.request` | Fully ordered provider request | Authoritative | On demand |
| `prompt.manifest` | Component hashes, budgets, reasons, prefix/cache data | Authoritative | Append receipt |
| `resume.handoff` | Minimum state required to wake safely | Authoritative | Materialized checkpoint |
| `branch.handoff` | Relevant outcomes from a lane being left | Hybrid | On demand/materialized |
| `learning.episode` | Strategy, calls, outcomes, verification, cost | Hybrid | Batch/materialized |
| `memory.episodic` | Causally grouped prior episodes | Advisory | Cached/materialized |
| `memory.semantic` | Supported assertions with temporal scope | Advisory | Incremental |
| `memory.procedural` | Successful reusable execution patterns | Advisory | Batch/materialized |
| `memory.failure` | Recurring failures, limits, and recovery patterns | Advisory | Batch/materialized |
| `memory.candidates` | Proposed facts, procedures, or programs | Advisory | Batch |

### 10.5 View dependency rules

- A program declares which event namespaces and other view versions it may read.
- Derived events are excluded from a program's source by default to prevent
  self-recursive summaries.
- A program may depend on another view only through an explicit versioned edge.
- Dependency cycles are rejected during validation.
- Authoritative views may depend only on deterministic programs and authoritative
  observations.
- Semantic outputs can become inputs to another semantic view, but provenance must
  retain the complete dependency chain.
- Workspace reads first append or reuse a `workspace.observed` event so the input is
  part of the replayable trace.

### 10.6 Program lifecycle

```text
proposed
  -> parse and type validation
  -> static safety, temporal, and cost analysis
  -> historical replay against sampled watermarks
  -> comparison with direct trace execution or gold outputs
  -> shadow mode on live events
  -> active on-demand program
  -> cached/materialized/incremental promotion when justified
  -> retirement on staleness, low utility, redundancy, drift, or repeated repair
```

Program definitions and receipts remain versioned after retirement. Activation is a
policy decision, not a side effect of generating syntactically valid code.

### 10.7 L0/L1/L2 serving paths

- **L0 hot path:** current task reducer state, open operations, recent model-visible
  tail, clock, budget, and workspace fingerprint. It must not require a model call.
- **L1 maintained path:** seeded or validated agent-authored views with cached or
  incrementally maintained results.
- **L2 ad hoc path:** a new bounded SQL/DSL/Python computation for an unseen query.

The runtime records query and update rates, cold/warm latency, hit rate, freshness
lag, maintenance cost, output drift, and downstream utility. It promotes a program
only when expected reuse savings exceed build and maintenance cost under its
freshness requirement.

## 11. Prompt construction and cache epochs

### 11.1 Four prompt regions

```text
P0 globally stable
  system behavior
  one python tool schema
  fixed safety and operating rules
  stable project instructions and skill catalog

P1 context-epoch stable
  task contract
  latest accepted compaction/checkpoint
  workspace identity
  phase-memory base
  frozen notebook workbench prefix

P2 append-only tail
  exact model-visible messages
  canonical notebook code/Markdown cells added in this epoch
  selected code-execution results and artifact references
  appended state deltas and steering

P3 ephemeral suffix
  current clock
  open/effect-unknown operations
  workspace drift
  query-specific memory
  current user query
```

P0 changes only when behavior, the single tool schema, fixed policy, trusted project
instructions, or selected stable skills change. P1 changes only at an intentional
epoch boundary. P2 grows by append. P3 is computed for one provider request and is
always after the reusable regions.

### 11.2 Canonical serialization

The prompt compiler fixes:

- section order and omission rules;
- UTF-8 and Unicode normalization;
- LF line endings;
- field and map-key order;
- canonical relative paths;
- numeric and timestamp formatting;
- empty-list and null rendering;
- maximum byte and token budgets;
- truncation markers and artifact-reference syntax.

P0 MUST contain no current timestamp, random identifier, session ID, task ID,
workspace temporary path, usage counter, or non-deterministically ordered data.

The notebook renderer has two modes. The archival renderer preserves complete
metadata and rich-output references. The model renderer emits deterministic,
line-oriented cell boundaries and only policy-selected metadata. Volatile timestamps,
execution counts, absolute paths, and generated notebook fields remain in the ledger
or archival notebook but are excluded from stable prefix bytes. Cell IDs, source
hashes, status, replay policy, and artifact digests remain when required for safe
reference and recovery.

### 11.3 Prompt manifest

Every request appends a `prompt.compiled` event containing:

```text
compiler version
model/provider
trace watermark and lane
P0/P1/P2/P3 hashes and byte/token counts
ordered component view IDs and hashes
component inclusion reasons
truncations and omitted bytes
prefix mutation reason
provider cache read/write tokens
```

The manifest is audit data and is not itself inserted into the prompt.

### 11.4 Epoch transitions

A new context epoch may start for:

- compaction;
- task phase transition when the phase-memory base materially changes;
- model/provider change;
- branch or checkpoint restoration;
- trusted project-instruction or stable-skill change;
- workspace replacement;
- an explicit operator request.

It must not start merely because a tool call completed or a volatile task counter
changed.

## 12. Compaction and recovery views

### 12.1 Triggering

Compaction is considered when:

```text
estimated_next_request > context_window - completion_reserve
```

It may also occur at phase, branch, model-switch, long-verification, checkpoint, or
sleep boundaries. It should not occur after every cell or capability call.

### 12.2 Safe cut points

The cut must not separate:

- a capability request from its outcome;
- a PTC cell from its code-execution result;
- an inner MCP/CLI call from its containing cell result;
- a retry attempt from its terminal outcome;
- a model tool-call declaration from the corresponding result.

It also must not rewrite or delete the durable notebook. Compaction changes the
bounded `notebook.context` and prompt views, not historical evidence. The harness
MAY append a generated Markdown handoff cell whose metadata identifies the
compaction program, source ranges, watermark, and content hash. That cell is derived
and never outranks the source events it summarizes.

A recent exact tail is retained. The previous compaction plus only newly aged-out
events are the default semantic summarization input.

### 12.3 Structured compaction result

```yaml
as_of:
  seq:
  observed_at:
  reason:
  previous_compaction_view:

task:
  goal:
  acceptance_criteria:
  constraints:
  non_goals:

position:
  phase:
  active_step:
  active_operation:
  last_successful_operation:
  next_intended_operation:

progress:
  completed_before:
  completed_since_previous_compaction:
  in_progress:
  pending:
  blocked:

limitations:
  failures:
  timeouts:
  cancelled:
  retries_exhausted:
  unknown_effects:
  missing_evidence:

workspace:
  base_revision:
  fingerprint:
  files_read:
  files_modified:
  external_drift:

verification:
  passed:
  failed:
  not_run:
  criterion_evidence:

decisions:
  confirmed:
  provisional:
  superseded:

context:
  critical_facts:
  artifact_refs:
  memory_view_refs:
  retained_tail_from_seq:

resume:
  next_safe_action:
  reconciliation_required:
```

Deterministic reducers populate task, position, limitation, workspace, and
verification fields. An LLM may compress narrative context and decisions, but its
summary cannot manufacture completion or erase unresolved effects.

### 12.4 Resume behavior

On resume, the harness first computes `execution.open`, `workspace.state`, and
`verification.state`. It reconciles operations marked safe to inspect or repeat.
External or effect-unknown writes are not replayed automatically. The model receives
the unresolved limitation and the safe next action.

### 12.5 Tunable compaction strategies

The default is a deterministic structured handoff plus a recent exact event tail.
Alternative strategies are ablations, not interchangeable prose settings:

| Strategy | Retained context | Compressed context | Cache consequence |
|---|---|---|---|
| Deterministic handoff | Recent event count | Ledger-derived task/progress/open/verification fields | Intentional dynamic epoch reset; P0 stays byte-stable |
| Pi-style incremental | Provider-valid recent token tail | Previous summary plus only newly aged-out history; deterministic file lists | New epoch at compaction, then append-prefix reuse resumes |
| Codex-style local/remote | Bounded selected message tail | Local continuation summary or provider compaction item | Provider-dependent epoch reset |
| Trace-view recomputation | Recent raw events | Versioned cached views plus optional semantic capsule | View cache keys reuse `(program, version, watermark, arguments)` |

Every strategy must preserve goal, acceptance criteria, done/in-progress/blocked work,
limitations, unknown effects, validation evidence, workspace identity, exact next safe
action, and source boundaries. Score strategies on pass rate, cost per passed task,
uncached input, cache-read ratio, tool calls, wall time, resume correctness, and lost
failure/open-work evidence. See
`examples/notebooks/02_cache_aware_compaction.ipynb` for an executable mock.

## 13. Notebook workbench and persistent CPython

### 13.1 Three-authority contract

The notebook, ledger, and worker answer different questions:

| Question | Answering authority |
|---|---|
| What programs and selected context form the current durable workbench? | Canonical notebook projection |
| What actually happened, including incomplete or conflicting evidence? | Append-only ledger |
| Which variables, imports, clients, and caches exist right now? | Current CPython kernel epoch |

The notebook is primary for authoring, resuming, inspecting, and exchanging PTC
work. It is not used to infer that a side effect completed. The ledger is primary
for audit, temporal reconstruction, effect reconciliation, and derived memory. The
worker is primary only for live execution and may disappear without invalidating
durable history.

### 13.2 Model-visible tool and notebook semantics

The model sees one schema:

```python
python(
    code: str,
    timeout_seconds: int | None = None,
) -> PythonExecutionResult
```

Calling `python` means "append and execute a PTC notebook cell." It does not expose
notebook CRUD as another model tool. The harness allocates the cell and attempt IDs,
persists provenance, executes the code, and materializes selected output. The result
contains:

```python
class PythonExecutionResult(BaseModel):
    notebook_id: str
    cell_id: str
    attempt_id: str
    kernel_epoch: str
    status: Literal["completed", "failed", "timeout", "cancelled", "abandoned"]
    effect: Literal["none", "observed", "changed", "unknown"]
    stdout: str
    stderr: str
    display: JsonValue | None
    exception: ExceptionSummary | None
    artifact_refs: list[str]
    truncated: bool
    duration_ns: int
```

The model receives only bounded exposed output, not every nested capability result.
Stable cell identity lets later code, Markdown, compaction, and verification refer
to earlier work without copying its full contents.

### 13.3 Write-ahead cell execution protocol

One PTC submission follows this order:

1. Validate code size, timeout, task ownership, and worker availability.
2. Allocate `cell_id`, `attempt_id`, code hash, and kernel epoch.
3. Atomically append `notebook.cell_added` and `repl.cell_submitted` with
   `status=started` before execution.
4. Materialize the input code cell into the session notebook before execution.
5. Execute the exact hashed source in the resident CPython worker.
6. Route every nested filesystem, shell, MCP, view, artifact, state, and clock call
   through the broker and ledger its lifecycle.
7. Capture streams, displays, exceptions, cancellation, timeout, worker loss, and
   partial artifact references.
8. Append exactly one terminal cell event, or leave a detectable open operation if
   the writer itself is interrupted.
9. Atomically materialize terminal status and selected outputs into the notebook.
10. Append `notebook.materialized` with renderer version, source watermark, and
    canonical content hash.

The notebook write is a recoverable projection update. If the process dies between
steps 8 and 10, startup rematerializes the same notebook bytes from committed
events. If it dies during execution, the attempt remains open or effect-unknown;
the presence of a code cell never implies success.

### 13.4 Notebook document contract

The durable workbench uses nbformat 4.5-compatible `.ipynb` with stable cell IDs.
It contains four useful cell roles:

| Role | Cell form | Purpose |
|---|---|---|
| PTC program | Code | Exact model-submitted Python |
| Narrative | Markdown | User messages, public agent replies, plans, and confirmed decisions |
| Derived handoff | Markdown | Compaction, phase memory, checkpoint, or recovery view |
| Result | Code output/MIME bundle | Selected textual, structured, tabular, or visual result |

Every harness-owned cell has a small metadata envelope:

```json
{
  "agent": {
    "cell_event_id": "019...",
    "attempt_id": "attempt_...",
    "ledger_start_seq": 1031,
    "ledger_end_seq": 1048,
    "kernel_epoch": "kernel_7",
    "source_sha256": "...",
    "status": "completed",
    "effect": "observed",
    "replay_policy": "safe",
    "artifact_refs": ["artifact://sha256/..."],
    "program_version": 1
  }
}
```

Observed and terminal timestamps are preserved in archival notebook cell metadata
alongside ledger provenance. They are excluded from the stable model renderer unless the
current query depends on time. Notebook execution counts are presentation metadata,
not causal order. Ledger sequence is causal order.

Markdown is evidence with authorship, not authority by prose. A generated handoff
or memory cell retains its program version and source ranges. It cannot declare a
write safe, a criterion verified, or a task complete.

User messages and public agent replies are projected to Markdown cells using their
original event IDs. The prompt compiler renders each logical item once: when a
message is selected through `notebook.context`, `history.model` contributes its
ordering/provenance but does not emit a duplicate textual copy.

### 13.5 Rich outputs and images

The notebook may contain Jupyter MIME bundles for text, JSON, tables, plots, images,
HTML, and other supported displays. Inline bodies remain bounded. Large or binary
outputs are written once to the artifact store and represented by digest, media
type, dimensions where applicable, byte size, and a compact fallback description.

`notebook.context` decides per request whether to expose:

```text
reference only | caption/summary | thumbnail | full body
```

The choice depends on task phase, query, model modality, sensitivity, and byte/token
budget. The retrieval receipt records which representation was shown. Repeated
images and outputs deduplicate by content hash.

### 13.6 Worker lifecycle

- One CPython worker is owned by an active session lane.
- The worker runs as a child process in the trusted local environment and uses the
  authoritative task workspace as its working directory.
- The process remains warm across model turns while resource and idle limits permit.
- Each worker lifetime has a unique kernel epoch recorded on every executed cell.
- Each cell has a stable notebook ID, code hash, deadline, cancellation token, and
  parent model-response event.
- Process death appends a terminal or abandoned cell event and starts reconciliation.
- A new process restores only supported checkpoint state and validated replay-safe
  definitions.

The `.ipynb` format does not require IPython. Production execution uses the
purpose-built CPython worker protocol; Jupyter kernels and servers are optional
development and interoperability surfaces, not correctness dependencies.

### 13.7 Durable versus warm state

Warm state may include imports, helper functions, parsed data, result handles,
clients, and temporary caches. Durable state is limited to:

- canonical notebook source and selected outputs;
- ledger events;
- workspace files and Git state;
- content-addressed artifacts;
- explicitly named JSON/Arrow/Parquet/NumPy values;
- registered pure helper source;
- environment and dependency manifests.

Cells are classified as:

```text
definition-only       safe to restore from source after validation
pure-computation      safe to recompute from immutable inputs
broker-read           repeat only after freshness and policy checks
broker-write          receipt required; never replay from cell source
unknown               never replay automatically
```

A notebook is therefore a reconstruction program plus evidence, not a memory dump.
Automatic replay of the whole notebook is prohibited because document order is not
execution order and repeated cells can duplicate effects.

### 13.8 Checkpointable state and restore plan

The broker exposes explicit methods such as:

```python
agent.state.put("name", value, format="json")
agent.state.put("table", arrow_table, format="parquet")
agent.state.get("name")
agent.state.list()
agent.helpers.register("normalize_results", source, pure=True)
```

Each state write creates an artifact and ledger event. Values are restored by name
and digest. Arbitrary objects, open sockets, processes, file descriptors, database
connections, async tasks, and credential-bearing objects are not checkpointable.
Pickle-like snapshots MAY be used as disposable acceleration caches but MUST NOT be
required for correctness or accepted across an untrusted environment boundary.

`notebook.restore` orders recovery inputs:

1. reconcile open and effect-unknown operations;
2. validate workspace and environment fingerprints;
3. load explicit named-state artifacts;
4. replay registered pure definitions in dependency order;
5. recompute pure cells only when inputs still match their recorded digests;
6. report skipped read/write/unknown cells and the next safe action.

### 13.9 Capability broker

The worker receives one prebound, typed object:

```python
agent.fs.read(path, *, start=None, end=None)
agent.fs.write(path, content, *, expected_hash=None)
agent.fs.edit(path, edits, *, expected_hash)
agent.shell.run(argv, *, cwd=None, timeout=None, env=None)
agent.mcp.call(server, method, arguments, *, timeout=None)
agent.views.get(name, *, as_of=None, **parameters)
agent.artifacts.get(ref, *, max_bytes=None)
agent.artifacts.put(value, *, media_type)
agent.state.get(name)
agent.state.put(name, value, *, format)
agent.clock.now()
agent.help(prefix=None)
```

The exact API is discoverable through Python introspection and compact generated
documentation. MCP schemas are loaded on demand into the worker, not placed in the
model's stable tool registry.

### 13.10 Broker enforcement

Every broker method:

1. normalizes and hashes its arguments;
2. appends a requested event;
3. classifies risk and checks policy/approval;
4. creates an operation receipt before an effect;
5. executes through the sandbox adapter;
6. redacts and bounds results;
7. stores large output as an artifact;
8. appends terminal status and effect;
9. returns a typed Python value or raises a typed exception.

The trusted-local source guard blocks direct imports of host control-plane packages,
physical database handles, ambient credentials, and unconfined subprocess/filesystem
APIs as defense in depth. It is not an OS security boundary.

### 13.11 Programmatic tool-call visibility

For one Python cell, the ledger and notebook relate as follows:

```text
ledger                                      notebook
------                                      --------
model.response
repl.cell_submitted ----------------------> code cell + started metadata
  capability.requested
  capability.completed/failed/timeout
  capability.requested
  capability.completed/failed/timeout
repl.cell_completed/failed/timeout --------> status + selected outputs
notebook.materialized ---------------------> canonical content hash
model-visible PythonExecutionResult
```

Raw nested results are available to the Python program and retained as bounded
events or artifacts. Only the cell's exposed result enters `history.model` and the
model-facing notebook view. This is the principal token-saving mechanism: filter,
join, aggregate, validate, and retry mechanically before crossing the model boundary
again.

### 13.12 `nb-cli` integration boundary

`nb-cli` is the reference notebook interoperability and operator tool because it
already provides stable cell IDs, AI-oriented line-delimited rendering, rich MIME
output handling, content-hashed externalization, local atomic writes, and optional
Jupyter collaboration.

The production harness does not delegate cell execution to `nb execute`:

- local `nb-cli` execution owns a temporary Jupyter kernel lifecycle rather than the
  harness's long-lived CPython worker;
- remote execution makes Jupyter Server session state another live dependency;
- notebook output persistence is not a substitute for ledger terminal events.

The harness uses a small nbformat 4.5 serializer/materializer only to write the
deterministic ledger-derived projection and golden-tests its output against `nb-cli`
read/write behavior. The required `nb-cli` remains the supported interface for
inspecting, exporting, and explicitly importing notebooks. If maintaining two
serializers becomes measurable maintenance debt, extract or embed the `nb-cli` serialization library; do not introduce a daemon or FFI layer
speculatively.

The implemented operator path is:

```bash
skein notebook --state-root STATE_ROOT [--task-id TASK_ID] [--cell-index N]
```

Without a task ID it lists materialized notebooks. With a task ID it first rebuilds
the canonical notebook from the append-only task events, then calls the required
`nb read --no-output`. Skein does not parse notebook JSON as a browsing fallback.
Model-authored `nb read --no-output`, local `nb search`, and `nb status` classify as
inspection; `nb execute`, cell/output mutation, remote server flags, and other modes remain approval-gated.

## 14. Phase- and query-specific memory

`context.phase_memory` is parameterized by task contract, phase, active step, current
query, files in focus, recent errors, workspace fingerprint, clock event, and
notebook watermark. It returns evidence-backed notebook cell references in addition
to ledger events and artifacts.

| Phase | Preferred evidence and views |
|---|---|
| Understand | Architecture boundaries, terminology, repository map, related prior episodes |
| Plan | Constraints, dependencies, confirmed decisions, prior failure patterns |
| Implement | Relevant APIs, changed-file relationships, successful procedures, current artifacts |
| Verify | Acceptance criteria, test adjacency, previous failures, flaky checks, environment limits |
| Recover | Open operations, idempotency, unknown effects, prior retries, restoration procedures |
| Review | Final diff, scope rules, conventions, criterion evidence, unresolved diagnostics |

Selection occurs in stages:

1. deterministic scope, authority, temporal, workspace, and policy filtering;
2. lexical and structured retrieval;
3. optional semantic reranking;
4. evidence diversity and contradiction checks;
5. deterministic byte budgeting and rendering.

A phase-memory base may be frozen in P1 for an epoch. Query-specific additions stay
in P3 so they do not invalidate the existing cached prefix.

## 15. Proactive and standing programs

A standing program consumes event deltas and may update an internal view or emit a
trigger candidate. A changed view is evidence, not permission to act.

```text
view delta
  -> idempotent trigger record
  -> cooldown and deduplication
  -> confidence and freshness checks
  -> authorization policy
  -> optional model decision
  -> internal action or explicit external approval
```

External notifications, writes, deployment, or communication remain separately
authorized. Late corrections or retractions may invalidate a pending trigger before
action.

## 16. Verification and completion

The verifier consumes only authoritative views and environmental evidence:

- `task.contract`;
- `task.state`;
- `workspace.state`;
- `execution.open`;
- `verification.state`;
- changed-file diff and configured commands.

Completion requires:

1. evidence for every acceptance criterion;
2. required commands executed successfully;
3. appropriate behavioral verification for executable changes;
4. `git diff --check` success;
5. no forbidden or unexpected changed path;
6. expected workspace/base identity;
7. no blocker or effect-unknown operation relevant to the result;
8. a deterministic verifier decision.

REPL code may request verification and analyze its outputs, but it cannot construct
the authoritative verification event or mark the task complete.

## 17. Google ADK integration

ADK remains the execution substrate:

| Concern | Target implementation |
|---|---|
| Root composition | Existing `App` and `HarnessFactory` |
| Deterministic loop | `Workflow` and bounded nodes |
| Coding model | One cache-stable ADK `Agent` |
| Model tool | One ADK function tool that appends and executes a notebook PTC cell in the CPython worker |
| Session persistence | Ledger-backed `SessionService` projection |
| Session workbench | Ledger-backed canonical `.ipynb` materializer and snapshot artifacts |
| Artifacts | Existing artifact boundary backed by content-addressed manifest events |
| Callbacks | Append normalized model/run/tool lifecycle events |
| Steering | Ledger-backed queue view and safe-boundary reducer |
| Verification | Separate deterministic workflow node |
| Provider caching | Prompt regions and recorded provider cache telemetry |
| ADK compaction | Overflow-only backstop after coding-aware compaction |

Mutable session/task/view/notebook state stays out of `static_instruction`. The
stable instruction contains the Python tool contract, safety rules, notebook
semantics, and durable-state protocol.

## 18. Physical storage architecture

### 18.1 One logical authority

"Single store" means one canonical event authority and one sequence. It does not
mean every byte must live in one file:

- event metadata and bounded payloads are canonical ledger rows;
- large bodies are canonical content-addressed artifacts referenced by events;
- reducer tables, indexes, cached results, and materializations are rebuildable;
- analytical replicas carry explicit source watermarks and never become a competing
  history.

### 18.2 Local MVP: configured JSONL or embedded DuckDB

Use one canonical ledger owned by the single server process. JSONL is the
dependency-free default when memory is enabled; DuckDB is the explicit analytical
backend:

```text
STATE_ROOT/ledger.jsonl | STATE_ROOT/ledger.duckdb
STATE_ROOT/artifacts/sha256/...
STATE_ROOT/notebooks/<session_id>/<lane>.ipynb
STATE_ROOT/workspaces/...
```

Notebook paths are local handles, not durable identities. Atomic replacement writes
the current projection; content-addressed notebook snapshots live in the artifact
store at checkpoints, branches, and explicit exports. The ledger records every
materialized hash and watermark, so the file can always be verified or rebuilt.

JSONL keeps the initial local profile small and implements the same canonical event
contract with an O(total events) scan ceiling. DuckDB is selected when the workload is append-heavy,
single-process, and increasingly analytical. It supports SQL over JSON and Parquet,
columnar scans, projection/filter pushdown, windowing, aggregation, and in-process
parallelism. A dedicated `LedgerWriter` serializes sequence allocation and commits;
read-only view connections use explicit watermarks.

This is a hypothesis, not a permanent mandate. Append p99, crash recovery, checkpoint
latency, and interactive tail-query latency are delivery gates. Backend changes are
explicit configuration; storage failures never trigger a silent fallback.

#### 18.2.1 Optional LanceDB search projection

LanceDB is a physical retrieval accelerator, not another historical authority. The
optional `memory-search` extra materializes immutable, content-addressed snapshots from
canonical event rows and caller-supplied vectors. Snapshot identity includes the event
content and embedding version. Reusing a snapshot embeds only the query; changing the
vectorizer, model, dimensions, or preprocessing requires a new version. Hybrid results
resolve to canonical event IDs, and deletion or corruption of a Lance directory loses
no evidence.
Each projection is nested under the SHA-256 task identity so explicit task erasure can
remove all derived search bytes without scanning or interpreting Lance metadata.

The dependency remains lazy because its installed footprint is material even though
the data path is inexpensive. A local 10,000-event spike produced a 1.36 MB projection;
warm mean vector, FTS, and hybrid queries took 2.06, 1.16, and 2.90 ms. The isolated
environment occupied about 303 MB and repeat process imports took about 0.52 seconds.
No embedding provider is selected by the harness: deployments inject a deterministic
vectorizer and version it explicitly. Live prompt use remains subject to the same
quality, cache, latency, and correctness ablation as every other memory view.

### 18.3 Why DuckLake is not the hot ledger

DuckLake provides cataloged Parquet data, snapshots/time travel, schema evolution,
partitioning, and multi-file storage. Those are valuable for long retention,
cross-project analytics, offline learning, and large shared memory catalogs. They do
not justify creating a lakehouse snapshot or small Parquet file for each interactive
event.

DuckLake enters when volume, retention, or multi-process sharing requires it:

- micro-batch sealed ledger segments into partitioned Parquet;
- record the exact source watermark in each export snapshot;
- keep hot recent events in the transactional ledger;
- query hot and sealed segments through one logical view;
- use a PostgreSQL DuckLake catalog for stable multi-process writers;
- run file compaction, snapshot expiration, and retention under explicit policy.

DuckLake materializations and exports are acceleration and retention tiers. Until a
formally tested cutover, the hot ledger remains the authority.

### 18.4 Proposed DuckDB schema

```sql
CREATE SEQUENCE ledger_global_seq START 1;

CREATE TABLE ledger_events (
    seq BIGINT PRIMARY KEY DEFAULT nextval('ledger_global_seq'),
    event_id UUID NOT NULL UNIQUE,
    stream_id VARCHAR NOT NULL,
    stream_seq BIGINT NOT NULL,
    lane VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    schema_version USMALLINT NOT NULL,
    source VARCHAR NOT NULL,
    visibility VARCHAR[] NOT NULL,
    session_id VARCHAR,
    task_id VARCHAR,
    run_id VARCHAR,
    operation_id VARCHAR,
    parent_event_id UUID,
    causation_id UUID,
    correlation_id VARCHAR,
    observed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    duration_ns UBIGINT,
    status VARCHAR,
    effect VARCHAR,
    payload JSON NOT NULL,
    artifact_refs VARCHAR[] NOT NULL,
    content_hash VARCHAR NOT NULL,
    idempotency_key VARCHAR,
    supersedes_event_id UUID,
    retracts_event_id UUID,
    UNIQUE(stream_id, stream_seq),
    UNIQUE(stream_id, idempotency_key)
);

CREATE TABLE stream_heads (
    stream_id VARCHAR PRIMARY KEY,
    next_stream_seq BIGINT NOT NULL,
    lane_heads JSON NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE artifact_manifest (
    digest VARCHAR PRIMARY KEY,
    media_type VARCHAR NOT NULL,
    byte_size UBIGINT NOT NULL,
    storage_uri VARCHAR NOT NULL,
    redaction_class VARCHAR NOT NULL,
    encryption_key_ref VARCHAR,
    owner_stream_id VARCHAR NOT NULL,
    created_event_id UUID NOT NULL,
    retention_policy JSON NOT NULL
);

CREATE TABLE program_projection (
    program_id VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    definition JSON NOT NULL,
    definition_hash VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    source_seq BIGINT NOT NULL,
    PRIMARY KEY(program_id, version)
);

CREATE TABLE view_materializations (
    view_id VARCHAR PRIMARY KEY,
    program_id VARCHAR NOT NULL,
    program_version INTEGER NOT NULL,
    lane VARCHAR NOT NULL,
    as_of_seq BIGINT NOT NULL,
    parameters_hash VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    body JSON,
    artifact_ref VARCHAR,
    valid_until TIMESTAMPTZ,
    source_seq BIGINT NOT NULL
);

CREATE TABLE retrieval_projection (
    receipt_id VARCHAR PRIMARY KEY,
    request_event_id UUID NOT NULL,
    consumer VARCHAR NOT NULL,
    program_id VARCHAR NOT NULL,
    program_version INTEGER NOT NULL,
    view_id VARCHAR NOT NULL,
    as_of_seq BIGINT NOT NULL,
    source_event_ids UUID[] NOT NULL,
    physical_plan VARCHAR NOT NULL,
    exposed_content_hash VARCHAR NOT NULL,
    exposed_bytes UBIGINT NOT NULL,
    visible_tokens UBIGINT,
    latency_ms UBIGINT NOT NULL,
    cost_usd DOUBLE,
    cache_hit BOOLEAN NOT NULL,
    source_seq BIGINT NOT NULL
);

CREATE TABLE notebook_projection (
    notebook_id VARCHAR NOT NULL,
    lane VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    task_id VARCHAR,
    materialized_seq BIGINT NOT NULL,
    content_hash VARCHAR NOT NULL,
    renderer_version VARCHAR NOT NULL,
    storage_path VARCHAR NOT NULL,
    snapshot_artifact_ref VARCHAR,
    conflict_status VARCHAR NOT NULL,
    PRIMARY KEY(notebook_id, lane)
);
```

The projection tables are populated from program, view, retrieval, and notebook
events. Cell state need not have a second physical table in the MVP; it is reduced
from the relevant event stream when materializing a notebook. Add a cell projection
only if measured rebuild or query latency requires it. A database and every canonical
notebook can be rebuilt from `ledger_events` plus artifact bytes; equality is tested
by content hash.

### 18.5 Write transaction

One writer service owns the database connection. For an append:

1. begin transaction;
2. look up an existing idempotency key;
3. validate identical replay or reject conflicting reuse;
4. allocate stream sequence from `stream_heads`;
5. insert the event with storage-assigned global sequence;
6. update synchronous authoritative projections needed for admission/recovery;
7. commit;
8. publish the committed watermark to in-process consumers.

View workers never consume beyond the committed watermark. A materialization records
the exact source watermark and is atomically published only after its result and
receipt are durable.

### 18.6 Indexing and partitioning

Initial indexes or sorted projections should support:

```text
stream_id + stream_seq
task_id + seq
session_id + seq
operation_id + seq
kind + seq
correlation_id + seq
recorded_at
idempotency_key
```

Do not index arbitrary JSON fields eagerly. Promote frequently queried fields into
typed columns or maintained projections based on workload evidence.

For DuckLake archival, partition primarily by tenant/project and coarse recorded
date, not by session or event kind, to avoid tiny partitions. Cluster or sort within
files by stream and sequence. File size, batch interval, and retention are benchmark
parameters rather than fixed constants.

### 18.7 Concurrency model

The first release has one server process and one ledger writer. Multiple tasks may
execute concurrently, but their events are serialized at commit and retain separate
stream sequences. Read-side work may use snapshots or independent read connections.

Multi-process write support requires a separate design gate. The preferred scale
path is PostgreSQL for transactional coordination and, where analytical scale
justifies it, DuckLake with a PostgreSQL catalog. Sharing a native DuckDB file among
independent writer processes is not an accepted production design.

### 18.8 Backup, recovery, and integrity

- On startup, verify database readability and stream-head consistency.
- Detect gaps, duplicate event IDs, conflicting idempotency keys, invalid parent
  links, and payload hash mismatches.
- Rebuild all projections into a fresh database and compare hashes in CI and during
  migrations.
- Back up the ledger and artifact manifest consistently at a committed watermark.
- Artifact garbage collection is mark-and-sweep from retained ledger events and
  materialized views and notebook snapshots.
- Corrupt or missing artifacts produce explicit limitation events; they are not
  silently omitted from memory.
- On startup, compare each notebook file with its recorded materialization hash.
  Rebuild a missing file; report and preserve an unexpected external modification.

## 19. Privacy, security, and deletion

### 19.1 Storage minimization

- Hidden reasoning is never retained.
- Secrets are redacted before event persistence and model exposure.
- Raw source and large outputs default to scoped artifacts, not JSON payloads.
- Notebook projections contain only content allowed by their ownership and
  visibility policy; secrets are not preserved merely because a kernel displayed
  them.
- Visibility and ownership are enforced before view execution and again before
  exposure.
- Memory programs cannot broaden their caller's data access.

### 19.2 Deletion under an append-oriented model

Append-only describes normal logical history, not a refusal to honor deletion.
Deletion may require:

1. append `data.retracted` to stop ordinary view use;
2. remove or crypto-shred artifact content;
3. rebuild affected materializations;
4. rebuild or remove affected notebook projections and snapshots;
5. expire and rewrite affected DuckLake snapshots/files when physical erasure is
   required;
6. append a non-sensitive erasure receipt;
7. verify that model/public/notebook views can no longer expose the data.

### 19.3 Program safety

SQL/DSL programs are parsed and bounded. Python view programs run in a read-only
sandbox over frozen inputs unless their policy explicitly permits internal
materialization. Standing triggers cannot call external capabilities directly.

## 20. Observability

Record at least:

```text
ledger append p50/p95/p99
ledger and artifact bytes
replay and projection rebuild time
open/effect-unknown operation count
REPL cold start and warm cell latency
cell repair and exception rate
narrative/code/output notebook bytes and materialization latency
notebook projection rebuilds, conflicts, and hash mismatches
notebook context selected/omitted bytes and rich-output representation
nested broker call count and raw/exposed bytes
view cold/warm latency and cache hit rate
view freshness lag and invalidation count
program validation, repair, activation, and retirement
prompt P0/P1/P2/P3 bytes and tokens
prefix versions and mutation reasons
provider cache read/write and uncached tokens
compactions and retained-tail size
verification pass rate and cost per passed task
resume success and duplicated side effects
unsupported claim and stale-memory error rate
```

Every performance claim must include task success and deterministic verification,
not token count alone.

## 21. Target package structure

```text
harness/
  ledger/
    models.py              # event and artifact contracts
    writer.py              # single-writer transaction boundary
    duckdb_store.py
    replay.py
    migration.py
  views/
    models.py              # program, request, result, receipt
    catalog.py
    validator.py
    runtime.py
    optimizer.py
    materialize.py
    seeded/
      history.py
      task.py
      execution.py
      workspace.py
      verification.py
      prompt.py
      compaction.py
      memory.py
  notebook/
    models.py              # notebook/cell metadata and replay policy
    reducer.py             # canonical cell state from ledger events
    materializer.py        # deterministic nbformat projection and atomic write
    renderer.py            # archival and model-facing representations
    importer.py            # explicit external-edit diff and event import
  repl/
    protocol.py
    manager.py
    worker.py
    checkpoint.py
    sandbox.py
  broker/
    api.py
    policy.py
    filesystem.py
    shell.py
    mcp.py
    artifacts.py
    state.py
  context/
    epochs.py
    compiler.py
    serializer.py
  verification/
  adk/
    session_service.py
    tool.py
    callbacks.py
```

ADK-specific wiring remains in `app/` or `harness/adapters/adk/`. Ledger, view, broker, and
notebook/REPL contracts remain importable and testable without cloud credentials.
The notebook package starts with one concrete implementation; no provider interface,
daemon, or collaboration abstraction is added until another implementation is
actually required.

## 22. Implementation and migration plan

Each phase is independently shippable and must not combine tool, prompt, model, and
evaluation changes in one experiment.

### Phase 0: freeze baseline and contracts

Deliver:

- this design as an accepted architecture decision;
- representative short, high-fanout, long-horizon, interruption, and branch tasks;
- baseline results for the current four-tool harness;
- Pydantic event, notebook-cell, program, view, receipt, and checkpoint contracts;
- canonical JSON and prompt serialization fixtures;
- architecture decision records for DuckDB, notebook authority, and the CPython
  sandbox.

Tests and gates:

- schema round-trip and forward-version rejection;
- golden byte/hash fixtures;
- baseline pass rate, uncached input, cache ratio, cost, calls, and wall time recorded;
- no runtime behavior change.

### Phase 1: canonical ledger behind existing behavior

Deliver:

- DuckDB `LedgerStore` and one writer service;
- unified event mapping for current task events, trace spans, receipts, checkpoints,
  steering, approvals, metrics, public events, run registry, and ADK callbacks;
- artifact manifest events;
- notebook/cell/materialization event contracts, initially populated only by test
  fixtures and explicit imports;
- importer for existing JSONL and SQLite state;
- replay validator and projection rebuild command.

Migration sequence:

1. snapshot existing state;
2. import with source identifiers and original timestamps;
3. shadow-append new normalized ledger events while current stores remain authority;
4. compare ledger-derived projections with current stores;
5. cut over readers one projection at a time;
6. cut over the writer only after replay equality;
7. retain rollback tooling until a full release cycle passes;
8. remove duplicate stores only after the cutover gate.

Tests and gates:

- deterministic import of the same state produces identical hashes;
- replay reconstructs task, receipt, checkpoint, public history, and metrics views;
- forced crash between request/start/outcome creates an open operation, not success;
- no duplicate side effect under callback and process retry;
- append and tail-query latency remain within the baseline margin.

### Phase 2: seeded deterministic view runtime

Deliver:

- program catalog and validator;
- `ViewRequest`, `ViewResult`, materialization, and retrieval receipt;
- authoritative seed views: history, task, execution, workspace, verification, time,
  budget, and notebook state;
- canonical nbformat 4.5 notebook reducer, atomic materializer, archival renderer,
  model renderer, and snapshot artifacts;
- golden interoperability fixtures against `nb-cli` without using its executor;
- source watermarks, dependency graph, invalidation, and rebuild tooling;
- L0 incremental reducers.

Tests and gates:

- view equality after full replay, incremental replay, and process restart;
- `as_of` correctness across corrections and late events;
- dependency-cycle and authority violations fail closed;
- materialization deletion followed by recomputation yields the same content hash;
- notebook deletion followed by rematerialization yields byte-identical canonical
  content;
- an external notebook edit is detected and never silently overwrites ledger state;
- time-dependent tests use recorded clock events.

### Phase 3: prompt compiler and cache epochs

Deliver:

- P0/P1/P2/P3 prompt compiler;
- canonical renderer and prompt manifest;
- context-epoch transitions;
- exact `history.model` projection;
- phase/query-bounded `notebook.context` with artifact-backed rich outputs;
- cache-aware task-state deltas and recent-tail handling.

Tests and gates:

- byte-identical P0 for unchanged behavior;
- append-only request prefixes between epoch transitions;
- every prompt component has a view ID and receipt;
- tiny budgets remain hard bounds;
- ablation shows no correctness regression and reports cache/uncached tokens.

### Phase 4: structured compaction and durable resume

Deliver:

- compaction preparation, structured deterministic frame, optional semantic
  narrative, and retained tail;
- provenance-bearing notebook handoff cells without rewriting source cells;
- `resume.handoff` and `branch.handoff`;
- open/effect-unknown operation reconciliation;
- workspace/checkpoint coupling;
- ADK overflow compaction retained as backstop.

Tests and gates:

- two or more chained compactions preserve newly aged-out events exactly once;
- no cut splits a cell/capability or tool-call/result pair;
- interruption at each operation boundary resumes without duplicate mutation;
- incomplete work, failed checks, missing evidence, and next safe action survive
  compaction;
- prefix reset occurs only at recorded epoch transitions.

### Phase 5: notebook-native PTC and persistent CPython behind a feature flag

Deliver:

- trusted-local CPython worker protocol and lifecycle manager;
- one ADK `python` tool implementing the write-ahead notebook-cell protocol;
- code, Markdown, bounded MIME output, status, replay-policy, and snapshot
  materialization into the canonical session notebook;
- typed broker for current read/bash/edit/write behavior;
- nested-call tracing and artifacts;
- supported named-state and pure-helper checkpoints;
- process death, timeout, cancellation, and restart recovery.

Tests and gates:

- broker confinement and approval tests equal or exceed current tools;
- common direct filesystem/network/process bypass attempts fail closed under the
  trusted-local source guard;
- cell and nested-call event trees are complete under success and failure;
- notebook cell provenance and terminal status agree with ledger replay;
- timeout, cancellation, and worker death cannot leave a cell looking successful;
- image and large-output cells externalize deterministically and remain queryable;
- raw intermediate bytes do not enter model history unless exposed by the cell;
- warm state survives turns; required state survives process restart explicitly;
- restart restores only named state and replay-safe code and never replays a write;
- four-tool and one-REPL ablation uses the same model/tasks/verification.

The feature flag remains off by default until the single-REPL variant passes all
hard gates and its cost per passed task is non-inferior.

### Phase 6: MCP and high-fanout programmatic routing

Deliver:

- on-demand MCP schema discovery and generated typed wrappers;
- policy classification for local and external capabilities;
- bounded concurrency, timeout, retry, and rate-limit primitives;
- deterministic examples for fan-out search, result filtering, joins, and batched
  verification.

Tests and gates:

- partial batch failure is represented per child operation;
- external writes use idempotency keys and are never blindly replayed;
- capability schemas do not enter P0 unless explicitly stabilized;
- high-fanout tasks reduce model-visible intermediate bytes without reducing pass
  rate.

### Phase 7: semantic and agent-authored memory programs

Deliver:

- phase-memory seeded views;
- restricted relational DSL and SQL validation;
- optional semantic reranking;
- program candidate, historical replay, shadow, activation, and retirement;
- episodic, semantic, procedural, and failure views as seeded programs;
- learning episodes and oracle-program evaluation condition.

Tests and gates:

- every assertion retains evidence and temporal scope;
- corrections and retractions invalidate affected outputs;
- candidate programs cannot affect prompts before activation;
- shadow comparison exposes output drift and unsupported claims;
- shifted-workload evaluation compares fixed views, ad hoc programs, adaptive
  programs, and oracle programs.

### Phase 8: DuckLake analytical and retention tier

Deliver only when scale measurements justify it:

- sealed event-segment export with source watermarks;
- DuckLake catalog and Parquet layout;
- hot-plus-sealed logical query view;
- file compaction, snapshot expiration, retention, and erasure workflows;
- PostgreSQL catalog deployment profile for multi-process use.

Tests and gates:

- hot and archived `as_of` queries agree at the same watermark;
- late events and retractions update only affected results;
- crash during export cannot publish a partial watermark;
- p95 time-to-context after warm-up is non-inferior to the strongest simpler
  baseline;
- lifecycle operations prove deleted data is no longer exposed.

### Phase 9: cutover and simplification

Deliver:

- default notebook-native single-REPL harness only if the ablation passes;
- ledger-backed ADK session/public/run projections;
- removal of superseded stores and compatibility code;
- updated architecture, security, operations, and implementation-status documents;
- migration and rollback release notes.

Do not retain two permanent execution architectures without an explicit product
reason. Complexity reduction is part of the final acceptance gate.

## 23. Evaluation plan

### 23.1 Required variants

| Variant | Purpose |
|---|---|
| Current four tools | Production baseline |
| Four tools plus canonical ledger/views | Isolate trace/view benefit |
| One REPL plus ledger, without notebook context | Isolate tool-interface benefit |
| Notebook-native PTC plus seeded deterministic views | Isolate durable-workbench benefit |
| One REPL plus fixed phase memory | Measure retrieval benefit |
| One REPL plus ad hoc programs | Measure semantic adaptability without reuse |
| One REPL plus adaptive materialization | Measure reuse and latency |
| Oracle memory programs | Separate architecture potential from synthesis failure |

### 23.2 Workloads

- short targeted edit;
- large repository exploration;
- high-fanout mechanical search/filter/join;
- 50+ capability-call long task;
- repeated repair and verification loop;
- two or more compactions;
- process and machine restart;
- notebook rematerialization and explicit external-edit import;
- visual or rich-output task with bounded image exposure;
- user steering during work;
- branch and model change at checkpoint;
- external workspace drift;
- timeout with uncertain external effect;
- delayed relevance, correction, retraction, and `as_of` memory questions;
- recurring task shape followed by workload shift.

### 23.3 Metrics

Primary:

```text
deterministically passed tasks
cost per passed task
```

Supporting:

```text
uncached input and cache-read ratio
visible-context and total processed bytes
first-correct-cell/program latency
cell/program repair calls
tool/broker calls and duplicate actions
p50/p95/p99 time-to-context
view hit rate, freshness lag, and maintenance cost
resume success and duplicate side effects
temporal/as-of accuracy
evidence recall and unsupported-claim rate
stale-memory and contradiction-resolution error
scope and policy violations
wall time and operator interventions
```

### 23.4 Decision rules

- Compare the same model, reasoning setting, tasks, workspace revisions, policy, and
  verification requirements.
- Change one architectural variable per ablation.
- Report failures and repeated runs.
- A token reduction with lower pass rate is not an optimization.
- A generated-program failure with an oracle-program success is a synthesis problem,
  not proof that memory programs are ineffective.
- Fast stale views fail correctness even if latency improves.

## 24. Implementation scorecard

### 24.1 Hard gates

An implementation receives an overall **fail** regardless of points if any of these
conditions holds:

1. Ledger replay has gaps, conflicting duplicates, or projection hash divergence.
2. A timeout, cancellation, or process loss can be represented as successful or
   proven no-effect without evidence.
3. Resume can duplicate a workspace or external side effect.
4. REPL code can bypass workspace, network, secret, or approval policy.
5. The model can mark coding work complete without deterministic verification.
6. A semantic view can overwrite authoritative task, permission, or verification
   state.
7. Prompt P0 changes without a recorded version and mutation reason.
8. A model-visible claim cannot be traced to the view and source evidence exposed.
9. Required deletion or secret redaction is absent from ledger, artifacts, or
   materializations.
10. The one-REPL default regresses cost per passed task beyond the pre-registered
    margin without an explicit decision to retain the baseline.
11. Notebook state can diverge from committed ledger evidence without detection or
    deterministic rematerialization.
12. Recovery automatically executes a broker-write or unknown-effect notebook cell.

### 24.2 Weighted rubric

| Area | Points | Full-credit evidence |
|---|---:|---|
| Ledger integrity and temporal semantics | 15 | Gap-free/idempotent append, observed/recorded time, corrections, retractions, deterministic replay. |
| Failure, effect, and recovery fidelity | 10 | Complete operation lifecycle, unknown-effect reconciliation, no duplicated effects across forced interruption. |
| Session, branch, and workspace durability | 10 | Cross-session task continuity, lane replay, checkpoint/workspace coupling, safe wake/sleep. |
| Notebook-native PTC, CPython, and broker | 15 | One model tool, durable cell provenance, rich outputs, typed composition, complete nested traces, warm state, explicit durable state, policy parity. |
| Memory-program and view runtime | 15 | Versioned programs/results/receipts, seeded views, as-of correctness, invalidation, program lifecycle. |
| Prompt and cache discipline | 10 | Canonical P0/P1/P2/P3, byte-stable prefix, bounded context, measurable cache improvement. |
| Verification and safety | 15 | Independent criterion evidence, scope checks, sandbox/approval/redaction parity, no model authority escalation. |
| Performance and physical optimization | 5 | Measured L0/L1/L2 routing, justified materialization, acceptable append/query/rebuild latency. |
| Evaluation and operability | 5 | Reproducible ablations, dashboards/receipts, backup/migration/rollback, failure attribution. |
| **Total** | **100** | All hard gates also pass. |

### 24.3 Tenet-to-rubric traceability

| Tenet | Scored area or hard gate | Required evidence |
|---|---|---|
| T1 Ledger authority | Ledger integrity; hard gate 1 | Fresh-database replay and projection hash equality. |
| T2 Views over evidence | Memory-program runtime; hard gates 6 and 8 | Source-addressed outputs and rebuildable materializations. |
| T3 One guarded tool | REPL and broker; hard gate 4 | Adversarial broker-parity and sandbox-bypass tests. |
| T4 Explicit durability | Session/workspace durability | REPL process-loss test restoring only declared state. |
| T5 Every attempt is evidence | Failure and recovery; hard gate 2 | Boundary-fault matrix with open, terminal, and unknown-effect views. |
| T6 Time is first-class | Ledger integrity | Recorded clock fixtures, late events, and reproducible `as_of` views. |
| T7 Deterministic control | Verification and safety; hard gate 6 | Semantic-output tampering cannot alter authoritative reducers. |
| T8 Independent completion | Verification and safety; hard gate 5 | False model completion claims fail the verifier. |
| T9 Cache discipline | Prompt and cache; hard gate 7 | Golden P0 bytes and append-prefix tests across complete tasks. |
| T10 Narrow exposure | View runtime; hard gate 8 | Prompt/retrieval receipt accounts for every exposed byte source. |
| T11 Measured optimization | Performance and physical optimization | Promotion decision reproduces from workload and cost telemetry. |
| T12 Earned activation | Memory-program runtime | Candidate, replay, shadow, active, and retirement transitions tested. |
| T13 Deletion support | Verification and safety; hard gate 9 | Erasure fixture removes exposure from events, artifacts, and materializations. |
| T14 No invisible effects | Failure/recovery and broker; hard gates 2 and 3 | Every effectful call has intent, authorization, receipt, and reconciled outcome. |
| T15 Notebook workbench | Notebook-native PTC; hard gate 11 | Byte-stable rematerialization and cell-to-ledger provenance. |
| T16 Notebook is not heap | Session durability; hard gate 12 | Restart restores declared state without replaying effectful cells. |
| T17 Bounded rich context | Prompt/cache and notebook-native PTC | Artifact deduplication and receipt-backed image/output selection under hard budgets. |

### 24.4 Assumption validation matrix

| Assumption | First decisive phase | Pass/fail comparison |
|---|---|---|
| A1 Python competence | Phase 5 | Cell success and repair cost versus equivalent direct-tool tasks. |
| A2 Token/cost benefit | Phase 5 | Cost per passed task and uncached tokens versus four tools. |
| A3 High-fanout filtering | Phase 6 | Exposed bytes and correctness on matched fan-out workloads. |
| A4 Warm-process value | Phase 5 | Warm versus cold task wall time and repeated setup cost. |
| A5 Observable evidence sufficiency | Phase 4 | Resume/compaction missing-evidence and unsupported-claim rate. |
| A6 One-ledger reconstruction | Phase 2 | Full replay and incremental projection equality. |
| A7 Semantic program utility | Phase 7 | Generated versus fixed, ad hoc, and oracle programs under shift. |
| A8 DuckDB suitability | Phase 1 | Append/tail p99, crash recovery, growth, and checkpoint latency. |
| A9 Cache-epoch value | Phase 3 | Cache ratio and pass rate versus per-turn dynamic packets. |
| A10 Evidence-derived memory accuracy | Phase 7 | Temporal, correction, and stale-memory accuracy versus record stores. |
| A11 Notebook workbench value | Phase 5 | Resume, reuse, inspection, and visual-task results versus ledger-only REPL transcript. |
| A12 Notebook cache suitability | Phase 3 | Stable projection hashes, cache ratio, and bounded expansion versus ledger-native rendering. |

An assumption is not accepted because its phase shipped. Its pre-registered metric
must pass; otherwise the stated falsification consequence becomes the next design
decision.

### 24.5 Rating bands

| Score | Rating | Meaning |
|---:|---|---|
| <70 | Not viable | Architectural or implementation gaps dominate. |
| 70-84 | Research prototype | Useful experiments; not the default harness. |
| 85-94 | Release candidate | All hard gates pass; long-horizon and migration evidence exists. |
| 95-100 | Production-ready target | All gates pass, measured advantage exists, operations and deletion are proven. |

No rating above research prototype is allowed without all hard gates.

### 24.6 PTC long-session checklist

Score each question `0` when absent, `0.5` when implemented but not demonstrated, and
`1` only when the named deterministic or captured-request evidence passes. A release
candidate needs at least 85/100 and every hard gate above.

| Area | Weight | Question | Required evidence | Current status |
|---|---:|---|---|---|
| Model boundary | 15 | Which exact nested-result and heap bytes reach the model? | Captured provider requests for high-fanout and 100-cell sessions. | Broker result isolation has a deterministic envelope regression; provider capture pending. |
| State identity | 10 | Is state owned by run, task, conversation, workspace, and user unambiguously? | Same-conversation restart plus concurrent-owner rejection. | Run-scoped only. |
| State discovery | 10 | Can the model rediscover names, types, sizes, provenance, and replay class without reading values? | Bounded catalog test with a large secret-marker value. | `agent.state.list/describe` implemented; compaction injection pending. |
| Durable recovery | 15 | Can committed state be reconstructed without executing an effect twice? | Process/server crash and state-hash equality at every interruption boundary. | Conservative local replay implemented; cross-run recovery pending. |
| Failure semantics | 10 | Can an exception, timeout, or lost response leave undocumented partial state or effects? | Partial-assignment rollback and unknown-effect reconciliation tests. | Failed-cell rollback and timeout uncertainty implemented; broker crash matrix pending. |
| Ledger provenance | 10 | Are intent, attempt, causal parent, time, terminal state, effects, and references recorded? | Gap-free replay and open-execution view equality. | Implemented for current cell/capability lifecycle. |
| Compaction/cache | 10 | Does compaction use actual request size and retain goal, progress, verification, open work, and state handles without changing P0? | Pre/post request bytes and continuation eval. | Stable P0 implemented; state-aware ADK handoff and request accounting pending. |
| Capability execution | 8 | Are return shapes, retry safety, idempotency, cancellation, and allowed concurrency explicit? | Contract and fan-out fault tests. | Sequential broker and receipts implemented; parallel orchestration pending. |
| Isolation | 7 | What protects filesystem, network, secrets, CPU, memory, and processes for the declared trust profile? | Profile-specific escape/resource tests. | Trusted-local profile only; source guard is defense in depth. |
| Evaluation | 5 | Is PTC better than four tools at equal model, tasks, policy, and verification? | Paired pass-rate, cost-per-pass, cache, latency, and duplicate-effect ablation. | Pending. |

The notebook is evidence and executable narrative, not a serialized heap. The minimal
durability path is therefore replay of proven self-contained data cells plus
content-addressed values for everything else; arbitrary pickle or marshal persistence
does not satisfy this checklist.

The local `llmvm` precursor informed two deliberately retained ergonomics: thread-local
variables should be reusable by name, and a model should be able to request a compact
locals inventory or explicitly select a result. Skein keeps those ideas through
metadata-only `agent.state` queries and final-expression egress. It does not copy
`llmvm`'s marshalled-function persistence, process-only object cache, or reinsertion of
full helper results into continuation messages because those weaken portability,
durability, or PTC context isolation.

## 25. Initial acceptance tests

The first end-to-end acceptance fixture should:

1. create a task and workspace;
2. invoke the model with one `python` tool;
3. durably materialize its code as a notebook cell before execution;
4. run the cell with at least three broker calls and local filtering;
5. fail one child call and continue safely;
6. produce one artifact-backed rich image or table output;
7. mutate one file with an idempotent receipt;
8. time out a second operation with effect unknown;
9. checkpoint and kill the CPython worker/server between start and terminal outcome;
10. restart without replaying the mutation, reconcile, and reconstruct the notebook
    and every authoritative view;
11. compact while preserving completed, in-progress, failed, unknown, and next-action
   fields plus a recent exact tail;
12. verify the change independently;
13. build the exact notebook/model/public/audit views and prompt manifest;
14. rebuild a fresh database and deleted notebook from the ledger, then compare all
    projection and notebook hashes.

This one fixture exercises the distinctive architecture rather than only its happy
path.

## 26. Open decisions

These require prototypes or measurements before finalization:

- DuckDB versus SQLite for the local hot ledger under interactive fsync load.
- CPython worker isolation mechanism and supported operating systems.
- Async top-level execution protocol and cancellation semantics.
- Canonical notebook renderer versioning and the minimal nbformat metadata subset.
- Whether explicit external-edit import is sufficient or collaborative Y.js editing
  earns a later implementation.
- Exact artifact representation thresholds for images and other MIME bundles.
- Restricted relational DSL shape and SQL subset.
- Vector projection choice, if lexical/structured retrieval proves insufficient.
- Context-epoch transition thresholds and provider-specific cache behavior.
- Durable named-state formats beyond JSON, Arrow, and Parquet.
- Exact criteria for materialization promotion and retirement.
- Retention and physical-erasure policy for local and DuckLake deployments.
- Stable multi-process architecture: PostgreSQL-only ledger versus PostgreSQL plus
  DuckLake analytical tier.

## 27. Recommended first implementation slice

Implement only:

1. the event, notebook-cell, program, view, and receipt models;
2. a DuckDB ledger adapter behind a test-only interface;
3. import of current `HarnessEvent`, `TraceSpan`, `ToolReceipt`, and `Checkpoint`;
4. deterministic `history.model`, `task.state`, `execution.open`, and synthetic
   `notebook.state` views;
5. a canonical notebook materializer over fixture events, with `nb-cli`
   interoperability and golden-hash tests;
6. replay and projection-equality tests.

Do not execute notebook cells or change the model prompt/tool surface in this slice.
It proves that the ledger can rebuild the primary durable workbench before live PTC
depends on it.

## 28. Source grounding

- `docs/design/pi-inspired-adk-coding-harness.md`
- `docs/architecture.md`
- `docs/security.md`
- `docs/evaluation.md`
- `docs/design/trace-driven-skill-learning.md`
- [Pi coding agent](https://github.com/earendil-works/pi): compaction, sessions,
  extension hooks, and session context
- *From Memory Stores to Memory Programs: A Database-Native Architecture for
  Language Agents*
- Anthropic, [*Advanced Tool Use*](https://www.anthropic.com/engineering/advanced-tool-use)
  and the [Programmatic Tool Calling cookbook](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/programmatic_tool_calling_ptc.ipynb)
- [`llmvm`](https://github.com/9600dev/llmvm): predecessor patterns for interleaved
  Python continuation, named locals, explicit result egress, and bounded locals
  inspection; its serialization and message-reinsertion choices are comparison inputs,
  not adopted durability contracts.
- [`nb-cli`](https://github.com/jupyter-ai-contrib/nb-cli): stable notebook cell
  operations, AI-oriented rendering, rich outputs, externalization, and atomic local
  persistence; its execution backends are interoperability references, not the
  production CPython worker.
- DuckDB documentation: [concurrency](https://duckdb.org/docs/current/connect/concurrency),
  [Parquet](https://duckdb.org/docs/current/guides/file_formats/query_parquet),
  [DuckLake extension](https://duckdb.org/docs/current/core_extensions/ducklake),
  and [attached databases](https://duckdb.org/docs/stable/sql/statements/attach)
- DuckLake 1.0 documentation: [overview](https://ducklake.select/docs/stable/),
  [snapshots](https://ducklake.select/docs/stable/duckdb/usage/snapshots), and
  [files](https://ducklake.select/docs/stable/duckdb/metadata/list_files)
