# AI coding harness rubric and implementation audit

Date: 2026-08-29

Harness revision: `04d06c3`

Primary source: *AI Agents in Depth: Design Principles and Engineering Practice*,
version 2.0 (2026-08-26), especially Chapters 1–7, 9, and 10.

## Executive assessment

The repository has unusually broad architectural coverage for a young coding
harness: a small model-facing tool surface, deterministic context assembly,
indexed retrieval, durable execution, a WebSocket protocol, steering, sandbox
adapters, trace capture, project memory, and guarded skill trials all exist as
real code with substantial deterministic tests.

The plan/execute/verify/refine cycle materially strengthened the live path. Typed
environmental evidence now decides completion; behavioral tasks require behavioral
verification; model and run liveness are bounded; hidden reasoning renews liveness
without leaking chain-of-thought; tool telemetry is live; tools cannot block the
WebSocket loop; project instructions require explicit trust; production mode refuses
the local sandbox; learning excludes false-positive episodes; and Magnitude startup
probes real inference while exposing reasoning effort.

The revised evidence-weighted score is **83/100 (production-capable control plane,
operationally constrained)**. This does **not** satisfy the requested 90-point SOTA
gate. The fresh local-model suite passed 4 of 10 held-out tasks with zero false
positives. Six safe non-completions were dominated by Magnitude/provider first-event,
idle, and throughput failures. The harness now detects and contains those failures,
but it cannot be called fast, token-efficient, or production-proven until declared
fallback models and a representative provider achieve the release SLO.

## Method

The audit uses three evidence layers:

1. **Contract evidence**: source code implements the behavior outside prompts.
2. **Deterministic evidence**: focused tests prove the contract and failure path.
3. **Operational evidence**: representative coding tasks demonstrate quality,
   latency, token cost, recovery, and safety under the actual model/provider.

Documentation alone can explain intent but cannot score above maturity level 1.
A unit-tested component without representative task evidence cannot score above
level 3.

### Maturity scale

| Level | Meaning | Minimum evidence |
|---:|---|---|
| 0 | Absent or contradicted | No implementation, or the live path bypasses it |
| 1 | Specified | Documentation, prompt rule, interface stub, or unused schema |
| 2 | Implemented | Happy-path production code is connected to the live path |
| 3 | Deterministically verified | Boundary, error, replay, and security tests pass |
| 4 | Operationally validated | Representative repeated tasks meet declared SLOs with auditable traces |

Each category earns `weight × maturity / 4`. Fractional maturity is allowed when
the evidence sits between levels.

### Grade bands

| Score | Interpretation |
|---:|---|
| 90–100 | State-of-the-art evidence: broad, efficient, reliable, and production-proven |
| 80–89 | Production-capable with bounded, documented limitations |
| 70–79 | Strong beta; core loops work but one or two release-critical gaps remain |
| 55–69 | Research beta; broad features, uneven end-to-end reliability |
| 40–54 | Integrated prototype |
| 0–39 | Early prototype or thin model wrapper |

## Book-derived principles for a coding agent

### 1. Treat the agent as model plus harness

The useful unit is not the LLM alone. A production harness expands context and
tools into five responsibilities: context management, tool interfaces,
constraints, verification, and correction. The system should remain simple,
transparent, and composable; complexity needs measured benefit.

### 2. Make context an explicit, cache-aware working set

Keep a stable prefix and append volatile state late. Use a compact status view to
surface the goal, current plan, environment, budgets, progress, and failures.
Prefer progressive disclosure, bounded reads, pagination, artifacts, and isolated
exploration over dumping raw search and tool output into the main trajectory.
Compression must preserve decisions, constraints, failed paths, sources, and
recoverable artifacts.

### 3. Design tools from the model's perspective

Tools should make likely mistakes impossible or obvious. Names, parameters,
defaults, execution arguments, and model-visible declarations must agree exactly.
General executors are valuable, but dangerous operations need code-enforced
boundaries. Perception tools should be bounded and paginated; mutation tools should
be atomic, conflict-aware, minimal, and reversible; every action should return fast,
structured feedback.

### 4. Use the repository as the durable coordination surface

Load scoped project instructions, infer build and test commands, map structure,
locate relevant code from coarse to fine, and keep code, tests, and documentation in
sync. Exact search, path search, structural relationships, and optional semantic
retrieval are complementary. Retrieval sophistication matters only if it improves
passed-task cost, files read, or turns to completion.

### 5. Plan in proportion to task complexity

Clarify ambiguous requirements. For broad changes, create a reviewable design and
plan before editing; for a tiny fix, keep the ceremony small. The invariant is that
the agent understands the goal, acceptance criteria, constraints, and non-goals
before it acts.

### 6. Let environmental verification decide when to stop

The completion claim is not evidence. A coding agent should iterate through
implement, check, diagnose, and repair until independent tests, types, lint, scope,
and acceptance checks pass. Hidden or held-out tests are essential against shallow
checks and reward hacking. A separate reviewer should judge artifacts and test
results, not inherit the producer's private reasoning.

### 7. Engineer every failure class as a closed loop

Classify API, tool, context, and control-flow failures. Decide whether retrying can
help, apply bounded exponential retry only to retryable faults, detect repeated-call
fingerprints, enforce idle watchdogs on streams, degrade or switch models when safe,
and surface failure only after recovery options are exhausted. Every failure class
needs detection, recovery, handoff, and termination.

### 8. Constrain both outcome and process

Passing tests does not justify deleting tests, escaping the workspace, exposing
credentials, or using destructive shortcuts. Separate data, input-trust, output-
impact, and cross-session boundaries. Default-disable network and destructive
operations, isolate execution, redact before storage, require precise approvals,
and retain reliable rollback.

### 9. Build for asynchronous human collaboration

Long-running agents need event streams, wake-ups, safe points, cancellation,
preemption, replay, and explicit state. Users should be able to steer without
waiting for a turn to finish. Slow work must not make the interface look dead.

### 10. Evaluate the system, not anecdotes

Measure outcome, process, and expression separately. Use reproducible task
environments, held-out verifiers, failure attribution at the first bad decision,
trajectory-prefix boundary tests, retention tests, repeated runs, confidence
intervals, and cost per passed task. Every major feature needs an independent
ablation. Observability must connect traces, tokens, tools, latency, cost, errors,
and final environmental state.

### 11. Learn only from verified, privacy-safe evidence

Operational traces become learning signals only after outcome and process
verification. Improvements can be encoded as knowledge, instructions/skills,
programs, or model parameters, in that order of reversibility. Every candidate needs
provenance, a boundary set, a retention set, an A/B gate, rollback, and privacy
sanitization. Never let self-reflection alone write durable policy.

### 12. Add multiple agents only for information gain

Parallel agents help when they contribute independent information, tools,
permissions, or execution—not merely more copies of the same reasoning. Isolate
contexts, use structured handoffs, independent validation, worktree isolation,
optimistic locking, explicit budgets, cancellation, and a deterministic merge gate.

## Weighted scorecard

| Category | Weight | Maturity | Points | Current judgment |
|---|---:|---:|---:|---|
| Architecture and composability | 8 | 3.5 | 7.0 | Strong ADK-first seams, executable prompt/model composition, swappable factory; Pi graph remains deliberately fixed |
| Task understanding and workflow governance | 8 | 3.25 | 6.5 | Typed task/ledger, action-derived progress, replan/handoff controls; explicit plan creation remains thin |
| Context and token economy | 10 | 3.0 | 7.5 | Stable bounded packets, aggregate budgets, compaction, pagination; passing runs still averaged 93k input tokens |
| Repository understanding and retrieval | 8 | 3.25 | 6.5 | Bounded FFF and incremental structure are strong; representative semantic/large-repo proof remains absent |
| Tool interface, editing, and feedback | 10 | 3.6 | 9.0 | Four tools, atomic feedback, post-write diagnostics, recoverable errors, nonblocking execution; no persistent terminal |
| Verification, correction, and completion integrity | 14 | 3.43 | 12.0 | Typed environmental evidence and behavioral gates produced zero false positives; local pass rate was only 40% |
| Safety, isolation, and trust boundaries | 12 | 3.5 | 10.5 | Explicit project trust, fail-closed production mode, approvals/redaction/receipts; local mode remains non-enforcing |
| Durability, recovery, and asynchronous control | 10 | 3.4 | 8.5 | Real replay/reconnect/steering plus bounded liveness and cleanup; provider fallback and active-call preemption remain absent |
| Observability, evaluation, and economics | 8 | 3.5 | 7.0 | Tool/event metrics reconcile exactly and traces drove fixes; repeated provider SLO and price evidence remain incomplete |
| Memory, skills, and continual evolution | 7 | 3.43 | 6.0 | Richer verified workflow signals and false-positive guards exist; promoted operational benefit remains unproven |
| Model/provider readiness | 3 | 2.67 | 2.0 | Effective identity/reasoning plus real startup probe; capability negotiation, circuit breaker, and fallback remain absent |
| Multi-agent and parallel execution | 2 | 1.0 | 0.5 | Reviewer seam and schema exist; Pi rejects parallel topology and shared-workspace runs are serialized |
| **Total** | **100** |  | **83** | **Control plane is production-capable; local-model outcome evidence is not SOTA** |

## Detailed checklist rubric

The checkboxes below describe the target. `x` means demonstrated, `~` means partial
or contradicted by operational evidence, and a blank box means absent or not yet
demonstrated.

### A. Architecture and composability — 8 points

- [x] A1. Core models, context, tools, verification, and state remain importable
  without cloud credentials.
- [x] A2. ADK owns model execution, sessions, events, caching, compaction, and resume;
  the harness does not create a competing model runtime.
- [x] A3. Strict, versioned configuration rejects unknown fields, arbitrary imports,
  invalid references, unreachable nodes, and completion paths without verification.
- [x] A4. A stable transport contract lets the TUI survive a harness factory swap.
- [~] A5. YAML can alter the complete behavior of the selected harness. The schema
  describes nodes including `parallel`, but `skein_v1` compares the graph to one
  fixed literal and rejects changes.
- [x] A6. A materially different harness can be registered behind the same server,
  event, and control interfaces.
- [~] A7. There is one authoritative implementation per concern. Legacy and managed
  tool/policy paths coexist, increasing audit and divergence risk.

Evidence: `app/agent/factory.py`, `harness/core/config/models.py`,
`harness/core/agent/contracts.py`, `tests/unit/test_harness_factory.py`, and
`tests/unit/test_harness_config.py`.

### B. Task understanding and workflow governance — 8 points

- [x] B1. The request carries goal, acceptance criteria, constraints, non-goals,
  path scope, verification requirements, and budgets.
- [x] B2. The durable ledger retains the goal, criteria, progress, decisions,
  blockers, paths, validations, and next action across rounds.
- [~] B3. Ambiguity produces targeted clarification before consequential edits.
  A blocked response is supported, but there is no deterministic ambiguity gate.
- [ ] B4. Complex tasks produce a reviewable design/plan and optional approval;
  trivial tasks automatically use a smaller workflow.
- [~] B5. The workflow tracks plan steps and evidence. The models exist, but the live
  initializer does not populate a plan.
- [x] B6. Tool callbacks project action fingerprints into durable progress; repeated
  no-progress paths drive bounded replan and human handoff without prompt discretion.
- [x] B7. Iteration limits give the workflow a hard termination bound.
- [x] B8. New user steering crosses a completion fence before a run can finish.

Required level-4 evidence: a task set sliced by ambiguity and complexity showing
clarification precision, plan usefulness, iteration count, and no over-planning on
small changes.

### C. Context and token economy — 10 points

- [x] C1. Static instructions and tool declarations are deterministic and hashed.
- [x] C2. Volatile ledger, repository state, recent events, skills, compaction, and
  steering are rendered in a bounded suffix.
- [x] C3. Reads have line ranges and line numbers; outputs preserve head/tail context
  and spill full redacted content to recoverable artifacts.
- [x] C4. Search results are grouped, ranked, bounded, and cursor-paginated.
- [x] C5. Compaction preserves goal, constraints, progress, failures, verification,
  file focus, and artifact identifiers.
- [~] C6. Token accounting uses provider tokenization. Current planning relies in
  part on a four-characters-per-token estimate.
- [~] C7. Duplicate full-file reads and repeated tool results are deduplicated before
  model input. The fresh evaluation still averaged 93,326 input tokens per passed
  small-file task despite high cache-read volume.
- [x] C8. Context budgets enforce packet and aggregate task limits with classified
  exhaustion and graceful handoff.
- [~] C9. Cache, compaction, programmatic routing, reviewer, skills, and retrieval
  ablations show reduced cost per passed task on representative work.
- [~] C10. High-volume exploration can be context-isolated. The swappable harness
  interface could support it, but the active Pi loop has one coding context.

Level-4 gate: no pass-rate regression, stable prefix on at least 95% of non-boundary
calls, at least 2× less context than baseline, and bounded p95 tokens per passed task.

### D. Repository understanding and retrieval — 8 points

- [x] D1. Manifest discovery reports revision, branch, dirty state, languages,
  tracked-file count, and canonical build/test commands.
- [x] D2. Scoped `AGENTS.md` and override files load deterministically.
- [x] D3. FFF is vendored/locked, workspace-confined, git-aware, refreshed after
  mutation, and exposes durable cursors.
- [x] D4. The FFF-versus-rg fixture proves complete safe pagination and better first-
  window relevance/context bytes on the 33-hit noise case.
- [x] D5. The incremental Python/TypeScript index records symbols, imports, calls,
  parser provenance, stale generations, and bounded signatures rather than bodies.
- [x] D6. Repository ranking incorporates lexical match, changed/recent paths,
  tests, and centrality.
- [~] D7. Semantic/LSP/Moderne providers are read-only, fingerprinted contracts but
  are disabled and have no live quality ablation.
- [~] D8. Representative large-repository latency and cost are published across
  cold/warm index states and developer machines.

Level-4 gate: improve pass rate, cost per pass, files read, or time to first correct
edit on PR-derived large-repository tasks; index size alone earns no credit.

### E. Tool interface, editing, and feedback — 10 points

- [x] E1. The model sees exactly `read`, `bash`, `edit`, and `write`.
- [x] E2. Tool declarations are short, typed, bounded, and match runtime arguments.
- [x] E3. `read` returns canonical paths, line numbers, hashes, pagination hints,
  binary rejection, and bounded content.
- [x] E4. `edit` requires one exact unique preimage and fails atomically on zero or
  multiple matches.
- [x] E5. `write` and `edit` support optimistic hashes, idempotency, diff feedback,
  and replay receipts.
- [x] E6. Shell results return status, exit code, duration, redacted bounded output,
  and an artifact handle when truncated.
- [~] E7. Blocked commands provide a mechanically useful safer rewrite. They explain
  risk and approval but do not reliably steer local models away from repeated
  compound-command attempts.
- [ ] E8. The default terminal is persistent across commands while isolated terminals
  remain available for parallel work.
- [x] E9. Successful Python and JSON writes trigger immediate syntax feedback in the
  same tool result.
- [ ] E10. Large edits can use an efficient boundary-matching or patch primitive
  without expanding the visible tool surface.

Level-4 gate: low failed-edit rate across model families, no tool-schema/argument
drift, and lower tool calls and bytes per passed task than a shell-only baseline.

### F. Verification, correction, and completion integrity — 14 points

- [x] F1. Every graph path to `finish` is statically required to pass through a
  verification node.
- [x] F2. Validation discovers syntax, adjacent tests, lint, type checking, broader
  tests, and `git diff --check` from repository evidence.
- [x] F3. Task-supplied deterministic verification commands are supported.
- [x] F4. Allowed and forbidden path checks run independently of the model.
- [x] F5. Failed checks return structured diagnostics and route back to repair.
- [x] F6. Acceptance evidence is typed and bound to harness-executed validation IDs;
  model-authored prose cannot satisfy a criterion.
- [x] F7. Behavioral tasks cannot pass on syntax plus diff checks alone and require a
  successful behavioral validation result.
- [x] F8. Eval cases can protect held-out tests, scope, revisions, and explicit
  verification commands.
- [~] F9. A representative end-to-end suite proves completion integrity. The fresh
  local 10-task suite passed 40%, but every credited completion passed held-out checks
  and no failed task was falsely accepted.
- [~] F10. A separate reviewer assesses the final artifact without producer context.
  The optional reviewer is isolated but advisory and disabled by default.
- [ ] F11. Verification has adversarial process checks for deleting/weakening tests,
  replacing implementations with constants, or otherwise gaming acceptance.
- [~] F12. Each failure has a first-error attribution and becomes a trajectory-prefix
  boundary regression, not only an end-to-end anecdote.

Level-4 gate: at least 90% held-out pass rate on the target task distribution, zero
false-positive completions, zero held-out modifications, and stable consecutive-pass
reliability over repeated runs.

### G. Safety, isolation, and trust boundaries — 12 points

- [x] G1. File tools reject traversal, symlink escape, unsupported URI schemes, and
  cross-task artifact access.
- [x] G2. Destructive actions deny by default; network, dependency, Git history,
  publishing, deployment, and unknown commands require exact approval or opt-in.
- [x] G3. Shell control operators and pipelines are segmented before classification.
- [x] G4. Approval fingerprints and mutation receipts constrain authorization and
  replay to exact operations.
- [x] G5. Secrets are redacted before bounding, persistence, artifacts, traces, and
  model-visible output.
- [x] G6. Docker, Kubernetes, and remote adapters fail closed and can enforce network
  and resource isolation.
- [~] G7. The default local adapter is an enforceable OS sandbox. It intentionally is
  not; shell commands may access host paths permitted by the user account.
- [~] G8. Shell policy uses semantic effects rather than executable/argument pattern
  classification. Current parsing is substantially better than a keyword blacklist
  but remains static and cannot infer arbitrary script behavior.
- [x] G9. Repository instructions and project skills are loaded only after an explicit
  per-launch trust decision; trusted roots remain validated and hashed.
- [x] G10. Resume validates workspace identity and content fingerprints, and cleanup
  refuses dirty worktrees without explicit force.
- [x] G11. Production guidance separates workspace, artifacts, credentials, network,
  and external side effects.
- [~] G12. The full required adversarial suite runs in CI, including prompt injection
  and exfiltration through package scripts. Many primitives are tested, but the
  documented full matrix is not represented end to end.

Level-4 gate: production deployments default to an enforceable container/remote
boundary and pass the complete adversarial matrix with no ambient credentials.

### H. Durability, recovery, and asynchronous control — 10 points

- [x] H1. Task events are append-only, replayable, and project into a deterministic
  ledger.
- [x] H2. Checkpoints bind task/session, base revision, workspace fingerprint, ledger
  hash/version, compaction, and parent checkpoint.
- [x] H3. Exact file mutations do not repeat after interruption.
- [x] H4. WebSocket clients can start, attach, replay from a cursor, acknowledge,
  steer, pause, cancel, heartbeat, and reconnect after backpressure.
- [x] H5. Steering is leased, acknowledged, bounded, and delivered at safe points.
- [x] H6. Cancellation and timeout close the ADK generator in the owning task under a
  cleanup deadline; synchronous tools run off-loop and subprocesses remain bounded.
- [x] H7. Every model stream has time-to-first-event, idle, and total-run deadlines
  with structured durable timeout events and one bounded cold-start retry.
- [ ] H8. Retryable API failures use bounded backoff/jitter and can safely degrade or
  switch models using a neutral trajectory representation.
- [~] H9. A server restart resumes in-flight work automatically. Current public-run
  recovery terminalizes a previously running record rather than rerunning it.
- [~] H10. Multiple workspaces/runs execute concurrently with fault isolation. The
  coordinator holds one shared-workspace lock across each complete run.

Level-4 gate: kill/restart and cancellation fault injection at model, tool, mutation,
verification, compaction, and transport boundaries with no duplicate side effects,
leaked tasks, or silent stalls.

### I. Observability, evaluation, and economics — 8 points

- [x] I1. Model usage records tokens, cache reads/writes, reasoning, prefix identity,
  latency, and optional price.
- [x] I2. Every live tool call records arguments/result hashes, status, duration,
  visible/omitted bytes, and replay state. The fresh run reconciled 75 metrics rows to
  75 public tool-start events exactly.
- [x] I3. Redacted traces cover model/tool/run lifecycle and preserve correlation and
  provenance without raw prompt/source storage.
- [x] I4. Eval schemas cover quality, context, tools, reliability, latency, and cost,
  with cost per passed task as the north-star metric.
- [x] I5. Search has a deterministic paired micro-ablation.
- [~] I6. Every major feature has completed paired ablations on representative tasks.
  Several definitions exist, but live programmatic, semantic, and full feature
  ablations remain undone.
- [~] I7. Results report repeated runs, percentiles, confidence intervals, timeout
  policy, and categorized failures. The current local run is informative but only ten
  single attempts with model changes mid-suite.
- [~] I8. Integration testing exercises the complete ADK/server/TUI/provider path.
  The deterministic integration directory currently contains one synthetic replay
  and verification scenario; most coverage is unit-level.

### J. Memory, skills, and continual evolution — 7 points

- [x] J1. Skill discovery uses trusted roots, path confinement, validation, hashes,
  progressive disclosure, and explicit-mention precedence.
- [x] J2. Candidate skills cannot silently broaden the four-tool surface, safety
  policy, or deterministic verification.
- [x] J3. Only deterministically completed tasks become learning episodes.
- [x] J4. Candidate/baseline assignment, support thresholds, non-regression metrics,
  provenance, promotion, disable, and rollback are durable.
- [x] J5. Project memory stores curated commands, decisions, and conventions only
  after verification rather than raw conversations.
- [~] J6. Learned skills encode the user's implicit workflow semantics. The current
  heuristic synthesizer converts normalized labels such as inspect/mutate/ok into
  generic procedural steps; it does not yet learn requirement framing, preferred
  sequencing, steering corrections, rationale, or scope decisions.
- [~] J7. Memory resolves contradiction, staleness, supersession, and cross-session
  retrieval with boundary/retention tests. The schema has provenance and
  `supersedes`, but the extraction/search path is deliberately narrow.
- [x] J8. False-positive, syntax-only, failed, and unverified outcomes cannot create
  learning episodes or contaminate skill promotion.
- [~] J9. Operational A/B evidence demonstrates at least one learned skill improves
  cost per passed task without quality, safety, or latency regression.

### K. Model/provider readiness — 3 points

- [x] K1. Native Gemini and OpenAI-compatible models use ADK adapters behind a
  closed provider registry.
- [x] K2. Credentials are environment references, not serialized configuration.
- [x] K3. The TUI receives allowlisted coding-model identity and distinguishes
  adapter initialization from a real model response.
- [~] K4. Requested reasoning is carried through validated generated composition and
  reported, but full tool/context/cache capability negotiation is not implemented and
  local models still emitted some thought tokens at `none`.
- [x] K7. Magnitude startup distinguishes discovery from responsiveness by requiring
  a real completion probe unless the user explicitly selects discovery-only mode.
- [ ] K5. The live Gemini credentialed workflow passes the core fail-to-pass suite.
- [ ] K6. Provider fallback and cross-provider handover are tested on an unfinished
  tool trajectory.

### L. Multi-agent and parallel execution — 2 points

- [~] L1. A separate final reviewer can run with isolated context and no mutation
  tools.
- [ ] L2. The selected Pi harness can compose parallel ADK agents from YAML. A
  `ParallelNode` schema exists, but the Pi factory rejects that topology.
- [ ] L3. Parallel workers receive isolated worktrees, namespaces, budgets, and
  structured acceptance contracts, followed by deterministic merge validation.
- [ ] L4. Faults remain local to a parallel batch; one failed child does not erase
  successful independent results.
- [ ] L5. Diverse reviewers or deterministic evidence address homogeneous common-
  cause errors.

Multi-agent support is intentionally low-weight. It should be added only after an
ablation shows independent information gain greater than its token and coordination
cost.

## Highest-priority findings

### Resolved — Completion evidence is bound to environmental facts

Completion claims now reference typed harness validation IDs, behavioral criteria
require behavioral verification, and learning consumes only verified outcomes. The
fresh suite produced zero false-positive completions.

### Resolved in part — Model liveness is bounded; fallback remains open

First-event, idle, total-run, and cleanup deadlines now produce classified durable
events; hidden reasoning renews liveness privately and one cold-start retry is
bounded. The remaining P0 operational gap is a circuit breaker with declared model
fallback/handover after repeated provider stalls.

### Resolved — Tool telemetry is live and reconciled

Managed tool callbacks write idempotent usage samples. The operational suite recorded
75 tool metric rows and 75 public tool starts.

### Resolved — No-progress detection uses live tool actions

Tool callbacks project stable action fingerprints and progress into the ledger, with
deterministic repeat-to-replan-to-handoff coverage.

### P1 — YAML topology is descriptive but not executable for Pi

The generic schema validates route and parallel nodes, but `SkeinHarnessFactory`
requires exact equality with one literal graph. This is safe and makes new harness
registration possible, but it does not satisfy “change the harness behavior entirely
through YAML.”

Required decision: either narrow the public schema to honest Pi-tunable behavior, or
implement a closed node-builder registry that compiles validated YAML into ADK
workflow nodes without arbitrary imports.

### P1 — Context is bounded but not efficient enough operationally

Packet and aggregate task budgets now fail closed, and compaction/search are bounded.
However, passing runs still consumed 373,303 input tokens (93,326 per pass), so
content-hash/delta-context ablations and opportunistic early verification remain P1.

### P1 — Cancellation and provider cleanup need fault-injection tests

The deterministic coordinator test closes a fake execution correctly, but the real
local run logged leftover workflow tasks and an unclosed client session. Cancellation
is documented as best effort for synchronous subprocesses.

Required change: test real ADK generator closure, LiteLLM client closure, cancellation
during inference/tool/verification, and process-group cleanup.

### P2 — Local developer execution is not a security boundary

The local adapter narrows environment variables and applies resource limits, but it
runs a host shell under the user's account. Production adapters exist; the installer
and server should make the active isolation level unmistakable and production mode
should refuse the local backend.

### P2 — Learned workflow semantics are too shallow

The learning control plane is conservative and well structured, but the synthesizer
learns sequences of normalized tool categories, not the user's implicit decisions.
It should incorporate verified steering corrections, requirement clarification,
plan structure, scope choices, command preferences, and failure/recovery patterns—
still in redacted form and still behind boundary/retention trials.

## Recommended execution order

1. **Provider recovery:** circuit breaker, declared fallback model, neutral trajectory
   handover, and capability negotiation.
2. **Completion convergence:** opportunistically run trusted verification after a
   valid mutation so already-correct artifacts do not wait for another long model
   turn.
3. **Token economy:** delta context, repeated-read suppression, and live paired
   ablations against the 93k-input-token-per-pass baseline.
4. **Steering preemption:** distinguish queued safe-point steering from active-call
   cancellation and avoid discarding an already-generated safe mutation.
5. **Configuration honesty:** execute a broader closed graph safely or expose only
   the Pi topology that is genuinely supported.
6. **Operational learning proof:** promote one privacy-safe workflow skill only after
   paired boundary/retention evidence improves cost per passed task.
7. **Optional parallelism:** only after a controlled task slice proves information
   gain and safe merge behavior.

## Release rubric

The harness may call itself production-capable only when all boxes below are checked:

- [x] Zero false-positive completion across the fresh core held-out semantic suite.
- [ ] At least 90% pass rate on a representative PR-derived task distribution.
- [ ] Pass-consecutive reliability is reported over repeated runs, not only Pass@1.
- [x] Every model call has first-event, idle, and total deadlines.
- [ ] Kill/cancel/restart fault injection produces no leaked tasks, sessions, or
  duplicate side effects.
- [x] Live tool/event counts reconcile exactly (75/75 in the fresh suite).
- [ ] Cost per passed task, p50/p90/p95 latency, cache ratio, uncached tokens, and
  failed-edit rate are published for every supported provider configuration.
- [ ] Stable prefix remains unchanged on at least 95% of non-boundary calls.
- [ ] Context is at least 2× lower than the stock baseline without meaningful pass-
  rate regression.
- [ ] Every enabled major feature has an independent ablation and rollback switch.
- [ ] The complete adversarial safety matrix passes in an enforceable production
  sandbox with network denied and no ambient credentials.
- [ ] Learned skills have provenance, boundary and retention evidence, paired trial
  support, promotion criteria, and automatic rollback.
- [ ] A live Gemini/ADK fail-to-pass run succeeds without modifying held-out tests.
- [ ] Documentation states the effective model, reasoning mode, provider capability,
  sandbox level, state root, and all control-plane paths.

## Validation performed for this audit

At revision `04d06c3`:

- Python compilation: passed.
- Ruff over `app`, `harness`, and `tests`: passed.
- Pyright over `app` and `harness`: passed with zero errors and warnings.
- Unit and integration suites: passed; 520 cases were collected across the two
  directories.
- Integration suite: passed; the integration directory currently contains one
  synthetic interruption/replay/verification scenario.
- Operational evidence: the fresh 2026-08-29 ADK/WebSocket/Magnitude report recorded
  4/10 held-out tasks passing, zero false positives, 75/75 reconciled tool events,
  durable reconnect, real steering, and classified safe non-completions.

The unit suite emitted warnings for experimental ADK agent configuration, event
compaction, resumability, and JSON-schema function declarations. These are not test
failures, but release qualification should pin the tested ADK minor and rerun the
live workflow before upgrades.

## Evidence index

- Architecture and fixed topology: `app/agent/factory.py:155`,
  `harness/core/config/models.py:133`
- Context compiler: `harness/core/context/compiler.py:113`
- Managed four-tool adapter: `harness/execution/tools/adk_adapter/__init__.py:241`
- Atomic file mutations: `harness/execution/environment/local.py:80`
- Verification discovery and evidence: `harness/verification/discovery.py:57`,
  `harness/verification/runner.py:106`
- No-progress logic: `harness/evidence/state/progress.py:25`,
  `harness/core/orchestration/core.py:78`
- ADK event streaming and cancellation: `harness/adapters/adk/runtime/runtime.py:135`,
  `harness/adapters/adk/runtime/runtime.py:605`
- Model-only metrics callbacks: `harness/evidence/telemetry/adk_plugin.py:172`
- Tool metrics schema: `harness/evidence/telemetry/metrics.py:43`
- Search and structural repository map: `harness/execution/repo/fff_search.py:268`,
  `harness/execution/repo/index.py:831`
- Trace-derived skill synthesis: `harness/learning/skills.py:29`
- Local operational evaluation:
  `.artifacts/local-model-eval-20260829/results-final/FINAL_REPORT.md`
