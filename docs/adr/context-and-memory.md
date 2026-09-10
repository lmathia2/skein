# Context, versioned memory programs, and long sessions

> Status: deterministic programs implemented; active retrieval and long-context
> treatments remain opt-in with live quality gates pending
>
> Updated: 2026-09-09

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

## How a prompt is constructed

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
and cannot be selected in `memory.context_programs.programs`.

## Types of memory

| Need | Implemented representation |
| --- | --- |
| Exact episodic history | `history.page`, `event.read`, and artifact byte ranges |
| Full-set facts | `events.count`; reviewed `failures.by_kind` |
| Query-relevant task memory | filtered lexical, semantic, or hybrid `history.page` |
| Tool accounting | top-level/nested `tools.usage` aggregate |
| Working intent | bounded, optimistic-concurrency `memory.note` event |
| Long-context handoff | deterministic control state + note metadata/excerpt + recent tail |
| Narrative compression | optional evidence-bound model summary, always advisory |

Semantic similarity ranks candidates; it does not establish truth. Lance rows retain
canonical event IDs, embedding version, and projection identity. Results are hydrated
from canonical evidence before exposure. No embedding model is selected or downloaded
implicitly, and task erasure invalidates the corresponding projection.

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
authorization.

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
