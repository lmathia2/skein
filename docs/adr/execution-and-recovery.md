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

The entire submitted cell is parsed and source-validated before any line executes,
including code in unreachable branches. Canonical results record `execution_started`;
failed compact results expose it when known. A rejected cell explicitly reports that
no assignment or capability ran. An existing same-name binding remains its old value,
not the output of the rejected program. `state_preserved` alone never establishes
non-execution: runtime failure with snapshot rollback can follow completed effects.
Successful compact replies omit the redundant field to preserve normal egress bounds.

A timeout during a broker call can leave an effect unknown even after the worker is
discarded. Retaining a variable, restoring a snapshot, or replaying a notebook cannot
resolve it. Automatic reconciliation is appropriate only where matching identities,
receipts, and workspace evidence establish the outcome; otherwise continuation stays
explicitly blocked. Current conservative blocking is not proof that every failure
changed the workspace, and broader automatic reconciliation remains gated work.

PTC preserves an explicitly classified effect from the host tool envelope. A reserved
memory parser or pre-commit note-validation/budget rejection records `effect=none`,
not an invented unknown shell effect. This is a completed rejection, not successful
note construction. Missing effect metadata retains the conservative fallback; canonical
append/publication errors and identity mismatches remain unknown. An idempotent retry
can republish the original committed note without duplicating it, but a callback failure
does not relabel that earlier write as a pre-execution rejection.

The [continuity implementation](../design/ptc-memory-continuity-plan.md) now carries
receipt-confirmed touched paths separately from the task-ledger modified-file list,
which can still lag in-loop edits. Neither list is a freshly verified workspace diff.
PTC receipts and direct-tool workspace-effect observations retain available content
hashes and conservative mutation uncertainty. Finding dependencies are annotated from
that evidence as historical, changed, or requiring revalidation; they do not claim to
detect external edits that were never observed. A current read/guarded mutation must
still establish the required source version. Restored historical content never silently
answers a current filesystem read or authorizes a stale guarded edit.

PTC artifact recovery pages immutable **bytes**, not independently decoded text
fragments. A valid UTF-8 page returns `encoding=utf-8` and exact `text`; a split
code point or binary page returns `encoding=base64`, null `text`, and exact base64.
Offsets and lengths remain byte-based. Join page bytes before parsing; `complete`
means end-of-artifact, not full coverage when starting at a nonzero offset. Scope
and content-hash validation remain required. Full-payload redaction is checked
before paging/encoding; a payload requiring redaction fails closed instead of
silently changing evidence or exposing a secret across pages. Detailed artifact
help describes the saved tool-result envelope and its historical-only authority.
When memory programs are disabled, the PTC handoff advertises artifact recovery;
memory handoffs advertise only enabled program versions. These are guidance and
transport fixes, not proof that a model uses recovered evidence reliably.

PTC `shell.run` results explicitly identify `result_kind=process|managed` before
canonical result capture. This describes the route, not execution or success.
Memory/search commands keep their native structured `data`; they do not acquire
fabricated subprocess exits or stdout. Process results retain actual exits and
streams, including unavailable exits on non-execution. Callers must branch on the
result kind and validate the relevant status/coverage before using evidence. Help
and the stable PTC example expose this distinction directly. No new tool or execution
authority is introduced, and existing receipt/effect semantics remain unchanged.

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

A successful command requires an observed zero exit code and `ok` status. Missing
exit codes are unknown, not success. Baseline-relative failure comparison requires
matching command/category identities, completed positive failing exit codes, and
untruncated diagnostics from both runs; timeout, blocked, signalled, or incomplete
results cannot become no-regression evidence. Such comparisons remain limited to
the failure identities recognized by the test-output parser, not a semantic oracle.
Excluding baseline-relative results from criterion evidence preserves the original
validation indices, so another command's success cannot satisfy the wrong row.

Criterion selection is not itself proof of semantic relevance: the current general
probe maps available successful checks to its row. A generic passing check cannot
demonstrate an arbitrary source-grounded answer. Controlled memory evaluations must
report first model proposals separately from independently accepted outcomes, and
must test the production verification/re-entry boundary before claiming end-to-end
evidence-backed completion. These are pending empirical/coverage gates, not grounds
to require every file to be read in full.

The offline PTC/workflow integration now verifies that a wrong answer with a passing
self-authored read-back assertion is rejected by a required task-specific oracle.
An unchanged second proposal stops without `task.finished`; a repair using just the
required one-line source range completes only after that oracle passes. This tests
the real verification/re-entry path with scripted model proposals, not live-model
reliability or semantic completeness of generic discovered checks.

The controlled development evaluator's required host-owned oracle also checks frozen
decisive source path/version/ranges against completed task-local reads before the last
managed answer write, and matches that write's content hash to the checked artifact.
A correct guess with missing source evidence is rejected; reading later does not
retroactively support an earlier write, but a subsequent answer submission can recover.
Already completed applicable ranges count without new reads. Unknown/unmapped evidence
does not pass, and corrupt evidence is an infrastructure error rather than a repairable
wrong answer. This is an evaluation-specific acceptance contract, not a production
requirement to use `fs.read` for every task or proof of arbitrary Python dataflow.

Coding-mode completion additionally requires at least one changed repository path.
This rejects unchanged-workspace completion; it is a necessary condition, not proof
that an arbitrary diff meets acceptance criteria. Analysis/answer modes do not inherit
that coding-only requirement.

## Steering and cancellation

Evaluation answer contracts can additionally require exact validation commands. PTC
shell request and terminal events carry the command hash; a successful host-observed
validation must match that operation and precede the answer request. Source text or
an unpublished, failed, or later result is not completed evidence. This reuses existing
validation observations and immutable result artifacts; it adds no execution authority.
The synchronous PTC API exposes no model-facing pending-task state. Its publication
boundary is tested by holding an actual command result before its receipt is recorded.
Failed shell effects remain unknown, even when a negative fixture expects failure.
Reporting that a model withheld an answer does not reconcile those effects or qualify
the task as safely complete. These evaluator contracts are not a general proof of
semantic dataflow or relevance for arbitrary production answers.

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
