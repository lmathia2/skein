# Execution, recovery, and verified completion

> Status: core contracts implemented; safe automatic recovery remains opt-in
>
> Updated: 2026-09-12

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. The model chooses tactics; deterministic reducers control task state.
2. All brokered effects use one write-ahead intent/terminal-receipt protocol.
3. Recovery reconciles evidence before retrying; unknown effects are never assumed
   safe or successful.
4. A checkpoint identifies evidence and environment state. It is not a filesystem
   snapshot and does not serialize the Python heap.
5. A model completion claim requires independent criterion-bound verification.
6. Steering, cancellation, budgets, approvals, and deadlines remain host-owned.
7. Recovery distinguishes live availability, historical evidence, and current
   workspace observations. Missing optional memory is not an unknown effect, and a
   memory summary cannot reconcile an uncertain operation.

## Execution loop

```text
initialize or replay task
         |
         v
build bounded work packet
         |
         v
model <----> tools/PTC broker
         |
         +--> answer/block --------------------------+
         |
         `--> claim done / request verification      |
                                                     v
                                      deterministic verifier
                                           |             |
                                         pass          fail
                                           |             |
                                      task.finished   counterexample
                                                         |
                                                         `--> next work packet
```

The worker owns the inner model/tool loop. The outer workflow re-enters after user
steering or failed verification. Malformed terminal output and bare `continue` fail
closed. Natural-language assertions are never parsed into authoritative state.

## Event reduction

`task.created` contains the typed task ledger. Explicit `ledger.patched` events
change its fields. `task.blocked` and `task.finished` perform small fixed transitions;
observational events do not implicitly mutate control state. Replay rejects sequence
gaps and conflicting duplicates.

This keeps three questions separate:

| Question | Owner |
| --- | --- |
| What happened? | Canonical events and artifacts |
| What should happen next? | Typed task reducer plus current user steering |
| Has the requested outcome been achieved? | Independent verifier |

## Effect protocol

Every host-brokered state-changing or externally observable operation follows:

```text
stable operation ID
      |
persist intent and authorization result
      |
execute through confined adapter
      |
persist completed | failed | blocked | timeout | cancelled | unknown
      |
publish bounded result/artifact reference
```

Idempotency returns the same prior result only when identity and content match;
reusing an idempotency key for different content is an error. File changes use
atomic confined primitives where practical. Shell and MCP results are redacted and
bounded. Large bodies become content-addressed artifacts.

Skein notebook PTC reaches file, shell, and registered capabilities through this
protocol. It records submitted source and terminal cell results, and refuses automatic
replay of interrupted or unknown cells.

The local adapter is intended for trusted workspaces and is not an OS security
sandbox. Docker isolates configured commands, not every host-side Python/file path.
Network, dependency installation, destructive commands, and history mutation remain
disabled unless policy and approval explicitly permit them.

## Checkpoint and recovery

A published checkpoint binds:

- task, session, and real ADK invocation identities;
- task-event and receipt evidence;
- reducer/schema and context epoch;
- workspace fingerprint;
- spent input budget and verification state;
- optional notebook boundary.

Publication is last: referenced events, artifacts, and receipts must already be
durable. On same-machine safe-auto recovery, Skein verifies the saved behavior and
workspace identity, locates the original ADK invocation, compares operational and
canonical task evidence, validates receipts, checks the context epoch and remaining
budget, rejects pending/expired approvals, and only then resumes without injecting a
duplicate user message.

Recovery blocks on missing/corrupt evidence, workspace divergence, exhausted budget,
or an unknown effect that cannot be reconciled. It does not restore arbitrary files,
promise exactly-once shell execution, or replay effectful notebook cells.

### Compaction is not a worker restart

An ordinary context cut changes the provider-visible suffix, not the Python worker.
The handoff records observed worker availability and the last committed epoch. Parse
and source-validation failures can preserve that worker; execution errors under the
default replay-safe policy discard it. Experimental snapshot rollback covers selected
primitive/container values within the same live worker, not timeout or process loss.
The [PTC ADR](trace-native-harness.md#state-policies) defines that limited scope.

A timeout during a broker call can leave an effect unknown even after the worker is
discarded. Retaining a variable, restoring a snapshot, or replaying a notebook cannot
resolve it. Automatic reconciliation is appropriate only where matching identities,
receipts, and workspace evidence establish the outcome; otherwise continuation stays
explicitly blocked. Current conservative blocking is not proof that every failure
changed the workspace, and broader automatic reconciliation remains gated work.

The [continuity implementation](../design/ptc-memory-continuity-plan.md) now carries
receipt-confirmed touched paths separately from the task-ledger modified-file list,
which can still lag in-loop edits. Neither list is a freshly verified workspace diff.
PTC receipts and direct-tool workspace-effect observations retain available content
hashes and conservative mutation uncertainty. Finding dependencies are annotated from
that evidence as historical, changed, or requiring revalidation; they do not claim to
detect external edits that were never observed. A current read/guarded mutation must
still establish the required source version. Restored historical content never silently
answers a current filesystem read or authorizes a stale guarded edit.

Recoverable context pressure uses bounded output/artifact indirection or the available
checkpoint without inventing missing findings. Corrupt captured history, scope/identity
mismatch, and unresolved effects still fail closed. Terminal reporting must distinguish
cumulative task-input-budget exhaustion from runtime bugs, provider failures, recovery
blocks, and independent verifier failure; unequal stopping conditions confound paired
quality and efficiency comparisons. The runtime now reports `TaskInputBudgetExceeded`
as `task_input_budget_exhausted` instead of generic `runtime_failed`. This classification
does not increase the budget or turn an incomplete run into a verified completion.

## Verification

Completion requires evidence for the requested acceptance criteria, applicable
build/type/lint/test checks, no new baseline-relative regression, no scope violation,
and no unresolved effect. The model's proposed evidence and self-authored tests can
support the decision but cannot own it.

Verification uses the same workspace, sandbox, policy, approvals, redaction, and
task identity as ordinary tools. A failing check becomes a durable counterexample for
the next model invocation. A passing pre-existing baseline proves only no regression;
it does not prove the requested behavior.

Coding-mode completion additionally requires at least one changed repository path.
This rejects unchanged-workspace completion; it is a necessary condition, not proof
that an arbitrary diff meets acceptance criteria. Analysis/answer modes do not inherit
that coding-only requirement.

## Steering and cancellation

Steering is durably queued and delivered at configured safe points before model/tool
work or at a work-batch boundary. Cancellation propagates through owned execution
boundaries. Started work must finish with an explicit terminal or unknown state.
One server process owns a state root; current SQLite/process-local locks do not claim
distributed coordination.

## Implemented boundary

- Real invocation-bound checkpoints and same-machine recovery validation exist.
- Safe-auto recovery is opt-in and covered by deterministic subprocess scenarios.
- Workspace fingerprints detect divergence but do not restore a workspace.
- Conversation notebook continuity and prior-run memory are separately authorized.
- Live model-quality, cost, and cache promotion gates remain pending.

## Rejected alternatives

- Replaying every incomplete action after restart: duplicates external effects.
- Treating ADK resumability as workspace or heap restoration: it is neither.
- Letting a model update authoritative completion state: unverifiable and fragile.
- Separate policy paths for direct tools and PTC: equivalent effects require the
  same authorization and receipt semantics.
