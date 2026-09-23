# ADK-native Skein simplification plan

Status: phases 1–8 implemented; qualification in progress
Updated: 2026-09-22

## Goal

Simplify Skein to one legible coding path while retaining the boundaries that make it
useful as a harness:

- Google ADK owns model execution, tool continuation, streaming, sessions,
  cancellation, and resumability.
- PTC v4.1 is the default and only model-facing coding tool.
- Skein owns effect authorization, trace-native evidence, bounded model-facing
  projections, recovery, and independent completion verification.
- Pi contributes loop simplicity and append-oriented interaction semantics.
- Strands contributes explicit lifecycle outcomes, boundary checkpoints, capability
  narrowing, collision validation, and bounded cancellation-aware execution.

The result should be one ADK agent, one `code` tool, one effect broker, one
canonical trace, and one verifier. It is not a new agent framework layered over ADK.

## Implementation progress

| Work | Status | Evidence |
| --- | --- | --- |
| Default foundation | Complete | The shipped composition exposes only `code` and applies the PTC v4.1 snapshot, recovery, output, and helper-call bounds. |
| Phase 0: characterize | Complete | Focused PTC, prompt-prefix, provider continuation, recovery, cancellation, broker, tracing, parity, and projection tests freeze the v4.1 boundary. |
| Phase 1: one trace sink | Complete | `HarnessEvent` carries correlation and parent fields; ADK spans, PTC cells, effects, context projections, recovery boundaries, and verification converge on the task stream. Specialized stores remain dual-written only for the retained comparison window. |
| Phase 2: typed outcomes | Complete | `HarnessOutcome` validates terminal state; presentation and evaluation consume typed status instead of prose. |
| Phase 3: minimal ADK workflow | Complete | The default ADK worker exposes only `code`, accepts an ordinary final response, and routes it directly to deterministic verification. Structured workflow selection was removed. |
| Phase 4: prompt and context | Complete | The provider prefix is byte-checked on every request. The single coding packet has a fixed section order, preserves required sections whole, fails on required overflow, and records rendered bytes before dispatch. |
| Phase 5: PTC dispatch and projections | Complete | Construction rejects surface collisions; outputs are bounded, pageable, versioned trace projections; replay deliveries remain separately observable. |
| Phase 6: boundary recovery | Complete | Explicit pre-model, post-model, post-effect, and post-verification boundaries complement existing unresolved-effect reconciliation and checkpoint restoration. |
| Phase 7: verification | Complete | Host verification is the sole completion authority; typed completion requires a passing report and the heuristic completion-review model call was removed. |
| Phase 8: learning | Complete | `verified_learning_episode` emits pinned, reproducible episodes only from clean host-verified traces. Learning remains disabled and off the runtime path. |
| Phase 9: cleanup | Complete | Workflow mode selection, structured provider schemas, duplicate work-packet builders, the review heuristic, and model-facing projection metadata were removed. Strict-parity adapters remain isolated evaluation infrastructure. |

The implementation promoted the existing simple path and then removed the selector.
There is no `pi_compatible`, `structured`, or `skein_simple` application mode.
Remaining work is live qualification, not another runtime implementation phase.

## Non-negotiable contracts

1. ADK remains the sole owner of the model/tool event loop.
2. The default model-visible tool set is exactly PTC v4.1 `code`.
   The internal callable may remain named `execute_code`.
3. `read`, `write`, `edit`, `bash`, and `verify` remain synchronous guest helpers,
   not separate ADK tools.
4. Every helper effect crosses the same Skein broker.
5. The append-only Skein trace is authoritative for attempts and effects.
6. The ADK session is authoritative for conversational continuation.
7. Only deterministic host verification can complete a coding task.
8. Stable instructions and the tool declaration remain byte-stable within a run.
9. Model-visible output is a bounded projection, never the canonical evidence record.
10. Learning can propose context or skills only from independently verified traces;
    it cannot mutate the worker, permissions, verifier, or stable prompt prefix.
11. Existing strict Pi/Skein parity adapters remain usable until the migration is
    complete and measured.

## Target architecture

```text
User or benchmark task
          |
          v
ADK Runner + ADK session
          |
          v
Minimal ADK Workflow
  |
  +-- Coding LlmAgent
  |     |
  |     +-- stable instruction
  |     +-- bounded dynamic context
  |     +-- code (PTC v4.1)
  |             |
  |             +-- read / write / edit / bash / verify
  |                         |
  |                         v
  |                  Skein effect broker
  |
  +-- Host verification
          |
          +-- pass: task.completed
          +-- fail: bounded feedback -> Coding LlmAgent

ADK lifecycle plugin ----+
PTC dispatcher ----------+--> canonical append-only trace --> derived views
Effect broker -----------+
Verifier ----------------+
```

### Authority boundaries

| Concern | Authority |
| --- | --- |
| Model calls, tool continuation, streaming | ADK |
| Conversation persistence and native resume | ADK session service |
| Workflow repetition | Minimal ADK workflow |
| Model-visible coding surface | PTC v4.1 `code` |
| Filesystem and command effects | Skein broker |
| Attempt and effect history | Skein canonical trace |
| Model-facing output | Versioned bounded projection over evidence |
| Completion | Skein host verifier |
| Learned context and skills | Gated projections from verified traces |

## PTC v4.1 model-facing contract

The migration must preserve v4.1 before simplifying surrounding orchestration. The
model sees one tool whose schema accepts either:

- a Python cell; or
- a retained result ID with byte offset and limit for paging.

The tool description is assembled once from stable components and does not absorb
task, progress, steering, session, time, or verification state.

The worker contract remains:

- persistent CPython values and functions across cells;
- synchronous `read`, `write`, `edit`, `bash`, and `verify` helpers;
- conservative stdlib AST preflight;
- `json`, `math`, and `re` preloaded;
- at most 64 helper calls per cell;
- at most 50 KiB in the immediate model observation;
- readable string-like helper results with useful attributes;
- retained-result paging for larger bodies;
- rollback of supported in-memory values after a Python exception;
- no rollback claim for already completed external effects;
- a bounded JSON-safe checkpoint after each successful cell;
- checkpoint restoration after worker timeout or transport loss;
- no transcript replay to reconstruct the Python heap;
- every nested capability call routed through the broker;
- complete typed receipts and artifacts outside model context.

Strands' Monty caller is not a replacement: it is a stateless orchestration sandbox,
while PTC v4.1 is a persistent, recoverable work environment.

## Minimal ADK workflow

Keep ADK `Runner`, `Workflow`, session services, native events, callbacks, streaming,
cancellation, and resumability. Reduce the outer workflow to two meaningful steps:

```text
CodingAgentNode -> VerificationNode
       ^                |
       +---- feedback --+
```

The transition rules are host-owned:

```python
if verification.passed:
    complete
elif limits.exhausted:
    verification_failed
elif approval_or_human_input_is_required:
    blocked
else:
    append_bounded_verification_feedback
    continue
```

ADK still runs every inner sequence of model response, `code` call, tool
result, and model continuation. Remove the parallel model-authored control protocol
from the main path:

- `AgentStep.status`;
- plan/implement/review phases;
- `continue`/`replan`/`verify` routing;
- progress-based human blocking;
- criterion-proposal transitions;
- open-ended counterexample-review loops;
- prose-derived completion state.

The model owns tactics. The workflow owns limits and verification.

## Prompt assembly

### Stable prefix

The provider prefix contains only byte-stable material:

1. behavioral and safety contract;
2. PTC v4.1 operating contract;
3. verification responsibility;
4. exact `code` tool declaration and configuration;
5. model/interface version fingerprint.

The runtime records the first-call canonical prefix bytes and hash. Every later model
request in the run checks them and fails closed on mutation. Prompt caching metrics
must distinguish prefix stability from ordinary cache misses.

Do not broadly rewrite the existing PTC prompt. The compact C4 ablation regressed
quality and increased input use. Future prompt changes are one narrow behavioral edit
at a time, versioned and compared against frozen requests and live tasks.

### Dynamic context

Dynamic context has a fixed order:

```text
TASK
PROJECT INSTRUCTIONS
SELECTED SKILLS
LATEST USER STEERING
CONTINUATION
RECENT COMPLETE TURNS
LATEST VERIFICATION FAILURE
EVIDENCE NAVIGATION
```

Rules:

- `TASK`, active steering, selected skill instructions, continuation control, and
  verification feedback are required and remain whole.
- Optional history is admitted only after required control fits.
- Structured values and instructions are never head/tail-spliced.
- Oversized bodies remain in artifacts with stable retrieval references.
- Required-context overflow returns a typed `context_overflow`; it never silently
  drops an instruction or increases a budget.
- Context creation records exact rendered bytes, section hashes, omissions, budgets,
  source event IDs, evidence watermark, and renderer version before dispatch.

### Steering placement

Steering is an immutable context exposure, not transient queue state. Before the next
model request, record:

- source steering event and message IDs;
- exact rendered bytes;
- insertion position in ADK history;
- prefix hash and invocation anchor;
- content and renderer hashes.

Retry and resume reinsert the recorded bytes at the recorded position. Queue
acknowledgement does not mean execution, and later steering supersedes only conflicting
instructions.

### Compaction

ADK remains responsible for session/event compaction mechanics. Skein supplies and
records the policy:

- preserve the stable prefix;
- preserve fresh steering until a subsequent native response exists;
- never split a tool call from its result;
- retain a recent exact tail;
- summarize only the covered historical range;
- record source range, summarizer version, generated summary, retained tail, and
  resulting context hash.

The full trace remains unchanged. A compaction summary is a model-facing projection,
not historical authority.

## Tool messages and model-facing projections

### ADK message semantics

`code` uses ordinary ADK function-call and function-result messages. Preserve
the provider tool-call ID and ordering exactly. Do not encode tool results as synthetic
assistant prose or unrelated user messages.

Host verification feedback is an explicit host/user control message added only after
an ADK coding invocation ends. It is not disguised as a `code` result.

Model requests must preserve provider-required reasoning and tool-call continuation
metadata. Strict parity capture retains the exact model context and serialized provider
request separately from the normalized trace.

### Projection envelope

Every PTC call produces two distinct representations:

1. canonical evidence: complete typed lifecycle, capability receipts, hashes, and
   artifact references;
2. model projection: bounded readable text or a retained-result page.

The trace links them with:

```json
{
  "projection_version": "ptc-v4.1-output@1",
  "artifact_uri": "artifact://...",
  "artifact_sha256": "...",
  "model_visible_sha256": "...",
  "model_visible_bytes": 1234,
  "omitted_bytes": 5678,
  "result_id": "result:...",
  "page": {"offset": 0, "limit": 50000}
}
```

The model projection follows these rules:

- successful helper results are readable and compact;
- shell output combines stdout, labelled stderr when present, and `[exit N]`;
- preflight, runtime, timeout, policy, transport, and unknown-effect failures remain
  distinguishable;
- truncation is explicit and includes the stable paging handle;
- paging returns the exact retained bytes for the requested range;
- internal policy objects, receipts, stack traces, secrets, and storage paths do not
  leak into model text;
- an empty result is represented explicitly rather than omitted;
- a failed helper becomes a catchable Python error when continued execution is safe;
- an unknown mutation outcome stops further effectful work until reconciliation.

### Capability narrowing

At cell admission, resolve and record:

```text
cell helpers
  subset of configured PTC helpers
  subset of registered broker capabilities
  subset of task-authorized capabilities
```

`code` cannot call itself. Helper and alias collisions fail at construction.
Verifier-only or host-control capabilities cannot become guest helpers accidentally.

### Bounded execution

Borrow the useful Strands mechanics without adding middleware:

- explicit interpreter and wall-clock deadlines;
- cancellation propagation from ADK through the worker to in-flight helpers;
- bounded independent helper concurrency behind a per-cell semaphore;
- the existing 64-call ceiling;
- bounded memory and output;
- cleanup that cannot leave detached helper operations.

The broker remains the only shared execution boundary.

## Trace-native logging

### One canonical stream

Use one append-only event API. Four producers append to it:

- the ADK lifecycle plugin;
- the PTC dispatcher;
- the effect broker;
- the verifier.

The minimum common fields are:

```text
event_id
sequence
task_id
run_id
session_id
invocation_id
kind
timestamp
caused_by
correlation_id
idempotency_key
payload_hash
payload
```

The ADK tool-call ID is the correlation identity shared by the ADK call, PTC cell,
and returned ADK tool result. Each nested helper also has a cell ID, helper-call index,
attempt ID, kernel epoch, and broker operation ID.

### Event vocabulary

Keep the core vocabulary small:

```text
task.started

model.requested
model.completed
model.failed

ptc.cell_submitted
ptc.preflight_completed
ptc.cell_completed
ptc.cell_failed
ptc.cell_timeout
ptc.output_projected
ptc.state_checkpointed
ptc.state_restored

capability.requested
capability.authorized
capability.completed
capability.failed
capability.effect_unknown

steering.received
context.created
context.compacted

verification.started
verification.completed

task.completed
task.failed
task.blocked
```

Specialized analytics may project more specific labels, but should not introduce a
second durable authority.

### Causal completeness

The trace must answer, without reading prose:

- Was a model request sent, and what exact context/request hash was sent?
- Was a tool call requested, admitted, denied, started, completed, or left unknown?
- What did the operation change?
- What complete result was retained?
- What exact projection did the model see?
- Which checkpoint can restore the worker?
- Which evidence and workspace fingerprint supported verification?
- Why did the task continue or stop?

Request capture stores exact provider payloads only in the existing protected
evaluation/debug path. The ordinary canonical trace stores redacted bounded projections
and hashes; it must not become a raw-secret archive.

### Derived views

Derive, and optionally cache:

- current task outcome;
- modified paths;
- unresolved effects;
- PTC bindings and retained results;
- verification attempts;
- context packets;
- notebook rendering;
- token, cost, latency, and cache metrics;
- recovery position;
- learning episodes.

Deleting a cache or projection must not lose canonical history. ADK session storage
remains separate because it serves conversational continuation.

## Recovery and boundary checkpoints

Record safe boundaries inspired by Strands:

```text
before_model
after_model
after_effects
after_verification
```

Use ADK resumability for conversation state and Skein events for effect certainty.
On restart:

1. restore the ADK session;
2. replay the Skein trace;
3. locate the latest safe boundary;
4. reconcile every nonterminal capability operation;
5. restart the PTC worker;
6. restore the latest valid JSON-safe v4.1 checkpoint;
7. verify kernel epoch, source watermark, and checkpoint hash;
8. continue through ADK without repeating a completed mutation;
9. verify the actual workspace before completion.

The live Python heap is disposable. Only supported checkpoint values are durable. An
unresolved mutation prevents effectful continuation until reconciliation.

## Verification

### Two verification layers

PTC `verify()` is model-invoked evidence acquisition. It gives the model immediate
feedback and records a normal brokered command receipt.

Host verification runs after the ADK coding invocation ends and exclusively controls
completion. It checks:

- required commands and minimum test counts;
- required verification strength;
- workspace scope;
- presence of required changes;
- baseline-relative regressions;
- unresolved effects;
- environmental evidence for acceptance criteria.

A prior guest `verify()` may be reused only when its command, task, workspace
fingerprint, scope, output completeness, and required strength match. Otherwise the
host reruns the required check.

### Verification feedback

Feedback returned to the model is a bounded projection containing:

- failed command or criterion;
- concise redacted diagnostic;
- new failing-test identities;
- scope violations;
- unresolved effects;
- smallest valid next action;
- artifact references for omitted detail.

It does not contain the complete verifier internals or ask the model to maintain
criterion IDs and workflow phases. The next ADK invocation treats it as host control,
not a tool result.

## Lifecycle outcomes

Expose one typed host result:

```text
completed
turn_limit
token_limit
cancelled
approval_required
context_overflow
verification_failed
blocked
error
```

Map ADK stops, PTC failures, broker decisions, and verifier results into this vocabulary
at one boundary. Runners and evaluators must not infer status from exception or assistant
message text.

## Learnability

Learning remains downstream of the verified trace, not inside the critical loop.

### Eligible episodes

An episode is learnable only when:

- independent verification passed;
- required strength was achieved;
- every criterion has typed environmental evidence;
- no scope violation or unresolved effect remains;
- trace causality and projection hashes validate;
- the episode was not contaminated by evaluation-oracle data;
- policy-blocked attempts did not create the apparent success.

Model claims and attractive final prose are never success labels.

### Learning projection

Produce a privacy-safe normalized episode from the canonical trace:

- task and repository fingerprints, not source bodies;
- selected prompt/tool/projection versions;
- normalized PTC code-shape features, not raw code by default;
- capability categories, outcomes, and causal order;
- read coverage and freshness relationships;
- mutation and verification evidence;
- model calls, cells, helper calls, tokens, cache use, cost, and latency;
- steering corrections and recovery events;
- terminal verification strength and outcome.

Raw prompts, source, tool arguments, outputs, secrets, and provider payloads do not enter
the default learning dataset.

### What may be learned

Learning may propose:

- candidate Agent Skills;
- better evidence-navigation projections;
- context selection policies;
- narrow prompt edits;
- PTC examples or helper guidance;
- verifier-command discovery improvements.

Learning may not change:

- the `code` schema;
- broker permissions;
- sandboxing or approvals;
- helper authority;
- stable prompt prefix during a run;
- verifier acceptance rules;
- trace redaction or retention policy.

### Promotion gates

Candidates remain disabled or shadowed until matched trials show:

1. no verification-quality regression;
2. no stale-evidence acceptance;
3. no increase in unresolved effects;
4. useful candidate uptake;
5. reduced calls, reacquisition, tokens, cost, or latency on held-out tasks;
6. stable cache behavior;
7. rollback readiness.

The existing learned-memory evidence remains a hold: it reduced some same-version
rereads but increased calls, input, and cost and did not meet promotion gates. Do not
put that machinery on the simplified default path. Preserve it as an opt-in experiment
over the same trace and context interfaces.

## Implementation sequence

### Phase 0: characterize the current contract

Add focused scripted-model tests and golden artifacts for:

- exact PTC v4.1 tool schema and description;
- system prefix and serialized provider request;
- reasoning/tool-call continuation metadata;
- helper signatures and return projections;
- preflight, paging, call ceiling, and output ceiling;
- variable persistence and exception rollback;
- checkpoint serialization and worker restoration;
- cancellation and timeout;
- nested broker receipts;
- guest verification and host verification;
- steering placement and compaction boundaries;
- restart after completed and unknown effects.

These tests define the migration boundary. Do not restructure PTC until they pass.

### Phase 1: converge on one trace sink

1. Reuse `HarnessEvent` and the current append-only store.
2. Add missing causal and correlation fields without creating a parallel schema.
3. Add one ADK lifecycle plugin.
4. Route PTC, broker, context, and verifier recording through the same append API.
5. Dual-write to current specialized stores temporarily.
6. Compare trace-derived task/effect/verification state with existing stores.

Exit: every characterization run is explainable from the canonical trace.

### Phase 2: add typed lifecycle outcomes

1. Define the single host-facing outcome type.
2. Map ADK, PTC, broker, context, and verifier terminals centrally.
3. Convert runners and evaluation adapters to consume it.
4. Stop matching exception or response prose.

Exit: callers do not depend on internal workflow phases.

### Phase 3: introduce the minimal ADK workflow

1. Build one `LlmAgent` with exactly `code`.
2. Build one verification node.
3. Implement pass, retry, blocked, limit, and error transitions.
4. Preserve ADK streaming, sessions, cancellation, and resumability.
5. Keep the existing workflow behind a profile for differential testing.

Exit: the new main path contains no model-authored task-control schema.

### Phase 4: consolidate prompt and context assembly

1. Freeze and fingerprint the stable prefix and tool declaration.
2. Implement the fixed dynamic-section order.
3. Record every context projection before dispatch.
4. Reconstruct steering exposures from trace events.
5. Use one compaction policy and one continuation renderer.
6. Delete duplicate packet builders only after request-body differential tests pass.

Exit: one code path produces every normal model context.

### Phase 5: normalize PTC dispatch and projections

1. Resolve helper capability intersections at cell admission.
2. Validate helper/tool/alias collisions at construction.
3. Centralize projection and paging metadata.
4. Preserve exact ADK function-result semantics.
5. Add cancellation-aware bounded concurrency where safe.
6. Ensure unknown effects stop further mutations.

Exit: every cell has one causal trace subtree and one versioned model projection.

### Phase 6: boundary recovery

1. Record safe-boundary events.
2. Reconcile nonterminal effects before resume.
3. Restore PTC v4.1 state from the latest valid checkpoint.
4. Add fault injection at every boundary.
5. Prove completed mutations are not repeated.

Exit: restart is safe without a second snapshot framework.

### Phase 7: simplify verification integration

1. Put existing verification behavior behind one public contract.
2. Reuse valid guest verification receipts by exact identity.
3. Produce one bounded feedback projection.
4. Remove criterion-ID and review-state protocols from the model loop.

Exit: completion has one deterministic authority and one feedback path.

### Phase 8: move learning off the critical path

1. Derive normalized episodes from the canonical trace.
2. Keep candidate synthesis and trials asynchronous and optional.
3. Pin selected candidate versions for reproducible trials.
4. Require held-out promotion and rollback gates.
5. Keep learned memory disabled by default until existing gates pass.

Exit: disabling learning cannot change runtime correctness or prompt stability.

### Phase 9: delete superseded machinery

Trace callers before deletion. Likely candidates include:

- structured `AgentStep` routing;
- model-owned workflow phases and replanning;
- duplicate work-packet renderers;
- approximate compatibility modes superseded by strict parity;
- separate progress, checkpoint, receipt, and metrics authorities now derived from
  the trace;
- importers used only for temporary dual writes;
- registries with one supported implementation;
- inactive semantic-memory and learning paths from the default profile;
- duplicate legacy package paths.

Keep strict evaluation adapters and historical artifact readers until retained
experiments no longer depend on them.

## Validation

### Unit checks

- event ordering, idempotency, and causal references;
- lifecycle outcome mapping;
- required-section context budgeting;
- stable-prefix mutation detection;
- steering exposure replay;
- tool/helper collision detection;
- capability intersection;
- projection hashes and exact paging;
- checkpoint eligibility and restoration;
- unresolved-effect detection;
- guest-verification reuse predicates;
- learnable-episode eligibility.

### Integration checks

- ADK model -> PTC -> model continuation;
- multiple helper calls in one cell;
- sequential conflicting mutations;
- bounded independent helper concurrency;
- approval interruption and resume;
- cancellation during a helper;
- timeout followed by checkpoint restoration;
- verification feedback entering a new ADK invocation;
- compaction with protected steering and tool-result pairs;
- recovery after each safe boundary;
- trace-to-notebook and trace-to-metrics projection.

### Differential checks

For frozen scripted conversations, compare old and new paths on:

- system prefix bytes;
- `code` schema and description;
- complete model context;
- serialized provider request;
- reasoning metadata;
- PTC code input;
- helper arguments;
- model-visible projection;
- retained result bytes;
- workspace result;
- verification result;
- terminal outcome.

Trace layout may change only where the new causal contract deliberately improves it.

### Live qualification

Run matched tasks and report:

- independently verified success;
- calls, cells, helper calls, and verification rounds;
- input, cached, uncached, output, and reasoning tokens;
- prefix cache stability;
- cost and wall time;
- repeated source acquisition;
- projection truncation and paging uptake;
- unresolved effects and recovery success;
- learned-candidate uptake when explicitly enabled.

No prompt, memory, or learned-skill change becomes default from a compelling single
trace. Require frozen matched cohorts and held-out confirmation.

## Rollout

1. Promote the simple path and remove workflow mode selection. **Done.**
2. Run characterization tests against both paths. **Done locally.**
3. Dual-write and compare canonical trace projections. **Implemented; retained during qualification.**
4. Run provider-request differential tests. **Done locally.**
5. Run the strict Pi/Skein parity suite. **Done locally.**
6. Run a focused live qualification cohort.
7. Promote the simplified ADK workflow when quality and recovery are equivalent or
   better. **Implementation promoted; live quality gate remains.**
8. Keep strict comparison adapters for one retained evaluation cycle.
9. Delete those adapters after no active experiment depends on them.

## Definition of done

- ADK is the only model/tool loop owner.
- The model sees exactly PTC v4.1 `code`.
- The stable prefix and tool declaration are byte-stable and fingerprinted.
- Dynamic context has one bounded, traceable assembly path.
- Tool messages preserve native ADK/provider continuation semantics.
- Every model-visible result is linked to complete evidence and exact projection bytes.
- Every helper effect passes through one broker.
- One append-only trace explains model requests, cells, helpers, effects, projections,
  recovery, and verification.
- ADK sessions resume conversation without repeating completed effects.
- PTC restores only supported JSON-safe checkpoint state.
- Independent host verification exclusively controls completion.
- The outer workflow is only code, verify, retry or finish.
- Learning is trace-derived, independently gated, reversible, and disabled without
  changing runtime behavior.
- The model-facing main path no longer requires model-authored phases, progress
  routing, criterion-transition protocols, or a structured comparison mode.

The implementation rule is simple: let ADK run the agent, let PTC v4.1 be the model's
single programmable surface, let Skein govern effects and completion, and derive every
other view from the trace.
