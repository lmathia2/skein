# Execution, recovery, and verified completion

> Status: core contracts implemented; safe automatic recovery remains opt-in
>
> Updated: 2026-09-19

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
8. `blocked` without a concrete question is not a human-input terminal. If execution
   is reconciled, changed work enters verification and unchanged work receives a
   bounded continuation; unresolved execution still fails closed.
9. PTC work batches and verification repair have independent host-owned limits.
   Hitting a changed-batch ceiling enters verification, while an absolute verification
   attempt budget prevents ineffective workspace churn from resetting retry control.
10. Harbor submission packaging is transport, not completion authority. The DeepSWE
    adapter commits a final dirty workspace for the external grader but does not mark
    the run verified or clear blocked/failed status.
11. Completion claims are advisory rows. Unknown, stale, or duplicate criterion IDs
    are rejected and recorded individually; they never satisfy a criterion and do not
    prevent recognized claims from reaching the independent verifier.

Task-input budget exits retain their own terminal category through ADK exception
wrappers. Classification follows typed causes, not exception-message matching;
unrelated callback failures stay runtime/harness failures. This does not raise the
budget, dispatch a rejected request, or clear any execution uncertainty. Evaluation
must also preserve diagnostics when a budget stop occurs before any context cut.

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

Malformed Responses tool arguments are rejected before this protocol admits a PTC
cell or direct tool invocation. The shared Codex/OpenRouter parser accepts only a
JSON object that can round-trip through the strict outgoing JSON encoder; it never
wraps an array/null as a callable `value` argument or guesses missing code. Invalid
argument text is held privately, excluded from ADK serialization, while the public
call carries a reserved bounded rejection marker.

The factory-installed ADK `InvalidToolArgumentsPlugin` owns
`invalid_tool_arguments@1`. After final usage accounting, it redacts the original
argument text into the existing content-addressed artifact store and appends
`tool.call_rejected` before exposing the artifact reference. The receipt identifies
task, invocation, call, program/source hash, original text hash/size, retained size,
redaction status, and explicit no-execution/no-effect semantics. Artifact bytes are
exact only when redaction leaves them unchanged. Existing task-scoped artifact
loaders provide bounded recovery; no new model-facing tool or evidence store exists.

Partial calls carry only the bounded parser placeholder and never execute; artifact
publication waits for final usage so a storage failure cannot discard an already
reported charge. Repeated final delivery is idempotent for identical content; mismatched
identity/content, corrupt artifacts or failed publication stop processing. Before-tool
handling verifies the invocation/call receipt and returns an explicit `error` without
submitting a cell, changing the heap, or replaying any code. A model-supplied reserved
marker also rejects execution but cannot confer artifact access. Corrected subsequent
calls use the ordinary broker and verification paths. Previously exposed history is
not retroactively rewritten under its existing cache epoch.

The local adapter is intended for trusted workspaces and is not an OS security
sandbox. Docker isolates configured commands, not every host-side Python/file path.
Network, dependency installation, destructive commands, and history mutation remain
disabled unless policy and approval explicitly permit them.

Docker command environments default `PYTHONPYCACHEPREFIX` to `/tmp/pycache`, outside
the mounted workspace, matching local execution's import-cache isolation. Ordinary
imports must not create incidental scope violations or require cleanup cells. Explicit
compiler output paths and configured environment overrides retain their semantics;
this is execution hygiene, not an exemption in the independent scope verifier.

Command classification separates shell commands before applying the existing risk
rules, but quoted or escaped separators remain argument data. In particular, a
semicolon in a quoted `python -c` body is not another shell command. Each real
pipeline/list command is still classified, including trailing network, publish and
destructive operations. Unclosed quotes, unsupported unquoted comments/expansion/
grouping, and substitution remain non-automatic. This lexical correction does not
make the risk classifier a shell security sandbox or inspect interpreter-program
effects: Python retains its existing trusted-workspace build/test category. Exact
persisted denials, task-scoped approvals, confinement and completion gates remain
owned by the same adapter and verifier.

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

### Pi-hosted v4.1 recovery boundary

The complete adapter decision is in the
[programmatic tool calling ADR](programmatic-tool-calling.md).

The Pi evaluation adapter uses the same CPython worker with a smaller recovery
contract than the ADK notebook path. Before each cell it snapshots supported live
values for in-process exception rollback. After each successful cell it atomically
writes a bounded JSON checkpoint containing exact JSON-safe plain values. A worker
timeout or transport failure discards the process; the next cell restores only that
checkpoint. External effects are never rolled back, opaque Python values are omitted,
and submitted cells are never replayed.

This design keeps model context independent from recovery cost and avoids Code Tool's
transcript reconstruction. It does not reconstruct functions, imports, live handles,
or every prior observation. The model-visible recovery notice names omitted bindings,
and recovery remains subordinate to effect reconciliation when a timed-out broker
operation may have changed the workspace.

Pi provider failures currently have a separate limitation at the adapter boundary.
Pi may emit an assistant message with `stopReason: "error"` and still exit zero. The
v4.1 adapter accounts its event usage but does not yet promote that terminal message
to a Pier agent error. Evaluation code must inspect the retained Pi events and treat
those trials as provider-interrupted; a graded unchanged workspace is not evidence of
agent failure or success.

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

Artifact operations use the same distinction. Invalid load/page arguments and denied
task references are explicit no-effect results; invalid publish metadata is rejected
before content publication. Verified content denied by the redaction policy is never
exposed. Recovery guidance points to exact task-authorized artifact listings, never
hash guessing or wider scope. Resolver integrity failures and publication exceptions
retain conservative unknown-effect handling, including a failure after content or its
publication event was written. No successful later load clears an older unknown effect.

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

The v12 live stability audit exposed an implementation gap: a completed Python cell
with a failed nested shell (`effect=unknown`) could still reach `task.finished` after
passing answer checks. Resume's open-capability check was not part of completion.
The corrective contract uses one deterministic unresolved-execution projection for
resume, completion and current handoff metadata. Pending capability/cell/validation
intents and unknown terminal effects stay unresolved; unrelated successful commands,
new answers, later checkpoints and workspace equality do not reconcile them. Missing
or contradictory operation identity fails closed. Explicit known no-effect rejections
remain distinct from unknown execution. Verification does not dispatch more commands
when this evidence is unresolved. No general automatic shell reconciliation is added.
The evaluator separately records unresolved execution and flags an accepted task as
false acceptance even if its answer bytes are correct. Live qualification is pending.

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

The counterexample pass is bounded as a decision boundary, not an open-ended second
implementation phase. The worker may execute one grouped PTC cell to falsify claimed
evidence or establish a concrete defect. It then returns to the outer workflow for a
fresh structured decision. Exact valid criterion claims are rendered as a resubmission
scaffold; malformed, stale, missing, or duplicate IDs never acquire authority and are
not fuzzy-matched. The harness does not automatically promote the pre-review claim,
because a read-only review cell may have discovered a counterexample that the model has
not yet interpreted. A correction must provide new completed evidence, and every renewed
claim still passes the unchanged independent verifier and unresolved-effect checks.

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

The controlled validation oracle now additionally binds a successful check to the
declared source versions observed before its broker dispatch. Previously, a passing
check on a temporary input could be followed by restoration of another input version
and a correct answer for that restored version; both arms incorrectly completed.
The regression executes real managed writes and a real passing subprocess. Source
observations at dispatch now reject that mismatch without requiring a rerun for an
unchanged applicable check. The rule concerns explicitly declared fixture sources,
not arbitrary semantic dependency discovery or continuous external-state freshness.
The independent result audit also classifies accepted-but-unsupported latest answers
as `false_acceptance`, while retaining the raw acceptance and artifact verdicts. A
test deliberately bypassing only the oracle evidence check exercises that detection.

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
Queue acknowledgement means delivery was consumed, not that its instruction expired
or its requested action succeeded. Later work/review packets retain the task's ordered
delivered instructions as required control. Review uses completed evidence for old
prerequisites while following the current request; it does not infer a state transition
or rewrite acceptance criteria by parsing user prose. Original scope and independent
checks remain authoritative. See `delivered_steering@1` in the context ADR.
Inner-loop delivery now records an immutable exposure at its first native-history
boundary (`steering_delivery@1`). Request reconstruction preserves that position
after acknowledgement instead of repeatedly moving the message to the suffix.
An exposure is not an execution receipt. Newly delivered steering and a preceding
unconsumed tool result cannot be compacted away before the model sees them. Failed
publication or inconsistent replay stops dispatch without consuming the queue item.
One server process owns a state root; current SQLite/process-local locks do not claim
distributed coordination.

## Implemented boundary

PTC binding invalidation preserves an optional historical completed-read handle in
the new cell result (`ptc_state_updates@1`). It never treats the old variable as live,
replays the failed cell, or restores its unfinished calculation. Recovery uses the
existing task-authorized, hash-checked artifact loader and retains original source
version/range coverage; a complete artifact page does not imply a complete source.
Unknown effects still block execution and completion. Actual exception-induced heap
loss has explicit recovery guidance; parse rejection retains existing bindings.
Snapshot rollback may preserve copied values while invalidating their object-identity
provenance, so a historical handle is not itself proof of heap loss.

Reads may complete before a later calculation fails in the same cell. Their durable
capability receipts remain recoverable under `ptc_state_updates@2`, even though no
successful cell manifest ever contained the binding. The failed heap is never the
source of that recovery metadata. The usual artifact authorization, original source
coverage, freshness requirements and unresolved-effect fences still apply. Required
binding invalidations can be grouped to leave room for the recent completed reads.

The `ptc_state_updates@3` recovery message and artifact help share a guarded Python
example for decoding a saved completed-read envelope. `artifacts.load` still pages
exact immutable bytes; it does not automatically decode source, restore bindings,
or read the current workspace. The example requires a complete first UTF-8 page
and successful saved result, preserves the original coverage metadata, and clears
its output bindings before checking an unavailable or partial page. Larger/binary
artifacts still require exact byte assembly using the existing loader contract.
This is model-side guidance, not reconciliation or permission to use stale evidence.

The PTC prompt and existing kernel help now share an explicit execution boundary:
the computation worker is not the workspace interpreter. Captured source is data,
not permission to exec/eval it or import project code into the host worker. Required
project checks remain brokered commands under existing policy, and a denied command
does not become executed evidence. Generated command arguments use standard-library
quoting. Kernel help derives blocked-call/module lists from the actual source guard.
The acquisition example retains path-keyed source envelopes separately from answer
and check results, including original citations and partial-range metadata; multiple
ranges/versions require distinct retained entries. No automatic source recovery,
import-policy expansion, review bypass or verification weakening is introduced.
This is implemented guidance; live efficiency and model uptake remain unqualified.

PTC error locations distinguish notebook source from parser input. Execution
exceptions use traceback frames belonging to the submitted source, not an arbitrary
exception's `lineno` attribute (for example, JSONDecodeError's JSON-data position).
Compilation uses a deterministic source-hash filename so a retained function from
an earlier cell cannot supply a line number for unrelated current source. Such an
error points to its current-cell call site; same-cell function failures retain the
inner source location. Syntax/parser and source-guard locations remain unchanged.
This changes diagnostics only, not source validation, execution, state rollback,
effect status, replay admission or completion authority.

Unavailable managed-search backends and invalid reserved-search syntax are known
pre-dispatch rejections. Their originating adapter records `effect=none`; the shared
PTC broker preserves it in the immutable result artifact and capability event.
No backend or sandbox operation ran. Unavailable search advertises bounded existing
alternatives without executing them. Backend exceptions after dispatch and ordinary
shell failures are not exempted from uncertainty, and later correct answers or
workspace inspections do not clear an older unknown effect. Duplicate cell
acknowledgement does not rerun the rejected operation or become a new result receipt.

File precondition conflicts now carry `effect=none` through the shared coding-tool
envelope, failed managed receipt, persisted artifact and nested PTC outcome.
`FileConflictError` is a trusted adapter contract: it is raised only before workspace
mutation, not a label inferred from error text. Both local and Harbor file adapters
check hash/absence/edit preconditions before dispatching a write. Local parent
directory creation now follows those checks, so a rejected missing-path write cannot
quietly create directories. The result remains an error, never a successful write;
unknown I/O/post-mutation failures remain fenced. This adds no automatic retry or
reconciliation. A new attempt may acquire current evidence and submit a fresh guard.
Idempotent content equality also requires an observed existing file: absence is not
an empty file. Both adapters now create an absent empty file as a real mutation,
reject a mismatched hash guard even when requested content is empty, and report
already-applied only for an existing matching file. The existing idempotency policy
for matching content is otherwise unchanged.

Completed failed validation is narrower than an ordinary shell failure: a single
classified validation command with a known nonnegative exit code, a completed same-task
receipt, and equal verified before/after workspace fingerprints is an observed *failed
attempt*. It is replayable as that failure, not a passing check. A timeout, incomplete
receipt, unknown/native effect, shell composition, or workspace divergence remains
unresolved. Non-Git local workspaces now receive a deterministic file-content
fingerprint rather than a constant empty digest; an untracked mutation cannot falsely
look unchanged. E4 Harbor traces exposed a separate boundary bug: managed tools
fingerprinted Pier's empty local shadow workspace although commands and edits affected
the remote repository under `/app`. Identical `e3b0c442...` receipts were not workspace
evidence. Managed tools now use the injected execution repository's fingerprint on both
receipt sides; local runs default to the local repository and Pier supplies its remote
content snapshot. Deterministic remote-versus-shadow tests pass; E5 live receipt
confirmation observed nonempty remote digests and changing remote edit fingerprints. This does not
retroactively clear E3's original unknown records or authorize user-facing completion.

E6/E7 add an opt-in successful-cell, content-addressed JSON-plain checkpoint. The
worker reports selected and unsupported/oversize names; restoration verifies the
task/notebook/source cell, artifact SHA/length, source event, binding summary
program, and exact restored-name manifest. A failed or timed-out calculation is
never captured. Restored values are marked historical and do not clear uncertain
effects, establish a current source version, or count as completion evidence.
An exact checkpointed source string may carry a historical path/SHA only when
its content hash equals the SHA of a completed `fs.read` receipt. The current
one-line whole-file SHA still must be checked before using that text for edits;
a mismatch requires reacquisition. The ordinary bounded `agent.state.list()`
browsing cap does not silently truncate the restore RPC's root-name report.

E7 live DeepSWE FastAPI traces exposed a recovery acknowledgement race: after
an execution exception reset the worker, the next model-authored cell was
already planned as a full reread before the start-of-cell checkpoint restore
ran. The returned failure text also said all bindings were discarded, despite
the pending safe restoration. E8 therefore adds a further opt-in timing switch:
for a no-effect/observed runtime failure with an available verified checkpoint,
restore immediately after recording the failed cell and before returning the
tool result. The response reports the actual new live epoch and attested
historical bindings; the failed cell's partial assignments remain discarded.
Timeouts, unknown or known-changed effects and corrupt/unavailable checkpoints
do not receive an eager success claim. This is an experimental usability change,
not a relaxation of effect reconciliation, source freshness, or verification.

E8 live DeepSWE confirmed eager recovery fires safely but **did not** improve
the paired outcome: Harbor reward 1/2 control to 0/2 eager, with higher calls,
input, cost and source reacquisition. Keep the switch opt-in; deterministic
recovery timing is not a quality gate. E8 also exposed two representation
boundaries. Work packets must take their latest worker epoch from a verified
`repl.state_restored` event when it follows a failed-cell event; otherwise a
second unnecessary full worker-transition packet can be produced. E9 corrects
that event projection unconditionally. Plain checkpoint recovery does not
restore `agent.state.cite("read:…")` live handles into the new epoch. Historical
citations should instead come from receipt-attested artifact URIs; stale live
handles must not be relabeled as current retained reads. That historical
citation handoff remains a separate empirical candidate.

- Real invocation-bound checkpoints and same-machine recovery validation exist.
- Safe-auto recovery is opt-in and covered by deterministic subprocess scenarios.
- Workspace fingerprints detect divergence but do not restore a workspace.
- Conversation notebook continuity and prior-run memory are separately authorized.
- Live model-quality, cost, and cache promotion gates remain pending; E8
  failed them, E9 tied quality but raised calls/cost, and E10 cut reads while
  raising aggregate calls/cost on a three-task pair. No default promotion.

E9's PSD Tools trace exposed an after-hash result-shape mismatch: a chained
PTC `fs.edit` read `data.sha256`, while the broker's attested after-SHA lived
only at `content_hashes[path]`. E10 makes `fs.edit`/`fs.write` expose the same
attested digest at both locations and documents expected-SHA chaining in
`agent.help`; the receipt/effect protocol remains authoritative. E10's fresh
Happy DOM trace then exposed another narrow shared-contract miss: failed
`npm run --workspace happy-dom test` commands had concrete exit code 1 and
equal before/after execution-workspace fingerprints, but the existing
recognized-test grammar rejected the flag position and left five test
attempts as unknown effects. E11 accepts only the ordinary workspace flag
before a `test`/`check` script, subject to the unchanged exit-code,
fingerprint, identity and no-metacharacter gates. A failed test remains a
failed check, not passing evidence; lint, timeouts and arbitrary shell
failures remain fail-closed. E11's live canary did not invoke the new
`npm run --workspace ... test` form; Happy DOM used the preexisting-recognized
`npm --prefix ... test` form. Thus the narrow parser fix has deterministic
contract evidence but no live value credit. E12 tests current net memory
value against PTC control on six fresh tasks; all default gates remain open.

## Rejected alternatives

- Replaying every incomplete action after restart: duplicates external effects.
- Treating ADK resumability as workspace or heap restoration: it is neither.
- Letting a model update authoritative completion state: unverifiable and fragile.
- Separate policy paths for direct tools and PTC: equivalent effects require the
  same authorization and receipt semantics.
