# Programmatic tool calling implementation and core design

> Status: accepted; Pi-hosted reference implementation is v4.1 and ADK compatibility mode is `pi_compatible`
>
> Updated: 2026-09-21

## Context

Skein tests whether a model can use one programmable action surface to compose file,
shell, and verification work with fewer model round trips. The implementation must
retain the same workspace authority as direct tools, keep model-facing output bounded,
survive worker loss without replaying effectful code, and remain inspectable in Harbor
traces.

Two integrations reuse the same
[`PersistentPythonWorker`](../../harness/ptc/repl/worker.py), but they are distinct:

- The ADK notebook profile exposes `execute_code`, records canonical lifecycle events,
  and materializes a notebook or JSONL projection.
- The Pi evaluation adapter exposes `code`; Pi owns inference and conversation state,
  while Skein owns Python execution and the Pier workspace bridge.

The ADK integration can select `workflow.mode: pi_compatible`. This does not embed or
fork Pi. It copies the model-visible v4.1 capability contract and continuation policy
onto Skein's existing worker while retaining ADK inference and Skein's native trace.
`thin` is retained as a compatibility alias for prior experiment commands.

This ADR specifies the Pi-hosted v4.1 reference implemented by
[`pi_skein_ptc_extension.mjs`](../../scripts/pi_skein_ptc_extension.mjs) and
[`pi_code_tool_harbor.py`](../../scripts/pi_code_tool_harbor.py). The broader ADK
notebook protocol remains specified in
[`trace-native-harness.md`](trace-native-harness.md).

## Decision

Use one persistent, brokered CPython subprocess behind one Pi extension tool. Keep
batching model-authored, derive the helper contract from real Python signatures, block
only errors the stdlib preflight can prove, return a compact text projection, and
checkpoint only bounded JSON-safe values. Do not replay the transcript to reconstruct
state.

```text
Pi model
   |
   | code({code}) or code({result_id, offset})
   v
Pi extension: prompt + completion evidence gate
   |
   | local HTTP bridge
   v
PiSkeinPtcPierAgent
   |                         \
   | execute cell             \ read retained result
   v                           v
PersistentPythonWorker      bounded result store
   |
   | read/write/edit/bash/verify requests
   v
_PierPtcBroker -> HarborWorkspaceEnvironment / Pier Docker task
```

## Model-facing contract

The extension registers one tool named `code`. Its schema accepts either one Python
cell or a retained-result identifier with byte offset and limit. The description is
assembled once from stable components: runtime, workflow, generated helper signatures,
contract rules, verification guidance, and one executable example. Task and session
state do not rewrite the description.

The worker preloads `json`, `math`, and `re`. The following synchronous functions are
available without `await`:

| Helper | Behavior |
|---|---|
| `read(path, offset=1, limit=400)` | Confined text read with coverage and SHA-256 metadata |
| `write(path, content, expected_sha256=None, expected_absent=False)` | Atomic confined write with changed flag, hash, and diff |
| `edit(path, old_text, new_text, expected_sha256=None)` | Guarded exact replacement with changed flag, hash, and diff |
| `bash(command, timeout_seconds=120)` | Workspace shell using ordinary Bash pipeline semantics |
| `verify(command, timeout_seconds=120)` | Workspace shell using `pipefail`; raises when the broker result is not successful |

Each helper returns a readable string-like object with attributes and legacy mapping
access. Shell text combines stdout, a labelled stderr section when present, and an
`[exit N]` marker. A cell may make at most 64 helper calls.

The ADK `pi_compatible` mode exposes the same five preloaded helpers through
`execute_code`. Its stable prompt names their exact signatures, the three preloaded
modules, blocked direct-I/O imports, and failure behavior. Internal result envelopes
remain available to trace capture, but the model sees direct text and compact mutation
results rather than canonical event records.

## Batching and variable reuse

There is no batch planner or separate batch tool. The model uses normal Python loops,
conditionals, filtering, and data structures to combine operations whose inputs are
already known. It returns to the parent model when an observation requires judgment.

The namespace persists for the worker epoch, including user functions and arbitrary
supported live Python values. The extension does not inject a variable inventory into
every prompt. Trace telemetry uses a conservative straight-line AST heuristic to note
when a cell reads an existing binding before reassigning it; this metric does not
change execution or context.

## Source validation and contract preflight

The entire cell is parsed and source-validated before any line executes. Direct host
I/O modules and calls are blocked so intended workspace effects cross the broker.

[`preflight.py`](../../harness/ptc/repl/preflight.py) then reads the real broker
signatures and result schemas. It rejects provable mistakes before execution:

- unresolved straight-line names;
- unknown helper arguments and invalid signatures;
- literal argument type mismatches;
- unknown keys on a value whose helper-result origin is still known; and
- selected invalid literal operators.

The checker deliberately abstains after branches, comprehensions, opaque calls,
dynamic scopes, mutation, or user-function boundaries. This produces false negatives
rather than blocking valid dynamic Python. Runtime checking remains authoritative for
everything the preflight cannot prove.

## Result projection and retention

The worker captures stdout, stderr, and a safe representation of the final expression.
The bridge returns plain model-readable text rather than its internal result envelope.
It automatically appends important information the cell did not select: shell stderr,
nonzero exits, timeouts, partial-read coverage, failed writes, rollback state, and
worker-loss notices.

One observation is capped at 50 KiB and 2,000 lines using bounded head-and-tail
selection. The bridge retains up to 32 fuller records of at most 256 KiB each. The
model can page those records with `result_id`/`more` and an offset. Retention is
bounded and discarded suffixes cannot be recovered.

Trace details record only newly created binding names; the model-facing text does not
append a state inventory. Full variable contents stay in the worker until code
explicitly prints or selects them.

## State, failure, and recovery

Before execution, snapshot mode captures supported live values for rollback. A Python
execution exception restores that in-process snapshot and reports `state_preserved`;
external file and shell effects are not rolled back. Parse or source-validation failure
executes no line and no helper.

After every successful cell, the adapter atomically writes an at-most-1 MiB checkpoint.
Only exact finite JSON-safe values cross this boundary: null, booleans, integers,
finite floats, strings, lists, and string-keyed dictionaries up to depth 20. Cycles,
tuples, functions, modules, handles, custom objects, and oversized values are omitted
and named in recovery diagnostics.

A hard cell timeout or worker transport failure discards the subprocess. Before the
next cell, the adapter restores the latest valid plain-data checkpoint into a fresh
worker. No prior cell is replayed, so recovery cost and prompt size do not grow with
the transcript. The tradeoff is explicit: opaque live state is lost.

Shell calls have their own broker timeout of 1–600 seconds. The worker grants a fresh
bounded Python interval after each returned shell call. A broker call that exceeds the
cell deadline discards the worker and marks the effect unknown because the host cannot
infer whether an external mutation completed.

## Verification

`bash` is for exploration and ordinary project commands; `verify` supplies the final
pipe-safe evidence boundary. The v4.1 extension maintains conservative mutation and
verification generations:

- a changed `write` or `edit` advances the mutation generation;
- every `bash` and `verify` call advances it; and
- a successful `verify` covers the resulting generation.

At the end of the Pi run, the extension may enqueue one follow-up review when the last
possible mutation is not covered or the final prose declares a known gap. This gate is
model guidance, not Harbor's independent grader.

The v4.2 experiment replaced the generation rule with a workspace fingerprint. It cut
`verify()` calls but did not cut total interactions: the model substituted more
`bash()` exploration, while cost and latency increased. V4.1 therefore remains the
reference implementation. See the
[`v4.1 comparison`](../experiments/e13-pi-skein-v4.1-comparison.md) and
[`v4.2 comparison`](../experiments/e13-pi-skein-v4.2-comparison.md).

In ADK `structured` mode, repeated identical managed-verification failures may exhaust
the configured retry policy. In `pi_compatible` mode those failures are observations:
the concise report is projected into the next model turn and repair continues until
verification passes or the ordinary task/model/time budget ends. Confinement,
unknown-effect reconciliation, approvals, and independent terminal verification are
unchanged; only continuation policy differs.

## Authority and isolation

The CPython subprocess is a computation environment, not the task's project
interpreter. Workspace code and dependencies execute through the broker. File helpers
use `HarborWorkspaceEnvironment` confinement and guarded writes; shell commands run in
the Pier task workspace with container-side termination. The worker's source guard is
defense in depth, not an adversarial OS sandbox.

Every nested helper call receives an ordered outcome record with operation, status,
duration, and relevant diagnostics. The model sees the compact projection; full Pi
events, cell results, patches, verifier output, usage, pricing, and Harbor results stay
in the trial directory.

## Known limits

- V4.1 conservatively treats every shell call as potentially mutating, which can cause
  repeated final verification.
- Persistent variables are not automatically summarized to the model.
- Rollback covers supported Python state, never external effects.
- Plain checkpoints preserve useful data, not arbitrary Python execution state.
- Retained result paging is per worker run and capped at 32 records.
- Pi may emit a terminal assistant message with `stopReason: "error"` while exiting
  zero. The current adapter accounts usage but does not promote that message to a Pier
  agent error; evaluation must classify those traces as provider-interrupted.
- The adapter is evaluation infrastructure, not a supported end-user Pi package.

## Consequences

The design keeps calls cheap as conversations grow, avoids transcript replay, and
lets the model compose mechanical work without granting ambient host authority. It
also makes state loss explicit and bounded. Quality is competitive with Pi Code Tool
on the completed v4.1 panel, but v4.1 still uses more interactions, tokens, and latency;
it remains an evaluation reference rather than the general Skein default.

The native compatibility mode makes the Pi loop a controlled Skein treatment instead
of requiring Pi as the host. `structured` remains the default until the pinned matched
evaluation establishes whether the lighter contract preserves quality.
