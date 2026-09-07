---
name: coding-harness-development
description: Implements and reviews Skein harness changes while preserving its one-tool notebook PTC, trace-native memory programs, replay, safety, and independently verified completion contracts. Use for orchestration, context, tools, repository access, durable state, verification, evaluation, and harness documentation.
license: Apache-2.0
metadata:
  upstream-workflow: google/agents-cli skills
  project: skein
  compatibility: Python 3.12+, Google ADK 2.x, Google Agents CLI, Git, uv
---

# Coding Harness Development

Use this workflow for every non-trivial Skein change.

## Core thesis

Skein tests this claim:

> One programmable code-mode tool plus one trace-native append-only log is a
> sufficient model-facing substrate for a long-running coding agent. A notebook
> is the durable PTC session document, and versioned programs derive bounded
> memory and prompt views from trace evidence. The host retains authority over
> effects, policy, budgets, recovery, and completion.

Preserve these invariants:

1. **One model-visible PTC tool is the target.** Notebook mode exposes persistent
   `python`; external capabilities remain under its guarded `agent.*` broker.
   The cache-stable `read`, `bash`, `edit`, `write` surface remains the compatibility
   and ablation arm until one-tool quality is measured. Do not add another
   top-level model tool.
2. **One effect broker.** Direct tools, nested PTC calls, verification, child work,
   and processes share authorization, confinement, approval, cancellation,
   redaction, bounded output, and receipt semantics. Code mode changes syntax,
   never authority.
3. **One canonical historical trace.** Every material harness fact is an
   append-only event or content-addressed artifact. ADK sessions, notebooks,
   reducers, indexes, caches, and databases may implement or project history;
   they must not become competing harness authorities.
4. **Memory is versioned programs over evidence.** Prompt packets, progress,
   summaries, compaction, retrieval, and durable named state are computations over
   addressed trace evidence. Each result identifies the program name/version,
   source hash, parameters, source watermark or `as_of` clock, content hash,
   evidence addresses, and applicable budgets. Materializations are disposable.
5. **Programs earn activation.** Seeded deterministic programs are preferred.
   Agent-authored programs remain candidates until type, cost, temporal, policy,
   historical replay, and shadow-execution gates pass. Programs remain versioned,
   attributable, reproducible, and retireable.
6. **The notebook is the durable PTC workbench.** Every submitted Python program
   has a stable cell and attempt identity with ledger provenance. The canonical
   notebook is rebuilt at an explicit ledger watermark and may contain PTC code,
   selected outputs, and task/user/assistant/steering/verification/compaction
   Markdown. Notebook order or output presence is not proof of historical order,
   success, or side effects.
7. **Notebook, ledger, and heap have distinct authority.** The ledger answers what
   happened; the notebook is the durable session document; the persistent CPython
   worker owns live values for one kernel epoch and is disposable. Restore only
   declared values and validated replay-safe code. Never replay an effectful cell
   merely to reconstruct state.
8. **`nb-cli` is the notebook interoperability boundary.** Use it to inspect,
   search, export, and explicitly import notebook documents. Skein materializes the
   deterministic ledger-derived `.ipynb`; do not add an ad hoc JSON browsing path.
   `nb execute` and ADK `BaseCodeExecutor` are not alternate production execution
   authorities. The `python` FunctionTool, write-ahead cell protocol, broker, and
   persistent CPython worker remain the single execution path.
9. **Every attempt is evidence.** Started, completed, failed, blocked, timed-out,
   cancelled, retried, and effect-unknown work remain distinguishable. Every
   effectful operation has an identity, authorization result, and terminal or
   reconciled outcome.
10. **Deterministic facts control execution.** Models own tactics and may propose
    plans or progress. Deterministic code owns authorization, task state, budgets,
    context ordering, recovery, verification, and terminal status.
11. **Completion is independently verified.** A model claim or self-authored test
    is supporting evidence, not authority. Completion requires criterion-bound
    environmental evidence, scope compliance, reconciled effects, and required
    checks. Baseline-relative checks prove no regression; they do not alone prove
    requested behavior.
12. **Context stays bounded and cache-aware.** Stable prefix bytes are canonical
    and versioned. Volatile task state belongs in the append-only suffix or an
    explicit compaction epoch. Large bodies remain artifact-backed, and every
    exposure records its view version and evidence.
13. **Complex machinery must earn its cost.** Simple tasks pay almost nothing for
    long-running features. Promote PTC, compaction, retrieval, delegation, semantic
    indexes, or a storage backend only after a controlled ablation demonstrates
    value.

The normative source is `docs/design/trace-native-repl-agent.md` T1-T17. Read
`docs/adr/long-running-context-memory-programs.md` for the memory-program contract
and `docs/adr/coding-harness-minimal-sota-extensions.md` for the target surface.

## Required workflow

### 1. Establish the contract

Read, in order:

1. `docs/IMPLEMENTATION_STATUS.md`
2. `docs/TODO.md`
3. `docs/architecture.md`
4. the relevant sections of the three normative documents above
5. the typed model and every caller on the path being changed
6. focused unit and integration tests

State one smallest independently testable todo. Separate prompt, tool-surface,
model, storage-authority, verification, and evaluation changes unless one cannot be
tested without another.

### 2. Map authority before editing

For each fact the change reads or writes, identify:

- its authoritative event or artifact;
- the reducer or versioned program that derives current state;
- program version, inputs, watermark, clock semantics, and output budget;
- operation/idempotency identity and workspace fingerprint;
- failure, interruption, retry, and reconciliation behavior;
- whether it is exposed to the model and under what byte/token budget.

If two stores answer the same historical question, do not add a third. Keep one
explicitly authoritative during migration and require replay equality before
cutover.

### 3. Implement outside the model first

Use deterministic code for event reduction, workspace selection, authorization,
path confinement, output truncation, secret redaction, test selection, token
budgets, recovery, acceptance evidence, program validation, and completion. Use an
LLM only where semantic judgment is necessary, and mark its result advisory.

Reuse an existing ADK primitive only when it preserves Skein's trace, notebook,
broker, and replay contracts. ADK is an execution framework, not an exemption from
them.

### 4. Preserve replay and safety

For every state-changing or externally observable operation:

1. record intent before execution;
2. enforce the same broker policy on every calling surface;
3. execute atomically and idempotently where practical;
4. record bounded/redacted success, failure, timeout, or unknown effect;
5. reconcile interruption before retrying;
6. add the smallest fault-boundary or replay test that proves the contract.

Never assume an ADK node or tool executes exactly once. Never automatically replay
publish, deployment, network, destructive, or otherwise effect-unknown work.

### 5. Preserve notebook PTC

For a PTC change, verify:

- cell intent is persisted before execution;
- cell, attempt, kernel epoch, and nested operation identities remain stable;
- all `agent.*` effects flow through the broker and ledger;
- failed cells cannot leak a dirty heap into the next attempt;
- recovery replays only explicitly classified safe definitions/data;
- notebook materialization is byte-stable at the same watermark;
- code, selected MIME output, Markdown, and artifact references preserve provenance;
- `nb-cli` inspection works without executing cells or parsing `.ipynb` through a
  second application path.

Do not replace the notebook with a tool-result transcript or treat the notebook as
the historical log. Do not replace the persistent worker with `nb execute`.

### 6. Preserve memory-program contracts

Every new or changed view must declare:

- name, version, language/runtime, source hash, owner, and activation state;
- allowed event/artifact sources and temporal policy;
- parameters, watermark or explicit `clock.observed`, lane/workspace scope;
- input, output, latency, and cost budgets;
- deterministic ordering and content hash;
- evidence addresses and a retrieval/materialization receipt;
- invalidation, replay, shadow, promotion, and retirement behavior.

The same program, parameters, and watermark must yield the same bytes. A later
watermark is a different query even if bytes happen to match. Semantic or
model-generated views may advise deterministic control but cannot override it.

### 7. Preserve prompt and verification authority

When changing model context, define stable prefix versus dynamic suffix,
deterministic source order, program version, evidence watermark, byte/token budget,
artifact spill behavior, and cached/uncached telemetry. Compaction is an explicit,
replayable epoch transition, not continuous prefix rewriting.

A model `done` claim routes to deterministic verification. Completion requires:

- explicit environmental evidence for each acceptance criterion;
- passing required checks under declared semantics;
- no new baseline-relative regression;
- no scope violation or unreconciled effect;
- `git diff --check` for code changes.

Do not silently downgrade required strength because discovery failed. Record
reachable strength separately and block when required evidence is unavailable.

### 8. Test, measure, and land

Run the focused test first, then applicable layers:

```bash
uv run python -m compileall -q app harness tests
uv run pytest -q tests/unit
uv run pytest -q tests/integration
uv run ruff check app harness tests
uv run pyright app harness
```

For behavior, context, tool-surface, or control-flow changes, run a controlled
ablation and report pass rate, cost per pass, wall/model/tool/verification time,
model and tool calls, input/uncached/output/reasoning tokens, cache-read ratio,
prefix versions, retries, duplicate effects, verification rounds, and terminal
reason. Do not claim an optimization from one metric or one successful example.

Update `docs/IMPLEMENTATION_STATUS.md` when a capability boundary changes. Commit
each completed todo independently after focused checks pass, keep unrelated work
out, and do not begin the next architectural slice before its gate is measured.

## Pre-change checklist

- [ ] The change advances or preserves one-tool notebook PTC; the four-tool arm
      stays until the ablation gate passes.
- [ ] No new top-level model tool or unbrokered capability is introduced.
- [ ] Ledger, notebook, live heap, ADK session, and derived-view authority remain
      distinct and explicit.
- [ ] The authoritative event/artifact and every derived program are named.
- [ ] Each memory program has version, sources, watermark/time, budgets, evidence,
      hash, and activation/replay semantics.
- [ ] PTC, direct tools, verification, and recovery share effect policy.
- [ ] Mutation identity, replay class, timeout, cancellation, and reconciliation
      are specified.
- [ ] Notebook cells and selected outputs retain ledger provenance; `nb-cli` stays
      inspection/import/export rather than execution authority.
- [ ] Model-visible context has deterministic ordering, provenance, and a bound.
- [ ] Volatile state does not mutate the stable prompt prefix.
- [ ] Completion and safety decisions remain outside model control.
- [ ] Baseline/no-regression evidence is not confused with criterion satisfaction.
- [ ] The smallest deterministic test covers the new contract or reproduced bug.
- [ ] The evaluation changes one independent variable and has a stopping condition.
- [ ] Existing machinery is reused or removed before adding an abstraction.

## Review rubric

Score each dimension `0`, `1`, or `2`: `0` violates the contract, `1` is incomplete
or unproven, and `2` is explicit and tested.

| Dimension | 2-point standard |
| --- | --- |
| Thesis alignment | Advances one-tool notebook PTC without expanding model authority. |
| Historical authority | One trace authority; projections are rebuildable and cutovers prove replay equality. |
| Notebook execution | Stable cells/attempts/epochs, transactional failure, safe restore, canonical materialization, and `nb-cli` interoperability. |
| Memory programs | Versioned, evidence-addressed, temporal, bounded, reproducible, receipted, and promotion-gated. |
| Effects and safety | One policy path with identity, receipts, confinement, redaction, cancellation, and reconciliation. |
| Deterministic control | Model output is advisory; reducers, budgets, recovery, and terminal state are host-owned. |
| Context and caching | Bounded provenance-carrying views preserve stable prefix bytes and explicit compaction epochs. |
| Verification | Criterion-bound independent evidence is required; no silent weakening or false completion path exists. |
| Simplicity | Reuses existing/ADK/stdlib mechanisms and keeps simple-task overhead low. |
| Evidence | Focused replay/fault tests pass and behavior claims use controlled ablations. |

Approval threshold: no hard rejection and at least `18/20`; any 1-point dimension
must have a named follow-up gate before merge.

Reject regardless of score if the change:

- creates a second historical authority without a temporary migration and
  replay-equality gate;
- adds a top-level model tool without an approved ablation;
- bypasses the broker or gives PTC broader authority than direct tools;
- makes authorization, recovery, budgets, or completion depend on model prose;
- loses, hides, or blindly replays a state-changing attempt;
- treats a warm heap, notebook, cache, index, or ADK session as unreconstructable
  historical truth;
- uses notebook execution outside the write-ahead `python`/broker/worker path;
- creates a memory result without program version, watermark, and source evidence;
- weakens required verification because tests are missing or already failing;
- changes stable-prefix bytes with volatile task/session state;
- claims success without focused tests or performance without comparison.

## Reference documents

- `docs/design/trace-native-repl-agent.md`
- `docs/adr/long-running-context-memory-programs.md`
- `docs/adr/coding-harness-minimal-sota-extensions.md`
- `docs/design/pi-inspired-adk-coding-harness.md`
- `docs/architecture.md`
- `docs/security.md`
- `docs/evaluation.md`
- `docs/development.md`
