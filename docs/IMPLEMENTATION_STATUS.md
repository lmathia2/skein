# Skein implementation status

The supported capability boundary is the simplified local harness, not the
historical feature checklist or book-rubric score.

## Context-program implementation (empirical gates pending)

The bounded context-program library now supports authorized exact history/event
reads (including byte ranges), frozen pagination, temporal and structured filters,
full-set counts, retrieval receipts, optimistic advisory notes, and a finite
reviewed-program reuse treatment. It keeps Python as the default implementation.
Optional semantic ranking is scope-filtered, canonically hydrated, and executed in
a killable worker; an explicit versioned, spawn-compatible embedding provider is
required. No model, index, or dependency is downloaded automatically.

The production factory exposes reserved `memory` commands through Bash/the same PTC
broker. Separate disabled-by-default window management publishes durable context
epochs before selection, retains an unconsumed tool result even in fresh mode, and
keeps transient steering out of replay hashes. Critical continuation/effect metadata
precedes advisory note/PTC state within existing budgets. Default and shadow
provider requests remain byte-equal in the offline tests; active profiles change
only declared model-visible context behavior, not the four-tool default surface.

The optional SQL catalog preserves exact source text and executes under time,
memory, scan, output, and no-spill limits in a killable worker. Summary generation
is an explicit injected async callback with evidence-bound recorded output and
optional cache reuse; it is not an automatically configured extra model agent.

Recovery now uses real ADK invocation identities, durable operation and validation
intents/results, publish-last integrity-bound checkpoints, preserved input budgets,
and exclusive workspace ownership. The opt-in safe-auto path resumes the original
ADK invocation without another user message; unknown effects, unavailable evidence,
expired permissions/budgets, and workspace divergence remain explicit blockers.
Six real subprocess cases include verified successful continuation and safe refusal
for incomplete or unsafe cases. This is same-machine, initialized-Git recovery, not
a workspace restore service or exactly-once arbitrary shell execution.

Owned prior-run recall and explicit other-session source selection are separate from
optional conversation notebook continuity. Only existing safe self-contained cells
restore; definitions, imports, dependent computations, and capabilities are not
silently replayed. Notebook metadata records source-task watermarks and attribution.
Passing deterministic contracts is not a live quality or promotion claim.

The standard notebook-PTC profiles keep exact ADK history; bounded context windows
remain an explicit experiment after a live screen showed exploration churn. PTC cell
composition uses a cache-stable, optimizer-visible phase policy, and tunable host
boundaries yield after 24 read-only cells or 48 total cells without resetting the
durable notebook/kernel. Each forced boundary enters criterion-gap review, with
explicit positive and never-reached-history checks for temporal requirements; the first
boundary reuses the existing single counterexample review;
  synthetic host yields are excluded from provider-call metrics. Cell terminal events
  record capability counts and operation classes. Complete successful build/test cells
on an unchanged workspace can satisfy an identical deterministic validation command
  under the same managed environment. PTC receives redacted machine-readable file,
  search, and command data separately from model-rendered text; the four-tool surface
  strips that program-only data. Programs can dispatch up to four independently
  validated file reads through one broker batch with stable result/receipt ordering;
  effectful and shell operations remain serial. Failed `fs.read` calls are terminal
  known-no-effect events, so a bad path may discard the failed cell's kernel epoch and
  continue without effect reconciliation; mutation-capable failures and genuine worker
  timeouts remain fail-closed. A broker call is bounded by its cell
  deadline, and timeout discards the kernel and blocks further execution until its
  unknown effect is reconciled. Metadata-only traces record canonical ADK request
  regions, and the OpenRouter adapter records the exact serialized request byte count,
  hash, and top-level region hashes without retaining prompt text.
  Once window management publishes a compaction epoch, its handoff bytes remain frozen
  across requests until a new threshold crossing publishes the next epoch; live steering
  remains in the suffix.

## Retained and verified

- The project identity is Skein: the Python distribution and primary CLI are `skein`,
  the runtime implementation key is `skein_v1`, launchers are `skein-start` and
  `skein-tui`, and runtime environment settings use the `SKEIN_` prefix.
- Versioned YAML worker prompt with content-sensitive behavior hashes; fixed
  agent schema, tool surface, safety outcomes, Docker network isolation, and memory
  implementation remain code-owned invariants rather than decorative YAML.
- A deterministic optimizer-facing export identifies the safe behavior surface:
  worker prompt, model/reasoning/generation settings, progress thresholds, context and
  tool-output budgets, and cache/compaction controls. It pins the baseline behavior
  hash and names the existing outcome, cost, cache, tool, and redacted-trace evidence.
  Safety, authority, topology, verification, persistence, and redaction remain outside
  optimizer control. Fully annotated standalone profiles cover the four-tool default,
  PTC with canonical JSONL, and PTC with canonical DuckDB.
- Strict YAML behavior, explicit workspace/state/trust identity, closed harness
  and ADK model-provider registries.
- One ADK coding worker with four tools; bounded context, a fail-closed byte-stable
  provider prefix, deterministic prefix identities and Codex cache routing keys,
  compact repository manifests, native FFF discovery, and ADK-owned token-threshold
  event compaction.
- Experimental, disabled-by-default notebook-native PTC mode for the local sandbox:
  the worker exposes one persistent `execute_code` tool, routes nested file and shell calls
  through the existing policy/approval adapters, appends lifecycle events, and
  deterministically materializes a durable nbformat transcript. Only self-contained
  data-construction cells restore automatically; calls, imports, definitions,
  attribute/subscript access, loaded-name dependencies, and broker use require
  reconciliation or are never replayed. A failed cell discards its kernel epoch so
  partial assignments cannot survive while disappearing on restart. Python exposes
  bounded `agent.state.list()` and `agent.state.describe(name)` metadata without
  returning stored values. Failures, timeouts, blocked calls, and
  unknown effects remain explicit in the event history. Task contracts, public
  user/assistant messages, steering, and structured compaction handoffs are projected
  as timestamped Markdown cells alongside code and selected outputs. The `notebook`
  operator command rematerializes by task ID and delegates compact reading to the
  required `nb-cli`; Skein does not parse notebook JSON as a browsing fallback.
  `nb execute` is not a second execution path. Clean PTC worker shutdown rematerializes the complete logical
  notebook and records one immutable content-addressed `.ipynb` snapshot at its source
  watermark; the ledger stays authoritative and the live heap is discarded.
  `start-ptc.sh` enables this
  path together with the dependency-free canonical JSONL ledger and an isolated state
  root; the ordinary launcher retains the four-tool default.
- PTC selection is now explicit: `skein_notebook` exposes `execute_code`, while the vendored
  ADK Code Mode 1.6.0 arm exposes `execute_code` using an explicitly pinned Docker
  image. Both call the same four brokered capabilities. Memory is independently
  optional: `trace_native` retains versioned ledger programs and `pi` uses native ADK
  session compaction with a bounded raw tail. Defaults remain unchanged.
- Optional canonical memory now shadow-captures task events, tool-receipt transitions,
  checkpoints, approvals (including expiration), steering, metrics, public/run events,
  redacted ADK session lifecycle, and ADK trace spans into a configured JSONL or DuckDB
  ledger shared by the server and its runs. Source-namespaced idempotency,
  observed and recorded timestamps, temporal reads, deterministic hashes, gap-free task
  order, and process-local single-writer locks are tested. Canonical memory is disabled
  by default, preserving the main four-tool persistence path. When enabled, JSONL is
  dependency-free and DuckDB is selected explicitly through YAML. Existing JSONL/SQLite
  stores remain operational projections. An idempotent `ledger-backfill` command imports all
  recognized local stores and audits source counts; deterministic fixtures reproduce
  identical task, run, and session hashes across fresh ledgers. The audit now compares
  every expected canonical event byte-for-byte, not only counts. The live task-event
  reader proves byte-equal reconstruction when canonical memory is enabled; otherwise
  task replay, recent context, and compaction retain the main JSONL path.
  DuckDB synchronously maintains per-task/source/kind/status counts and a stream
  watermark/hash in the append transaction. Unfiltered `events.count` and
  `failures.by_kind` reads use that projection without loading event payloads into
  Python; temporal, searched, filtered, and cross-ledger reads retain the exact
  bounded event path. Projections rebuild deterministically from `ledger_events`.
- Versioned deterministic memory programs provide model history, task progress,
  open/unknown-effect execution, time, query-relevant task memory, and dream/failure
  views. P0-P3 prompt manifests account for source view IDs and stable hashes. A
  restricted relational catalog enforces candidate -> shadow -> active -> retired
  promotion; only active programs may serve retrieval. Execution copies only the
  requested task into an isolated DuckDB connection, disables external access, and
  caps returned rows. DuckDB is in the optional `memory-duckdb` extra. The optional `memory-search`
  extra adds immutable, content-addressed LanceDB projections for combined vector and
  keyword retrieval. They contain canonical event IDs, are rebuilt from DuckDB ledger
  evidence, and can serve `task.memory` without becoming a second authority or adding
  Lance imports to the default startup path. Cached projections embed only the query.
  The embedding implementation remains an explicit injected, versioned dependency;
  changing it requires a new version. Live `retrieval: lance` configuration fails
  closed until an embedding provider is wired.
- Registered MCP capabilities can be invoked from Python through the same bounded,
  traced broker. Unknown capabilities fail closed without expanding the ADK tool list.
- Trusted directory skills and redacted interaction traces.
- Atomic confined file mutations, replay receipts, command approvals, and
  local/Docker command execution.
- Deterministic completion verification sharing the configured sandbox and
  task-scoped approvals. Coding tasks preserve the full request as their default
  criterion and receive one durable, task-agnostic counterexample pass before
  verification after workspace changes or mutation-capable tool use. Repository
  test failures are recorded before coding so later runs can prove no regression
  without treating a broken baseline as proof of requested behavior. Discovered
  targeted tests remain verifier-owned; unchanged validation results are reused from
  trace evidence, and two identical failures on an unchanged workspace stop
  autonomous retry.
- Complete successful PTC checks reuse their separate stdout rather than rendered
  transcripts. Harbor workspace fingerprints reuse initial clean-file hashes and
  rehash only initially/currently dirty, untracked, or base-relative committed paths.
  This keeps committed model changes visible to affected-test discovery and behavioral
  verification. Failed verification packets name the bounded set of extracted failing
  test identifiers for the existing focused repair iteration. Pristine baselines keep
  the independent pre-mutation boundary with a 60-second per-command cap; delaying
  them until failure remains unsafe until an equivalent isolated initial workspace
  is available.
- Local task events, SQLite checkpoints/steering/metrics/run registry,
  SQLite or in-memory ADK sessions, and local or in-memory artifacts.
- WebSocket/AG-UI transport, replay, steering, cancellation, and Pi-toolkit terminal.
- Pi harness public-output opt-in: structured worker text stays internal; the workflow
  publishes prose and a compact outcome explicitly. Coding completion replies are
  withheld until deterministic verification passes. Other ADK factories keep their
  ordinary text streaming by default.
- Eligible conversational replies stream through ADK callbacks after validation of
  a complete control header. Partial words and potentially sensitive spans are held
  for redaction; raw workflow JSON never reaches the terminal. Coding replies still
  wait for verification. Legacy JSON responses remain supported without streaming.
  A live ADK Runner/production WebSocket/scripted-model test reconnects a fresh Pi
  client mid-reply without duplicating text or model execution.
- Standalone Pi terminal toolkit client with authenticated transport, multi-turn
  conversations and replay, tested against the real server/ADK stack with a scripted
  model and now installed by default.
- Server-owned durable follow-ups, bounded conversation queries, queue continuation
  and removal controls; the terminal catches up through successor runs in order.
- Read-only public transcript pages with a stable snapshot cursor, event/byte
  limits, redaction and ownership checks; reads never attach or execute a run.
- Pi-style `/resume` selector, `/history` paging and `/session` identity view.
  Restoring history reuses the live reducer and catches up an active run without
  starting it again. Stopped work and pending queues require explicit continuation.
- Authenticated server-owned provider login/status/cancel/logout controls. OAuth
  workers never block the server event loop; credential tokens remain server-side.
- Pi-style terminal `/login`, `/auth` and confirmed `/logout` dialogs use those
  controls, preserve editor drafts and disclose server credential storage paths.
- Searchable `/model` with background catalog refresh, per-conversation selection
  and explicit saved defaults. The server freezes each run's choice; selection
  changes the next ADK turn without altering active work or replaying mutations.
- Server-owned, bounded resource metadata: `/resources` discloses workspace/state/
  configuration paths and trust; `/skills` and `/skill:NAME` use the runtime's trusted
  directory loader. Ctrl+O shows metadata, not file bodies. Actual selected skill
  names arrive before model execution and are distinct from available resources.
- Pi terminal command approvals: deterministic policy auto-executes recognized local
  read, build, test, and workspace mutations. Dependency, network, Git-history,
  publish/deploy, and unknown operations require exact task-scoped decisions;
  destructive commands remain denied. Worker and verification checks wait asynchronously
  for those decisions. Deny is the dialog default; deferred requests
  remain visible through `/approvals`. Cancellation, expiration, reconnect and
  uncertain decision retries are tested against the real server/ADK/tool stack.
  Approval dialogs accept explicit A/Y approve and D/N deny shortcuts while keeping
  denial as the Enter default. The Pi terminal is the installed client; the superseded
  Go client was removed.
- Plain-language turns support a distinct `answered` outcome without inventing a
  coding task. Direct answers permit no managed shell/write/edit action and no
  explicit verification obligation; coding results still route through verification.
- Gemini, Codex subscription, and direct OpenRouter OpenResponses adapters. The
  OpenRouter path keeps API keys out of serialized results, records exact
  provider-reported cost/cache usage, and exposes the concrete routed model.
- A pinned Harbor 0.22 host-side adapter, immutable public benchmark manifests,
  sequential fixed-intelligence matrices, official-reward import, and paired
  analysis. A live six-task Muse Spark 1.3 Contributor four-tool/PTC comparison
  has run; the broader Luna/max matrix remains pending.
- Fresh uv checkout installation and default TUI build; no Magnitude requirement.

## Removed

Magnitude/LiteLLM, remote/Kubernetes execution, cloud/distributed state, duplicate
bootstrap/model/context contracts, the standalone approval CLI, the context-bound
workspace-environment protocol, fake graph configuration, advisory reviewer,
automatic skill learning/trials/promotion, project-memory injection, disconnected
semantic-intelligence scaffolding, and comparison-only report CLIs.

These removals are intentional simplification, not claims that the features were
successfully integrated. Old source and historical reports remain available in Git.

## Validation

See [the measured cleanup report](simplification.md) for exact source/complexity
measurements and [the Pi terminal migration record](design/pi-terminal-migration.md)
for deterministic and live-provider evidence. The live sample is an acceptance set,
not a model-quality benchmark.

Remaining limitations include the host-local trust boundary, single-process state
ownership, experimental ADK APIs, and the still-complex server run controller.
The default CPython heap/notebook remains run-scoped; the opt-in conversation mode
restores only approved replay-safe data across owned runs. The live heap remains
disposable. Wire-level provider-request capture remains empirical work; canonical ADK
request regions are now measured on every call. The 2026-09-08 recovery/verification
rerun passed 2/6 tasks with Muse Spark 1.3 Contributor. It recovered Koota from an
ordinary failed read and preserved all existing tests except Wazero's two regressions,
but did not close Kombu, Testem, or Textual's hidden behavior gaps. It therefore did
not pass the default-promotion gate.
Notebook-native PTC supports trusted local workspaces. Its source guard
blocks direct imports, file/process/network primitives, dunder traversal, and common
introspection bypasses, but it is not a security sandbox. Production or adversarial
execution is outside the supported boundary; OS isolation is an optional deployment
profile to add only when a concrete deployment requires it. Operational-store cutover,
live prompt/view adoption, DuckLake archival, and the measured four-tool-versus-PTC
ablation remain gated work. DuckDB smoke measurements on
this host (250 events) were 11.35 ms mean append and 6.05 ms p95 50-event tail read;
that scale does not justify a DuckLake tier.
DuckDB can nevertheless seal an explicit task watermark to an atomic Zstd Parquet
segment, with byte-reproducible exports and hot-versus-sealed row equality tests.
An isolated 10,000-event LanceDB spike measured a 1.36 MB projection and warm mean
vector/FTS/hybrid query times of 2.06/1.16/2.90 ms on this host. The optional Python
environment occupied about 303 MB and repeat process imports took about 0.52 seconds,
so LanceDB remains lazy and optional rather than part of the base runtime.
An explicit destructive erasure API removes an exact task from the ledger and recognized
operational stores, its JSONL/notebook, uniquely referenced local artifacts, and sealed
segments carrying a task manifest, including that task's derived Lance projections. It
is not invoked automatically.
