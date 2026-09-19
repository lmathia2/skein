# Trace-native harness and composable PTC

> Status: historical implementation record. The current core architecture is
> [Skein architecture design](skein_architecture_design.md).
>
> Updated: 2026-09-12

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. When trace-native memory is enabled, one append-only canonical ledger is the
   historical source of truth. The default memory-off profile retains its operational
   compatibility stores without claiming a cross-module canonical trace.
2. The target model-facing interface is one `execute_code` tool; the current
   default remains `read`, `bash`, `edit`, and `write` until the PTC ablation passes.
3. PTC execution, durable serialization, runtime-state recovery, and memory programs
   are separate architectural responsibilities. Memory programs are independently
   selected today; PTC execution, serialization, and recovery currently resolve as
   validated native bundles behind one session seam. A future split must not let a
   model-facing tool name or document format select another responsibility implicitly.
4. Brokered PTC composes capabilities through the same host-owned effect broker. A
   runtime with native OS access is a distinct, explicit trust profile and cannot claim
   broker enforcement for those effects.
5. JSONL persistence means lifecycle events already owned by the task event stream; it
   does not add a runtime-specific transcript writer. When canonical memory is enabled,
   those events are also represented in its JSONL or DuckDB ledger. A notebook is an
   optional rebuildable projection. None of these representations is the live Python heap.
6. Runtime-state recovery is explicit: `none`, conservative event replay, or bounded
   runtime snapshots. A serializer never decides which recovery policy applies.
7. Every attempted operation is evidence, including failure, timeout, cancellation,
   blocking, and an unknown external effect.
8. Memory is selected by exact program name and version over authorized evidence.
   Configuration may select registered programs and shared resource bounds, never
   import paths or arbitrary executable source.
9. Deterministic code owns policy, budgets, recovery, verification, and completion.
10. PTC continuity is an evidence-to-finding-to-reuse contract, not a promise that a
    warm interpreter alone prevents rediscovery. The accepted
    [continuity plan](../design/ptc-memory-continuity-plan.md) assigns live-value
    tracking to PTC, durable findings/retrieval to memory, and prompt placement to
    context assembly; those additions must pass their lifecycle gates before promotion.

## Architecture

The diagram shows the checked-in assembly seam. The runtime/state/serialization split
inside each session is still a target extraction, not a current plugin graph.

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
                  closed session dispatch
                         |
                   Skein notebook
                         |
               guarded capability broker
                 |       |       |
               files    shell    MCP
                 `-------+-------'
                         |
                  intents + receipts
                         v
             operational events and optional
                  canonical event ledger
                 |       |       |
              reducers  views  notebook projection
```

ADK supplies the runner, session service, streaming, provider integration, caching,
and resumability. Skein supplies the coding loop, tool policy, state reduction,
evidence capture, context construction, and independent verification.

## Configuration model

The checked-in schema is the source of truth. It exposes one closed PTC implementation
selector plus persistence fields that validate supported native pairings:

```yaml
notebook_ptc:
  enabled: true
  implementation: skein_notebook
  serialization: native              # native or the implementation's explicit format
  state: native                      # native or the implementation's explicit policy
  continuity: run                    # run | conversation when supported
  default_timeout_seconds: 120
  max_timeout_seconds: 600
  max_output_bytes: 16000
  no_progress_cells_per_batch: 24
  max_cells_per_batch: 48
  max_parallel_reads: 4

memory:
  enabled: true
  implementation: trace_native       # trace_native | pi
  ledger: jsonl                       # jsonl | duckdb
  context_programs:
    mode: active                      # off | shadow | active
    programs:                         # omitted means all allowed reviewed programs
      history.page: 1
      tools.usage: 1
```

This is a closed composition, not dependency injection from YAML. Configuration
validation rejects unknown values and unsupported combinations before the ADK app is
assembled. PTC implementations resolve through `select_ptc_session`; memory programs
resolve by exact `(name, version)` through `PROGRAM_REGISTRY`. The current program
configuration has one mode and shared budgets; it does not accept per-program source,
parameters, import paths, or executable code.

The initial compatibility mapping is deterministic:

| PTC value | Execution | Serialization | State | Continuity |
| --- | --- | --- | --- | --- |
| `skein_notebook` | `skein_repl` | `notebook` | `replay_safe` | existing value |

`serialization: native` and `state: native` preserve these pairings. Their explicit
equivalents are accepted where implemented. The complete validated configuration,
including the selected implementation and persistence fields, contributes to the
behavior hash.

### Supported combination rules

Implementation-native presets are the first supported configurations. Selecting an
implementation preserves its own execution, persistence, and error behavior; cross
combinations become selectable only when their adapters and contract tests exist.
Known but unsupported combinations raise `NotImplementedError` during configuration
loading. Unknown enum values, invalid budgets, and missing required settings remain
validation errors. There is no fallback to another runtime or persistence policy.

The transitional `notebook_ptc` schema accepts `serialization: native` and
`state: native` by default. Skein additionally accepts explicit `notebook` serialization
with `replay_safe` or experimental `snapshot` state. The matrix below is a delivery
target, not a promise that all combinations are available now.

- `replay_safe` requires a persistent runtime and canonical cell lifecycle events.
- The current `snapshot` policy is bounded pre-cell rollback within a live worker,
  not durable snapshot-manifest recovery after process loss. A future durable policy
  would additionally require validated snapshot manifests. Neither is effect evidence.
- `serialization: notebook` requires a registered deterministic notebook projector.
- `serialization: jsonl` adds no runtime-specific transcript writer: lifecycle events
  remain in the operational task stream and, when enabled, the canonical ledger.
- `capability_access: native` is allowed only by an explicit trusted execution profile.
  Its direct Python effects are not described as brokered, idempotent, or recoverable.
- Conversation continuity requires server-owned conversation/workspace identity and a
  state policy that supports cross-run restore.

## Component contracts

### Required modular boundaries

The table separates current extension seams from intended replaceability. A row marked
"native bundle" is a responsibility boundary enforced by validation, not yet a freely
selectable plugin contract.

| Module | Current code seam | Current status | Host-owned invariant |
| --- | --- | --- | --- |
| Environment lifecycle | `ExecutionRuntime`, `CommandSandbox`, and PTC-owned local processes | Typed host and runtime implementations | Exclusive ownership, clean setup/reset, cleanup verification |
| PTC session | `PtcSession` returned by closed `select_ptc_session` dispatch | Common assembly/lifecycle seam; Skein notebook is the retained implementation | One model tool and explicit cleanup/reconciliation hooks |
| PTC serialization | Implementation-owned notebook or ledger events | Native bundle | Stable attempt identity and provenance; serialization never grants execution authority |
| Runtime-state policy | Implementation-owned safe replay or bounded in-worker snapshot rollback | Native bundle; `none` is unsupported | Explicit restore eligibility; uncertain effects are never automatically replayed |
| Memory programs | `MemoryProgramSpec`, `MemoryProgramRuntime`, `ViewRequest`/`ViewResult` | Exact version selection through one finite registry | Authorized scope, watermarks, bounds, evidence and result hashes |
| Context assembly | Pure `select_context_cut` plus one `ContextWindowPlugin`; ADK Pi compaction is separate | Policy seam; strategies are not one plugin registry | Stable prefix, bounded dynamic suffix, complete tool-call/result boundaries |
| Result presentation | `ToolEnvelope` and `compact_tool_result` | Shared by all PTC implementations; not configuration-selectable | Redaction, output bounds and links to authoritative evidence |

Turning a replaceable module off selects its identity behavior; it does not bypass an
invariant. PTC without memory still records its owned execution lifecycle and receives
normal bounded ADK history, but has no historical memory-query bridge. Memory without
PTC derives views over direct-tool and task events. Context window management without
trace-native memory is invalid because it cannot reconstruct an evidence-backed suffix.
Serialization and runtime-state recovery remain independent of memory visibility.

The assembly behavior is therefore explicit:

| PTC | Trace memory | Memory programs | Bounded context | Result |
| --- | --- | --- | --- | --- |
| off | off | off | off | Four direct coding tools; ordinary ADK history only |
| on | off | off | off | One `execute_code`; PTC lifecycle persists without a memory bridge |
| off | on | off | off | Four direct tools; trace captures direct receipts but is not model-queryable |
| off | on | active | off | Four direct tools; reviewed memory commands are available through guarded bash |
| on | on | off | off | One `execute_code`; trace is durable but has no model memory commands |
| on | on | active | off | One `execute_code` with brokered memory commands when that PTC profile supports them |
| either | on | either | on | Evidence-backed bounded suffix; programs may remain model-inaccessible |
| either | off | active | off | Configuration error: programs require canonical memory |
| either | off | off | on | Configuration error: bounded context requires trace-native memory |

Disabling PTC never initializes a runtime, serializer, snapshot owner, or container.

The support matrix is closed: a combination exists only when assembly validation and
contract tests cover it. Known unavailable combinations raise `NotImplementedError`;
unknown values fail schema validation; no module silently falls back to another
implementation. Safety, redaction, authorization, trace authority and independent
completion verification are invariants rather than selectable modules.

Context selection is a pure policy computation over retained content identities,
the prior cut, configured budgets, and reconstruction mode. It returns only a cut
boundary after complete tool interactions. The single ADK context plugin renders the
header and suffix, publishes the epoch receipt, and mutates the provider request only
after durable publication succeeds. A newly returned tool result always retains its
matching call; no policy may split that pair to satisfy a budget.

The next smallest useful split is one coordinator and three narrow implementation
contracts. These are target interfaces, not claims about current Python types:

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

In the target split, `PtcSession` owns ordering and is the only caller used by `execute_code`. Runtimes do
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

The following is the target coordinator protocol. The Skein notebook preserves the
same safety order internally while owning its event, restore, execution, and
persistence sequence.

For each `execute_code` call, the extracted coordinator will perform this sequence:

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
`reconciliation_required`. Notebook PTC additionally returns bounded failure diagnostics,
`state_preserved`, and an observed `kernel` liveness/epoch record so a historical cell
epoch is not mistaken for a currently live worker. It does not repeat stdout, stderr,
display bundles, notebook paths, or the entire internal state catalog already represented
by `model_text`, artifacts, or the durable terminal event. The result hash identifies semantic output
and is stable across replay and runtime-epoch changes. Full receipts remain in the
canonical trace; bounded reads recover them when authorized.

Every completed non-artifact nested capability result is deterministically serialized and stored as
an immutable content-addressed artifact before model presentation. Its terminal event
records the result hash, artifact URI, media type, byte size, operation identity, and
effect; the live Python worker still receives the complete result mapping. Automatic
result-artifact URIs remain internal unless selected, so durability does not add a URI
to every model turn.
Artifact load/list/publish results are not recursively artifacted.

Images and other rich MIME results are content-addressed before presentation. When
stdout or stderr exceeds the configured cell-output cap, the complete stream is stored
as an artifact and the model receives a bounded head-and-tail preview, omitted-byte
count, and reloadable URI. Task-scoped `agent.artifacts.load` and
`agent.artifacts.list` preserve confinement and byte limits. The explicit
`agent.artifacts.publish(value, name, description=None)` operation adds normalized
host-facing metadata to an immutable artifact; it does not itself send, display, or
forward data. Automatic tool-result and overflow artifacts are internal by default.

The invariant execution doctrine stays in the cache-stable instruction and tool
description. Kernel, CLI, and capability inventories remain deterministic and
progressively disclosed through targeted `agent.help()` calls. Runtime discovery must
not append a newly learned environment catalog to the system instruction after the
first cell, because that would mutate the provider-cache prefix mid-run.
Help rendering is capped deterministically: detailed contracts degrade to compact
signatures, then to a bounded names list with an exact-query pointer.

Intermediate values can remain outside provider context while still existing in the
live heap or artifact store. Before a registered result, printed stream, or published
value crosses into durable artifacts or model-visible output, the configured secret
redactor produces the persisted representation. Skein does not yet implement reversible
PII tokenization for opaque cross-MCP transfer and must not claim that stronger privacy
property. Such a data-flow policy requires explicit source/destination authorization and
an independently protected token vault before activation.

Within one live worker epoch, brokered `fs.read` is catalog-aware. Before reusing captured
lines it confirms the current path/hash, then composes the requested range from attested
coverage and fresh reads of only missing intervals. Changed or uncertain identity uses the
ordinary full-read path. The completed capability receipt remains one logical read and
records reused lines, newly selected source lines and the identity probe separately; it
does not relabel historical content as current or suppress required freshness checks.

The shipped notebook kernel is persistent within its owned run and intentionally differs
from stateless hosted calculation tools. Its trusted-local adapter is not a production
security sandbox, package availability is reported by `agent.help("kernel")`, and model
temperature remains an evaluated profile setting rather than a code-execution override.
The worker is a constrained coordination and working-state plane, not the repository
environment: filesystem, shell, network, clock, and external actions require explicit
host bridges. Interpreter state complements bounded message history and durable
artifacts; it does not replace either authority. Snapshots preserve only validated
serializable working data, never live handles or evidence of completed effects. This
matches the interpreter boundary described by [Deep Agents](https://www.langchain.com/blog/give-your-agents-an-interpreter)
without adding its subagent bridge or a second JavaScript runtime.

`tools.usage@1` is the code-owned trace-memory view for accounting. It reports bounded
counts by name and terminal status for top-level calls and nested PTC capabilities,
plus model-visible and omitted bytes. ADK nested tool metrics share the enclosing
invocation identity; brokered notebook capabilities have explicit capability receipts.
The view accepts exact status and name-query filters and exposes hashes and aggregates,
not raw arguments or full results.

Existing histories are not rewritten. Current Skein notebook writers emit
`notebook.cell_added` plus `repl.cell_submitted` and a `repl.cell_*` terminal event.
There is no normalized `ptc.cell_*` writer yet. Any future vocabulary unification must
retain import compatibility and prove replay equality before changing writers.

### State policies

`none` makes no continuity claim. A runtime may remain warm during its natural lifetime,
but restart begins with an empty namespace.

`replay_safe` is the shipped default. A cell execution error discards the dirty runtime
epoch; parse/source-validation errors preserve it because execution did not begin.
Restart executes only completed, self-contained data-construction cells classified as
safe. Imports, definitions, calls, dependent expressions, and broker effects are not
silently replayed. `none` is not a supported Skein notebook configuration today.

Experimental `snapshot` captures a size-limited selection of exact primitive/container
values before a cell and restores that selection after a caught execution exception.
Unsupported, cyclic, excessively nested, and over-budget values are omitted; rollback
therefore does not promise preservation of the full namespace. It does not undo brokered
effects. The bytes stay inside the worker: timeout, transport loss, and process restart
do not restore them. Restart still uses conservative safe-cell replay. This is distinct
from the immutable `.ipynb` artifact emitted as `notebook.snapshotted` at shutdown.

A future durable runtime snapshot requires atomic payload/manifest publication, scope
and runtime compatibility validation, and bounded restore. Neither an in-worker rollback
nor a future snapshot proves provenance, current workspace equivalence, or external
effect completion. Unknown effects still require reconciliation.

### Working values and durable memory

Stage 2 extends the live state catalog through the existing PTC surface:

```python
agent.state.annotate("sources", "Module needed for the next edit", selector=("main",))
agent.state.describe("sources", selector=("main",), preview=True)
```

`annotate` records a bounded advisory purpose for one supported live value; it does
not create a durable semantic finding. `describe` retains name/type/size/cell/replay
metadata, optionally adds a bounded string or structural preview, and includes a
broker-established read reference when available. Selectors contain at most eight
string/integer keys into plain containers, never expressions evaluated with `eval`.
Preview is opt-in; listing the catalog does not dump value contents.

Broker read results carry task/operation identity, artifact URI, source path/hash,
and range. Before returning those results to Python, the runtime registers bounded
fingerprints for the result mapping, its data mapping, and source text where supported.
References are detached from model-mutable containers. Merely copying or constructing
a dictionary with a `read_reference` field does not establish broker provenance.
Registration also retains the exact `read_value_kind` (`result`, `data`, `text`)
for supported successful text reads. Only an unchanged attested object exposes that
form. Shared prompt projections derive its exact source-content expression; full
state inspection retains the original locator, fingerprint and structural metadata.

The live worker also retains up to 64 unchanged successful read envelopes under their
existing artifact URIs. `agent.state.reads(path=None)` exposes bounded path/version/range
metadata and a compact epoch-local handle; `agent.state.reuse(handle)` returns the
original result without depending on a model-chosen variable name. Reassignment or
deletion of `r`, `result`, or another user binding therefore does not erase the access
path. In-place mutation, eviction, or worker loss makes the handle unavailable. This is
ephemeral runtime state, not a cache, freshness claim, or second historical authority.

Descriptions and provenance require both the recorded object and a matching bounded
content fingerprint. A description attached to an attested read is also retained with
that read for the live epoch; other reassignment clears the affected annotation. Same-size in-place
mutation invalidates its description and the relevant read association. Aliases can
reuse an unchanged registered object, while a separately retained original string can
remain valid historical evidence after its parent mapping changes. Nested descriptors
distinguish the parent `binding_type` from the selected value's `type`, and supply
escaped `access_expression` and `inspect_expression` recipes. These are advisory
references, not executed code or new authority. Selector keys and integer
representations are bounded. Missing selectors
are unavailable. Failed-cell annotation changes are rolled back; committed state
manifests are redacted in the terminal event and scoped by its cell and kernel epoch.
Worker loss discards the live registry rather than claiming old descriptors are live.

This is deliberately limited plain-data support: at most 64 annotations/catalog
entries and 128 registered source values, 500 UTF-8 bytes per annotation, and bounded
fingerprint traversal (1,024 nodes, depth 12, and 65,536-byte content accounting).
Unsupported/cyclic/over-budget values lose eligibility instead of acquiring guessed
lineage. Preview/fingerprinting does not call arbitrary `repr`, properties, iterators,
or serialization hooks. The catalog does not track every transformation or establish
current workspace freshness; read references remain historical snapshots.

Automatic catalog discovery now navigates plain-container bindings to registered
read values, so a parallel result retained only as `reads` can expose `reads[0]`
with its original attested source/range and exact inspection recipe. It reuses the
same identity/fingerprint check as explicit describe; it does not infer lineage
from copied text or self-authored receipt fields. Identical object aliases share
one automatic nested navigation entry, while explicit selectors remain available.
Descriptions/annotations retain first priority, then discovered read associations,
then generic root bindings within the existing 64-entry catalog. Discovery visits
at most 512 values from the first 128 sorted roots, with a 512-item queue, eight
selector levels, 64 children per plain container and 128-byte automatic string
keys. Omitted values require an explicit supported `state.describe` or rebinding
a nearer intermediate value when the selector would exceed eight levels; this is
not an exhaustive heap listing. No source contents are previewed by default.
Completed-cell manifests and existing bounded state-update messages carry these
entries; mutation/deletion/epoch loss cannot preserve an invalid live association.
This repairs an observed representation gap, not proof of model reuse or a refreshed
phase-boundary context packet.

Focused descriptor and notebook integration tests cover these delivered seams. Stage 3
now separately persists typed evidence-linked findings and explicit corrections in
versioned memory notes, with a bounded `working_set@1` view; see the
[finding contract](context-and-memory.md#delivered-finding-lifecycle-and-working-set-program).
Annotations are not automatically converted to those durable findings. Stage 4 adds
opt-in bounded changed-only state notices under `notebook_ptc.emit_state_updates`, with
cell/epoch attribution and a selective-inspection pointer. At most eight entries are
selected under the existing output budget; unchanged or irrelevant catalog metadata
does not generate a notice. Lost associations are reported as invalidated, not as
evidence that the underlying historical artifact disappeared. Pre-execution failures
preserve the prior state description; actual worker loss does not advertise it as live.
The compact result separately reports observed kernel liveness/epoch, including after
execution failure, instead of relying on a prior successful cell's metadata.
Selected output, exception diagnostics, and retained cell source redact known secrets.
If redaction changes source, that cell is never automatically replayed; an original
source digest preserves content-sensitive operation identity without persisting the
secret-bearing source. Redaction is not permission to replay altered Python code.

The context renderer admits described bindings only when observed worker availability
and epoch match. Its metadata/described/findings representation choices support
controlled comparison without changing broker authority. Focused tests cover the
changed-only notices and whole-entry handoff construction; these checks do not establish
that the complete continuity mechanism or its live quality gate has passed.

Grounded working-set findings are joined at handoff to matching live read recipes by
their exact evidence artifact URI. The finding supplies the learned summary and the
PTC catalog supplies the executable value; unsupported prose is never promoted into a
summary. A joined source is not emitted again as a standalone binding. Eager per-cell
state notices remain disabled by default. Two live four-family warm-worker
cohorts produced zero avoidable source rereads in the no-notice arm; the notice arm did
not improve that floor and increased aggregate cost in both cohorts. The canonical
manifest, explicit `agent.state` inspection and phase handoff still expose the same
attested bindings when needed. This decision is limited to an intact worker without a
context cut; post-cut recovery and learned-memory qualification remain separate gates.

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
append repl.cell_submitted
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
- code cells containing retained, secret-redacted Python plus the original source
  digest, attempt, effect, artifact, source-event, watermark, and kernel metadata;
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

At assembly, the registry resolves every configured `(name, version)` and enforces the
reviewed/model-visible and reuse gates. Shared scan, time, and output budgets live in
`ContextProgramConfig`; typed query parameters and temporal boundaries are validated by
`ViewRequest` at execution. The receipt records program, execution, and result
identities plus evidence addresses. Active mode exposes selected programs through the
reserved `memory` command handled by Bash or a brokered PTC capability. Shadow mode
runs only the fixed `events.count@1` probe and never changes the prompt.

The `pi` mechanism remains a context-compaction strategy over ADK session history. It
must not be registered as a trace-native memory program because it does not satisfy the
same deterministic evidence contract. Context compaction and memory-program selection
remain separate configuration axes.

## Implementation and migration plan

This delivery ledger distinguishes landed seams from the remaining extraction:

1. **Landed:** PTC assembly returns a shared `PtcSession` result and compact envelope;
   selection is centralized. Skein notebook is the retained runtime after ADK Code
   Mode removal, not one of two currently available implementations.
2. **Landed:** unsupported serialization/state/continuity/module combinations fail at
   configuration loading with specific errors.
3. **Landed:** exact memory program versions resolve through one finite registry; the
   prior mutable SQL and duplicate prompt-program catalogs were removed.
4. **Landed:** the evaluated ADK Code Mode arm, image, reusable worker, and Docker SDK
   were removed after it exhausted the matched 200k input budget; stale configuration
   fails with a migration error and Git retains the experiment.
5. **Remaining:** extract a common cell coordinator, runtime, serializer, and state
   policy without changing event bytes, tool declarations, provider prefixes, or
   failure behavior.
6. **Remaining:** run the deterministic composition matrix and matched live provider
   comparison before adding cross-pairings or changing the four-tool default.

Canonical runtime/persistence matrix after implementation audit:

| Runtime | Serialization | State | Status | Contract |
| --- | --- | --- | --- | --- |
| `skein_repl` | `notebook` | `replay_safe` | supported | Durable cells and conservative replay |
| `skein_repl` | `notebook` | `snapshot` | experimental | Bounded in-worker pre-cell rollback; no durable heap restore |
| `skein_repl` | `jsonl` | `replay_safe` | not implemented | Requires extraction of notebook serialization from the coordinator |

For identical brokered programs, changing only serialization must preserve runtime
result, operation identities, authorization, receipts, workspace result, and
verification outcome. Changing only memory programs must not change tool declarations,
execution authorization, or effect semantics. The live comparison reports pass rate,
cost per pass, tokens, cache reads, model/tool/verification time, cell and retry counts,
duplicate or unknown effects, snapshot bytes/failures, and terminal reason.

## Implemented boundary

### Pi-hosted PTC v4.1 evaluation adapter

The Pi comparison arm in `scripts/pi_code_tool_harbor.py` and
`scripts/pi_skein_ptc_extension.mjs` reuses `PersistentPythonWorker`, but it is not the
ADK notebook implementation described above. Pi owns inference, conversation state,
and compaction. The adapter registers one model-visible tool named `code` and bridges
each submitted cell to a resident Skein CPython subprocess and the Pier task
workspace.

| Concern | Implemented v4.1 behavior |
|---|---|
| Model contract | A compact stable prompt is assembled from independent runtime, workflow, generated helper-signature, contract, verification, and example components. Session state never enters it. |
| Helpers | `read`, `write`, `edit`, `bash`, and `verify` are synchronous Python functions. Each nested call is captured separately; one cell accepts at most 64 helper calls. |
| Batching | The model writes normal Python to loop, filter, compute, and combine helpers. There is no automatic batch planner or second batch tool. |
| Variable reuse | The live namespace persists across cells. `json`, `math`, and `re` are preloaded. A straight-line AST metric records reads of prior bindings but does not inject a binding inventory into the prompt. |
| Preflight | The stdlib AST checker reads the real broker signatures and result schemas. It blocks known undefined names, bad helper arguments/signatures, known result-key mistakes, simple literal type mismatches, and selected invalid operators before execution. It deliberately abstains after opaque calls, branches, comprehensions, and dynamic namespace changes. |
| Result projection | Only selected stdout and the final expression are model-facing by default. Missing shell stderr, nonzero exits, timeouts, partial reads, and failed external effects are appended as compact diagnostics. Observations are capped at 50 KiB; up to 32 fuller 256 KiB records can be paged by result ID. |
| State and rollback | Supported live values are snapshotted before a cell and restored after a Python execution exception. File and shell effects are not rolled back. Parse and source-validation rejection execute nothing. |
| Worker loss | A successful cell writes an at-most-1 MiB checkpoint containing only finite JSON-safe scalars, lists, and string-keyed dictionaries. Timeout or transport loss discards the worker and restores that checkpoint before the next cell. Functions, modules, tuples, objects, and omitted oversized values are lost. No transcript is replayed. |
| Verification | `bash` uses normal shell pipeline semantics. `verify` enables `pipefail` and raises on a non-success result. The extension conservatively invalidates verification after changed writes/edits and after every shell call, and may issue one final evidence-review turn. |
| Isolation | Worker source guards and the Harbor file adapter restrict intended access, while Pier/Docker isolates task commands. The CPython worker is not presented as an adversarial OS sandbox. |

V4.1 remains the Pi PTC default because its complete 20-task run matched Code Tool's
binary quality within the uncertainty of the panel. The v4.2 revision-gating
experiment reduced repeated verification but increased interactions, uncached input,
cost, and latency on the clean comparison slice, so it was not promoted. The v4.2
run also exposed a current adapter limitation: Pi can record a terminal provider
message with `stopReason: "error"` while exiting zero, and the adapter does not yet
turn that message into a Harbor agent error. Such rollouts must be classified from
their event logs and excluded from quality comparisons.

- Four tools are the default profile.
- `execute_code` is the model-facing name for Skein notebook PTC and returns the compact
  result envelope.
- `code` is the separate model-facing name used only by the Pi-hosted v4.1 evaluation
  adapter; it returns plain text plus trace details and does not materialize the ADK
  notebook/ledger protocol.
- Runtime, serialization, and state policy are not yet independently composed; the
  current `NotebookPtcConfig.implementation` remains the compatibility bundle until the
  migration above lands.
- Notebook PTC is implemented and disabled by default.
- The Skein local adapter requires project trust and is not a production security
  sandbox.
- Registered MCP calls, direct tools, brokered PTC calls, and verification share policy,
  receipts, redaction, output limits, and task identity.
- Canonical JSONL and DuckDB ledgers are implemented and optional.
- One code-owned memory-program registry controls configuration, execution, and model
  exposure; legacy prompt/reducer and mutable SQL catalogs were removed.
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
