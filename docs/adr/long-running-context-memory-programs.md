# Context programs for long-running agents

> Status: experimental implementation available; live empirical gates pending
>
> Date: 2026-09-05
>
> Related: [current architecture](../architecture.md), [trace-native tenets](../design/trace-native-repl-agent.md), [implementation status](../IMPLEMENTATION_STATUS.md), [evaluation](../evaluation.md)

## Brief: the problem, proposal, and contribution

A long-running coding agent must remember what happened, recover the current task,
and decide which evidence to inspect next. A larger transcript helps until it no
longer fits; a summary fits but can omit the detail needed to verify a decision.
Retaining every event solves storage, but does not by itself solve useful recall.
Process recovery adds another question: which actions actually happened before the
agent stopped? A remembered completion claim cannot answer that reliably.

Skein proposes treating the construction of context as an explicit computation over
retained evidence. For example, “count failed verification attempts before checkpoint
C, grouped by command, and fetch the corresponding errors” becomes a parameterized
Python function or SQL query. The function's result carries its scope, historical
boundary, source references, and completeness. A later invocation can rerun that
computation at C or request a new view at a later boundary. An optional model call
can explain the result without becoming the authority for the count.

This preserves both evidence and the method used to select it. A small working note
explains the agent's current intent; context programs retrieve or compute missing
details. The same evidence also supports deterministic task reconstruction and
effect reconciliation. The desired outcome is lower rediscovery cost and more
reliable continuation, measured through actual task execution.

The broad ideas are established. Executable memory, tiered recall, temporal records,
and programmatic context management all have precedents. The proposed contribution
is a language-independent, bounded, provenance-bearing context-program contract
integrated with a coding harness's recovery and verification boundary. Its usefulness
and any research novelty remain hypotheses, not demonstrated results.

## Decision

Implementation handoff: [the experiment runbook](../context-experiment-runbook.md)
contains complete treatment profiles, offline preflight, fixture preparation,
subprocess schedules, independent grading, and deferred scan benchmarks. The
implementation remains opt-in. Real scripted-ADK tests cover fresh context,
cross-process recall, safe interruption blocking, and successful same-invocation
resume through deterministic verification; these are not live model-quality results.

An implementation review identified a necessary additional ablation:
`context.window_management` separates bounded inner-loop reconstruction from active
retrieval. The W baseline enables windows without model-facing memory access;
R adds retrieval with the same window policy. Without W, an R/B1 improvement could
be caused by reconstruction rather than retrieval. `memory.prior_runs` separately
controls historical source access; notebook continuity never implicitly grants it.

Optional SQL and summary APIs are implemented and independently testable. SQL remains
an operator-reviewed catalog API, not arbitrary model-supplied SQL through Bash.
Semantic execution accepts an explicit versioned embedding-provider factory; no
provider is silently chosen or downloaded. Summary generation requires an injected,
authorized async model callback whose calls/usage the experiment driver must account
for. `SummaryCache.summarize(cache=...)` controls its reuse ablation; there is no
decorative `memory.summary_cache` YAML flag or unmetered automatic summarizer.

A context program is a versioned computation that constructs useful context from
durable evidence. Python functions, SQL queries, and evidence-bound model calls can
implement context programs. A database is an optional execution/storage choice.

Unify evidence access, scope, budgets, temporal semantics, and provenance. Do not
require SQL, a new DSL, a query planner, or a generalized program registry. Extend
the existing `harness/memory` runtime and typed view contracts in place; retain
existing names for compatibility rather than doing a package-wide rename.

The notebook is the authoring/composition workbench. The ledger records programs,
notes, attempts, and outcomes. The Python heap is disposable. Deterministic harness
code retains authority over authorization, effect recovery, verification, and
completion; agent-authored programs and summaries cannot override those decisions.

Deliver bounded retrieval and working notes independently of automatic crash
recovery. Establish identity and checkpoint consistency before enabling recovery.
Compare deterministic handoffs with fresh-context reconstruction empirically.
SQL, semantic retrieval, and summary caching are independently gated extensions.

## Relationship to cognitive memory architectures

Cognitive architectures provide a useful vocabulary for separating responsibilities.
ACT-R distinguishes declarative facts from procedural productions and accesses
modules through buffers. Soar combines working memory, procedural productions, and
long-term semantic/episodic systems. This predates LLM memory and already separates
stored knowledge from the mechanisms that retrieve and act on it.
Sources: [ACT-R overview](https://act-r.psy.cmu.edu/about/),
[Soar architecture](https://soar.eecs.umich.edu/soar_manual/02_TheSoarArchitecture/),
[Soar memory overview](https://soar.eecs.umich.edu/home/About/).

The following is an engineering analogy, not a claim that Skein implements those
architectures or models human cognition. The categories do not require separate
databases, agents, or services.

| Memory function | Skein representation | Important distinction |
| --- | --- | --- |
| Working memory: what is available now | Bounded model packet, recent tail, selected note/context results | The live Python heap is additional computational workspace, not automatically model-visible context. |
| Episodic memory: what happened | Ordered task/session events, attempts, receipts, source artifacts | A record is evidence of an observation or action, not proof that every recorded assertion is true. |
| Semantic memory: what is believed or known | Typed facts and evidence-bound derived views, with correction/scope metadata | Semantic memory is a kind of knowledge; vector similarity is only one retrieval technique. |
| Procedural memory: how to do or recall something | Reusable context programs and ordinary tested tool procedures | A query that constructs context is distinct from a skill that performs an external action. |
| Future intentions and monitoring | Task ledger, unresolved work, steering, verification obligations | These describe pending commitments; they cannot be reconstructed safely from advisory notes alone. |
| Consolidation and forgetting | Optional summary derivation, explicit invalidation/retention, context eviction | Dropping prompt content is different from deleting evidence or learning new facts. |

A context program crosses these categories: it may retrieve an episode, aggregate
facts, or construct working context. It is an access/computation abstraction rather
than a proposed new category of memory. A stored program for reconstructing context
is a form of procedural knowledge; its output may be episodic or semantic content.

Skein adopts the separation of knowledge, working state, and control. It does not
adopt cognitive activation/decay equations, production-rule conflict resolution,
automatic chunking, or claims of biological plausibility. Those mechanisms would
require their own workload and evaluation, not names borrowed from cognitive science.

## Related implementations and limits of the comparison

The papers and primary documentation below establish conceptual precedents. They
are not a benchmark ranking. Reported results use different models, tasks, and
success criteria; this ADR does not transfer their scores to Skein. A capability
not discussed by a source is not evidence that the system lacks it.

| System / line of work | Common or related mechanism | How this proposal differs in emphasis |
| --- | --- | --- |
| [MemGPT (2023)](https://arxiv.org/abs/2310.08560) | Agent-controlled movement between limited active context and external memory. | Formalize retrieved/computed views with temporal boundaries and provenance; tiered memory itself is established. |
| [Generative Agents (2023)](https://arxiv.org/abs/2304.03442) | Retained observations, higher-level reflections, and retrieval for planning. | Notes/reflections remain advisory; the evaluation target is verified coding and recovery, not behavioral believability. |
| [Voyager (2023)](https://voyager.minedojo.org/) | Reusable executable skills stored for later composition. | Store methods for constructing context as well as task procedures; executable procedural memory is not new. |
| [RLMs (2025)](https://arxiv.org/abs/2512.24601) | Inspect and decompose externally held context programmatically. | Add durable cross-invocation program/evidence identities and coding-specific recovery contracts; Python context access is established. |
| [Zep/Graphiti (2025)](https://arxiv.org/abs/2501.13956) | Temporal knowledge graphs combining conversational and structured evidence. | Use a common bounded computation contract without requiring a graph representation; temporal memory is prior art. |
| [Letta Context Repositories (2026)](https://www.letta.com/blog/context-repositories/) | Git-versioned memory files, scripts for context management, progressive disclosure, and background consolidation. | Keep retained evidence authoritative and views rebuildable; no requirement for a consolidation agent or mutable system-prompt memory. |
| [M★ (2026)](https://arxiv.org/abs/2604.11811) | Explicit Python memory programs covering schema, storage/read-write logic, and agent instructions, optimized through program evolution. | Closest precedent for memory-as-programs. Skein fixes safety/control contracts and starts with reviewed programs; automatic harness evolution is outside this proposal. |
| [User as Code (2026)](https://arxiv.org/abs/2606.16707) | Append-only history checkpointed into typed Python state/functions, supporting executable queries and aggregation. | Closest precedent for log-plus-executable-memory. Skein focuses on bounded context construction and recovery of coding work rather than an executable user model. |

The coding-agent source comparison reviewed Codex `986ff1cc7c`, Pi `853a80d26`,
and OpenCode `8e0f1c253b`. Configuration and later releases can change behavior:

| Implementation | Observed mechanism | Lesson for Skein |
| --- | --- | --- |
| [Pi](https://pi.dev/docs/latest/compaction) | Append-only session tree; structured summary and retained recent messages; branch summaries and extension hooks. | Preserve history and interaction boundaries; start with simple local storage. Model-facing exact retrieval is a separate design decision. |
| [Posthorse](https://github.com/fitchmultz/pi-posthorse) | No-summary context rollover for its Pi fork, durable notes, and historical recovery. | Direct precedent for the fresh-window/notes/history experiment. Its retained-history exposure policy is not Skein's authorization/redaction contract. |
| [Codex reset](https://github.com/openai/codex/blob/986ff1cc7c/codex-rs/core/src/compact_token_budget.rs) and [history/notes](https://github.com/openai/codex/blob/986ff1cc7c/codex-rs/ext/history-notes/src/tools.rs) | Conventional compaction plus model/configuration-gated fresh-window reset and history/notes access. Separate [memory extraction/consolidation](https://github.com/openai/codex/blob/986ff1cc7c/codex-rs/memories/README.md) supports later recall. | Separate window recovery from cross-session learning. Do not assume every client/model enables the same path or that backend history is immediately consistent. |
| [OpenCode V2](https://opencode.ai/v2/docs/compaction) | Durable messages outlive active context; a generated checkpoint combines summary and serialized recent tail; instruction epochs are explicit. V1 has different pruning/tail behavior. | Make context boundaries durable and historical material distinct from current instructions. A conversational checkpoint is not a workspace/effect restore point. |

### What is established, what is proposed, and what could be novel

**Established mechanisms:** external memory, programmatic retrieval, executable
skills/memory, versioned files, database views, event replay, temporal queries,
summary-plus-tail compaction, and fresh-window recovery with notes. Neither the
term “context program” nor language independence establishes research novelty.

**Proposed Skein synthesis:** one explicit contract for authorized evidence selection,
computation, bounded model exposure, and reproducible context reconstruction; combined
with receipts and checkpoints that keep execution authority in deterministic code.
Its distinguishing engineering emphasis is the boundary between what the model
remembers, what the evidence supports, and what the harness may safely resume.

**Potential contribution to establish empirically:** preserving and reusing the
computation that constructs context may improve temporal/aggregate correctness,
reduce rediscovery, and make post-reset behavior more reproducible than summary-only
or ad hoc retrieval. No claim of first invention or proven superiority is made.
A targeted literature review cannot establish uniqueness of this combination.

The decisive comparison is not just memory on versus off. It is ad hoc programmatic
retrieval versus persisted, versioned program reuse over identical evidence with
the same model, permissions, and budgets. If reuse provides no benefit, keep ordinary
tested functions and provenance, and do not build a richer lifecycle or claim that
program persistence is the source of improved quality.

## Existing Skein foundations and delivery boundary

This is an integration plan, not a greenfield memory system. Skein already has:

- shared optional canonical JSONL/DuckDB capture, temporal events, backfill,
  erasure, and byte-equal task-event reconstruction;
- Python programs for history, progress, open execution, time, and task memory;
- a restricted relational catalog and optional Lance projections;
- deterministic compaction and prompt manifests, although full live view adoption
  remains gated;
- notebook lifecycle events, snapshots, conservative data-cell restoration, and
  metadata-only heap inspection;
- ADK sessions, artifacts, resumability configuration, receipts, and checkpoints.

Before this implementation, the server refused automatic rerun after restart,
notebook continuity was run-scoped, and checkpoints used an iteration label rather
than the real ADK invocation ID. The opt-in implementation now binds checkpoints to
real invocations, verifies evidence and effects before resume, and supports explicitly
owned conversation notebook continuity. Checkpoints detect workspace state; they do
not restore it. Deterministic subprocess tests establish the tested recovery contracts,
not improved model performance or unrestricted crash safety.

The first release adds bounded recall and notes to these foundations. Later phases
test context reset, safe process recovery, and cross-session continuity. Cognitive
categories and related-work comparisons do not imply additional services or stores.

## Authority and fixed invariants

| Concern | Owner and rule |
| --- | --- |
| Live conversation/workflow | ADK Runner, session service, and resumable nodes; reuse pinned APIs. |
| Historical evidence | Canonical ledger when enabled; operational stores retain their documented roles until cutover parity is proven. |
| Task/control state | Deterministic reducers and verifier, reconstructed from recorded evidence. |
| Working notes | Advisory ledger-backed projection, including hypotheses and source references. |
| Notebook | Durable, rebuildable workbench; completed cells do not imply replay safety. |
| Heap | Disposable; restore only currently supported self-contained data cells. |
| Effects | Stable operation identities, receipts, and explicit unknown-effect reconciliation. |
| Workspace | Current files/Git plus fingerprint; a fingerprint is not a restore bundle. |
| Search/summary caches | Disposable derived data; never authority for execution or permission. |

Use ADK SQLite sessions and existing file artifacts locally. Keep ADK wiring in
`app/` or `harness/adk/`. Do not add another blob store or a second live prompt
compiler. ADK resume/rewind does not restore external effects or a Python heap.
ADK MemoryService is an optional semantic implementation only if it satisfies the
scope/provenance contract; exact reads do not depend on it.

Keep four default tools; experimental PTC uses its existing Python surface and the
same guarded capabilities. Mutable state stays out of `static_instruction`;
actual provider prefix bytes remain stable within an execution profile. Safety,
redaction, verification, and effect checks have no ablation bypass. Configuration
does not upgrade the trusted-local adapter into a production sandbox.

## Context-program contract

Extend `ViewRequest`, `ViewResult`, and `MemoryProgramRuntime`. Initially a
checked-in Python callable with an explicit version in the existing dispatch table
is sufficient registration.

```text
request = program + version + typed parameters + authorized source scope
          + immutable evidence boundary + output/work budgets
result  = bounded data + source references + program/content identity
          + applied boundary + completeness/paging metadata
```

Keep three identities separate:

- **Program:** exact source bytes, parameter/output contracts, allowed sources,
  execution kind, and relevant dependency versions. Hash SQL as written; do not
  normalize whitespace inside literals.
- **Execution:** program hash, canonical parameters, resolved scope, evidence
  boundary, recorded clock when used, effective budgets, and exposure-policy version.
- **Result:** canonical result bytes and provenance. Telemetry timestamps and
  durations do not participate in deterministic result hashes.

Python/SQL deterministic programs reproduce results for the same execution inputs.
Model calls record prompt/model/settings versions and actual output; replay uses
the recorded output, while re-execution may differ. Denials, timeouts, unavailable
sources, and incomplete scans are explicit outcomes, not empty successful results.
Large aggregate provenance uses a bounded source-view manifest rather than flooding
the prompt with event IDs.

Built-in query/control programs remain harness-owned. Model-authored Python runs
only through guarded PTC; no arbitrary code import/evaluation from query parameters.
Ad hoc cells remain recorded cells. Promote reusable Python through normal review
and tests. Extend the existing SQL candidate/shadow/active catalog only when needed;
that lifecycle is not required for every notebook experiment.

### Scope, time, composition, and exposure

Reuse owned conversation identity, bound to the ADK tuple
`(app_name, user_id, session_id)` and its child tasks/runs. Record actual invocation,
workspace, branch/lane, notebook, and kernel identities separately.

Begin with current-task reads. Add owned conversation history and explicitly selected
other sessions in Stage 5. A common project path alone never authorizes another
user's data. Scope is a server-validated binding, not an optimizer setting.

Canonical sequence is currently task-scoped. Multi-task reads freeze a manifest of
selected task IDs and watermarks; one task's sequence is not a global clock. Define
stable cross-task tie-breakers without claiming a causal total order.

Composition order:

1. Resolve authorized sources and immutable knowledge boundaries.
2. Apply sequence/recorded-time boundaries, then event-domain observed-time filters.
3. Apply structured/keyword filters; rank semantically only when requested.
4. Aggregate the complete filtered set, or explicitly label a top-k/partial aggregate.
5. Optionally summarize selected evidence with citations and coverage limits.
6. Apply egress bounds and record a retrieval receipt for the evidence actually exposed.

Resolve relative time against a recorded clock and explicit timezone. Corrections
are later evidence: an earlier `as_of` view cannot see them. Pagination binds scope,
parameters, ordering, program version, and source boundary. Bound scan work as well
as output; later appends must not change an existing cursor's snapshot.

Exact reads mean exact bytes of the authorized, redacted retained representation,
not raw secrets or provider-private reasoning. Use an allowlisted projection of
event kinds/fields and authorized artifact ranges. Some operational data remains
unavailable to the model even within its session. Historical instructions cannot
override current policy. Current authorization and erasure apply to old snapshots;
deleted evidence yields unavailable results and invalidates derived data. Exclude
retrieval receipts from ordinary history search to avoid recursive noise.

### Working notes and reconstruction

Start with one bounded active note per task: objective, hypotheses, unresolved work,
next action, and evidence references. Store idempotent updates with an expected
prior version as ledger events; render notebook Markdown when PTC is enabled.
Notes are advisory and can be wrong. They never certify success.

The dynamic handoff contains deterministic task state, unresolved effects, pending
steering, the history boundary, a bounded note excerpt/reference, and retrieval
instructions. PTC adds metadata-only state inventory, last committed kernel epoch,
and unavailable-state markers. Notes consume the existing handoff budget rather
than silently increasing total context.

Compare two small, explicit reconstruction policies:

- `handoff_tail`: deterministic handoff plus bounded recent context.
- `fresh`: the same required control state and notes/history entry point, omitting
  the prior conversational tail; persist a new context epoch before continuation.

Fresh context is not a new task, invocation, approval scope, budget, or heap.
Preserve pending user intent and tool-call/result integrity. Failed note/checkpoint
publication cannot silently discard active context. Do not enable ADK's
model-authored compactor as an implicit fallback.

## Configurable ablations

Reuse strict YAML, existing profiles, behavior hashes, `tuning-export`, and the
evaluation runner. Existing fields select canonical capture, ledger, retrieval, PTC,
context budgets, and persistence. Do not rename those fields merely for this ADR.

This is a fragment under `harness.config`; the full runnable compositions are in
`harness/config/profiles/context-*.yaml`. Unsupported provider/backend combinations
fail startup rather than silently doing nothing.
The displayed defaults preserve current behavior.

```yaml
memory:
  enabled: false                 # existing
  prior_runs: false              # explicit owned prior-run recall, independent of PTC
  ledger: jsonl                  # existing: jsonl | duckdb
  retrieval: lexical             # existing: lexical | lance
  context_programs:              # stage 1
    mode: "off"                  # off | shadow | active
    max_result_bytes: 16000
    max_scan_events: 10000
    timeout_seconds: 2
    reuse: false                 # stage 5 experiment: reviewed program library
  working_notes: false           # stage 2
context:
  window_management: false       # bounded windows independent of retrieval
  reconstruction: handoff_tail   # stages 2/3: handoff_tail | fresh
notebook_ptc:
  enabled: false                 # existing tool-surface switch
  continuity: run                # stage 5: run | conversation
adk:
  resumable: true                # existing ADK substrate
  recovery: explicit             # stage 4: explicit | safe_auto
```

With window management disabled, `off` retains current behavior. `shadow` computes fixed read-only seed/fixture
queries without model exposure or changed requests; record overhead separately.
`active` exposes bounded retrieval through the existing tool surface. Do not shadow
writes or model calls. Effective egress is the minimum of program and existing tool
limits; reuse current packet and component token budgets.

Validate before starting a worker:

- Non-off programs require canonical memory. Notes require active programs.
- Bounded windows require canonical memory. Fresh reconstruction additionally
  requires active retrieval and durable notes/handoff support.
- Safe-auto requires persistent sessions/artifacts and the completed recovery
  implementation; it never enables unknown-effect replay.
- Conversation continuity requires PTC and owned conversation binding.
- Program reuse requires active programs; expose only reviewed, version-pinned
  functions from a finite allowlist. It cannot load arbitrary notebook code.
- Lance retains existing DuckDB/provider prerequisites. Storage selection does not
  imply SQL execution or promote a program.
- Budgets must be positive and within fixed code-owned ceilings.
- Behavior is frozen per run. Changed experiment configs apply to new trials.

Use full YAML profiles based on existing profiles, not a new overlay language.
Every trial records resolved config, revision, behavior and program hashes.
Recovery, persistence, scope, and safety stay outside automatic tuning.
Only approved context behavior/budget fields enter the optimizer allowlist.

## Staged implementation and actual execution

Stages 0–2 are the first context-memory release. Stage 3 is an optional policy
experiment. Stage 4 delivers recovery after its identity/checkpoint prerequisites;
it does not depend on fresh context winning. Stage 5 extends continuity and scope.
Each stage includes focused implementation/tests, execution evidence, and a report.
The staged checks below remain the acceptance contract. The runbook distinguishes
executed deterministic tests from live experiments and scale measurements still pending.

| Phase | Smallest useful delivery | Question answered by execution |
| --- | --- | --- |
| 0 | Frozen fixtures, requests, and baseline | Can we reproduce and independently grade long-running work? |
| 1 | Bounded Python retrieval and aggregate views | Can the agent recover exact evidence omitted from active context? |
| 2 | Durable notes and handoff | Does explicit working state reduce rediscovery without making stale notes authoritative? |
| 3 | Fresh-window alternative | Can notes and retrieval replace the old tail at lower cost without losing constraints? |
| 4 | Validated checkpoints and effect-aware resume | Does process death lead to safe continuation or a correctly diagnosed blocker? |
| 5 | Explicit history scope, notebook continuity, reusable programs | Do previous experience and reusable retrieval procedures help subsequent work? |
| Optional | SQL, semantic retrieval, summary caching | Does each added mechanism improve its specific workload enough to justify its cost? |

### Stage 0 — freeze baseline and execution fixtures

Implementation:

1. Freeze a source revision, full four-tool config, provider/model/reasoning,
   workspace fixtures, and independent verification commands.
2. Extend existing eval/test infrastructure with a scripted model through the real
   factory, ADK Runner, tools, storage, and provider-request assembly.
3. Drive at least three compactions, steering, oversized artifacts, and a failed
   operation. Capture redacted provider requests, prefix hashes, tool/effect counts,
   and final verified workspace.
4. Add test-only process-kill hooks at named persistence/effect boundaries; fault
   injection is not a production YAML feature.

Execute: run twice from fresh state/workspaces and compare deterministic replay and
verification. Run a small authorized live baseline through `skein eval-run --config`.

Gate: reproducible deterministic artifacts and complete baseline accounting.
Live access unavailable means the empirical gate stays pending; scripted model
success proves integration, not model quality. No new runtime dependency.

Limitation: no new recall capability. Stage 1 addresses missing historical evidence.

### Stage 1 — bounded Python programs and exact retrieval

Touch `harness/memory/{models,runtime}.py`, existing ledger readers, reserved Bash
routing, config models/profiles, and focused memory/tool tests.

1. Extend view contracts with snapshot scope, filters, cursor, completeness, work
   bounds, and provenance while preserving existing program semantics.
2. Add `history.page@1` and `event.read@1` over JSONL/Python; reuse progress/time
   reducers. Add one fixed count-by-kind/status reducer over the full filtered set
   to test aggregation without a generic planner. Artifact ranges obey
   authorization/redaction.
3. Add reserved `memory history/search/read` commands, following FFF routing. PTC
   initially calls the same commands through its shell broker; typed helpers may
   wrap this service later.
4. Record retrieval receipts; expose a small fixed capability hint only in active
   mode. Integrate with the existing live context path, not a second compiler.
5. Implement mode/budget validation, behavior hashing, and profile tests.

Execute: off/shadow request-byte parity; exact event/artifact recovery after three
compactions; append-during-pagination, erased data, unauthorized IDs, malformed
cursors, scan exhaustion, and deadline tests. Benchmark actual reads at 1k/10k/100k
events, recording p50/p95 latency, RSS, bytes processed/exposed, and completeness.

Live pair: active retrieval versus programs off, with canonical capture, model,
tools, thresholds, and budgets otherwise fixed. Include evidence present only
before compaction and beyond inline output truncation; grade source IDs/bytes and
the final workspace independently.

DuckDB additionally maintains the measured hot full-set aggregates
`events.count` and `failures.by_kind` transactionally with their source watermark
and stream hash. These programs use the projection only for unfiltered current-state
queries; temporal, searched, filtered, and cross-ledger requests scan bounded canonical
evidence. The projection is disposable and deterministically rebuilt from
`ledger_events`, which remains authoritative.

Gate: deterministic/exposure tests pass; live retrieval recovers required evidence
and meets the common promotion criteria. Keep linear scans while latency fits the
declared budget. DuckDB is not a prerequisite.

Program deadlines must cover scanning and result construction. Check elapsed time
between bounded reads and enforce artifact byte limits. Arbitrary Python/SQL needs
a killable worker before admission; a timeout around an uncancellable synchronous
call is not a resource limit.

Limitation: the model must rediscover current intent. Stage 2 provides working notes.

### Stage 2 — durable notes and bounded handoff

Touch ledger note events/reduction, live context/compaction, notebook Markdown
projection, config, and provider-request integration tests.

1. Implement the versioned task note and `memory note read/write` through the
   existing guarded router, with optimistic concurrency and operation receipts.
2. Add task state, notes references, history boundaries, unknown effects, and pending
   steering to the dynamic handoff, preserving the recent-tail policy.
3. Add existing PTC metadata and kernel epoch; mark unavailable state explicitly.
4. On publication failure retain the prior valid context. Note updates never mutate
   permissions, verification outcomes, or authoritative task completion.

Execute: compaction/restart after note writes, conflicting edits, failed cells,
stale notes, and missing artifacts. Capture actual requests to prove large notes,
artifacts, and heap values cannot inflate packets or mutate static prefix bytes.

Live pair: Stage 1 versus Stage 1 plus notes/handoff. Exercise corrected instructions,
unfinished subtasks, deliberately wrong advisory notes, and an empty restarted heap.
Measure rediscovery calls, stale assertions, verified success, and total note/retrieval
cost. Do not assert exact natural-language note text in pytest.

Gate: durable advisory notes, bounded handoff, and common empirical criteria pass.
This completes the first useful release with explicit continuation.

Limitation: the recent tail may carry irrelevant context. Stage 3 tests removing it.

### Stage 3 — fresh-context experiment

Touch only the existing context-selection boundary and ADK integration.

1. Add fresh versus handoff-tail selection without a pluggable policy framework.
   Preserve the same required control packet, notes, retrieval, and agent.
2. Persist the epoch then reconstruct model input. Retain durable ADK/canonical
   history and transition at complete tool boundaries.
3. Preserve task/invocation identity, pending steering, approvals, budget counters,
   and execution state. Failure cannot leave a half-published context transition.

Execute: serialized requests show old conversational bodies absent and required
control state present. Kill before/after epoch publication and reconstruct the old
or new complete state. Force three resets and recover exact early evidence.

Live pair: fresh versus Stage 2 handoff-tail with identical model, tools, notes,
thresholds, and total budgets. Include rare early constraints and note-write failure.

Gate: common empirical criteria pass. Otherwise retain handoff-tail; Codex's gated
behavior is not evidence of effectiveness for every Skein model.

Limitation: context reconstruction does not prove safe interrupted-effect recovery.

### Stage 4 — identity, checkpoint consistency, then safe resume

Depends on Stage 0 and Stage 1 ledger/scope contracts, not Stage 3 promotion.
Touch checkpoint models/stores, receipts, `app/agent/workflow.py`, and
`harness/server/runtime.py`.

1. Persist real ADK invocation IDs and owned conversation/run/workspace mappings.
   Reuse shared canonical capture. Reject concurrent conversation writers and
   conflicting ownership of one mutable workspace.
2. Persist operation identity before dispatch, then receipt or unknown outcome.
   Identity survives replay; command-text equality alone cannot deduplicate
   intentional repeated operations.
3. Persist referenced events/artifacts/receipts first and publish checkpoint marker
   last. Bind per-stream watermarks, schema/reducer versions, task-state hash,
   workspace fingerprint, context epoch, and optional notebook boundary.
4. Repair rebuildable projections. Ignore incomplete publication; block referenced
   corruption. Falling back to an older checkpoint still examines all later effects.
5. Reconstruct post-checkpoint state before checking workspace divergence.
   Distinguish receipted own writes from outside changes. Unknown shell/external
   effects require evidence-based reconciliation or explicit operator action.
6. Only then enable safe-auto via the pinned ADK resume API, same invocation and no
   duplicate user message. Preserve cancellation, approval expiration, deadlines,
   spent budgets, queues, and verification requirements.

Execute: kill an actual server/scripted-ADK subprocess around intent, dispatch,
filesystem effect, receipt, ADK event, verification, checkpoint, and compaction.
Restart on the same disk. Test effect-without-receipt, own writes after checkpoint,
expired approval, exhausted budget, cancellation, and duplicate continuation.
Specify transaction/flush durability for process death; power-loss durability is
a separate claim. Derive open-effect state from evidence rather than adding another
authoritative mutable store.

Gate: each case resumes once to verified completion or blocks for its specified
reason; no unknown effect is automatically retried. Compare explicit continuation
and safe-auto on identical fault schedules for recovery time, success, and operator
interventions. Follow with authorized live interrupted-task trials. Do not claim
exactly-once execution of arbitrary external commands.

Limitation: same-machine recovery; conversation notebook continuity remains Stage 5.

### Stage 5 — prior-run, explicit cross-session, and notebook continuity

Depends on Stage 4 identity/lifecycle contracts. PTC remains optional.

1. Extend retrieval to a frozen manifest of owned prior runs. Other sessions require
   explicit source selection checked by the server; project matching is insufficient.
2. Add conversation PTC continuity using existing notebook reduction/snapshots and
   safe-cell restoration. Preserve run attribution and task-local notes; select prior
   notes explicitly rather than silently merging objectives.
3. Maintain one kernel owner per conversation/lane. Restore self-contained data cells
   only: definitions/imports/calls/dependent computations remain unavailable or
   reconciliation-required under current rules. Do not pickle the heap.
4. Extend existing erasure/invalidation to cross-session views and shared artifacts.

Execute: A records a fact; B in the same conversation retrieves it; C in a separately
selected session retrieves it explicitly. Test denied users, absent grants, deleted
evidence, branch divergence, dead kernels, and simultaneous starts. Old approvals
and completion status never propagate through memory.

Live pairs: task-only versus explicit prior-run recall with four tools fixed;
separately run versus conversation PTC continuity with PTC fixed. Measure cold-start
rediscovery, correct reuse, stale-fact errors, and verified task outcomes.

Gate: access/lifecycle checks and common empirical criteria pass. Automatic global
memory consolidation/project-memory injection is not introduced.

After those continuity checks, test program reuse as an independent treatment:

1. Record context computations authored during training/development tasks. Review
   and test a small reusable subset as ordinary Python functions, with fixed versions,
   typed inputs, and no embedded answers. No learned program is admitted from held-out
   test data. Include preparation/review cost in the reuse-cost report.
2. Add `context_programs.reuse` to make that finite library available; primitive
   history/read/filter capabilities remain identical in both treatments. Where PTC
   is used, both arms use PTC so tool-surface changes do not explain the result.
3. Run repeated and shifted tasks with reuse off/on, matching initial evidence,
   context policy, and budgets. Use changed dates, commands, repositories, and source
   scopes to detect hardcoded assumptions. Pin versions during a trial.
4. Measure program validity, semantic correctness, calls saved, total cost including
   construction, cross-task reuse, and results after restart/schema incompatibility.
   Reject incompatible versions explicitly; do not execute stale code silently.

This isolates the procedural-memory hypothesis from simple evidence retention.
If reuse fails to help, leave it off and retain ad hoc composition; do not add a
general registry or automated program evolution to rescue the claim.

Limitation: no portable workspace restore or distributed execution.

### Independently gated extensions

| Extension | Reuse and implementation | Actual execution gate |
| --- | --- | --- |
| SQL | Existing catalog; typed parameters, full contract hashes, snapshot filtering, forcibly interruptible time/memory/output limits, external access disabled | Python/SQL equality for joins/full-set aggregates; literal-whitespace regression; kill timed-out worker; benchmark scale. Live pair only when model-visible behavior changes. |
| Semantic retrieval | Existing Lance projection plus explicit versioned embedder; structured scope/time filtering and canonical hydration | Paraphrase/exclusion/temporal corpus, stale/deleted index rejection, and paired end-task outcomes including index/build/embedding costs. |
| Summary calls/cache | Advisory evidence-bound program, source manifest, prompt/model/settings identity, cache keyed by execution inputs | Cached/uncached comparison, correction/deletion invalidation, factual support and repeated-synthesis savings including failed-task costs. |

SQL does not depend on notebook continuity. Summary caching does not depend on
semantic retrieval. Retain current Lance/DuckDB prerequisites initially. Build/reuse
index snapshots on demand, not on every event append. Portable/distributed recovery
requires a separate deployment decision and a real workload.

## Empirical protocol and treatment profiles

Create full YAML profiles alongside current profiles only as their stage lands.
The table lists deltas; each trial stores complete resolved configuration.
Do not execute a Cartesian product of all switches.

| Treatment | Change from comparator | Comparator |
| --- | --- | --- |
| B0 | Frozen existing four-tool behavior | Current baseline |
| B1 | Canonical JSONL enabled, programs off | B0, mechanical capture parity/overhead |
| H | Programs shadow | B1, identical model requests |
| W | Bounded windows enabled, programs off | B1; isolates reconstruction |
| R | Programs active, bounded windows enabled | W; interface and fixed hint are the declared treatment |
| N | Notes enabled, handoff-tail | R |
| F | Fresh reconstruction | N |
| A | Safe-auto recovery | N with explicit recovery and identical crash schedule |
| P | PTC enabled | Winning four-tool profile, separate surface experiment |
| PC | Conversation PTC continuity | P with run continuity |
| U | Reviewed context-program reuse enabled | Same active-retrieval/PTC profile with reuse off |

Keep SQL/Python, lexical/semantic, and cached/uncached summaries as separate pairs.
Compare JSONL/DuckDB mechanically when model-visible context is identical.

### Hypotheses and comparisons tied to prior work

| Hypothesis | Closest precedent | Controlled comparison and falsification |
| --- | --- | --- |
| External evidence improves recall after compaction | MemGPT, Pi, Codex, Posthorse | R versus W; if evidence recovery/task success does not improve at acceptable cost, do not claim value from active retrieval. |
| Working notes improve continuation | Cognitive working-state separation, Posthorse/Codex, Letta | N versus R; measure wrong-note resistance as well as reduced rediscovery. Reject if notes amplify stale beliefs. |
| Fresh windows can replace retained tails | Codex/Posthorse versus Pi/OpenCode-style retention | F versus N; rejection means handoff-tail remains the default, not that persistent memory failed. |
| Computation handles temporal/full-set questions better than selected snippets | User as Code, Zep temporal modeling | Full-set deterministic views versus bounded snippet retrieval over the same ledger; grade exact counts, knowledge boundaries, and completeness. Both arms retain access controls. |
| Reusable context procedures improve repeated work | M★, RLM programmatic access, executable skill libraries | U versus reuse-off with identical primitives; improvement must exceed construction/selection cost and generalize to held-out parameters. |
| Evidence-bound checkpoints improve recovery | Event replay and existing ADK/Skein execution mechanisms | Safe-auto versus explicit continuation with identical fault schedules; no safety checks are disabled. |

These are mechanism ablations inside Skein. A handoff-tail baseline is not a full
Pi/OpenCode reproduction, and ad hoc Python is not an implementation of RLM or M★.
Any later native-harness benchmark must freeze each harness/model/config separately
and report protocol, scope, and verifier differences. Do not label internal ablations
as head-to-head wins against those systems.

### Cases, measurements, and promotion

Start with six executable cases: early exact evidence, corrected temporal fact,
full-set aggregation beyond one page, oversized artifact, interrupted mutation,
and later-run recall. Exercise negative scope cases in deterministic tests.
Each case supplies workspace fixtures, interaction/steering/fault schedules,
expected evidence assertions, and a held-out verification command. Add a stress
case with 50+ tool calls, three context transitions, and a cold process restart.

For each stage:

1. Run focused pytest contracts and live-entrypoint/scripted subprocess checks.
   For implementation changes run required repository lint/type/unit/integration checks.
2. Run the six-case live smoke once per treatment to expose integration failures.
3. Run three attempts per case per treatment: 36 trials per pair. This is a pilot,
   not a broad quality claim.
4. Freeze the candidate and confirm on at least 18 held-out tasks with two attempts
   per treatment. Cluster intervals by task; repeated attempts are not independent.

Freeze model snapshot, reasoning/generation, tool/policy surface, task revisions,
provider, thresholds, budgets, and verifier per pair. Counterbalance trial order;
record warm/cold caches. Isolate workspace and state per trial, except intentional
continuity inside that trial. Never leak learned notes between treatments.

Record pass rate, unsupported/stale evidence, scope errors, duplicate/unknown effects,
recovery success/time, cost per passed task, uncached input, cache-read ratio,
output tokens, model/tool calls, median/p95 wall time, context bytes, and query
latency/RSS. Include notes, summaries, embeddings, failed trials, and recovery costs.
Unavailable cost is null, not zero; separate API-equivalent estimates. No passes
means cost per pass is undefined, not an improvement.

Before running, declare promotion thresholds. Suggested defaults: all deterministic
contracts pass, zero observed scope/duplicate-effect violations, and held-out evidence
of either better verified success at an agreed cost ceiling, or at least 10% lower
cost or latency with no more than a 5-percentage-point success regression.
Report confidence intervals; insufficient evidence keeps the feature opt-in and gate
pending. The sample floor may not resolve that margin. Revised thresholds require
a new experiment identity, not reinterpretation of completed results.

### Running the stages

Reuse `skein eval-run --config` for individual tasks and existing matrix/import/
analysis where its schema fits. Current fixed-provider/Harbor experiments must not
be silently repurposed. Extend them explicitly for context cases if needed; start
with ordinary trial files and the existing result schema instead of a new scheduler.

The following uses existing runner syntax and the implemented retrieval profile;
substitute an authorized frozen provider/model and prepared fixture paths:

```sh
skein eval-run \
  --config harness/config/profiles/context-retrieval.yaml \
  --workspace /tmp/skein-trial-r-01/workspace \
  --state-root /tmp/skein-trial-r-01/state \
  --task-id evidence-r-01 \
  --provider openrouter --model "$SKEIN_EVAL_MODEL" \
  --api-key-env OPENROUTER_API_KEY --reasoning high \
  --wall-time-seconds 900 \
  "Complete the fixture task and verify its acceptance criteria."
```

Prepare directories and approved dependencies beforehand. Multi-turn/crash cases
need a thin driver through the real server protocol, not unrelated eval-run calls.
Have it emit the same result/artifact contract. Use Agents CLI evals for model
behavior where supported and scripted models for offline integration. Keep credentials
outside workspaces; follow [existing live-run authorization](../evaluation.md).
This ADR authorizes no provider spend or credential workaround.

Each trial emits resolved config/hash, fixture/source revisions, canonical evidence
references, redacted provider-request captures, program/retrieval receipts, context
boundaries, fault schedule, independent verification, and a machine-readable result.
Failed trials remain in the ledger. Stage reports state paired outcomes, limitations,
and promote/retain/reject decisions. Config parsing or a demonstration notebook alone
does not validate a stage.

## Implementation slices and stopping rule

Commit each slice with its implementation and focused tests; separate independent
behavior changes and their empirical reports.

1. Baseline fixtures and config/result identity capture.
2. Bounded Python program contracts and exact reads; shadow parity.
3. Active reserved-command routing and retrieval evaluation.
4. Durable notes, bounded handoff, and notes evaluation.
5. Fresh-context selection and paired evaluation.
6. Real invocation binding and durable operation identities.
7. Checkpoint publication/reconciliation and crash tests.
8. Safe-auto recovery and interrupted live evaluation.
9. Prior-run/explicit-session retrieval, then optional conversation PTC continuity.
10. Optional reviewed-program reuse and its held-out reuse-versus-ad-hoc evaluation.
11. Only justified SQL, semantic, or summary extensions, independently tested.

Stop after slices 1–4 for the first context-memory release. Finish slices 6–8 for
safe local process recovery whether or not fresh context wins. Keep defaults until
empirical gates pass. Do not add a generalized registry, consolidation agent, query
DAG, distributed store, or snapshot manager without a demonstrated workload.

## References

- [Pi-inspired design](../design/pi-inspired-adk-coding-harness.md)
- [Existing evaluation workflow](../evaluation-experiments.md)
- [Posthorse](https://github.com/fitchmultz/pi-posthorse)
- [Pi compaction](https://pi.dev/docs/latest/compaction)
- [OpenCode V2 compaction](https://opencode.ai/v2/docs/compaction)
- [Codex reset at reviewed revision](https://github.com/openai/codex/blob/986ff1cc7c/codex-rs/core/src/compact_token_budget.rs)
- [Codex history/notes at reviewed revision](https://github.com/openai/codex/blob/986ff1cc7c/codex-rs/ext/history-notes/src/tools.rs)
- [ADK resumability](https://github.com/google/adk-python/blob/main/src/google/adk/apps/_configs.py)
