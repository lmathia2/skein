# Context, versioned memory programs, and long sessions

> Status: deterministic programs implemented; active retrieval and long-context
> treatments remain opt-in with live quality gates pending
>
> Updated: 2026-09-07

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. A prompt is a bounded, deterministic projection of retained evidence.
2. Stable instructions and tool declarations precede volatile task context so the
   provider can reuse a byte-stable prefix.
3. Memory is a versioned computation over authorized ledger evidence, not prose
   inserted into the system prompt and not a second database of truth.
4. Factual, semantic, progress, failure, and handoff memory are different views over
   the same evidence and share one request/result contract.
5. Stored projections and caches are disposable, watermark-bound optimizations.
6. Long context is handled by bounded views, artifact indirection, explicit
   compaction epochs, and a recent exact tail—not by replaying everything forever.

## How a prompt is constructed

The checked-in memory prompt compiler uses four regions:

```text
P0  stable worker instruction + stable tool declarations
 |
P1  ledger watermark / history boundary
 |
P2  derived progress + selected task memory
 |
P3  current query + bounded recent exact events
 v
provider request
```

`P0` is cache-oriented and excludes task/session state. `P1`–`P3` are the dynamic
suffix. Each component records its content hash and source view IDs; the full prompt
records a context epoch, watermark, and prompt hash.

The live worker packet additionally budgets these named sections in a fixed order:

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

The harness does not paste all eight raw records into the prompt. It may derive:

```text
P1 {"history_watermark":48}

P2 {
  "progress": {
    "completed":["tool.read","tool.edit"],
    "failed_or_blocked":["tool.bash"],
    "last_event":"steering.received"
  },
  "memory": {
    "query":"retry timeout",
    "relevant":[{"seq":45,"kind":"memory.note",...}]
  }
}

P3 {
  "query":"continue fixing the flaky retry",
  "recent":[events 43..48 after allowlisting, redaction, and bounds]
}
```

The result is reproducible because the programs, parameters, source watermark,
ordering, exposure policy, and serialization are identified. The model can request
exact supporting evidence with `memory history`, `memory query`, or `memory read`;
it never receives private reasoning or unbounded raw traces.

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

Memory programs are selected independently of the PTC runtime, PTC serialization,
runtime-state policy, ledger backend, and context-compaction strategy:

```yaml
memory:
  enabled: true
  ledger: jsonl
  programs:
    - name: history.page
      version: "1"
      mode: active
      parameters: {}
    - name: failures.by_kind
      version: "1"
      mode: shadow
      parameters:
        statuses: [failed, timeout, blocked]
```

The loader resolves each exact `(name, version)` through a finite code-owned registry.
Each registry entry declares its parameter model, allowed event/artifact sources,
temporal semantics, default and maximum budgets, exposure policy, and implementation
hash. YAML cannot provide an import path, callable, SQL body, or Python source.

Assembly fails before model execution when a program or version is unavailable, its
parameters are invalid, its ledger capability is missing, or requested budgets exceed
the registered ceiling. Duplicate `(name, version)` selections are invalid. Registry
iteration and execution order are canonical by configured list position followed by
the program's own deterministic result ordering.

At runtime, `shadow` executes against the same frozen evidence boundary and records a
receipt but contributes no prompt bytes. `active` may contribute its bounded result at
its declared prompt region. Promotion changes configuration and therefore the behavior
hash; it never occurs implicitly because a shadow result looked useful.

The existing `pi` option is a context-compaction strategy over ADK session history, not
a versioned program over canonical ledger evidence. It remains separately configurable
and cannot be selected in `memory.programs`.

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

[`PROGRAM_REGISTRY`](../../harness/memory/programs.py) is the only code-owned
`(name, version)` catalog used by configuration, execution, and model exposure.
Reusable logic must be reviewed, typed, tested, and version-pinned before entering
that finite library. Notebook code, YAML callables, and arbitrary SQL are never
auto-promoted or executed as memory programs.

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
