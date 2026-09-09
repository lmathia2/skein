# Skein implementation TODO

## Context programs and long-running recovery

- [x] Add strict, disabled-by-default context-program, reconstruction, recovery, and
  owned-continuity configuration with dependency and budget validation.
- [x] Integrate bounded history/aggregate programs, receipts, notes, and safe artifact reads.
- [x] Publish replayable context epochs and bounded advisory/PTC handoffs.
- [x] Bind operation identities and checkpoint integrity to safe ADK resume.
- [x] Add explicitly owned prior-run recall and conversation notebook continuity.
- [x] Prepare executable context experiment profiles, schedules, and independent graders.
- [ ] Run the ADR's live paired experiments and decide promotion independently per feature.

See [the context and memory ADR](adr/context-and-memory.md). Implementation
does not satisfy the live empirical gates; default behavior remains unchanged.

## Project identity

- [x] Rename the distribution, CLI, launchers, runtime/config identity, environment
  prefix, documentation, terminal labels, and repository to Skein; document the name.

## Optimization-facing behavior configuration

- [x] Externalize model generation, stagnation routing, and stable project-context
  budgets; export the safe prompt/model/context/tool/cache surface with behavior-hash,
  outcome-metric, and redacted-trace contracts for external optimization loops; ship
  fully annotated four-tool, PTC+JSONL, and PTC+DuckDB standard configurations.
- [x] Enforce byte-stable provider request prefixes across worker calls and derive
  Codex/OpenResponses cache routing keys from the complete stable request shape.

## Trace-native notebook PTC

- [x] Add a disabled-by-default, local-only notebook PTC configuration gate.
- [x] Add deterministic append-only notebook reduction and nbformat materialization.
- [x] Add a persistent CPython worker with bounded output, timeout termination, and safe-cell restoration.
- [x] Route nested file and shell capabilities through existing policy, approval, receipt, and redaction paths.
- [x] Record submitted/completed/failed/timed-out cells and requested/completed/failed/blocked capabilities.
- [x] Externalize oversized rich MIME outputs as content-addressed artifacts.
- [x] Add the canonical DuckDB event schema, idempotent writer, temporal reads, deterministic importers, and shadow capture for task events, receipts, checkpoints, and traces.
- [x] Shadow-capture approvals, steering, metrics, public/run events, and redacted ADK session lifecycle alongside task events, receipts, checkpoints, and traces.
- [x] Add idempotent historical backfill for recognized JSONL, SQLite, and ADK session stores with deterministic hashes and source-count equality auditing.
- [x] Prove byte-level semantic equality for the live task-event reader, then serve task state, recent context, and compaction from canonical ledger events with idempotent compatibility-store read repair.
- [x] Implement deterministic history, progress, open-execution, time, task-memory, and dream-mode views plus receipt-bearing P0-P3 prompt manifests.
- [x] Add an optional immutable LanceDB hybrid-search projection with canonical event provenance; keep DuckDB as the sole ledger authority.
- [x] Preserve the main four-tool path when canonical memory is disabled and add a canonical JSONL fallback with byte-equal deterministic views.
- [x] Make DuckDB and LanceDB optional installation extras selected through validated YAML.
- [ ] Wire an explicit embedding provider before allowing live `retrieval: lance` prompt use.
- [ ] Cut ledger views into live prompt/compaction readers after byte, cache, and correctness ablations pass.
- [x] Add candidate, shadow, active, retired lifecycle enforcement for restricted relational memory programs.
- [x] Add atomic deterministic Parquet sealing with hot-versus-sealed watermark equality; defer DuckLake until scale measurements justify it.
- [x] Add explicit physical task erasure covering ledger rows, recognized operational SQLite rows, JSONL, notebooks, uniquely referenced artifacts, and manifested sealed segments.
- [x] Run the four-tool versus notebook-PTC quality, token, latency, and cache-hit
  ablation before changing the default; the six-task gate failed, so four tools
  remain the default.
- [x] Define notebook PTC's supported execution boundary as trusted local workspaces; production/adversarial isolation is an optional future deployment profile, not an activation gate.
- [x] Add standalone executable notebooks for PTC state, cache-aware compaction, and versioned trace-memory programs.
- [x] Project task, public message, steering, and compaction events into timestamped notebook Markdown cells.
- [x] Add task-scoped notebook rematerialization and compact required `nb-cli`
  inspection without a notebook-JSON parsing fallback.
- [x] Snapshot the complete logical notebook as one immutable content-addressed
  artifact when its PTC worker closes; keep the ledger authoritative and the live
  Python heap ephemeral.
- [x] Classify only non-externalizing local `nb-cli` reads as automatic; keep notebook execution and mutation approval-gated.
- [x] Make failed PTC cells transactional by discarding the dirty kernel epoch;
  restore only previously committed cells before accepting more work.
- [x] Restrict automatic cell replay to self-contained data construction; classify
  calls, imports, definitions, attribute/subscript access, and loaded-name dependencies
  as requiring reconciliation.
- [x] Add bounded `agent.state.list()` and `agent.state.describe(name)` metadata views,
  compact state deltas, and a regression proving bulk nested capability results remain
  in Python until explicitly selected.
- [x] Key notebook/REPL recovery by stable owned conversation identity rather than a
  server run ID, with process/server restart and concurrent-run rejection tests.
- [x] Add the metadata-only REPL state catalog and last committed kernel epoch to the
  compaction handoff without moving them into the cache-stable prefix.
- [x] Capture actual serialized provider requests and prove long-session context grows
  with selected egress rather than nested result bytes or the live heap.

See `docs/design/trace-native-repl-agent.md` for tenets, contracts, phased gates,
and the implementation/evaluation rubric.

## PTC execution refinement

### Modular architecture execution checklist

- [x] U1: Prebuild the pinned ADK sandbox image and verify its package contents.
- [x] U2: Reuse one trusted-eval container per sequential worker with a fresh
  interpreter, workspace view, environment and tool package per example; prove
  descendant cleanup, no cross-example state, replacement on reset failure, and
  container/start/reset metrics.
- [x] U3: Normalize PTC model-visible results to a compact stable envelope and make
  top-level plus nested tool usage queryable through versioned trace-memory views.
- [x] U4: Replace scattered PTC assembly branches with one closed dispatch point;
  document and exhaustively test behavior with PTC/memory/context independently off.
- [x] U5: Consolidate memory programs into one code-owned `(name, version)` registry
  used by configuration and execution; remove unused competing prompt/program code
  only after proving it has no production callers.
- [x] U6: Extract context selection as a pure policy computation while retaining one
  renderer, stable-prefix bytes, complete call/result pairs and bounded evidence.
- [ ] U7: Re-evaluate the resulting module combinations from first principles,
  update the canonical support matrix, and run unit/integration/type/lint plus the
  controlled live comparison before changing defaults.
  - [x] Audit and document the actually supported and rejected combinations.
  - [x] Pass the full unit suite, runnable integrations, full lint, and changed-code typing.
  - [x] Clear the production `app`/`harness` Pyright baseline; executable tests remain
    covered by pytest rather than static analysis of their deliberately dynamic fakes.
  - [ ] Run the matched provider comparison; keep four tools as the default until that
    gate passes. Prior live PTC artifacts confirm OpenRouter execution with
    `meta/muse-spark-1.3-contributor`; the 2026-09-09 task process did not inherit its
    intentionally unpersisted `OPENROUTER_API_KEY`. The matched model/revision contract
    also remains unfrozen. The pinned ADK image and its reset-isolation integration passed.
  - [x] Run a one-task, four-mode matched OpenRouter smoke with memory disabled. Prime
    used the fewest calls, wall time, and serialized request bytes; ADK Code Mode
    exhausted the 200k input budget. See `docs/audits/ptc-matched-smoke-2026-09-09.md`.

- [ ] Extract the shared `PtcRuntime` and `PtcSession` contracts behind
  `execute_code` without changing either current implementation's tool declaration.
  - [x] Extract native lifecycle adapters behind a shared `PtcSession`.
    Keep runtime-specific protocols separate until cross-pairing requires more.
- [ ] Separate PTC serialization (`notebook` or ledger-only `jsonl`) from runtime-state
  recovery (`none`, `replay_safe`, or bounded `snapshot`) and reject invalid combinations.
- [x] Replace bundled memory implementation selection with exact code-owned
  `(program name, version)` configuration and receipt identity.
- [x] Add the pinned Prime REPL runtime and bounded snapshot policy as optional
  implementations without importing Prime's daemon, TUI, or session authority.
- [x] Bundle the PTC-specific Python dependencies with licenses and provenance;
  verify Prime recovery without site-packages and ADK SDK import without installed extras.
- [x] Run the deterministic composition matrix across PTC, memory-program, context,
  serialization, state, and continuity selections, including explicit rejection of
  unsupported combinations.
- [ ] Run the matched live comparison before changing the four-tool default; retain
  the preflight blockers recorded under U7 until the experiment contract is frozen.

- [x] Make Skein notebook PTC and vendored ADK Code Mode selectable ADK tools,
  and independently select optional trace-native or Pi-derived memory for
  matched ablations.
- [x] Add executable notebooks demonstrating both PTC tools and all optional
  memory strategies without requiring credentials or a running sandbox.

- [x] Reconcile the PTC proposal with the Anthropic sources, reference runtimes,
  and current Skein execution path; publish an actionable implementation plan.
- [x] Add machine-readable capability results and actual provider-payload checks.
- [x] Add executable phase-aware programming examples using the existing policy surface.
- [x] Add bounded brokered parallel reads and deadline/reconciliation checks.
- [x] Reduce redundant verification/snapshot work without weakening evidence.
- [x] Freeze bounded context handoffs at explicit cache-stable epochs.
- [x] Validate the bundle on the same six DeepSWE tasks with Muse Spark 1.3 Contributor.

See [PTC execution refinement](design/ptc-execution-refinement.md) for scope,
implementation seams, deterministic checks, and the single bundled live screen.
This replaces per-feature paid ablations for this proposal, not the safety or
independent-completion contracts. Existing runtime defaults remain unchanged.

## Pi terminal experience

- [x] Prove standalone Pi toolkit reuse with deterministic rendering fixtures.
- [x] Separate public replies from workflow control and support non-coding turns.
- [x] Preserve conversations and wire queues, model/auth controls and resources.
  - [x] Add cancellable server-owned provider authentication controls.
  - [x] Connect Pi-style login/status/logout dialogs to the authenticated server.
  - [x] Wire searchable model selection and saved defaults to actual next-turn ADK configuration.
  - [x] Expose bounded read-only transcript pages from durable public events.
  - [x] Connect `/resume` and historical transcript navigation to those pages.
  - [x] Expose trusted resource metadata and actual skill selection through the server.
- [x] Complete Pi-style UI and migrate installation/launching.
  - [x] Prevent stored approvals from leaking across tasks or surviving expiration in a shared adapter.
  - [x] Wire command approval decisions to waiting worker/verification execution and terminal controls.
  - [x] Stream eligible public replies with immutable control headers, verification gates and reconnect tests.
  - [x] Finish quiet activity presentation and live visual/latency comparisons.
  - [x] Migrate installation/launching after the new client passes the delivery gates.
- [x] Verify conversational/coding examples, replay and harness swap; remove Go TUI.

See `docs/design/pi-terminal-migration.md` for delivery gates.

## Minimal-harness simplification

- [x] Remove Magnitude, LiteLLM integration, and installer/launcher branches; retain Codex and native ADK provider seams.
- [x] Replace shadowed legacy tools with the tested atomic file primitives and remove unwired adapters.
- [x] Reduce fixed-graph configuration and optional orchestration layers without weakening verification or approvals.
- [x] Remove ADK's model-authored compactor; derive bounded memory views from the append-only log.
- [x] Give the ADK worker sole ownership of the coding loop and fail closed on non-terminal results.
- [x] Add one durable counterexample pass before coding-task verification without
  expanding the worker or tool topology.
- [x] Verify retained paths and report source-line and McCabe-complexity changes.

Each checked item is committed independently.

## Fixed-intelligence benchmark evaluation

- [x] Add a Harbor 0.22 host-side external agent that maps Skein's execution
  boundaries into the task environment without copying provider credentials.
- [x] Freeze deterministic 6/18/42/105-task DeepSWE 1.1, Terminal-Bench 2.1,
  and SWE-Atlas-QnA manifests with immutable task hashes and equal-weight scoring.
- [x] Add fail-closed experiment matrices, sequential task selection, official
  Harbor reward import, idempotent result ledgers, and paired task-level analysis.
- [ ] Install Docker or select another supported Harbor environment, then run all
  selected official oracles; SWE-Atlas also requires its approved judge key.
- [ ] Freeze the approved subscription account/workspace, Luna/max snapshot,
  client version, and harness revisions after the authorization gate passes.
- [ ] Run the six-task live adapter smoke, 18-task Skein ablation, 42-task
  multi-harness pilot, and 105-task/two-attempt finalist confirmation in order.

## Historical delivery record

The entries below describe earlier deliveries, not the supported feature inventory.
See `IMPLEMENTATION_STATUS.md` and `simplification.md` for current capabilities and
intentional removals.

- [x] Commit the Pi-inspired ADK coding-harness design brief.
- [x] Add an Agents CLI-compatible prototype scaffold and pin upstream Google skills.
- [x] Implement typed task, ledger, tool, context, checkpoint, and verification models.
- [x] Implement deterministic context compilation, prefix hashing, and coding-aware compaction.
- [x] Implement the environment abstraction, command policy, bounded output, and four coding tools.
- [x] Implement repository discovery, lexical/structural indexing, and compact repository maps.
- [x] Pin native FFF search and expose bounded, cursor-paginated discovery through the existing `bash` tool.
- [x] Implement event reduction, SQLite persistence, tool receipts, steering, and checkpoints.
- [x] Implement deterministic verification and acceptance-criterion evidence.
- [x] Wire the ADK 2.x coding agent, deterministic verification workflow, caching, and resumability.
- [x] Add unit, integration, resume, security, and Agents CLI evaluation fixtures.
- [x] Add CI and operational documentation after the MVP contracts are stable.

## Declarative runtime and interactive client

- [x] Define strict YAML composition, volatile runtime bindings, ADK model/App assembly seams, and versioned AG-UI/control protocol contracts.
- [x] Replace singleton application wiring with a closed registry of harness factories that assemble and reuse ADK primitives.
- [x] Adapt the current coding harness to the common runtime and prove a second registered test harness can be selected without server or client changes.
- [x] Make safe Pi prompt/model choices executable configuration and reject unsupported topology or agent changes during parsing.
- [x] Implement the durable run registry and bidirectional WebSocket/AG-UI server with replay and backpressure.
- [x] Implement a Bubble Tea TUI that depends only on the public protocol.
- [x] Add deterministic harness-swap, reconnect, replay, steering, cancellation, and client compatibility tests.
