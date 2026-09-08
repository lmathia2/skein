# Trace-native harness and notebook PTC

> Status: accepted architecture; canonical memory and notebook PTC remain opt-in
>
> Updated: 2026-09-07

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

## Decisions

1. One append-only canonical ledger is the historical source of truth.
2. The target model-facing interface is one persistent `python` tool; the current
   default remains `read`, `bash`, `edit`, and `write` until the PTC ablation passes.
3. Python composes capabilities through the same host-owned broker. It does not gain
   filesystem, shell, network, approval, or completion authority.
4. The notebook is a rebuildable session document. It is not the event ledger and it
   is not the live Python heap.
5. Every attempted operation is evidence, including failure, timeout, cancellation,
   blocking, and an unknown external effect.
6. Deterministic code owns policy, budgets, recovery, verification, and completion.

## Architecture

```text
user / TUI / API
       |
       v
ADK Runner and Skein workflow
       |
       +---- default worker: read | bash | edit | write
       |
       `---- PTC worker: python
                         |
                         v
                  guarded capability broker
                   |       |       |
                 files    shell    MCP
                   `-------+-------'
                           |
                    intents + receipts
                           v
                 canonical event ledger
                    |       |       |
                 reducers  views  notebook
```

ADK supplies the runner, session service, streaming, provider integration, caching,
and resumability. Skein supplies the coding loop, tool policy, state reduction,
evidence capture, context construction, and independent verification.

## One trace, three representations

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
| Notebook | What code, selected output, and narrative form the session workbench? | Rebuildable document |
| CPython heap | Which values are live in this kernel epoch? | Disposable runtime state |

JSONL and DuckDB implement the same canonical event contract. Existing per-task
JSONL and SQLite stores remain operational compatibility stores during migration.
When canonical memory is enabled, `LedgerBackedEventStore` writes both and can prove
byte-equal task-event reconstruction from the canonical ledger. They must not evolve
into competing sources of truth.

## Write-ahead PTC protocol

For each Python cell, the harness:

1. assigns stable cell, attempt, and kernel-epoch identities;
2. persists and materializes the submitted cell before execution;
3. routes nested effects through the normal broker and records intent first;
4. records bounded success, failure, timeout, or unknown effect;
5. materializes selected output and artifact references afterward.

A failed cell discards the dirty kernel epoch. Restart restores only completed,
self-contained data-construction cells classified as safe. Imports, definitions,
calls, dependent expressions, and broker effects are not silently replayed.

## Notebook contract

The `.ipynb` is continuously regenerated from events during execution:

```text
model calls python(code)
        |
        v
choose notebook ID + cell/attempt/kernel IDs
        |
restore prior replay-safe cells if this is a new kernel
        |
append notebook.cell_added + repl.cell_submitted
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

The worker instruction explicitly tells the model to use `nb read`/`nb search`
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

## Implemented boundary

- Four tools are the default profile.
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
- Pickling the heap: unsafe, environment-dependent, and unable to reconcile effects.
- A second unguarded Python tool stack: two authority paths make replay unreliable.
- Model-declared completion: a claim is evidence for the verifier, not a state change.
