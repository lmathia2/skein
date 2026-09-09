# Trace-native harness and composable PTC

> Status: accepted architecture; composable PTC and canonical memory remain opt-in
>
> Updated: 2026-09-09

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. One append-only canonical ledger is the historical source of truth.
2. The target model-facing interface is one `execute_code` tool; the current
   default remains `read`, `bash`, `edit`, and `write` until the PTC ablation passes.
3. PTC execution, durable serialization, runtime-state recovery, and memory programs
   are independent configuration axes with code-owned registries. A model-facing tool
   name or document format must not select the other axes implicitly.
4. Brokered PTC composes capabilities through the same host-owned effect broker. A
   runtime with native OS access is a distinct, explicit trust profile and cannot claim
   broker enforcement for those effects.
5. JSONL means the existing canonical ledger without another writer. A notebook is an
   optional rebuildable projection of the same lifecycle events. Neither representation
   is the live Python heap.
6. Runtime-state recovery is explicit: `none`, conservative event replay, or bounded
   runtime snapshots. A serializer never decides which recovery policy applies.
7. Every attempted operation is evidence, including failure, timeout, cancellation,
   blocking, and an unknown external effect.
8. Memory is selected by exact program name and version over authorized evidence.
   Configuration may select registered programs and bounded parameters, never import
   paths or arbitrary executable source.
9. Deterministic code owns policy, budgets, recovery, verification, and completion.

## Architecture

```text
user / TUI / API
       |
       v
ADK Runner and Skein workflow
       |
       +---- default worker: read | bash | edit | write
       |
       `---- PTC tool: execute_code
                         |
                  PTC session coordinator
                    |       |       |
                 runtime  state   serializer
                    |       |       |
                    +-------+-------+
                            |
                  guarded capability broker
                   |       |       |
                 files    shell    MCP
                   `-------+-------'
                           |
                    intents + receipts
                           v
                 canonical event ledger
                    |       |       |
                 reducers  views  optional notebook
```

ADK supplies the runner, session service, streaming, provider integration, caching,
and resumability. Skein supplies the coding loop, tool policy, state reduction,
evidence capture, context construction, and independent verification.

## Configuration model

The current `NotebookPtcConfig.implementation` field is a transitional bundle. The
target shape names each independent decision:

```yaml
ptc:
  enabled: true
  execution:
    implementation: skein_repl       # skein_repl | adk_code_mode | prime_repl
    default_timeout_seconds: 120
    max_timeout_seconds: 600
    max_output_bytes: 16000
    capability_access: brokered       # brokered | native
  persistence:
    serialization: notebook           # notebook | jsonl
    state: replay_safe                 # none | replay_safe | snapshot
    continuity: run                    # run | conversation
  batching:
    no_progress_cells: 24
    max_cells: 48
    max_parallel_reads: 4

memory:
  enabled: true
  ledger: jsonl                        # jsonl | duckdb
  programs:
    - name: task.progress
      version: "1"
      mode: active                     # shadow | active
    - name: task.memory
      version: "1"
      mode: active
      parameters:
        retrieval: lexical
```

This is a closed composition, not dependency injection from YAML. Each key resolves
through a code-owned registry to a typed implementation. Configuration validation
rejects an unknown key/version, unknown parameter, missing optional dependency, or
unsupported combination before the ADK app is assembled.

The initial compatibility mapping is deterministic:

| Legacy PTC value | Execution | Serialization | State | Continuity |
| --- | --- | --- | --- | --- |
| `skein_notebook` | `skein_repl` | `notebook` | `replay_safe` | existing value |
| `adk_code_mode` | `adk_code_mode` | `jsonl` | `none` | `run` |

Legacy input may be normalized at the configuration boundary for one migration
window. The normalized configuration, not the legacy spelling, contributes to the
behavior hash. New profiles use only the decomposed shape.

### Supported combination rules

Implementation-native presets are the first supported configurations. Selecting an
implementation preserves its own execution, persistence, and error behavior; cross
combinations become selectable only when their adapters and contract tests exist.
Known but unsupported combinations raise `NotImplementedError` during configuration
loading. Unknown enum values, invalid budgets, and missing required settings remain
validation errors. There is no fallback to another runtime or persistence policy.

The transitional `notebook_ptc` schema accepts `serialization: native` and
`state: native` by default. Skein additionally accepts its explicit `notebook` and
`replay_safe` pairing. ADK Code Mode accepts native ADK-managed history and `none`;
it does not yet implement a selectable JSONL session serializer. Prime's native pairing
is JSONL transcript plus runtime snapshots, gated by explicit native execution and
project trust. Its first adapter supports run continuity only. Conversation snapshot
lineage, safe-auto effect recovery, and the brokered memory-command bridge raise
`NotImplementedError`. The matrix below is a delivery
target, not a promise that all combinations are available now.

- `adk_code_mode` is turn-scoped and initially supports `state: none` and
  `continuity: run` only.
- `replay_safe` requires a persistent runtime and canonical cell lifecycle events.
- `snapshot` requires a runtime that implements bounded snapshot/restore and records
  snapshot manifests. It does not make opaque heap bytes historical evidence.
- `serialization: notebook` requires a registered deterministic notebook projector.
- `serialization: jsonl` adds no serializer or duplicate file: the canonical ledger
  already is the durable representation.
- `capability_access: native` is allowed only by an explicit trusted execution profile.
  Its direct Python effects are not described as brokered, idempotent, or recoverable.
- Conversation continuity requires server-owned conversation/workspace identity and a
  state policy that supports cross-run restore.

## Component contracts

### Required modular boundaries

Every implementation in a row uses that module's common typed contract. Configuration
may select the replaceable behavior; it may not disable or replace the invariant.

| Module | Replaceable implementations | Host-owned invariant |
| --- | --- | --- |
| Environment lifecycle | Local process, fresh container, reusable evaluation worker | Exclusive ownership, clean setup/reset, cleanup verification |
| PTC serialization | Notebook document, ledger-native JSONL representation | Stable cell identities and ledger provenance; serialization never executes code |
| Runtime-state policy | Fresh state, safe replay, bounded snapshot | Explicit restore eligibility; uncertain effects are never automatically replayed |
| Memory programs | Versioned history, progress, retrieval, summary, handoff | Authorized sources, watermarks, budgets, evidence and result hashes |
| Context assembly | Recent history, handoff-plus-tail, fresh reconstruction, Pi compaction | Stable prefix, bounded dynamic suffix, complete tool-call/result boundaries |
| Result presentation | Compact text, structured data, images, artifact references | Redaction, output bounds and links to authoritative evidence |

Turning a replaceable module off selects its identity behavior; it does not bypass an
invariant. PTC without memory still records its owned execution lifecycle and receives
normal bounded ADK history, but has no historical memory-query bridge. Memory without
PTC derives views over direct-tool and task events. Context window management without
trace-native memory is invalid because it cannot reconstruct an evidence-backed suffix.
Serialization and runtime-state recovery remain independent of memory visibility.

The support matrix is closed: a combination exists only when assembly validation and
contract tests cover it. Known unavailable combinations raise `NotImplementedError`;
unknown values fail schema validation; no module silently falls back to another
implementation. Safety, redaction, authorization, trace authority and independent
completion verification are invariants rather than selectable modules.

The smallest useful split is one coordinator and three narrow implementation
contracts. These are behavioral interfaces; exact Python names may change during
implementation.

```python
class PtcRuntime(Protocol):
    async def execute(self, request: CellRequest) -> CellResult: ...
    async def reset(self) -> RuntimeEpoch: ...
    async def close(self) -> None: ...

class PtcStatePolicy(Protocol):
    async def restore(self, runtime, history) -> RestoreResult: ...
    async def after_cell(self, runtime, result) -> StateReceipt: ...
    async def close(self, runtime) -> StateReceipt: ...

class PtcSerializer(Protocol):
    def prepare(self, events, destination) -> SerializationResult: ...
    def materialize(self, events, destination) -> SerializationResult: ...
```

`CellRequest` contains stable task, invocation, tool-call, cell, attempt, work-batch,
and runtime-epoch identities; exact code; deadline; and a capability broker reference.
`CellResult` contains a terminal status, bounded stdout/stderr/value/display data,
runtime epoch, state metadata, effect classification, duration, and artifact references.
It never declares task completion.

`PtcSession` owns ordering and is the only caller used by `execute_code`. Runtimes do
not append ledger events or materialize documents. State policies do not authorize
effects. Serializers do not execute or restore code. This prevents a new runtime or
document format from becoming a second authority path.

## One trace, four representations

This simulated trace follows the implemented event shapes. The agent submits:

```python
result = agent.shell.run("pytest -q")
```

The important persisted facts are conceptually:

```jsonl
{"kind":"repl.cell_submitted","status":"started","payload":{"cell_id":"c7","attempt_id":"a1","kernel_epoch":"k2"}}
{"kind":"tool.bash","status":"started","effect":"intended","payload":{"tool_call_id":"t9","command":"pytest -q"}}
{"kind":"tool.bash","status":"completed","effect":"applied","payload":{"tool_call_id":"t9","result_hash":"..."}}
{"kind":"repl.cell_completed","status":"completed","payload":{"cell_id":"c7","attempt_id":"a1","stdout":"12 passed"}}
{"kind":"verification.completed","status":"completed","payload":{"passed":true}}
```

The exact records include stable IDs, task-local sequence, source/source ID,
observed and recorded times, correlation/parent IDs, payload hash, and idempotency
key. Large output is artifact-backed; the trace retains bounded metadata and hashes.

Those events serve different representations:

| Representation | Question answered | Authority |
| --- | --- | --- |
| Canonical ledger | What was observed or attempted? | Historical authority |
| Task state reducer | What is the current phase, goal, and blocker set? | Deterministic control projection |
| PTC serialization | What durable code/output document was selected? | Ledger-only JSONL or rebuildable notebook |
| Runtime-state artifact | Which opaque values may a compatible runtime restore? | Bounded recovery aid, never historical authority |
| Live runtime heap | Which values are live in this runtime epoch? | Disposable runtime state |

JSONL and DuckDB implement the same canonical event contract. Existing per-task
JSONL and SQLite stores remain operational compatibility stores during migration.
When canonical memory is enabled, `LedgerBackedEventStore` writes both and can prove
byte-equal task-event reconstruction from the canonical ledger. They must not evolve
into competing sources of truth.

## Execution protocol

For each `execute_code` call, `PtcSession` performs this sequence:

1. Resolve and validate the configured runtime, state policy, serializer, and memory
   program versions before model execution.
2. Derive stable cell and attempt identities from the ADK invocation and function-call
   identity; read the current runtime epoch.
3. Reconcile an earlier attempt with the same identity. Return its completed receipt
   without re-executing, or block when its outcome/effect remains unknown.
4. Ask the state policy to restore the new runtime epoch from its declared evidence or
   snapshot manifest. Persist a bounded restore receipt.
5. Append `ptc.cell_submitted` with the exact source hash and identities before running
   code. Invoke `serializer.prepare`; notebook mode materializes that started cell and
   JSONL mode confirms the ledger watermark. Preparation failure appends a blocked
   terminal event and prevents execution.
6. Execute through the runtime under the configured cell deadline. Every brokered
   nested operation separately records authorization, intent, and terminal receipt.
7. Classify the terminal result as completed, failed, timed out, cancelled, blocked, or
   effect-unknown. Persist it before publishing model-visible output.
8. Invoke `state.after_cell`. Conservative replay may discard a dirty epoch; snapshot
   policy may publish a bounded snapshot manifest; `none` does nothing.
9. Invoke the selected serializer. Notebook mode atomically rebuilds at the resulting
   ledger watermark; JSONL mode returns the canonical stream identity without writing.
10. Return one bounded `execute_code` result. The outer deterministic workflow retains
    sole authority over verification and completion.

The terminal event is authoritative even if post-execution state persistence or document
projection subsequently fails. Such a failure appends its own failure event and blocks
or degrades recovery according to policy; it must not rewrite the execution outcome.

### Model-visible results and tool accounting

All PTC implementations project their richer terminal receipt into the same bounded
model envelope:

```json
{
  "status": "ok",
  "model_text": "12 passed",
  "artifact_uris": ["artifact://sha256/..."],
  "result_hash": "...",
  "effect": "observed",
  "attempt_id": "..."
}
```

Only applicable non-default fields are emitted. The envelope may additionally include
`exit_code`, `truncated`, `omitted_bytes`, `replayed`, and
`reconciliation_required`. It never repeats stdout, stderr, display bundles, notebook
paths, runtime epochs, or internal state deltas already represented by `model_text`,
artifacts, or the durable terminal event. The result hash identifies semantic output
and is stable across replay and runtime-epoch changes. Full receipts remain in the
canonical trace; bounded reads recover them when authorized.

Images and other rich MIME results are content-addressed before presentation. Text may
remain inline within the output budget; binary or oversized results become
`artifact://sha256/...` references. Existing managed artifact reads enforce confinement,
redaction, and byte limits, so a PTC implementation cannot create a second unbounded
result channel.

`tools.usage@1` is the code-owned trace-memory view for accounting. It reports bounded
counts by name and terminal status for top-level calls and nested PTC capabilities,
plus model-visible and omitted bytes. ADK nested tool metrics share the enclosing
invocation identity; brokered notebook capabilities have explicit capability receipts.
Prime native Python effects cannot be reconstructed as individual tool calls and are
therefore counted only as `native_untracked_cells`, never mislabelled as brokered usage.
The view accepts exact status and name-query filters and exposes hashes and aggregates,
not raw arguments or full results.

Existing histories are not rewritten. During the migration, reducers accept the current
`notebook.cell_added` plus `repl.cell_submitted` pair and the normalized
`ptc.cell_submitted` event. New writers emit only the normalized lifecycle vocabulary
after replay-equality tests prove identical reduced state. A schema/program version in
the receipt identifies which vocabulary was reduced.

### State policies

`none` makes no continuity claim. A runtime may remain warm during its natural lifetime,
but restart begins with an empty namespace.

`replay_safe` preserves current Skein behavior. A failed cell discards the dirty runtime
epoch. Restart executes only completed, self-contained data-construction cells classified
as safe. Imports, definitions, calls, dependent expressions, and broker effects are not
silently replayed.

`snapshot` preserves Prime-style heap continuity. The runtime serializes names
independently under total and per-value byte caps, writes payload and manifest atomically,
and reports skipped values. Restore is valid only for the same owner, workspace,
runtime implementation/version, and compatible Python environment. Snapshot bytes are
an opaque recovery artifact: they do not prove how a value was produced, whether an
external effect completed, or that the current environment is semantically equivalent.
Unknown effects still block retry or completion.

## Notebook contract

The `.ipynb` is continuously regenerated from events during execution:

```text
model calls execute_code(code)
        |
        v
choose notebook ID + cell/attempt/kernel IDs
        |
restore prior replay-safe cells if this is a new kernel
        |
append ptc.cell_submitted
        |
reduce events ----------> atomically materialize .ipynb before execution
        |
execute code in persistent CPython
        |
nested agent.* calls ----> broker intents/receipts/artifacts
        |
append repl.cell_completed | failed | timeout
        |
reduce events ----------> atomically rematerialize .ipynb with selected output
        |
append notebook.materialized(path, watermark, content hash)
```

This write-ahead order means an interrupted cell remains visible as started and
requires reconciliation; it cannot disappear merely because Python died. The
notebook reducer creates:

- Markdown cells for the task request, user/assistant messages, steering, working
  notes, and compaction handoffs;
- code cells containing exact submitted Python plus attempt, effect, artifact,
  source-event, watermark, and kernel metadata;
- selected bounded stdout, stderr, display data, and exceptions.

The same events at the same watermark produce byte-stable `.ipynb` output. Clean
shutdown rematerializes the complete notebook, stores its bytes as a content-addressed
artifact, and appends one idempotent `notebook.snapshotted` event containing the
artifact URI, content hash, source watermark, and last kernel epoch.

### How `nb-cli` participates

`nb-cli` reads the projection; it never executes it and never owns persistence:

```text
skein notebook --state-root RUN_STATE --task-id RUN_ID
       |
       +--> read task events
       +--> reduce + rematerialize canonical .ipynb
       `--> nb read NOTEBOOK --no-output [--cell-index N]

inside PTC:
agent.shell.run("nb read path/to/notebook.ipynb --no-output")
agent.shell.run("nb search path/to/notebook.ipynb PATTERN")
```

When notebook serialization is selected, the worker instruction tells the model to use `nb read`/`nb search`
through the guarded shell capability and never parse notebook JSON directly. This
keeps one interoperability path for humans and agents. `nb execute` is deliberately
excluded: executing a document would bypass the cell identity, write-ahead event,
broker, receipt, failure, and recovery contracts.

### Long-running and cross-session use

Run continuity and conversation continuity are distinct:

```text
run-scoped (default)                    conversation-scoped (opt-in)

run A -> notebook A                    run A events ----+
restart A -> rebuild A                                  |
                                                    shared notebook
run B -> notebook B                    run B events ----+
                                       (same owner/thread/workspace only)
```

Within one run, the persistent worker keeps values warm. After a process restart,
the notebook supplies safe code provenance and the reducer identifies completed
self-contained data cells that may rebuild a new heap. The old heap itself is gone.

With `continuity: conversation`, the server selects completed prior runs from the
same owned thread and workspace, freezes their order and task-local watermarks, and
reduces their cells into a shared conversation notebook. Cells retain their source
run attribution. A new kernel may restore only the same conservative replay-safe
subset; effectful, failed, dependent, import, definition, and call cells remain
historical context rather than executable restore instructions.

Notebook continuity does not automatically authorize prior-run memory queries.
Conversely, prior-run ledger retrieval does not require sharing a notebook. They solve
different problems:

| Mechanism | Long-running purpose |
| --- | --- |
| Notebook | Preserve exact code/narrative organization and safe data-cell provenance |
| Canonical ledger | Preserve every attempt, receipt, correction, and historical boundary |
| Working note/handoff | Preserve a small statement of current intent across context epochs |
| Memory programs | Recover selected facts from current or explicitly authorized prior runs |
| Artifact store | Preserve large outputs and immutable notebook snapshots outside the prompt |

Together these allow a later session to open the workbench, retrieve exact historical
evidence, and reconstruct only safe computational state without replaying the entire
conversation or trusting stale notebook output.

## Versioned memory selection

Memory configuration selects exact programs independently of PTC execution,
serialization, and state recovery. The program contract and evidence semantics remain
defined in [Context, versioned memory programs, and long sessions](context-and-memory.md).

At assembly, the registry resolves every `(name, version)` and validates its typed
parameters, evidence sources, temporal policy, and budgets. At execution, the request
adds authorized task scope, ledger watermark, and clock boundary. The receipt records
program, execution, and result identities plus evidence addresses. Only configured
`active` programs may contribute model-visible context; `shadow` programs execute and
record receipts without changing the prompt.

The `pi` mechanism remains a context-compaction strategy over ADK session history. It
must not be registered as a trace-native memory program because it does not satisfy the
same deterministic evidence contract. Context compaction and memory-program selection
remain separate configuration axes.

## Implementation and migration plan

Each step is independently testable and lands in its own commit:

1. **Extract runtime:** adapt `PersistentPythonWorker` and vendored ADK Code Mode to a
   shared `PtcRuntime`; keep generated tool declarations and behavior hashes unchanged.
2. **Extract coordinator:** move `execute_code` lifecycle logic from
   `build_coding_worker` into `PtcSession`; prove existing notebook PTC event bytes,
   receipts, outputs, and failure behavior remain equal.
3. **Extract serializer and state policy:** adapt the current notebook reducer and safe
   replay logic; add ledger-only JSONL and `none` implementations without another store.
4. **Normalize configuration:** introduce the decomposed schema and compatibility
   translation; update standard profiles and reject invalid combinations at load time.
5. **Register memory programs:** replace implementation-wide selection with exact
   code-owned `(name, version)` resolution while retaining current program hashes and
   result bytes.
6. **Add Prime runtime and snapshots:** vendor the minimum MIT-licensed REPL source at a
   pinned upstream commit, implement its host protocol adapter, and keep native OS access
   in a separate explicit trust profile.
7. **Run the matrix:** compare supported runtime/serializer/state combinations with the
   same model, tasks, budgets, broker, and verifier before changing any default.

Required deterministic matrix:

| Runtime | Serialization | State | Required assertion |
| --- | --- | --- | --- |
| `skein_repl` | `notebook` | `replay_safe` | Current behavior remains byte-equivalent |
| `skein_repl` | `jsonl` | `replay_safe` | No notebook file or duplicate JSONL writer |
| `adk_code_mode` | `jsonl` | `none` | Turn-scoped execution remains brokered |
| `prime_repl` | `jsonl` | `snapshot` | Serializable names restore within bounds |
| `prime_repl` | `notebook` | `snapshot` | Document choice does not change execution result |

For identical brokered programs, changing only serialization must preserve runtime
result, operation identities, authorization, receipts, workspace result, and
verification outcome. Changing only memory programs must not change tool declarations,
execution authorization, or effect semantics. The live comparison reports pass rate,
cost per pass, tokens, cache reads, model/tool/verification time, cell and retry counts,
duplicate or unknown effects, snapshot bytes/failures, and terminal reason.

## Implemented boundary

- Four tools are the default profile.
- `execute_code` is the shared model-facing name for the current Skein notebook and
  vendored ADK Code Mode implementations.
- Runtime, serialization, and state policy are not yet independently composed; the
  current `NotebookPtcConfig.implementation` remains the compatibility bundle until the
  migration above lands.
- Notebook PTC is implemented and disabled by default.
- PTC currently supports the trusted local adapter; this source guard is not a
  production security sandbox.
- Registered MCP calls, direct tools, PTC calls, and verification share policy,
  receipts, redaction, output limits, and task identity.
- Canonical JSONL and DuckDB ledgers are implemented and optional.
- Physical task erasure is an explicit operator action and removes its recognized
  projections; append-only means normal writes do not rewrite history, not that
  retention policy can never delete data.

## Rejected alternatives

- A notebook as the audit log: document order and output presence do not prove
  historical order or effect completion.
- Treating a heap snapshot as history or effect evidence: opaque serialization is only
  an optional bounded recovery aid and cannot reconcile effects.
- Making document format select runtime or recovery: `.ipynb` versus ledger-only JSONL
  is a projection choice, not an execution policy.
- Loading implementations or program source from YAML: configuration selects only
  typed, code-owned registry entries.
- A second unguarded Python tool stack: two authority paths make replay unreliable.
- Model-declared completion: a claim is evidence for the verifier, not a state change.
