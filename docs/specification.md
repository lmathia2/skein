# Skein implementation specification

> Version: 1
>
> Updated: 2026-09-07
>
> Scope: current local, single-process Google ADK 2.x implementation

This document specifies what Skein implements and maps each contract to source and
tests. The ADRs explain why:

- [Trace-native harness and notebook PTC](adr/trace-native-harness.md)
- [Context, versioned memory programs, and long sessions](adr/context-and-memory.md)
- [Execution, recovery, and verified completion](adr/execution-and-recovery.md)

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative. “Opt-in” means implemented
but disabled in the default profile. “Not implemented” is outside the runtime
contract regardless of its appearance in historical design material.

## 1. System boundary

Skein MUST run as one ADK application with one coding worker. The server owns local
transport, run identity, replay, cancellation, and lifecycle. The harness owns coding
policy, tools, trace capture, context, state reduction, and verification.

```text
client
  |
WebSocket + AG-UI protocol
  |
run registry / ADK Runner
  |
Skein workflow
  |
coding worker
  +-- default: read, bash, edit, write
  `-- opt-in: python -> capability broker -> same managed operations
```

| Contract | Code owner | Principal tests |
| --- | --- | --- |
| App/factory assembly | [`app/agent/factory.py`](../app/agent/factory.py) | [`test_harness_factory.py`](../tests/unit/test_harness_factory.py), [`test_adk_app.py`](../tests/unit/test_adk_app.py) |
| Workflow and terminal states | [`app/agent/workflow.py`](../app/agent/workflow.py) | [`test_orchestration.py`](../tests/unit/test_orchestration.py), [`test_replay_and_verification.py`](../tests/integration/test_replay_and_verification.py) |
| Server lifecycle | [`harness/server/runtime.py`](../harness/server/runtime.py) | [`test_server_runtime.py`](../tests/unit/test_server_runtime.py), [`test_conversation_runtime.py`](../tests/integration/test_conversation_runtime.py) |
| ADK/provider boundary | [`harness/ai/`](../harness/ai/) | `test_model_*`, `test_codex_responses.py`, `test_openrouter_responses.py` |

The current boundary is trusted local execution. The local adapter MUST NOT be
described as a production sandbox. Docker confines configured commands but does not
isolate every host-side harness primitive. See [security](security.md).

## 2. Configuration and activation

Configuration MUST validate through
[`harness/config/models.py`](../harness/config/models.py). Workspace, task,
state-root, conversation, and trust bindings MUST remain outside stable behavior
configuration.

| Profile/capability | Current status |
| --- | --- |
| [`default.yaml`](../harness/config/default.yaml) / [`four-tool.yaml`](../harness/config/profiles/four-tool.yaml) | Supported default; four tools; canonical memory off |
| [`notebook-ptc-jsonl.yaml`](../harness/config/profiles/notebook-ptc-jsonl.yaml) | Opt-in one-tool PTC and canonical JSONL |
| [`notebook-ptc-duckdb.yaml`](../harness/config/profiles/notebook-ptc-duckdb.yaml) | Opt-in PTC, DuckDB ledger, incremental counts |
| `context-*.yaml` profiles | Opt-in treatments for capture, windows, retrieval, notes, recovery, reuse, prior runs, and continuity |

Unsupported combinations MUST fail rather than silently downgrade. Conversation
notebook continuity requires PTC. Live semantic retrieval requires an explicitly
injected provider.

## 3. Identities

The runtime MUST keep these identities distinct:

| Identity | Purpose |
| --- | --- |
| application/user/session | ADK conversation ownership |
| run/task | task-local event sequence and operational state |
| invocation | one resumable ADK invocation |
| workspace fingerprint | environmental divergence detection |
| notebook | run or explicitly owned conversation workbench |
| cell/attempt/kernel epoch | write-ahead PTC execution and replay decisions |
| tool call/operation/idempotency key | deduplication and effect reconciliation |
| event/source/source ID | canonical historical identity |
| program/execution/result | memory-computation reproducibility |

One identity MUST NOT grant another identity's permissions or imply a global causal
order. Cross-task views MUST carry a source manifest and watermark per task.

## 4. Canonical trace ledger

### 4.1 Event envelope

[`LedgerEvent`](../harness/ledger/models.py) MUST include:

```text
event_id, task_id, sequence
source, source_id, kind
status, effect
observed_at, recorded_at
payload, payload_hash, idempotency_key
optional correlation_id, parent_event_id
```

`payload_hash` MUST use canonical JSON. Event and idempotency identities MUST be
stable. Reuse with different content MUST fail.

Statuses distinguish `observed`, `requested`, `started`, `completed`, `failed`,
`blocked`, `timeout`, and `open`. Effects distinguish `none`, `intended`, `applied`,
`not_applied`, and `unknown`. Notebook appearance MUST NOT override effect evidence.

### 4.2 Captured sources

When canonical memory is enabled,
[`harness/ledger/importers.py`](../harness/ledger/importers.py) imports:

- harness task and notebook events;
- tool receipt transitions and trace spans;
- checkpoints, approvals, and steering;
- model/tool/outcome metrics;
- server run and public events;
- redacted ADK session records.

Import MUST preserve source identity and observed time. Retention MUST NOT make
private model reasoning or secrets model-readable.

### 4.3 Physical backends and migration

[`JsonlLedgerStore`](../harness/ledger/jsonl.py) is dependency-free and uses one
append-only file, process-local locking, canonical validation, idempotency checks,
and `fsync`. Scans are linear in total events.

[`DuckDbLedgerStore`](../harness/ledger/store.py) provides transactional append,
uniqueness constraints, task/kind indexing, and incremental count projections. One
process MUST own writes to a state root.

[`LedgerBackedEventStore`](../harness/ledger/shadow.py) is the migration adapter. It
writes operational task JSONL and imports the event into the canonical ledger; reads
reconstruct ordinary `HarnessEvent` values from canonical evidence. Operational
stores remain compatibility stores until cutover equality is accepted.

Backfill, archive, and erasure live in [`harness/ledger/`](../harness/ledger/).
Sealed Parquet is archival projection. DuckLake, PostgreSQL, distributed leases, and
cloud object storage are not implemented.

## 5. Operational task state

[`HarnessEvent`](../harness/state/events.py) is the task-local operational event.
`task.created` MUST initialize a typed [`TaskLedger`](../harness/models/ledger.py).
Only explicit `ledger.patched`, `task.blocked`, and `task.finished` reductions change
task state. Observational events MUST NOT mutate it implicitly.

Replay MUST sort by sequence, ignore identical event-ID duplicates, reject gaps, and
revalidate the complete typed ledger. The task ledger answers “what should happen
now”; the canonical ledger answers “what happened.”

## 6. Effect broker and tools

All direct and nested operations MUST use adapters assembled by
[`harness/tools/adk_adapter.py`](../harness/tools/adk_adapter.py). Equivalent effects
MUST share workspace confinement, command classification, approvals, operation and
invocation identity, receipts, redaction, output bounds, timeout/cancellation, and
artifact externalization.

The default model sees `read`, `bash`, `edit`, and `write`. Reserved search and memory
commands MAY route within `bash`. PTC sees only `python`; its `agent.fs.*`,
`agent.shell.run`, state metadata, and registered MCP operations use the same broker.

Unknown capabilities MUST fail closed. Network, dependency installation, destructive
commands, and Git-history mutation remain disabled unless explicit policy and
approval permit them.

## 7. Notebook PTC

PTC is assembled in [`app/agent/builders.py`](../app/agent/builders.py), executed by
[`PersistentPythonWorker`](../harness/repl/worker.py), reduced by
[`reduce_notebook`](../harness/notebook/reducer.py), and serialized by
[`materialize_notebook`](../harness/notebook/materializer.py).

### 7.1 Cell execution protocol

For `python(code)`, the harness MUST:

```text
1  resolve task/invocation/notebook identity
2  restore eligible cells once for a new kernel epoch
3  derive stable cell and attempt identity
4  append notebook.cell_added and repl.cell_submitted
5  reduce events and atomically materialize .ipynb
6  execute in persistent CPython through the cell broker
7  record nested intents, receipts, and artifacts
8  externalize oversized rich output
9  append repl.cell_completed | failed | timeout
10 reduce and rematerialize with selected output
11 append notebook.materialized(path, watermark, hash)
12 return a bounded result to the model
```

A repeated completed attempt MUST NOT repeat effects. An interrupted nonterminal
attempt MUST require reconciliation. A Python exception MUST discard the dirty
kernel epoch.

### 7.2 Heap and replay

The heap is warm only for one worker. Only completed self-contained data-construction
cells classified `safe` MAY restore a new kernel. Imports, definitions, calls,
attribute/subscript access, loaded-name dependencies, broker use, failures, and
timeouts MUST NOT restore automatically under the current classifier.

`agent.state.list()` and `agent.state.describe(name)` expose bounded metadata only:
name, type, size, provenance, and replay class. Values stay in the worker until code
explicitly selects them.

### 7.3 Materialization and `nb-cli`

The notebook MUST contain exact code, bounded selected output, Markdown narrative,
attempt/kernel/effect metadata, artifacts, and source watermarks. Equal source events
and watermark MUST produce equal bytes. Clean shutdown MUST rematerialize, store a
content-addressed snapshot, and append an idempotent snapshot event.

`skein notebook` MUST rebuild from events and delegate display to `nb read`. The
agent MAY use `nb read` and `nb search` only through guarded shell execution. Skein
MUST NOT create a second JSON-inspection path; `nb execute` MUST NOT become an
alternate executor.

### 7.4 Cross-session continuity

The default notebook is run-scoped. Conversation continuity MAY combine completed
prior runs only after the server proves the same user, thread, harness, and workspace.
The reducer MUST preserve source-run attribution and task watermarks. A new kernel
restores only the safe subset.

Notebook continuity MUST NOT authorize prior-run ledger retrieval, and prior-run
memory MUST NOT imply notebook continuity.

## 8. Prompt construction

### 8.1 Stable prefix

[`build_static_prefix`](../harness/context/prompt.py) constructs stable instructions.
Volatile task, session, progress, time, and steering data MUST NOT enter it. Before
each model call, the worker MUST compare model, system instruction, tool declarations,
and tool configuration with the first call and fail on mutation. Provider cache keys
cover these stable inputs and output format, not the dynamic packet.

### 8.2 Dynamic packet

The live workflow builds bounded sections in this order:

```text
TASK
CONVERSATION
SELECTED SKILLS
REPOSITORY MANIFEST
COMPACTED HISTORY
RECENT EVENTS
USER STEERING
```

Section and total budgets are in
[`SkeinWorkflowDependencies`](../app/agent/workflow.py). Project instructions and
skills require explicit trust. Recent events MUST be redacted and bounded.

[`harness/memory/prompt.py`](../harness/memory/prompt.py) defines reproducible memory
components:

```text
P0 stable prefix
P1 history watermark
P2 task.progress + task.memory
P3 current query + recent exact tail
```

Each component carries a content hash and source view IDs. The manifest carries the
context epoch, task, watermark, and prompt hash.

## 9. Memory programs

### 9.1 Request and result

[`ViewRequest` and `ViewResult`](../harness/memory/models.py) MUST bind:

```text
program/version + parameters + authorized sources + watermark/time/filters
+ retrieval policy + scan/time/output budgets
    ->
bounded data + status/completeness + applied watermark + evidence IDs
+ source manifest + program/execution/result hashes
```

Incomplete aggregates MUST NOT be labeled complete. Pagination MUST bind original
parameters, program version, and source manifest. Later appends MUST NOT change an
existing cursor.

### 9.2 Exposure

[`project`](../harness/memory/context.py) MUST apply an explicit event-kind/field
allowlist and redaction. Exact reads expose exact bytes of that public projection,
not raw retained payloads. Artifact reads require an exposed matching reference and
bounded byte range.

### 9.3 Implemented programs

| Program | Meaning | Execution |
| --- | --- | --- |
| `history.model` | model-readable history | Python reducer |
| `task.progress` | completed, open, failed work | Python reducer |
| `execution.open` | open and unknown effects | Python reducer |
| `time.state` | observed time range | Python reducer |
| `task.memory` | lexical/optional semantic recall | Python + optional Lance |
| `dream.analysis` | advisory patterns | reviewed reusable Python |
| `history.page` | frozen bounded page | context runtime |
| `event.read` | exact event/UTF-8 byte range | context runtime |
| `artifact.read` | authorized artifact range | context runtime |
| `events.count` | complete kind/status counts | scan or DuckDB projection |
| `failures.by_kind` | reviewed failure aggregate | scan or DuckDB projection |

[`ContextProgramService`](../harness/tools/memory.py) owns the public command route.
The model requests logical names such as `memory query --program events.count`; it
MUST NOT discover tables or select physical backends.

### 9.4 Versioned Python and SQL

Python programs in [`harness/memory/runtime.py`](../harness/memory/runtime.py) use an
explicit `(name, version)` dispatch table. Reusable code MUST be reviewed, typed,
tested, and pinned. Notebook code MUST NOT auto-promote.

SQL programs in [`harness/memory/catalog.py`](../harness/memory/catalog.py) preserve
exact source and follow `candidate -> shadow -> active -> retired`. Only active
programs MAY serve retrieval. Execution MUST run in a killable isolated DuckDB
process with authorized task data, external access disabled, and scan, row, memory,
byte, and time bounds.

### 9.5 Caching and projections

Ordinary deterministic Python and SQL views currently recompute; no general result
cache exists. [`SummaryCache`](../harness/memory/summary.py) MAY reuse an advisory
model output only when source view, prompt, model, settings, and source availability
match.

DuckDB transactionally maintains `ledger_event_counts` by task/source/kind/status
and `ledger_stream_heads` with watermark and stream hash. Current unfiltered
`events.count@1` and `failures.by_kind@1` use these rows. Temporal, filtered,
searched, cross-ledger, and historical requests MUST use bounded canonical evidence.
Projection mismatch triggers deterministic rebuild from `ledger_events`.

### 9.6 Semantic memory

[`LanceMemorySearch`](../harness/memory/lance.py) builds optional immutable,
content-addressed projections. Identity MUST include source events and embedding
version. Search MUST return canonical IDs and hydrate exposed rows from canonical
evidence. Semantic rank is not truth. No embedder MAY be silently selected/downloaded.

## 10. Long-context transitions

[`ContextWindowPlugin`](../harness/adk/context.py) implements opt-in window
management. Before changing active context, it MUST persist a context epoch and a
handoff prioritizing task/control state, unresolved effects, steering, history
boundary, note metadata/excerpt, and PTC state. An unconsumed tool result MUST remain.

`handoff_tail` keeps the handoff plus a recent tail. `fresh` omits older conversation
while preserving control state and retrieval entry points. A transition MUST NOT
create a task, permission scope, budget, or heap. It MUST refuse if required state
cannot fit or required notes cannot publish.

Prior-run recall requires owned conversation sources or explicitly selected completed
runs in the same workspace. Source tasks and watermarks MUST be frozen. A shared path
alone is not authorization.

## 11. Checkpoints and recovery

[`CheckpointStore`](../harness/state/checkpoints.py) and
[`validate_recovery_evidence`](../harness/state/recovery.py) implement same-machine
recovery. A checkpoint MUST bind invocation/session/task identity, event and receipt
evidence, reducer/context state, workspace fingerprint, and relevant budget/notebook
boundaries. Referenced evidence MUST precede checkpoint publication.

Safe-auto recovery in [`harness/server/runtime.py`](../harness/server/runtime.py)
MUST validate unchanged behavior/ownership, initialized Git identity, original ADK
invocation, published checkpoint, operational/canonical event equality, canonical
receipts, context epoch, remaining budget, and approval state.

Missing evidence, divergence, exhaustion, or unreconciled effects MUST block. Recovery
does not restore arbitrary files, preserve the old Python process, or guarantee
exactly-once external execution.

## 12. Verification and completion

[`harness/verification/`](../harness/verification/) owns discovery, execution, scope,
and reports. Verification MUST use the same workspace, sandbox, policy, approvals,
redaction, and task ID as tools.

Completion requires acceptance-criterion evidence, required checks, no new baseline
regression, no scope violation, and no unresolved effect. Model claims and self-authored
tests are supporting evidence only. Failed verification MUST become a durable
counterexample for a later work packet.

## 13. Steering and observability

[`SteeringQueue`](../harness/state/steering.py) durably queues user changes. Delivery
MAY occur only at configured safe points. Cancellation MUST propagate through owned
boundaries; every started operation MUST end terminal or unknown.

[`harness/tracing/`](../harness/tracing/) defaults to bounded metadata. Metrics SHOULD
separate cached/uncached input, output, reasoning, tools, verification, wall time,
retries, and outcomes. Deterministic tests MUST NOT be presented as live model quality.

## 14. End-to-end example

This simulated sequence uses implemented events and views:

```text
1 User: "Fix retry timeout; preserve exponential backoff."

2 Persist task.created(seq=1) and message.recorded(seq=2).

3 Build prompt at watermark 2:
    P0 stable instruction/tools
    P1 history watermark
    P2 task.progress@1 + task.memory@1
    P3 current query + recent events 1..2

4 Model calls:
    python("source = agent.fs.read(...); test = agent.shell.run(...)")

5 Before Python runs:
    notebook.cell_added(seq=3)
    repl.cell_submitted(seq=4)
    materialize notebook at watermark 4

6 During Python:
    persist read and bash intents/receipts
    return bounded text; spill large bodies to artifacts

7 After Python:
    append repl.cell_completed
    rematerialize notebook with selected output
    append notebook.materialized(path, watermark, sha256)

8 Agent writes memory.note@1 citing the failure, edits code, requests verification.

9 Verifier records passed=false. Next prompt derives failed verification,
  completed edit, relevant note/evidence, and recent exact tail.

10 Near the context limit, persist a context epoch and handoff containing goal,
   constraints, unresolved effects, verification state, note reference, and tail.
   Full evidence remains in the ledger/artifacts.

11 After restart or in an authorized later run:
   replay task state; rebuild notebook at frozen watermarks; restore only safe data
   cells into a new heap; retrieve prior facts through versioned programs; block
   unknown effects; finish only after independent verification passes.
```

## 15. Test mapping

| Area | Regression suites |
| --- | --- |
| Canonical parity/import/incremental counts | `test_canonical_ledger.py`, `test_context_programs.py` |
| Backfill/archive/erasure | `test_ledger_backfill.py`, `test_ledger_archive.py`, `test_ledger_erasure.py`, `test_context_erasure.py` |
| Programs/SQL/semantic/summaries | `test_memory_programs.py`, `test_memory_program_catalog.py`, `test_context_semantic.py`, `test_lance_memory.py`, `test_memory_summary.py` |
| Prefix/context transitions | `test_harness_factory.py`, `test_codex_responses.py`, `test_context_windows.py`, `test_workflow_compaction.py` |
| REPL/notebook/write-ahead | `test_repl.py`, `test_notebook.py`, `test_notebook_ptc_integration.py` |
| Tools/policy/receipts/artifacts | `test_environment_and_tools.py`, `test_managed_adapter.py`, `test_approvals.py`, `test_tool_artifact_plugin.py` |
| Recovery/verification | `test_context_recovery.py`, `test_context_resume.py`, `test_replay_and_verification.py`, `test_verification.py` |
| Ownership/server/conversation | `test_server_runtime.py`, `test_server_run_registry.py`, `test_conversation_runtime.py`, `test_conversation_contract.py` |

Contract changes MUST add or update the smallest deterministic test that would fail
if replay, identity, authority, exposure, or verification broke. Tests MUST NOT assert
natural-language model output.

## 16. Explicit non-capabilities

Skein currently does not claim production isolation for hostile Python, multi-process
writers, distributed/cloud persistence, arbitrary model-authored program activation,
automatic program evolution or global consolidation, an always-on embedder, general
view-result caching, unrestricted heap restore, effectful notebook replay, workspace
restore from checkpoints, exactly-once external effects, or superiority of an opt-in
treatment before its controlled live ablation passes.

