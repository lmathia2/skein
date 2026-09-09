# Skein architecture

The supported runtime is local, single-process, and built around one ADK coding
worker. This document supersedes the larger historical designs.

## Boundaries

```text
Bubble Tea TUI
  │ versioned WebSocket control + AG-UI event envelopes
Server / run registry / ADK Runner
  │
HarnessFactory ← strict YAML behavior + explicit runtime identity
  │
ADK App → bounded coding loop → one worker
  │                             │
ledger / verify / checkpoint     read · bash · edit · write (default)
                                python → guarded broker (experimental)
```

- `harness/agent` defines the factory, assembly, control, and provider-neutral
  descriptor contracts. A registered factory can replace the harness without
  changing the TUI.
- `harness/config` validates YAML and separates stable behavior from workspace,
  task, state-directory, and project-trust bindings.
- `app/agent/factory.py` assembles ADK agents, plugins, sandbox, tools, and workflow.
  The server and Agents CLI application entrypoint use this same factory.
- `harness/ai` builds ADK models. Built-ins are native ADK/Gemini, the Codex
  subscription adapter, and a direct OpenRouter OpenResponses adapter; there is no
  second agent runtime or LiteLLM route.
- Factories can opt into `ModelConfigurableHarness` to expose their coding model
  and validate a replacement configuration. The server owns catalog/preferences
  and freezes model identity and behavior hashes when admitting each run. The
  terminal only sends control requests; it does not rewrite YAML or build models.
- An optional resource-discovery hook uses the same trusted loaders as execution.
  Server metadata includes paths, availability and trust, never instruction/skill
  bodies. Selected skill names are flushed through ADK state before the worker
  starts; the terminal cannot decide project trust or scan server directories.
- `harness/server` owns authenticated local transport, run ownership, replay,
  cancellation, deadlines, and ADK event translation. The TUI imports none of the
  Python harness implementation.

The Pi implementation's loop is Python code, not a configurable graph interpreter.
YAML references a versioned worker prompt and tunes its model/generation settings,
progress thresholds, budgets, skills, tool limits, safety allowances, tracing,
persistence, steering, and iteration limits. A deterministic export exposes only the
safe optimization subset; change topology by registering another factory.

## Minimal coding loop

The workflow initializes/replays a task ledger and builds a bounded work packet.
The ADK coding worker then owns the complete model/tool loop until it returns a typed
answer, verification request, or blocker. The deterministic workflow only re-enters
the worker after external steering or failed verification; a terminal `continue` or
malformed result fails closed. Model prose cannot mark a task verified.

The static instruction excludes volatile task/session data. Dynamic packets hold the
ledger, repository manifest, selected skills, recent events, compaction snapshot, and
steering. Before every model call, the worker compares canonical bytes for the actual
model, system instruction, tool declarations, and tool configuration with the first
call and fails closed on mutation. Codex/OpenResponses cache keys cover those stable
inputs and the output format, never the dynamic packet. Diagnostic prefix hashes are
deterministic behavior identities rather than claims about provider serialization.
Actual usage is checked before every inner model call. ADK's model-authored event
compactor is disabled; bounded memory views are derived reproducibly from the
append-only task log.

Directory skills are trusted operator/project inputs. Project instructions and
skills require explicit project trust; selected bodies are budgeted and hashed.
There is no automatic synthesis, trial assignment, promotion, or project-memory
injection. Traces remain useful for manual investigation and future measured work.

## Execution boundary

The experimental `notebook_ptc.enabled` path selects Skein's notebook tool or the
trusted Prime-native REPL behind the same `execute_code` name and `PtcSession` assembly
seam. Skein receives brokered coding capabilities; Prime has explicit native OS access
and records its effects at cell granularity as
`native_untracked`. See [the trace-native harness ADR](adr/trace-native-harness.md).
Skein's notebook `execute_code` tool is backed by a
persistent CPython worker and parent-owned capability broker. Registered MCP, file,
and shell capabilities traverse that broker and its lifecycle trace. This path
supports trusted local workspaces. Production or adversarial execution is outside
the supported boundary; the source guard is defense in depth, not a sandbox. An
OS-isolated profile may be added when a concrete deployment requires it, but it is
not a notebook-PTC activation or completion gate.

PTC is a different model-facing composition surface, not a second implementation of
the underlying tools. Python calls such as `agent.shell.run(...)` reach the same Bash
adapter, command classifier, approval path, receipts, output bounds, and redaction as
the default profile. A submitted cell is appended and materialized before execution;
its terminal event and selected output are materialized afterward. `nb execute` is
never used to resume or execute harness work.

Managed file tools call the same atomic, confined primitives exercised by tests.
Successful mutations have replay receipts; failed operations are never persisted
as successful receipts. Shell output is redacted and bounded.

FFF provides grouped, cursor-paginated search through reserved `bash` commands,
not another model-visible tool. The repository manifest supplies only compact build and
verification metadata.
No LSP/Moderne planning-only bridge remains.

Local and Docker command adapters use the authoritative host/mounted workspace.
Verification is supplied explicitly with that same sandbox, YAML approval policy,
redactor inputs, state directory, and actual task ID. Approval fingerprints do not
leak across tasks. Validation disables uv dependency synchronization and online
resolution; project dependencies must already be available.

The local adapter is not a security sandbox. Docker isolates commands, not the
entire Python harness or its host-side file/search primitives. See
[security](security.md).

## State and interaction

When canonical memory is enabled, the configured JSONL or DuckDB ledger captures task events, receipts, checkpoints,
approvals, steering, metrics, public/run events, redacted ADK session lifecycle, and
ADK traces. JSONL is the dependency-free local backend; DuckDB is an optional analytical
backend. With canonical memory disabled, the factory uses the unchanged main task JSONL
and SQLite stores. Other SQLite stores remain operational projections during measured
migration. Artifacts use local files or memory.

The ledger, notebook, and CPython worker deliberately have separate authority:

- The append-only ledger records what happened, including open, failed, blocked,
  timed-out, retried, and unknown-effect work.
- The canonical notebook is a rebuildable workbench projection containing timestamped
  task/message/steering/compaction Markdown, exact PTC programs, and selected outputs.
- Clean worker shutdown rematerializes that complete projection, stores its canonical
  bytes once under `artifacts/sha256`, and appends an idempotent
  `notebook.snapshotted` event with the source watermark and last cell kernel epoch.
- The worker owns only current live variables, imports, clients, and caches. Restart
  restores only completed self-contained data cells; it never infers success from
  notebook order. Failed cells discard the dirty kernel epoch before more work is
  accepted. `agent.state.list()` and `agent.state.describe(name)` expose only bounded
  name/type/size/provenance/replay metadata, so values remain inside Python until code
  explicitly selects them.

For a server run, operational notebooks and task events live under
`STATE_ROOT/runs/RUN_ID`. `skein notebook --state-root RUN_STATE
--task-id RUN_ID` rematerializes from events and uses the required `nb-cli` for compact
reading. Skein does not parse `.ipynb` JSON as a browsing fallback.

PTC continuity remains run-scoped by default. Opt-in conversation continuity uses
explicitly owned prior-run manifests, task-specific watermarks, and shared notebook
projections. Only supported self-contained data cells restore; the CPython heap,
imports, and dependent computations are not durable. Historical retrieval permission
is separate from notebook continuity.

Opt-in context programs expose bounded, evidence-backed history through the existing
tool surface. Window management, notes, fresh reconstruction, prior-run recall, and
program reuse are independent treatment controls. Safe-auto recovery reuses ADK
invocations only after checkpoint, workspace, ownership, and effect checks; unknown
effects fail closed. See the [experiment runbook](context-experiment-runbook.md) for
controlled profiles and limitations. None of these treatments changes defaults.

When installed through the optional `memory-search` extra, LanceDB provides immutable
hybrid-search projections over ledger events. Projection identity includes the exact
event rows and embedding version; changing the vectorizer requires a new version. A
warm projection embeds only the query, and results return canonical event IDs for view
provenance. DuckDB remains the sole authority and all Lance data is disposable. LanceDB
and its embedding implementation are imported only when this projection is configured,
so the default harness pays no startup or dependency cost. Projections live beneath a
SHA-256 task directory and explicit task erasure removes that directory with the
canonical evidence.

Active context programs can use Lance through an explicitly injected, versioned
semantic-search factory. YAML selection alone fails closed without that provider;
no embedding model is downloaded or selected implicitly.

`ledger-backfill` idempotently imports recognized legacy stores and reports source-count
equality. Explicit task watermarks can be sealed atomically to deterministic Parquet;
DuckLake is not installed or claimed because current scale measurements do not justify
another catalog tier.

Physical erasure is an explicit operator-authorized exception to append-only retention.
It removes an exact task from canonical and recognized operational stores plus its
notebook, unshared artifacts, JSONL stream, and manifested sealed segments. Shared
content-addressed artifacts are retained while another ledger task references them.
PostgreSQL, GCS, Vertex, and distributed-worker leases are not supported.

Steering is durably queued and consumed at safe model/tool/work-batch boundaries.
The server owns cancellation and replay. Run a single server process per state
directory; SQLite persistence does not imply multi-worker coordination.

ADK still owns Runner invocation, agent execution, streaming, session services,
caching, and resumability. The harness adds coding-specific contracts, not a
replacement framework.

See [simplification results](simplification.md), [development](development.md),
and [current status](IMPLEMENTATION_STATUS.md). The durable decisions are split into
three short ADRs: [trace-native harness and composable PTC](adr/trace-native-harness.md),
[context and memory](adr/context-and-memory.md), and
[execution and recovery](adr/execution-and-recovery.md). The normative current-code
mapping is the [implementation specification](specification.md).
