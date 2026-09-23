# ADR: Execution and recovery

Status: accepted and implemented

## Decision

Every workspace effect passes through one Skein execution boundary, whether requested
through a direct ADK tool, a nested PTC capability, or host verification. Recovery is
based on recorded boundaries, receipts, checkpoints, and current workspace state; it
never guesses that an interrupted effect was harmless.

## Effect boundary

The execution layer owns:

- workspace path confinement;
- command classification, sandboxing, and approvals;
- secret redaction;
- timeouts and bounded output;
- optimistic concurrency for edits and writes;
- mutation receipts and artifact references;
- workspace fingerprints used by verification and recovery.

Tool syntax does not change authority. The PTC worker cannot bypass this layer because
its helpers are injected capabilities rather than unrestricted Python I/O.

## Tool failures

Expected and unexpected tool exceptions are converted to bounded recoverable results so
ADK can return them to the model. Internal metadata remains available to the host but is
excluded from the normal model projection.

Approval-required operations wait through the approval store. Denial or expiry becomes
a tool result. An operation whose outcome cannot be proven becomes an unknown effect,
not a silent retry.

## Recovery boundaries

The workflow records a boundary before each model dispatch and around effectful work.
Task state is rebuildable from its append-only events. On resume, Skein compares
receipts and workspace fingerprints, restores safe PTC checkpoint values, and continues
through ADK's resumability mechanism.

The following are not automatically replayed:

- arbitrary shell commands;
- mutations without a confirmed receipt;
- failed or timed-out PTC cells;
- model calls whose external effect state is unknown.

Unknown effects must be reconciled or the task is blocked.

## Completion

The model does not decide terminal success. After an ordinary final response or an
explicit verify signal, the host executes the configured validation against the actual
workspace. A failure is recorded and included in the next coding packet. A passing
verification produces `HarnessOutcome(status="complete")` with changed paths and the
report.

Direct answers are permitted only when the request is eligible, the workspace is
unchanged, no mutation requested verification, and no steering is pending.

## Cancellation and budgets

ADK owns active invocation cancellation. Skein enforces work-batch, task input-token,
tool timeout, and iteration bounds. Exhaustion yields a typed blocked or failed outcome
rather than an invented completion.

## Implementation

- `harness/execution/`
- `harness/evidence/state/receipts.py`
- `harness/evidence/state/recovery.py`
- `harness/evidence/state/checkpoints.py`
- `harness/ptc/repl/`
- `app/agent/workflow.py`
- `harness/verification/`
