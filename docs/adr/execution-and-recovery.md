# Execution, recovery, and verified completion

> Status: core contracts implemented; safe automatic recovery remains opt-in
>
> Updated: 2026-09-09

Code-level requirements and test mappings are in the
[implementation specification](../specification.md).

[Skein architecture design](skein_architecture_design.md) explains the motivation and links the companion ADRs.

## Decisions

1. The model chooses tactics; deterministic reducers control task state.
2. All brokered effects use one write-ahead intent/terminal-receipt protocol. The
   trusted Prime-native profile is an explicit exception: its direct Python effects are
   recorded only at cell granularity as `native_untracked` and never represented as
   broker receipts.
3. Recovery reconciles evidence before retrying; unknown effects are never assumed
   safe or successful.
4. A checkpoint identifies evidence and environment state. It is not a filesystem
   snapshot and does not serialize the Python heap.
5. A model completion claim requires independent criterion-bound verification.
6. Steering, cancellation, budgets, approvals, and deadlines remain host-owned.

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

Skein notebook PTC reaches file, shell, and registered capabilities
through this protocol. Prime-native code has direct trusted OS access instead. Skein
therefore records the submitted source, terminal cell result, snapshot outcome, and
`native_untracked` effect, refuses automatic replay of interrupted/unknown cells, and
rejects Prime with safe-auto recovery or active brokered memory commands. This is a
deliberate trust profile, not equivalent broker enforcement.

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

## Verification

Completion requires evidence for the requested acceptance criteria, applicable
build/type/lint/test checks, no new baseline-relative regression, no scope violation,
and no unresolved effect. The model's proposed evidence and self-authored tests can
support the decision but cannot own it.

Verification uses the same workspace, sandbox, policy, approvals, redaction, and
task identity as ordinary tools. A failing check becomes a durable counterexample for
the next model invocation. A passing pre-existing baseline proves only no regression;
it does not prove the requested behavior.

## Steering and cancellation

Steering is durably queued and delivered at configured safe points before model/tool
work or at a work-batch boundary. Cancellation propagates through owned execution
boundaries. Started work must finish with an explicit terminal or unknown state.
One server process owns a state root; current SQLite/process-local locks do not claim
distributed coordination.

## Implemented boundary

- Real invocation-bound checkpoints and same-machine recovery validation exist.
- Safe-auto recovery is opt-in and covered by deterministic subprocess scenarios.
- Prime-native snapshot recovery is run-scoped, explicitly trusted, and separate from
  safe-auto effect recovery; interrupted or unknown native effects require reconciliation.
- Under Harbor, Prime's persistent worker and trial-stable snapshot directory live
  inside the disposable task container. Host-side orchestration sends bounded requests
  through Harbor's environment interface; it never treats a host shadow checkout as
  execution authority. A new trial/container is the cross-example reset boundary.
- Workspace fingerprints detect divergence but do not restore a workspace.
- Conversation notebook continuity and prior-run memory are separately authorized.
- Live model-quality, cost, and cache promotion gates remain pending.

## Rejected alternatives

- Replaying every incomplete action after restart: duplicates external effects.
- Treating ADK resumability as workspace or heap restoration: it is neither.
- Letting a model update authoritative completion state: unverifiable and fragile.
- Separate policy paths for direct tools and PTC: equivalent effects require the
  same authorization and receipt semantics.
