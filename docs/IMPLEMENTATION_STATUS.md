# Skein implementation status

## Supported product boundary

Skein is a one-shot Harbor/Pier coding-agent harness built on Google ADK. It ships
two evaluated profiles:

- `four-tool.yaml`: model-visible `read`, `bash`, `edit`, and `write`.
- `notebook-ptc-jsonl.yaml`: one persistent `execute_code` tool with brokered
  capabilities and canonical JSONL evidence.

The interactive terminal, WebSocket service, launcher variants, ADK Code Mode,
context experiment profiles, and custom pre-Harbor graders are not supported
product surfaces and have been removed.

## Retained contracts

- `app/agent` composes one ADK coding worker and owns the model/tool loop.
- `harness/core` owns validated configuration, typed task state, bounded context,
  skill selection, and deterministic orchestration.
- Opt-in context windows use provider-reported input plus the new-turn delta and support
  immediate compaction or cache-friendly deferral to sufficiently large semantic
  task-phase boundaries. The independent context-window ceiling forces a
  safety compaction; the cumulative task-input budget stops further dispatch.
  Compaction prefers populated working notes and freezes its handoff per epoch; note
  absence does not turn recoverable threshold pressure into a plugin exception. Each
  new handoff also carries a bounded deterministic index of recent read ranges,
  content hashes, modified paths, and validation receipts.
  PTC workspace mutations advance the implementation phase; oversized parallel
  reads return actionable errors without discarding notebook values.
- `harness/execution` owns all filesystem and command effects, including confinement,
  policy, approvals, redaction, bounded output, receipts, and workspace inspection.
- `harness/evidence` owns append-only events, canonical ledgers, trace capture,
  metrics, and versioned memory views. JSONL is the shipped profile; optional DuckDB
  and Lance implementations remain library components, not standard launch modes.
- `harness/ptc` owns the persistent CPython worker and deterministic notebook
  projection. The ledger is historical authority; the notebook is a workbench; the
  live heap is disposable runtime state.
- Notebook PTC exposes deterministic capability, kernel, CLI, and search manifests
  through `agent.help()`. Stable criterion rows and row-bound validation receipts
  keep requirement coverage under host control.
- Oversized help catalogs degrade deterministically from full contracts to compact
  signatures and finally a bounded names list with an exact-query pointer.
- Complete nested capability results and truncated stdout/stderr are immutable,
  content-addressed artifacts. Internal result artifacts stay out of model responses;
  bounded task-scoped load/list operations recover them, while explicit publish adds
  normalized host-facing metadata without forwarding data.
- Active memory supports `reads.lookup` and `read.recover` over addressed PTC and
  newly captured four-tool reads. Recovery checks artifact integrity and recorded
  path/version/range, returns bounded historical text with UTF-8 byte paging, and
  supports explicitly authorized prior-run artifact roots. Decoding is capped at
  16 MB; older unaddressed reads remain unavailable. This is evidence recovery, not
  a claim of current source freshness.
  Recovery-page completeness is separate from historical whole-file coverage;
  read references and handoffs retain source line counts and missing-range pointers.
- PTC read results now carry durable read references into a bounded live-value
  catalog. `agent.state.annotate` attaches advisory descriptions to supported plain
  data; `state.describe` supports restricted container selectors and opt-in previews.
  Bounded fingerprints invalidate descriptions/provenance after reassignment or
  mutation. Catalogs hold at most 64 descriptors/annotations and 128 tracked source
  locations; oversized/opaque values are not implicitly given provenance. Committed
  descriptors are redacted into the existing cell event, not a second memory store.
- Working notes accept typed advisory findings with authorized evidence citations,
  source dependencies, explicit conflicts/supersession, and task/path relevance links.
  `working_set@1` selects whole findings deterministically at an identified watermark;
  text-only notes remain compatible. Whole-entry handoffs now integrate findings,
  described live bindings, historical read handles, and receipt-confirmed touched paths.
  Changed-only PTC notices and actual kernel observations preserve bounded output;
  displaced output remains a redacted artifact. Headers freeze per epoch and the
  model/tool prefix stays invariant. Same-version contained read ranges are collapsed
  without inventing merged artifacts or discarding distinct versions.
- Source dependencies distinguish historical snapshots, observed changes, and
  revalidation requirements. Unobserved external changes require current reads or
  version guards; restored artifacts are never silently substituted for fresh reads.
  Prior recall requires both the feature and explicit owned source bindings. Worker
  loss preserves findings/artifacts without promising arbitrary heap restoration or
  replaying unresolved effects. Cumulative input-budget exhaustion has its own runtime
  terminal category. These are tested code contracts, not live quality conclusions.
- Conservative replay-safe recovery remains the shipped default. A bounded
  primitive/container snapshot policy is available only as an experimental opt-in.
- `harness/verification` owns acceptance checks and the final complete/retry/blocked
  decision. Model completion claims are never authoritative, and coding-mode tasks
  cannot complete without a changed repository path.
- `harness/adapters` contains external boundary code only: Google ADK integration,
  supported model providers, and the Pier task-environment adapter.
- `evals` contains frozen manifests, campaign execution, and result analysis. It is
  deliberately outside the harness package.

## Empirical status

Four tools remain the default because notebook PTC has not cleared the matched quality
gate. Notebook PTC remains available for controlled evaluations. The reference-six
audit and subsequent two-task comparison are retained under `docs/audits/`.

An earlier isolated OpenRouter development canary (2dee395) reached 4/4 independently
verified completions. Across correction and multi-file reasoning, findings used
12 calls versus metadata's 17, reread zero source lines versus nine, and cost
$0.02419874 versus $0.03187776. These two development pairs are a positive pilot,
not held-out reliability or repeated-cut qualification. Both arms have working notes
and retrieval: this compares continuity representation, not memory on versus off.
DeepSWE expansion and default promotion remain held. Prior cohorts had mixed or
negative results, and one lifecycle pair was invalidated by oracle-source access.
See [the continuity audit](audits/ptc-memory-continuity-2026-09-12.md) for separate
cohort results, accounting, confounds, and remaining gates.

The evaluator now runs the actual root workflow and independent verification, keeps
expected answers in a host-owned checker, and isolates ordinary live commands in
Docker through the existing runtime factory. It forces one initial checkpoint and
then restores phase-boundary timing, separating recovery from repeated-cut stress.
PTC documents the real citation path and explicitly distinguishes pre-execution
rejection from heap preservation; an old binding is not the result of a rejected read.
Independent completion checks now reject missing exit codes/incomplete baseline
comparisons and preserve criterion-reference indices; real PTC/workflow regressions
reject self-confirming wrong answers and accept sufficient one-line evidence only
after a required independent oracle passes. Latest deterministic checks: 767 passed,
two skipped; the opt-in real Docker isolation test also passed separately. Ruff clean
and Pyright zero errors (one existing warning).

The evaluator additionally audits host-frozen decisive source ranges against completed
task-local reads before each managed answer write. Wrong versions, pending/failed reads,
and later reads cannot support an earlier answer; unmapped retrieval remains unknown.
All four latest pilot answers had the required ranges available before their first write.
This is historical evidence availability, not proof of model consumption or dependence.
A deterministic correct-guess regression established that artifact correctness alone
was insufficient. The development oracle now also requires the declared source ranges
before the last managed answer write and binds that receipt to the current answer hash.
It rejects a correct guess; a new sufficient read followed by resubmission can recover.
Previously captured applicable evidence still counts without rereading. This fixture
verification policy is not general semantic provenance enforcement in production.

The stricter four-trial missing/freshness diagnostic passed every source and artifact
gate, but findings cost 24.4% more with no avoidable rereads in either arm. Its wire
audit found duplicate handoff delivery by the workflow and context plugin. Factory
wiring now assigns a single owner, preserves workflow summaries when the plugin is
absent, and keeps budget reservation and post-reconstruction metrics intact. The
matched rerun confirms one handoff at the provider boundary: findings input fell
41.7% and cost 21.5% with 2/2 accepted. Metadata accepted 1/2; its other correct,
source-backed artifact reached the call limit before outer verification. Overall
acceptance was 3/4, not a quality qualification. Both arms again had no avoidable
rereads. Model-written checkpoints, held-out variants, and repeated cuts remain next.

The model-written development screen now exercises acquisition, a model-authored note,
an acknowledged checkpoint, and a delayed follow-up through the same root workflow.
It includes lookup, multi-file calculation, and an unavailable-policy negative; no
source reads or findings are host-seeded. All stages are charged to the trial budget.
Scripted end-to-end tests cover recovery without rereads, while failed/pending/partial/
wrong-version reads cannot advance a checkpoint. The real Docker learning preflight
passed. The first live cohort exposed quoted-multiline note rejection, note-bound
guidance gaps, and PTC discarding known no-effect rejection metadata. These are fixed
without relabeling append/publication failures as safe. Evaluator runtime ownership is
also private from trace serialization. The fixed six-trial model-written screen passed
all four independent answer verifications and both strict missing-evidence abstentions.
Findings used 25 versus 31 calls and cost $0.04718 versus $0.05526. Both arms had zero
post-cut source rereads, so reduced rereads and held-out reliability remain unproven.
See the plan's model-written screen results for paths, limits, and remaining gates.

The next unseeded worker-loss screen adds an explicit note/recall-off control while
sharing PTC artifacts, safe restoration, read-index capacity and context-cut policy.
Two unambiguous held-out pairs passed first verification in both arms: findings reread
zero source lines versus 57, used 14 versus 20 calls, and cost 25% less. All six planned
worker stops/cuts were exercised, but the third pair had an ambiguous `settled_rows`
output contract and cannot qualify model quality. Raw acceptance was 3/3 control versus
2/3 findings. Control also spent calls attempting disabled recall commands instead of
available artifacts, so capability guidance remains a confound to address. The full
qualification gate, changed-source/repeated-cut tests, and defaults remain held.

The follow-up fixes make PTC artifact byte paging lossless for split UTF-8 and binary
content, with full-payload secret checks before encoding. Help documents byte-page
assembly and saved result envelopes. Inactive-memory PTC handoffs now offer artifact
recovery; restricted memory handoffs advertise only enabled commands, and note guidance
lists valid finding kinds. Deterministic broker/worker and real worker-loss tests cover
these contracts. A repeat of rollout/units is a routing diagnostic only, not fresh
held-out qualification; the original ambiguous settlements fixture remains unchanged.
That four-trial diagnostic passed all first independent verifications. Both controls
used artifacts, attempted no disabled memory commands, and reduced source rereads to
zero. Findings avoided 56 exact re-emitted source lines, but calls tied at 16 per arm
and findings cost 35.3% more ($0.03220 versus $0.02381). Preparation overhead included
a safely rejected dunder expression and an undocumented finding-ID constraint. Full
note-schema discoverability, changed-source/repeated-cut evidence, and the efficiency
gate remain next; this repeat does not establish new held-out reliability.
`memory note schema` now supplies the write validator's typed schema, live byte
budget and runtime merge/evidence rules through the existing broker. It is bounded,
hashed, read-only and disabled with working notes. Direct validation and real PTC
workflow tests cover discovery and rejection; reduced preparation cost is unproven.
The staged evaluator now supports two acknowledged worker-loss cuts around a brokered,
authorized source revision. Completion requires both checkpoints, a completed new-version
capture after the change, and final source hashes. Four valid scripted recoveries avoid
unchanged-source rereads; six stale/early/reverted-source completions are rejected.
Four additional tests enforce change/read/checkpoint ordering. Live qualification and
cost remain unproven; no defaults are changed.
The first four-trial live staged screen applied every revision correctly, but none
reached the second cut/final question. Repeated acknowledgement wording conflicted
with a still-pending coordinator message; allocation/findings additionally treated
successful virtual memory results as subprocess failures. All completion attempts
were blocked. Distinct stage/steering acknowledgement and the shared PTC output
contract require fixes before another diagnostic; no two-cut quality claim is supported.
The coordinator now uses distinct stage tokens and consumes only its own completed
revision message; old/failed acknowledgements cannot advance and unrelated steering
remains pending. PTC marks process versus native managed result shapes explicitly,
without fabricating exit codes or parsing native data as stdout. Deterministic checks
cover both changes. The repeated four-trial staged diagnostic reached both actual
worker-loss cuts and passed every first independent verification, with completed
decisive source evidence before every answer write. Findings used 20 versus 22 calls,
but cost 37.6% more and reread 27 versus 28 unchanged-source lines. Its dispatch
handoff contained the correct revised facts: missing delivery does not explain that
reread. The prompt repeats substantial provenance metadata and retains historical
next-action notes. A smaller deterministic evidence projection with explicit advisory
scope is the next investigation; freshness and verification guards remain unchanged.
This reused diagnostic is not fresh held-out qualification or default promotion.
The next prompt projection (`continuation@3`) now factors exact repeated finding
provenance/dependencies into local tables without changing canonical notes or tools.
It labels recorded next-actions as historical advisory proposals rather than pending
requests. Exact reconstruction and budget tests pass. Projecting the saved v2 advisory
blocks reduces dispatch bytes 15–17% and allocation about 1%, including the added
action-authority notice. This is not a total-prompt, cost or behavioral improvement claim.
The live projection diagnostic passes all four first independent verifications through
both cuts, with completed decisive evidence before all answers. Findings reread zero
source lines versus control's 28, but use 23 versus 21 calls and cost 41.2% more
($0.05023820 versus $0.03557308). Both findings runs explicitly query canonical memory
before answering, so direct prompt-projection consumption remains unproven. The next
cost seam is checkpoint preparation and full note-write output, followed by a fresh
diverse repeated-use panel with one-shot overhead and missing/pending evidence controls.
Latest regression: 792 passed, two skipped; full lint clean, typing zero errors with
the existing warning. No default or broader benchmark promotion is justified.
Note writes now return compact commit receipts through both PTC and four-tool bash,
while canonical records and full-note observer publication remain unchanged. The latest
note and exact committed event stay recoverable; retries retain the original version,
and failed publication remains unknown. Schema/help distinguish acknowledgement from
content. On v3's five stored notes the compact body is 367 bytes versus 564–6,119 bytes
for the full public note; this is an offline body-size measurement, not live cost savings.
The two-trial live allocation receipt canary passed both first verifications and both
cuts, with zero source rereads in either arm. Findings used 12 versus 10 calls but cost
$0.01836083 versus $0.02036761 (9.9% less); this single pair is not a powered efficiency
claim. The model accepted compact receipts and, after the final worker loss, wrote its
answer without a memory query, artifact load or source read. Completed-source/final-hash
gates still passed. Next is multi-answer temporal evidence checking and fresh diverse
one-shot/repeated-use fixtures, including actual missing/failed/pending tool negatives.
Latest full regression: 794 passed, two skipped; the overall qualification gate stays open.

Multi-answer evaluator contracts now require every requested artifact's expected JSON
value, matching managed-write/current-byte hash, completed decisive sources before its
write request, and assigned cut interval. Intermediate checkpoints are checked before
and after acknowledgement; rejected answers receive bounded repair guidance and need
a fresh checkpoint/handshake. Final verification checks earlier artifacts as well as
the last answer. Unsupported historical submissions remain separately reportable after
a legitimate correction. These are deterministic evaluator guarantees, not evidence of
held-out memory quality or general production semantic provenance. The next live work
remains the fresh diverse one-shot/repeated-use panel, including actual tool-evidence
negatives. No provider calls were made for this gate implementation.
Final regression: 830 passed, two skipped; full lint clean and typing zero errors
with the existing warning. All four cached-Docker multi-answer/repaired-checkpoint
preflights pass. Legacy fixture hashes and decisive source requirements are unchanged.

The fresh repeated-use evaluator now has three source families—repository call paths,
configuration precedence with an authorized revision, and signed cross-file reconciliation—
each in one-use and three-use forms. Every question has its own output/evidence window.
Both arms retain artifact recovery. Unchanged phases can reuse historical checkpoints
without another note write; the compactor still labels an unchanged note stale, while
changed-source phases require new captures and checkpoints. New cases share a declared
20-call/350k-input ceiling; older cohorts retain 12 calls/200k. No model-quality claim
or paid dispatch follows from these scripted cases: actual validation-operation
negatives and a frozen final live manifest remain required.
Verification: 845 tests pass, two skip; six three-use cached-Docker preflights pass.
Compile/lint pass and typing has zero errors with the existing warning. The offline
v9 dry manifest retains every legacy fixture hash; it is not a dispatched campaign.

Validation controls now execute real PTC commands through the root workflow in both
arms. Required successful checks are matched to canonical request/terminal identities
and must precede the answer write. A real subprocess result held before broker receipt
publication cannot support an answer; later completion cannot justify an earlier write.
Failed checks retain unknown-effect semantics. Answer withholding is reported separately
from safe abstention or completion. Correct guesses without checks are rejected.
All 14 focused tests pass locally and in cached network-disabled Docker. Full regression
passes 859 tests with two skips. The subsequent 18-trial live diagnostic cost $0.35171184,
with no provider errors, missing costs or false acceptance. Both reconciliation pairs
passed first verification: findings removed post-cut artifact loads and reduced cost
by 28–35%, but both controls had zero source rereads. Several other pairs exposed
ambiguous output schemas, now corrected in the v11 fixtures (29 focused tests pass).
The revised-config findings trial exposed a real projection weakness: obsolete values
remained prominent while their changed-source status was factored into a lookup table.
The model used those values, failed verification, then reread five lines and corrected
the answer. `continuation@4` now withholds invalidated conclusion text and unstructured
note excerpts from the default handoff, keeps freshness inline and attaches scoped
newer capture handles without claiming current freshness. Historical notes remain
recoverable. Final regression passes 863 tests with two skips, plus cached-Docker
changed-source preflights. The six-trial v11 live canary passed all first verifications
for $0.13071729. Its configuration trial actually encountered invalidated findings,
queried the note, and answered correctly without rereading configuration sources.
That is one exercised regression, not broad qualification: the findings arm still
cost 48.3% more on that case, mainly during setup and revision/checkpointing. A note
update hit the 8,000-byte canonical budget and required a shorter retry. Preparation,
note-budget accounting and read-result exposure are the next efficiency targets.
The canary record is `.artifacts/invalidated-projection-live-20260912-v1/analysis.md`;
the earlier diagnostic record is
`.artifacts/diverse-continuity-live-20260912-v1/analysis.md`; qualification remains open.

The note-cost investigation found that supplied instructions unnecessarily requested
a read before every write and encouraged a plan note before source acquisition. The
failed revision also appended parallel finding IDs: an exact merge replay reusing
the two existing IDs fits in 7,649 bytes instead of 10,088, with unchanged provenance
and the same 8,000-byte limit. Note-schema v3, `continuation@5` initial guidance and
the static PTC instruction now teach version reuse, evidence-first checkpoints,
same-ID revisions and single-copy result exposure. No merge/CAS/budget semantics
or default activation changed. A deterministic contract test verifies bounded upsert,
retained historical versions, conflict handling and idempotency; live measurement is next.

The six-trial note-guidance diagnostic cost $0.11372484. All three findings trials
passed first verification without pre-acquisition plan notes, budget retries or source
rereads. Changed-source findings used 15 calls/$0.0314 versus the earlier guidance's
19/$0.0473, but clean paired efficiency remains unproven: repository control falsely
claimed an unexecuted checkpoint marker, and configuration control hit its 20-call
cap with correct artifacts before verification. Both failures remain in the record.
The v12 evaluator now supplies one explicit pending-marker reminder per stage without
accepting prose as execution, and gives both repeated-use arms 24 calls under unchanged
input/wall limits. These are evaluator corrections, not memory-quality successes.
See `.artifacts/note-guidance-live-20260913-v1/analysis.md`.

The frozen v12 six-trial repeat passed every first verification and completed-evidence
gate for $0.10507343, with all ten worker-loss cuts exercised and no reminders or
unknown effects. Findings cost 26.4% less on repository one-use and 14.7% less on
configuration three-use, but 18.2% more on completed validation. Aggregate cost fell
12.1% (28 versus 34 calls). Post-cut artifact loads fell from 23 to zero; source
rereads were zero in both arms, so this is not a source-reread reduction claim.
Configuration findings reread four lines of its own answer during self-inspection;
that is neither source rediscovery nor independent proof. The record is
`.artifacts/checkpoint-feedback-live-20260913-v1/analysis.md`.
Full regression: 866 passed, two skipped; lint clean, typing zero errors with the
existing warning. A frozen full-family stability repeat is next; these reused
development fixtures cannot establish new held-out reliability or promote defaults.

The 18-trial v12 full-family repeat finished for $0.30356570. Raw answer checks
passed all 14 positives and all four negatives withheld answers, but a deeper audit
found one **unsafe acceptance**: repository-three control finished with an unresolved
failed shell operation after correct answers. Completion lacked the capability/cell
uncertainty check used during recovery; the handoff saw only started tool receipts.
The cohort fails the safety gate. Control's 68 post-cut artifact loads and 189 exact
source-content re-emissions versus zero with findings remain diagnostic observations,
not promotion evidence. All positive source-fetch reread counts were zero.
See `.artifacts/continuity-stability-live-20260913-v1/analysis.md`.
The implemented correction shares unresolved execution admission across completion,
recovery and handoff (`continuation@6`), with independent evaluator classification
in v13. The full regression passed 878 tests with two skips. Final targeted checks
cover the handoff hash and abstention gate; three real Docker cases additionally
exercise successful completion, rejection after an actual failed nested shell, and
independent detection when the production fence is deliberately bypassed in a test.
Lint is clean and typing has zero errors with the existing warning. No new paid
cohort has run with this correction; fresh live qualification and defaults remain held.

The subsequent frozen v13 safety check completed all six live trials for $0.08703809:
four positive first verifications passed with completed support and no unresolved
execution; both failed-check trials withheld answers and stayed blocked, retaining
their unknown effects. They did not force a completion claim after failure; that
boundary remains directly exercised by the Docker regressions. Repository three-use
findings used 13 versus 19 calls, cost 39.0% less and removed 21 artifact loads/45
exact source-content re-emissions. Both arms again had zero source-file rereads.
See `.artifacts/completion-fence-live-20260913-v1/analysis.md`. The regression screen
passes; genuinely new held-out cases and broad qualification are still required.

Four new, not-yet-dispatched qualification fixtures now exercise 12/18-file imported
transformation graphs with three separately verified worker-loss continuations, and
two partial TOML captures whose selected profile was not initially available. They
reuse the existing evaluator and both arms' normal artifact capabilities. Scripted
models derive graph transformations from captured ASTs and recover only three needed
new lines in the partial cases; correct guesses without those lines are rejected.
The evaluator separately flags premature full-file acquisition or opaque preparation
routes, so an easy correct answer cannot masquerade as an exercised missing-range
test. All 12 focused local and cached-Docker checks pass, including deliberate
over-acquisition. v14's dry manifest retains all 23 historical fixture hashes. These
are the first two families of the broader held-out contract, not completed reliability
qualification; new changed-source, conflicting-finding, validation and owned-prior
families remain before the full panel can be frozen and dispatched.
Final regression: 891 passed, two skipped; all 12 fresh-fixture Docker preflights
pass, lint is clean and typing has zero errors with the existing warning.

Deterministic unit and integration tests establish code contracts, not model quality.
See [Package layout](package-layout.md), [Architecture](architecture.md), and
[Harbor evaluation](evaluation-harbor.md).

The fresh qualification panel now also has two guarded shipment/pricing revisions
and two conflicting-candidate cases with deferred activation evidence. Both stop
the real idle worker twice. The conflict variants exercise disputed finding links
and explicit supersession; one includes a nominally completed activation with a
mismatched candidate hash. These activation states are source data, not actual tool
execution outcomes. Fresh actual-validation and owned-prior cases remain required.
All 28 qualification checks pass locally, including 16 new checks that also pass
in cached Docker. Stale answers are rejected before repair; an early correct guess
remains an unsupported historical submission after a valid rewrite. Preparation
over-acquisition is reported separately, not counted as successful delayed evidence
use. The v15 dry manifest preserves all 27 preceding fixture hashes. No new live
calls, default changes or qualification claims accompany these fixtures.
Full regression: 907 passed, two skipped; lint/compile pass and typing reports zero
errors with the existing runtime export warning.

Fresh qualification now includes actual signed-batch validation: a successful check
is preserved across a second worker loss without rerunning it, and a genuinely
failing subprocess requires withholding the answer. The new negative cases exposed
an evaluator gap: success on a temporary source version could justify an answer for
a restored different version. Validation/source binding at dispatch now rejects it;
an independent post-run audit flags unsupported acceptance even when the oracle
evidence check is deliberately bypassed. All 32 validation Docker checks pass,
including pending publication and the 18 new controls.

The production finding reducer also stops a successful, host-confirmed unchanged
validation from leaving its own provisional source invalidation behind. Earlier or
intervening uncertainty is retained; execution reconciliation and completion fencing
are unchanged. Eleven focused fault-boundary checks and real PTC validation tests
cover it. No live improvement is claimed yet. The fifth fresh fixture family is
implemented; explicitly owned prior recall and broader qualification remain open.
Final regression: 936 passed, two skipped; all 32 validation Docker checks pass.
Lint and compilation are clean; typing has zero errors with the existing warning.

The frozen v16 six-trial live regression completed for $0.08178951: four positive
first verifications have full source/window/validation support and no unresolved
execution; both failed checks withhold answers and retain uncertainty. All ten
worker-loss cuts were exercised. Repository findings used 15 versus 18 calls and
cost 25.0% less, removing 15 artifact loads and 72 measured exact source-line
re-emissions. Source-file rereads remained zero in both arms. Short validation cost
22.0% more with findings; total positive cost fell 11.4%. No provider/measurement/
accounting/false-acceptance errors occurred. The regression clears, not the held-out
or promotion gate. No causal live saving is attributed to the invalidation fix:
the live validation pair had no post-check cut. See
`.artifacts/validation-binding-live-20260913-v1/analysis.md`; owned-prior coverage is next.

Owned-prior admission testing exposed a second unnecessary freshness invalidation:
an idempotent write/edit reports no changed paths and a known file hash, but was
being treated as an unknown workspace-wide change. The shared finding reducer now
recognizes successful observed single-file no-ops without invalidating unrelated
findings. Earlier uncertainty, failed/unknown effects, missing or malformed hashes,
and arbitrary shell commands remain conservative. Eight reducer boundary tests and
actual PTC repeated-write/edit retrieval checks cover this correction. It changes
advisory freshness only, not execution reconciliation or verification authority;
no live cost/reread benefit has yet been measured for this fix.

The controlled evaluator now has an optional owned-prior availability audit on all
three answer paths: checkpoint admission, the host oracle and independent post-run
classification. It requires explicit matching owner/conversation/workspace/state
bindings, a completed verified producer with resolved effects, a canonical source
manifest, and a completed managed retrieval before answer dispatch. Selected findings
or complete captured read pages count only after a matching consumer file-version
observation. This records derived availability, not semantic consumption or truth;
current-task validation requirements and independent expected-answer checks remain.
Metadata-only lookup, opaque artifact/shell routes and partial byte pages stay unmapped.
Public view receipts may deduplicate across queries; each completed operation supplies
its own availability boundary. Twenty-three actual PTC admission/integrity checks pass.
Their host-supplied producer terminal is explicitly a unit fixture, not a fresh
model-written producer episode. That two-run protocol, full frozen live panel and
real-coding confirmation remain open. The v17 dry manifest retains all 33 preceding
fixture hashes, includes the new audit source hash, and makes no provider calls.
Final full regression: 965 passed, two skipped. The two subsequently added recovery-
page checks also pass in the complete 23-check prior suite. Lint and compilation
pass; typing has zero errors and the existing runtime export warning only.

The sixth fresh family now uses two real verified tasks: an owned catalog audit and
a distinct shipment calculation, with unchanged and changed selected-policy variants.
Producer completion is actual root-workflow verification, not a host-inserted terminal.
The consumer starts only after a source-supported first producer verification, clean
accounting and resolved effects. Both arms pay for their own fresh producer. Runtime
bindings and canonical producer snapshots are frozen outside the workspace; the old
answer is preserved outside the new task, whose source revision is an explicit fixture
boundary. Only decisive field/code ranges gate answers; incidental comments do not.

Ten scripted root-workflow checks pass, including producer failure admission, a correct
unsupported consumer guess and stale prior data. Three dispatch checks cover fresh
repetitions, source-admission stopping and missing-cost stopping. Scripted unchanged
consumer overlap is seven source lines in control versus two one-line identity checks
with findings; changed-source reads are reported separately. These demonstrate the
mechanism, not autonomous model quality or live savings. v18 supports three isolated
repetitions and includes producer calls, tokens and cost in paired totals. All six
families are implemented; the frozen live qualification and real-coding gates remain.
Pre-dispatch checks: full regression 977 passed, two skipped; final focused prior/
dispatch suite 13 passed locally and in cached Docker after tightening decisive
ranges. Lint/compile pass; typing has zero errors and the existing export warning.

The v18 qualification campaign stopped under its frozen gate after 40 trials
(44 model episodes); 32 queued trials were never started. There were 33 verified
completions, four workflow blocks, two call-limit exits and one pre-cut budget exit
misclassified as a harness error. Total recorded provider cost was $1.12553090,
including actual prior producers. All 19 positive pairs cost 1.0% more with findings;
among the 14 both-verified pairs, findings used 8.0% fewer calls but cost 6.5% more.
These success-conditioned figures do not qualify reliability or savings. Artifact
admission misclassification, review rereads and prior-recall usability remain next.
See `.artifacts/qualification-v18-live-six-families-r3/analysis.md`; defaults stay held.

v19 corrects the diagnostic boundary without changing any historical result: the
runtime and evaluator recognize typed task-input exhaustion through ADK exception
causes, and zero-cut measurement yields an empty interval list. Unrelated callback
failures remain harness errors; real measurement/accounting failures still stop
queued dispatch. The original six-family fixtures and budgets remain unchanged.
The 121-check focused regression completes with 120 passing and the explicit Docker
check skipped; lint/compile pass and changed-file typing reports zero errors.

PTC artifact admission now returns explicit no-effect outcomes for malformed URI/page
arguments, unauthorized task references, redaction-denied loads, and invalid publication
metadata. Guidance points to exact authorized artifact listings. Resolver corruption
and interrupted publication retain unknown-effect fencing; a later successful load
does not reconcile an unrelated failed shell. Actual workflow regressions now complete
after safe rejection and recovery, and fault tests retain corruption/publication blocks.
This addresses two v18 control blocks; no live quality gain is claimed for the fix.
Full unit/integration regression: 991 passed, two skipped. All six cached-Docker
recovery and unknown-shell completion checks pass. Lint/compile pass and typing has
zero errors with the existing runtime export warning only.

Prior working sets now include the consumer's separately watermarked observations
without selecting unrelated consumer notes. Each prior finding retains producer
provenance and gains a bounded `consumer_versions` record: unobserved, changed,
requiring revalidation, matching recorded versions, or lacking source dependencies.
Matching identity is neither continuous filesystem freshness nor finding truth.
`continuation@7` can show an available historical finding after matching consumer
observations even when the producer's later state was uncertain; changed/unknown
consumer versions and unavailable provenance remain withheld. Execution uncertainty
and independent completion checks are unchanged. Existing focus selection keeps
relevant prior findings within the same output budget; no response-limit increase.
Focused tests include time boundaries, bounded scans, source selection, both-task
manifests, stale/missing evidence and actual producer/consumer workflows. Live effect
on rereads/cost, review-stage reuse and foreign-citation usability remain unqualified.
Full unit/integration regression: 996 passed, two skipped. All 13 cached-Docker
producer/consumer checks pass. Lint/compile pass and typing has zero errors with
the existing runtime export warning. A final guidance-only clarification makes
version reads explicitly conditional on permission from the current task; all 105
focused checks pass after that clarification.

The frozen consumer-version prior diagnostic at revision `f182027` completed all
four trials/eight episodes, each passing first independent verification with
supported submissions and no unresolved effects/accounting failures. It does not
pass efficiency: findings cost $0.10790794 versus control $0.07820191 (38.0% more),
45 versus 42 calls, and both refetched 19 prior same-version source lines. Neither
findings consumer refreshed working_set after current version observations, so no
outgoing request exposed matching_observations. Five foreign-note citation attempts
were safely rejected across the two findings consumers. See
`.artifacts/consumer-version-prior-live-v1/analysis.md`; these reused cases are
diagnostic, not new held-out qualification. No paid run remains active.

Read-only inspection also found the host's review action absent from all six
provider review packets in two v18 reread-heavy cases: generic head/tail truncation
cuts through sorted task JSON and removes next_action. The old advisory handoff
remains elsewhere, not a refreshed review navigation packet. Preserve complete
control instructions and bounded completed-evidence navigation before attributing
these rereads solely to model instruction-following. See
`.artifacts/review-continuity-diagnosis.md`. Defaults and broader runs remain held.

`work_packet@2` now preserves complete task-control JSON and reserves supplied
skills, continuation metadata and steering before optional history. Preferred
section allocations still bound optional detail; required control can borrow space
within the unchanged total packet budget. Optional task fields are admitted whole
with named omissions. Oversized required context stops before provider dispatch as
`context_control_budget_exceeded`, separate from task-input exhaustion and harness
failures; the runtime recognizes ADK wrappers and the eval stops queued work under
this gate. No new tool, context-window increase, review bypass or completion authority.
Deterministic tests cover complete goal/criteria/scope/next_action, bounds, identical
serialization, unmodified input ledger, typed overflow and real zero-dispatch
overflow. The actual root workflow preserves the complete review action in compiled
OpenRouter requests and retains its stable prefix. All 14 cached-Docker review/prior
workflow checks pass. Full regression: 1006 passed, two skipped. After the final
steering-budget alignment and zero-dispatch test, the focused suite passes 88 checks
with one explicit Docker skip. Lint/compile pass; typing has zero errors and the
existing runtime export warning. Offline replay of the two v18 review ledgers retains
the exact goal, criteria and next_action at 1131/1293 packet tokens, within the unchanged
20k total rather than the old 600-token task target. Reread effects and phase-boundary
navigation remain unqualified. Eval manifest is now v20; no live run is active.

PTC now automatically discovers registered read values retained in plain containers,
including unannotated parallel-read lists. Existing completed-cell manifests and
state-update messages expose exact selectors/access recipes, source hashes, captured
ranges and artifact references. This is bounded navigation of the original broker
attestations, not inferred lineage or semantic summaries. Duplicate nested aliases
are collapsed; deep copies, mutation, deletion and a new worker epoch cannot acquire
or preserve a false association. Default catalog output contains no source preview.
The real PTC test exposes `reads[0]` immediately and recovers the original citation
with exactly one source read. Full regression: 1012 passed, two skipped; the broader
PTC/context/qualification subset passes 167 with one explicit Docker skip. Lint and
compile pass; typing has zero errors with the existing runtime export warning.
Explicit phase/work-batch refresh and prior applicability/citation usability remain
outstanding; no new live evaluation or default change.

The subsequent `work_batch_navigation@1` implementation now appends a bounded
completed-evidence snapshot in actual host work packets after work-batch/review
transitions. The canonical navigation event records redacted replay inputs, selected
text, content/program hashes, explicit source clock/watermark and task/batch identity
before dispatch. It reuses continuation assembly (`continuation@8`), preserves old
prefix bytes between calls, and does not introduce a cut, new tool or second store.
Task-focused binding selection collapses identical read-reference aliases; readable
check commands and available receipt/artifact/workspace references improve recovery.
Historical source ranges, kernel epochs, invalidated findings and unresolved effects
retain their scope. Required metadata overflow uses the existing typed context-budget
terminal; publication/identity failures remain closed.

The actual compiled-provider regression recovers `reads[0]` at review without a new
source read and keeps its snapshot unchanged on the next model call. All 35 scripted
cached-Docker workflow checks pass, and the final focused context/packet/factory suite
passes 91 checks. These are deterministic integration results, not a live-model
reread/cost improvement. Prior applicability after inner-batch observations and
scoped citation usability remain outstanding. Eval manifest is v21; no new paid run
or default promotion.

Full regression completed: 1020 passed, two skipped, at
`.artifacts/work-batch-navigation-regression`. Cached-Docker scripted checks are at
`.artifacts/work-batch-navigation-docker-regression`. Lint/compile pass; typing has
zero errors and the existing runtime export warning. All test processes are terminal.

Prior reuse now has two additional implemented paths. Working-set entries carry a
`reference_in_place` contract and an exact source-task/note-event recovery command;
note-schema version 4 and no-effect foreign-citation rejection make the same scope
explicit. Already durable prior findings need not be duplicated into current notes.
Current-note evidence admission is unchanged.

Active authorized-prior findings profiles can also append `prior_applicability@1`
identity metadata after completed PTC read cells. It validates the source-view hash
and consumer identity, focuses the existing query on completed read paths, preserves
both source clocks and never exposes learned content in this update. A canonical
event records inputs, selection, hashes, response identity and budget before exposure;
same-attempt replay is immutable and unchanged version observations do not re-emit.
Updates fit within 2048 bytes and the original whole-response ceiling without
displacing execution output or bypassing later ADK observers. No-prior/inactive
profiles, failed or pending cells and full outputs do not acquire the update.

The scripted provider integration now observes matching applicability without another
model-issued query; changed-policy and unchanged-pricing statuses fit together after
removing repeated per-entry guidance. A new negative receives matching identity
metadata before answering but lacks required retrieved content; independent
verification still rejects it. All 50 isolated cached-Docker prior/verification
checks pass at `.artifacts/prior-applicability-docker-checks`; focused memory/context
checks pass. Full regression passes 1033 tests with two skips at
`.artifacts/prior-applicability-regression`. Lint/compile pass; typing has zero
errors and the existing runtime export warning. Live behavior, reread/cost benefits
and diverse held-out qualification remain unproven. The next bounded reused-case
diagnostic is frozen in the continuity plan; no default promotion.

That diagnostic closed at `5f2f6cf`, with three of six executed episodes independently
verified and two consumers not started. Three episodes hit call/input caps; all six
answer files were value-correct, which does not qualify unverified completion.
Total cost was $0.20349057 over 95 calls. No complete two-arm consumer comparison
exists. A findings consumer demonstrably reused retained `pages[...]` values during
review without source rereads, but later repeated its preparation marker and stopped
at budget. Its one automatic applicability update is mechanism evidence, not savings.
Detailed results remain at `.artifacts/prior-reuse-navigation-live-v1/analysis.md`.

The next correction retains ordered delivered steering after queue acknowledgement
in host work packets (`work_packet@3`, `delivered_steering@1`). Current questions no
longer depend on optional recent-event excerpts while the original goal remains
required. Review audits old prerequisites from completed receipts rather than
reenacting them. All original criteria, fixture questions, source/range/time checks,
budgets and independent verification remain unchanged; no evaluation-only exception
or implicit criterion rewrite is added. Source/task identity errors fail closed;
oversized required control uses the existing typed packet overflow. Live effect
remains unproven until the next separately frozen diagnostic completes.

Verification: 1037 passed/two skipped in `.artifacts/steering-continuity-regression`,
50 cached-Docker checks in `.artifacts/steering-continuity-docker-mounted-checks`,
and 15 final serialized-provider checks in `.artifacts/steering-continuity-wire-checks`.
The first Docker attempt failed because Colima could not mount the default macOS
pytest temporary directory; its log is preserved separately. The successful run uses
a fresh workspace-local directory and the same cached image. Ruff/compile pass;
typing has zero errors and the pre-existing runtime export warning.

The steering diagnostic at `4609f62` now closes 8/8 episodes on first independent
verification, with every answer submission source-supported and no missing accounting.
All 11 review-packet occurrences retain the delivered question; no preparation
marker repeats after review and no source reads occur during review. Findings
consumers refetch three unchanged prior-source lines (all identity candidates)
versus control's 18, while correctly acquiring the changed policy in the second case.
This is successful mechanism evidence on two reused cases, not held-out reliability.

Efficiency still fails: findings uses 42 calls/$0.12438927 versus control's
37/$0.07868618, including producers (+58.1% cost). Consumers account for the overhead:
23 calls/$0.08707714 versus 17/$0.03264860, with note lifecycle work and higher
uncached input. Total diagnostic cost is $0.20307545. Full results and limitations:
`.artifacts/steering-continuity-live-v1/analysis.md`. No trial remains running.
Measure bookkeeping, prompt/cache overhead and finding dependency granularity
before another frozen diagnostic; diverse held-out and DeepSWE gates remain open.

The subsequent wire audit found an inner-loop prefix defect: all 22 inspected
within-epoch steering displacements had static-only cache hits (2747 tokens), while
append-only transitions reused nearly the preceding input. The correction records
first steering exposure at a native-history boundary and reconstructs exact bytes in
place, including after acknowledgement and plugin restart. Canonical exposure
events carry scope, source/prefix/program/content hashes and a watermark. New steering
and its preceding unconsumed tool result remain protected from cuts. Complete
required steering is still retained in host packets; no criterion, oracle, note
protocol or budget is relaxed. Deterministic prefix preservation is not yet a live
cache/cost claim. Audit: `.artifacts/steering-continuity-live-v1/cache-audit.md`.

Anchored-steering verification: 1045 full-suite passes/two skips at
`.artifacts/anchored-steering-regression`, 116 lifecycle checks at
`.artifacts/anchored-steering-focused`, and 50 cached-Docker checks at
`.artifacts/anchored-steering-docker-checks`. The final integrity tightening checks
exposure hashes before scope filtering, so a corrupted anchor cannot hide a message;
84 focused checks including that additional negative pass at
`.artifacts/anchored-steering-final-checks`. Lint/compile pass; typing has no errors
and the existing export warning. No note lifecycle or evaluation criterion changed.

The anchored-steering diagnostic at `15cea79` completed all eight episodes on first
independent verification, with every answer submission source-supported and full
accounting. All 73 within-epoch provider transitions preserve the previous input as
an exact prefix; zero steering displacements remain. Findings costs $0.09347522
versus control $0.07010133 (+33.3%). Unchanged prior-source refetch is 24 versus 21
lines; excluding three identity candidates makes them equal, not improved. The
earlier reread benefit did not reproduce. These are two reused cases, not held-out
qualification. Detailed action/cache analysis: `.artifacts/anchored-steering-live-v1/analysis.md`.

Both producers bundled six independent policy facts into one finding, whose joint
applicability correctly remained unobserved after checking only one policy. The next
instruction/schema correction recommends independently reusable entries in one write,
while retaining all dependencies for genuine cross-source conclusions. It also makes
prior reuse-in-place explicit in PTC guidance, avoiding local citation manufacture.
Note-schema guidance is version 5; admission, note bytes, views, historical claims and
verification are unchanged. Deterministic selective-source checks pass; live authoring
and efficiency remain unproven. Actual exception-induced worker loss and unnecessary
review rereads remain separate follow-ups, not attributed to ordinary compaction.

Finding-scope verification passes 1047 full-suite tests with two skips at
`.artifacts/finding-scope-regression`, 62 focused checks at
`.artifacts/finding-scope-focused`, and 50 cached-Docker checks at
`.artifacts/finding-scope-docker-checks`. Ruff/compile pass; typing has zero errors
and the existing runtime export warning. This qualifies the unchanged-contract
diagnostic, not live memory reliability or default promotion.

The `7c662db` finding-scope diagnostic completed all eight episodes: seven pass first
verification and one value-correct control remains blocked on an unresolved managed
search effect. Every answer submission has declared completed-source support; the
blocked control is not counted as accepted. Both producers now author seven separate
single-source findings, and both findings consumers reuse prior learning before the
first answer. Findings prior-source refetch is six lines, three identity candidates,
versus 24 in its preceding run. Only the changed-policy pair has matching verified
terminals: identity-adjusted prior refetch is equal and findings costs 62.7% more.
No efficiency/reliability gate closes. Total diagnostic cost $0.15265168; all 67
within-epoch wire transitions preserve prefixes. Full evidence and limitations:
`.artifacts/finding-scope-live-v1/analysis.md`. No live process remains.

Two next interface corrections are grounded in that run: a proven pre-execution
unavailable managed-search response lacks explicit no-effect metadata, causing the
broker's conservative unknown fallback; and a model confuses schema `version=5`
with current note `version=1`, causing a safely rejected CAS write. Repair those
response contracts without relaxing unknown-effect reconciliation or note versions.

Managed search now reports `effect=none` only when the backend is absent or the
reserved command is rejected by its parser, before backend/sandbox dispatch. The
unavailable response suggests bounded sandbox rg/grep when available or a known-path
managed read; it does not execute a fallback. Post-dispatch backend failures and
ordinary failed shell commands retain conservative uncertainty. Actual PTC tests
preserve the effect through canonical publication and duplicate acknowledgement;
root-workflow regressions accept supported completion after a no-effect rejection
and still refuse completion with unknown shell effects. This does not rewrite the
previous blocked live trial or qualify a new live cohort.

The on-demand note contract now uses `schema_version=6` and no top-level `version`.
Committed note/read receipts keep their existing `version` used for CAS writes;
schema identity is not a note revision. Stable PTC guidance and schema commit rules
state that distinction. The read-only schema remains bounded and deterministic;
using its identifier as an expected note version still returns a no-effect conflict.
All 62 focused memory/factory checks pass. No note payload, historical revision,
admission rule, independent verifier or budget changed.

Combined interface verification passes 1055 full-suite tests with two skips at
`.artifacts/interface-contracts-regression`, 52 isolated Docker checks at
`.artifacts/interface-contracts-docker-checks`, 61 adapter/PTC checks at
`.artifacts/search-rejection-final-focused`, eight explicit completion-fence checks
at `.artifacts/search-rejection-verification`, and 62 memory/factory checks at
`.artifacts/note-schema-identity-focused`. Lint/compile pass; typing has zero errors
and the existing runtime export warning. The initial new replay test wrongly expected
an effect field on duplicate acknowledgement; the corrected test checks the unchanged
canonical effect and no redispatch. No production replay behavior was changed to
satisfy it. All test processes are terminal; no new paid run or promotion.

PTC now retains historical completed-read recovery handles in bounded binding-loss
updates (`ptc_state_updates@1`). The existing artifact loader supplies exact saved
result envelopes, not live variables or proof of an unfinished calculation. Canonical
view metadata permits deterministic reconstruction; required invalidation notices
take priority over optional recovery details. Failed/unknown operations remain fenced,
partial source coverage stays partial, and source freshness is not inferred from a
successful historical load. Snapshot rollback and identity provenance remain distinct.
Seven worker lifecycle checks and the scripted root verification path pass; the latter
recovers after a real exception without any recovery/review source read. Final checks:
1064 passed/two skipped at `.artifacts/lost-binding-regression-final`, 53 cached-Docker
checks at `.artifacts/lost-binding-docker-checks-final`, and four explicit root checks
at `.artifacts/lost-binding-verification-final2`. Lint/compile pass; typing has zero
errors and the existing runtime export warning. The original full run retained a
report-field assertion failure; focused correction also separated seeded read counts.
No production behavior was changed to satisfy those test assumptions. Live uptake,
cost and fresh held-out reliability remain unqualified; no paid run or default change.

A subsequent real-worker probe identifies the next boundary: a read completed in
the same cell that later raises retains its result artifact, but has no earlier
completed binding manifest and exposes no direct recovery entry. The probe at
`.artifacts/same-cell-recovery-probe/pytest.xml` observes one completed read, one
artifact, a discarded heap and empty updates. The current fix covers earlier
completed bindings, not this case. The follow-up must derive recovery from completed
capability receipts without admitting dirty heap values or incomplete operations.

The sibling boundary is now implemented as `ptc_state_updates@2`. Completed same-attempt
read receipts supply historical recovery entries independently of dirty binding state.
Request/terminal identity, task/attempt scope, source coverage and artifact identity
are checked; missing/failed/pending or unknown-effect reads are not promoted. The real
root regression initially exposed eight older invalidations crowding out the complete
new read. Grouping those invalidations preserves their identities and gives recent
completed evidence priority within the unchanged eight-entry/2048-byte ceiling.
The scripted model now recovers and independently verifies with no extra source read.
Focused checks pass (113 tests/one skip plus the actual missing-read negative);
full regression passes 1084 tests/two skips at `.artifacts/same-cell-recovery-regression`,
and all 54 cached-Docker checks pass at `.artifacts/same-cell-recovery-docker-checks`.
Lint/compile pass; typing has zero errors and the existing export warning. All checks
are terminal, and the earlier crowding failures remain in their original reports.
Live qualification and cost improvement remain unproven; no new paid run has started.

The four-trial seeded recovery-message diagnostic subsequently closed at `0141ae1`:
all four actual read-then-fail interventions were exercised, all first verifications
passed, and every answer had completed source support. Messages on used 11 calls and
$0.01925322 versus off's 12 calls/$0.02101734 (8.39% less in this small run).
Both arms had zero avoidable source rereads; partial captures required the same ten
new source lines. The complete/on model used the exact artifact handle, while its
control used `reads.lookup` and `read.recover`. Partial/on skipped the control's
lookup and acquired the missing range directly. All 19 within-epoch prefixes were
append-only, with complete accounting and no unknown effects. Total cost $0.04027056.
No live process remains. Report: `.artifacts/recovery-message-live-v1/analysis.md`.

The complete/on model spent an extra cell treating the saved JSON result envelope as
source text before correctly decoding it. That is a concrete remaining usability
issue, not lost evidence. This pilot establishes recovery-handle use and correct
coverage handling, not fresh held-out reliability, amortized preparation savings or
new reread reduction. Defaults and DeepSWE expansion remain held.

The saved-result decoding ambiguity now has explicit guidance: `ptc_state_updates@3`
adds one bounded shared recipe next to historical recovery handles, also available
through existing artifact help. It checks complete offset-zero UTF-8 byte coverage,
retains the successful saved result's source metadata, and clears proposed outputs
before handling unavailable or partial pages. It does not automatically read source,
decode arbitrary partial artifacts, restore a failed calculation, or establish
current freshness. Its program/notice hashes and budget are recorded for replay.
Actual root-workflow checks consume the emitted recipe and independently verify
without recovery/review source reads. The focused suite passes 121 tests/one skip;
all 48 cached-Docker root/recovery checks pass. Full regression passes 1100 tests
with two skips at `.artifacts/recovery-decode-regression`; isolated checks are at
`.artifacts/recovery-decode-docker-checks`. Lint/compile pass and typing has zero
errors with the existing runtime export warning. Every test process is terminal.
Live uptake and efficiency are not yet measured for this version. The next fresh repeated-use panel is specified in
the continuity plan but still requires fixture implementation and preflight; no
new paid jobs or default changes accompany this implementation.

Preflighting the new unseeded transfer panel exposed a real file-tool boundary bug:
a stale hash guard rejected the write but lost no-effect metadata, leaving failed
managed/PTC receipts unresolved. The shared envelope now preserves typed precondition
rejections as `effect=none`, still with error status. Local parent creation follows
the guard so a failed missing-path write cannot create directories. Both local and
Harbor preconditions are checked; failures after actual mutation retain unknown
effects and completion fences. No new auto-retry, success reinterpretation or
historical receipt rewrite. The original failing Docker report is retained at
`.artifacts/transfer-docker-preflight`; the live panel remains undispatched.

The transfer panel is implemented through the existing qualification registry and
root coordinator: ordered routing, SQL eligibility, and a revised dependency graph,
with three independently source/value/window-gated answers each. Acquisition and
checkpointing are unseeded and charged. Tests derive answers from recovered source
bytes and cover early/unsupported answers, unauthorized source changes, missing
revised captures, stale write guards and stale calculations. The current 15 panel
checks and 18 file-conflict checks pass in cached Docker at
`.artifacts/transfer-guard-docker-checks`; 61 combined focused checks pass locally.
The 35 previous fixture hashes are unchanged; only three fresh cases are added.
The v22 dry manifest is not a clean live freeze. Production remains at `8bda3bf`;
the final full regression and separately committed evaluation freeze precede dispatch.

A sibling precondition probe found that both adapters equated a missing path with
empty content, falsely reporting already-applied even for a mismatched hash guard.
Both now require existing-file evidence before the idempotent equality shortcut.
Empty-file creation is a real mutation, repeated matching writes remain idempotent,
and rejected missing-path guards do not create directories. The two original failures
remain at `.artifacts/missing-empty-guard-probe`; focused local/remote checks pass.
This is a tool-receipt correctness fix, not measured live memory improvement. A
read-only audit of the earlier v18 campaign's 44 managed-tools databases found zero
FileConflictError result receipts, so these fixes are not presented as explanations
for that campaign's earlier performance. Final post-fix regression remains the
pre-dispatch gate for the unchanged twelve-trial panel.

Final preflight is complete: 1137 full-suite passes/two skips at
`.artifacts/transfer-final-regression`, all 35 isolated panel/precondition checks at
`.artifacts/transfer-final-docker-checks`, and 67 focused passes. Lint/compile pass;
typing has zero errors and the existing runtime export warning. All test processes
are terminal. The live twelve-trial freeze is authorized to proceed with runtime
`83a37af`, unchanged fresh fixture hashes, Luna/max and concurrency six; the live
manifest records its exact documentation-inclusive clean revision before dispatch.

The twelve-trial transfer screen has now closed at clean revision `85a36ab`:
`.artifacts/transfer-v22-live-r2/analysis.md`. Ten independently verified; two correct,
source-supported final files exhausted the call limit before verification and remain
unaccepted. All 36 submitted answers had completed required source coverage and
correct values, with no unknown effects/accounting gaps and 192 append-only
within-epoch provider transitions. Findings/control each verify 5/6, but on different
failed pairs. Findings reduce calls 125 to 115 while increasing source reread lines
526 to 550 and cost $0.20981411 to $0.21717200. Total spend $0.42698611; gate failed.
No live job remains, and these cases are consumed rather than reusable holdouts.

SQL findings answer all six delayed questions without pre-final source rereads, then
reread 107 lines afterward. Actual review packets contain the learned findings and
source handles; routing additionally has a live binding access path. Review still
reacquires sources. Routing exposes blocked exec/dunder calls and an attempted
workspace import into the separate worker, causing a real exception and further
reacquisition. Existing navigation must be reused rather than duplicated. The frozen
source-emission counter misses custom JSON and Python repr source wrappers, so its
264-to-104 mapped duplicate-line change is not proof of lower total exposure.
PTC execution guidance and review evidence reuse remain unqualified follow-ups;
defaults, natural-compaction/prior-run claims and DeepSWE expansion stay held.

PTC now shares explicit computation-versus-workspace execution guidance between its
stable instructions and existing kernel help. The catalog's blocked direct-call and
module lists derive from the actual guard; no policy changes. The acquisition example
retains source envelopes by path separately from answer/check outputs, preserving
their citations and range metadata. Sixty-six focused checks pass, including actual
execution of the shipped example and guard/catalog consistency; all 24 cached-Docker
transfer/worker-loss checks pass. Lint/compile pass; typing has zero errors and the
existing export warning. Full regression closed with 1139 passes/two skips at
`.artifacts/ptc-boundary-regression`; focused and isolated reports are at
`.artifacts/ptc-boundary-focused` and `.artifacts/ptc-boundary-docker-checks`.
All checks are terminal; live uptake is still unmeasured.

Read-only byte accounting adds an economic concern: the three fixture source sets
are 746, 967 and 1248 bytes, while findings-arm review packets occupy 8746-15946 bytes.
SQL/r1's finding text alone is 1189 bytes for 967 source bytes. Build/r1 spends 5800
compact-JSON bytes on six live-binding entries and omits eight advisory candidates;
the control also pays substantial binding/index metadata overhead. This motivates
reducing redundant navigation representation through the existing projection, not
dropping uncertainty/provenance or claiming a smaller prompt already improves quality.

`continuation@9` and `work_batch_navigation@2` now omit internal fingerprints,
redundant builtins labels and duplicate locator forms from live-binding prompt hints.
Full read references, source scope/coverage, origin cell, replay policy, shape,
descriptions, freshness and unfamiliar uncertainty fields remain; original manifests
and state inspection are unchanged. New packets carry new renderer identities;
captured historical packets still replay their bytes. No new tool or larger budget.
All twelve offline transfer review snapshots retain prior selected evidence: packets
with spare capacity save up to 2060 bytes; two full build/findings packets instead
gain five and three entries. Report: `.artifacts/lean-navigation-offline/analysis.md`.

The original focused suite passes 153 checks/one skip; the final focused suite with
actual prompt-derived access-expression execution passes 100/one skip, and all 54
cached-Docker root/transfer checks pass. Reports are `.artifacts/lean-navigation-focused`,
`.artifacts/lean-navigation-final-focused` and `.artifacts/lean-navigation-docker-checks`.
Lint/compile pass and typing has zero errors/one existing export warning. Full
regression closed with 1142 passes/two skips at `.artifacts/lean-navigation-regression`.
All test processes are terminal; no new paid job has started.

The consumed-case follow-up uses an explicit evaluator `--diagnostic` flag (manifest
v23). Manifest and trial rows retain that label; summary scope cannot infer a held-out
claim merely from a `qualification_*` fixture name. Dry-run and queued-failure checks
pass without provider work. Runtime/navigation remains `0917b90`; the planned six-trial
output is `.artifacts/lean-navigation-live-diagnostic-v1`, at unchanged declared limits.

The six-trial diagnostic closed at clean `2823018`, costing $0.21560601. Control
verified 3/3; findings verified 1/3, with one call limit and one pre-dispatch input
budget stop before verification. All 18 final files are correct; all 19 submissions
have completed required source availability, but SQL/control first wrote an incorrect
answer and repaired it before advancing. No unknown effects/accounting gaps remain;
all 102 captured within-epoch request transitions are append-only. Findings/control
source reread lines are 293/204, so the overall goal still fails despite SQL/findings'
zero rereads, 13 calls and $0.02089325 versus control's 71 lines, 22 calls and $0.04131155.
Incomplete endpoints confound aggregate cost comparisons. No paid job remains.
Full report: `.artifacts/lean-navigation-live-diagnostic-v1/analysis.md`.

The live errors identify next work: routing repeats approval-gated project execution
after cuts and raises on known blocked results; build parses Markdown as JSON and
later attempts blocked `__import__`. The JSON error is also mislocated by the harness:
its data-relative `lineno=1` is incorrectly attached to the notebook's `import json`
line. Fix the shared error-location path, then examine receipt-backed blocked-approach
continuity and recovery usability. Preserve policy, dirty-state disposal and independent
verification; do not promote defaults or call consumed reruns held out.

The error-location probe reproduced three failures: JSON data-relative positions
misidentified two notebook lines, and an older-cell function's line was attached to
unrelated current source. The worker now uses source-hash compilation filenames and
current-source traceback frames for execution failures, retaining parser/source-guard
locations. Original failures remain at `.artifacts/cell-error-location-probe`.
All 109 focused checks and three cached-Docker prompt/recovery checks pass at
`.artifacts/cell-error-location-focused` and `.artifacts/cell-error-location-docker-checks`.
Lint and typing pass apart from the known export warning. The full suite completed
with 1,149 passing and two skipped tests at `.artifacts/cell-error-location-regression`;
no paid model job is active. The diagnostic fix has not yet been live-tested.

Additional read-only inspection found five distinct approval-pending Python commands
in routing/findings, not just its two raised cell exceptions. Their exact commands,
reasons and pending status already exist in `approval.pending` events, while the
corresponding `capability.blocked` receipts record no effect and addressed results.
Use that existing authority for the next blocked-approach continuity investigation;
do not infer a permanent command-family prohibition or ignore later authorization.

Deeper inspection found a shared execution-policy defect before adding that memory
projection: regex splitting treated semicolons/newlines/pipes inside quoted arguments
as shell command boundaries. All five recorded routing `python -c` commands (canonical
sequences 106, 211, 248, 420 and 476) reproduce `unknown`; the corrected lexical
segmentation classifies them as the already-existing `build_or_test` category. This
does not establish those proposed programs are correct or predict a successful run.
No saved command was executed and the original results remain unchanged.

The correction uses quote/escape-aware segmentation followed by standard-library
`shlex` argument parsing. Actual trailing commands retain independent risk checks;
unsupported comment/expansion/grouping forms remain approval-gated. All 89 focused
checks pass, including an actual quoted Python command through both direct and PTC
tools and a rejected trailing network command without losing the live worker.
The initial failing probes are retained under `.artifacts/quoted-command-policy-probe`
and `.artifacts/quoted-command-policy-broker` (the latter exposed a test's incorrect
stdout-envelope lookup, corrected in the integration test).
The initial full regression passed 1,175 tests with two skips at
`.artifacts/quoted-command-policy-regression`.
No new paid run or default promotion has occurred; blocked-attempt continuity and
live memory reliability remain open.

The offline audit covered all 18 canonical trial ledgers from the last two campaigns.
Fifteen distinct approval requests change from `unknown` to `build_or_test`: eight
in the older transfer panel (four per arm), and seven in the latest diagnostic
(five findings, two control). Three SQLite/nested-shell requests remain `unknown`.
This counts recorded approval requests, not all repeated capability attempts.

An additional adversarial probe reproduced an escaped `>` before `&` incorrectly
hiding a background command. Redirection pairs are now recognized only while they
are actual unquoted/unescaped syntax; literal `>` followed by `&curl` is blocked.
The failed probe remains at `.artifacts/quoted-command-policy-redirection-probe`.
All 95 final focused checks pass at `.artifacts/quoted-command-policy-final-focused`;
the exact final runtime passed 1,181 tests with two skips at
`.artifacts/quoted-command-policy-final-regression`. Four cached-Docker checks also
pass on the final runtime at `.artifacts/quoted-command-policy-final-docker-checks`.
Lint and typing pass (typing requires
`--pythonpath .venv/bin/python`; the default interpreter probe had missing dependencies).

The two-trial dry preflight at `.artifacts/quoted-command-policy-dry-preflight`
matches the earlier diagnostic's model, selected fixture hash, evaluator source hashes
and per-task budgets. The next paid scope is only the documented routing pair, not
a benchmark expansion. Full offline analysis: `.artifacts/quoted-command-policy-audit/analysis.md`.

The two-trial routing diagnostic is closed at clean `24e169b`; supervisor exited
zero and no paid job remains. Control independently verifies in 19 calls for
$0.03178639; findings stops at its input-budget reserve after 21 calls for
$0.06258172. Tracked source rereads are 171 versus 221. All six answer submissions
are correct/source-supported, but both first verification attempts reject incidental
Python bytecode outside the allowed paths; only control repairs that scope violation.
No unresolved effects or provider/accounting errors remain. The memory gate fails.
Full report: `.artifacts/quoted-command-policy-live-diagnostic-v1/analysis.md`.

This run exposes stronger deterministic next fixes. A malformed 8192-output-token
call leaves 40,172 raw argument bytes in history despite no dispatch, growing the
next input from 7,188 to 43,578 tokens. A temporary note-checkpoint reminder then
disappears from the next same-epoch request, breaking one of 32 captured prefix
transitions and repeating a large uncached input region. Finally Docker command
imports create workspace bytecode, unlike the local cache-isolated execution path.
Fix bounded malformed-call evidence, stable checkpoint-message placement and Docker
cache isolation before another paid diagnostic. Preserve the original failures,
scope verification and budgets; no default promotion or held-out claim is justified.

The checkpoint-prefix defect is now corrected using the existing steering exposure
pattern. `note_checkpoint_delivery@1` publishes the exact reminder at a native-history
boundary and reconstructs that position across note updates and plugin restart.
Later steering stays newer; only an explicit cut removes the historical reminder.
All 80 focused context/steering checks pass at `.artifacts/checkpoint-prefix-final-focused`,
including outgoing OpenRouter request equality and publication/history/content/task/
duplicate-boundary negatives. The original failure is retained at
`.artifacts/checkpoint-prefix-probe`; an intermediate wire test's missing required
reasoning argument is retained at `.artifacts/checkpoint-prefix-negative`.
Docker import-cache isolation is also implemented: `PYTHONPYCACHEPREFIX=/tmp/pycache`
keeps incidental bytecode outside the workspace while explicit compiler outputs and
scope verification remain unchanged. All 51 sandbox/verification checks pass at
`.artifacts/checkpoint-cache-docker-checks`, including the actual cached-Docker import
and explicit-output regression. The original structural failure is preserved at
`.artifacts/docker-cache-policy-probe`. The combined full regression passes 1,189 tests
with three skips at `.artifacts/checkpoint-cache-regression/pytest.xml`; live
qualification remains pending.

Malformed Codex/OpenRouter tool-call JSON is now bounded before ADK history
serialization. The new factory hook archives redacted argument text in the existing
artifact store and records `tool.call_rejected` before publishing a reference; the
reserved marker never dispatches a PTC cell or direct tool. Corrected calls remain
available, and no already-sent request prefix is rewritten. Program and lifecycle
contracts are in the execution/recovery ADR. All 73 focused checks pass at
`.artifacts/malformed-call-parallel-focused`, including real ADK/PTC streaming and
mixed valid/invalid parallel calls, unchanged kernel epoch, artifact recovery, complete
model charges and an explicit error tool metric. The initial parser failure remains
at `.artifacts/malformed-call-boundary-probe`. The combined full regression passes
1,212 tests with three skips at `.artifacts/malformed-call-regression`. Lint and
compilation pass; typing has zero errors and one existing `__all__` warning.

An offline projection of the exact saved malformed routing response preserves its
40,172 argument bytes (SHA-256
`6f7065d4652099673fdb65e4729e61c1e202a28da229333a897fe2e3bd2f2322`)
in an artifact without executing any submitted code. Canonical compact JSON for the
argument object shrinks from 72,349 bytes to 225 bytes (99.689%). The earlier report's
72,350-byte count used default JSON separators rather than compact separators. This is the malformed argument
region only, not whole-prompt token savings, lower live cost, or improved memory use.
The original live cohort remains unchanged and still fails the memory gate.

The common-fix routing pair is now closed at clean `d333c6a`. Both arms independently
verify on the first attempt with all six answers source-supported and all six cuts
exercised. Control uses 12 calls/$0.01666828; findings uses 23 calls/$0.04120137. Both
reread 171 tracked source lines; the memory efficiency gate still fails. All 27 outgoing
same-epoch prefixes pass, with no unknown effects or accounting errors. Neither a
malformed call nor a checkpoint-reminder exposure occurred, so their direct recovery
evidence remains deterministic rather than live. Docker bytecode no longer causes a
scope violation or repair cells. Full report and next mechanisms:
`.artifacts/continuity-common-fixes-live-v1/analysis.md`.

The remaining reread mechanism is now clearer: findings are present after the cut,
but normal handoffs omit the direct artifact-load/decoding recipe already available
in PTC recovery notices. Findings reloads all three sources after every cut and never
loads an artifact. One note update correctly rejects a manually truncated local
artifact hash, but supplies misleading prior-run recovery guidance. A later update
using live read references succeeds. Investigate ordinary-cut executable recovery,
copy-safe citations and completed-phase instruction continuity next; no more paid
dispatch, default promotion or benchmark expansion is implied by these two passes.

Ordinary PTC handoffs now offer the saved-read recovery recipe rather than only an
artifact address (`continuation@10`, `work_batch_navigation@3`). The exact load code
checks historical source identity and returns content plus a copy-safe citation;
page completion never establishes whole-source coverage or current freshness.
The optional recipe respects the existing handoff budget and four-tool boundary.
All 119 focused checks and 14 cached-Docker lifecycle cases pass, including actual
post-worker-loss artifact recovery, configuration revision and independently verified
answers without overlapping source reads. Artifacts are under
`.artifacts/ordinary-read-recovery-focused` and `.artifacts/ordinary-read-recovery-docker`.
This is deterministic mechanism evidence, not observed live model uptake or a memory
efficiency pass. The full regression passes 1,244 tests with three skips at
`.artifacts/ordinary-read-recovery-regression/pytest.xml`. Lint and compilation pass;
typing reports zero errors and the existing `__all__` warning. No additional paid
diagnostic has been dispatched.

Note citation rejection now identifies malformed artifact-address syntax separately
from unavailable current-task evidence and gives exact-reference recovery guidance.
It no longer assumes every missing citation is foreign. The update remains no-effect,
preserves the last checkpoint/version, never suggests a guessed address, and retains
prior-run authorization. Note schema version 7 carries copy-safe citation guidance.
All 36 focused note tests and 12 cached-Docker continuation checks pass at
`.artifacts/citation-guidance-focused` and `.artifacts/citation-guidance-docker`;
the initial failing probe is retained at `.artifacts/citation-guidance-probe`.
The full regression passes 1,247 tests with three skips at
`.artifacts/citation-guidance-regression/pytest.xml`. Lint and compilation pass;
typing has zero errors plus the existing `__all__` warning. Live uptake remains
unqualified; no citation validation or independent-verification rule was relaxed.

The completed-preparation audit confirms current steering and the explicit instruction
not to repeat satisfied preparation were present in the common-fix final review's
actual outgoing requests. The extra acknowledgement is not a missing-delivery defect.
No second phase registry, criterion deletion or reduced verification is justified by
that evidence. See `.artifacts/continuity-common-fixes-live-v1/phase-review-audit.md`.
The next recovery diagnostic preserves the fixture and workflow; repeated preparation
remains a measured behavior, not a claimed fix. Its bounded frozen plan is
`docs/experiments/ordinary-read-recovery-20260913.md`.

The ordinary-recovery live pair is closed on clean `08df057`. Both arms pass first
independent verification with all six answers source-supported and all six cuts
exercised. Findings uses 14 calls/$0.02833852 versus control's 15/$0.03265830, but
rereads 285 tracked source lines versus 284; the joint efficiency gate still fails.
All 21 outgoing same-epoch prefixes pass with sound effect/usage accounting. Every
first-post-cut handoff includes the executable recovery recipe, with no omitted entries.
Control loads six artifacts but prints their serialized bodies and then rereads files;
findings loads none. Both also repeat reads while retaining a live kernel. Findings
cells 87 and 110 reacquire the same 57 lines in the same epoch without an intervening
source mutation. The next mechanism is productive decoded recovery and warm-worker
value reuse, not simply another handoff recipe. Full analysis:
`.artifacts/ordinary-read-recovery-live-v1/analysis.md`. No paid job remains active;
default promotion and benchmark expansion stay held.

The warm-worker notice correction records broker-attested result/data/text forms
and shares one compact projection across changed-only PTC notices and context
handoffs (`ptc_state_updates@4`, `continuation@11`, `work_batch_navigation@4`).
It exposes exact content expressions without increasing budgets or changing full
canonical manifests, invalidation, source coverage, historical freshness or
verification authority. The recorded three-source shape fits in 1,999 bytes; a real
PTC reproduction captures three sources once and executes all delivered content
expressions in the same epoch without additional reads or artifact loads.
Focused validation passes 216 tests with one skip at
`.artifacts/live-binding-content-focused-v2/pytest.xml`; lint and diff checks pass,
typing has zero errors and the existing `__all__` warning. Initial failing probes
are retained. This patch has not received a full-suite rerun or live evaluation.
Per user request, stop further implementation/paid experiments after this validation
and analyze the failed recovery uptake. See
`docs/audits/recovery-uptake-2026-09-13.md`; the broad goal remains unproven.

PTC now additionally retains up to 64 unchanged successful read envelopes under their
existing artifact URIs for the live worker epoch. `agent.state.reads(path)` exposes
bounded path/version/range metadata and `agent.state.reuse(handle)` returns the
original result after disposable model variables are overwritten. Read annotations
remain attached to the retained catalog entry, while mutation, eviction and worker loss
invalidate live reuse. The static instruction prefers descriptive path/purpose names.
`continuation@12` and `work_batch_navigation@5` join usable evidence-backed finding text
to matching live source recipes and omit the duplicate standalone binding;
`ptc_state_updates@5` preserves newest-first recovery when an epoch is lost. Focused
worker, projection and real verified-workflow tests pass. This establishes mechanism,
not live-model reread reduction or default promotion.

The PTC broker now applies that catalog on every read. It performs a bounded identity
probe, returns already covered lines from the retained envelope, and reads only missing
contiguous intervals; a changed hash, failed probe, inconsistent response or new worker
epoch falls back to a full fresh read. Results carry separate reused, newly selected and
identity-probe line counts, and the read audit preserves both logical overlap and actual
catalog reuse. Large repeated source output is replaced at egress with its live `read:N`
recipe while full content remains available to Python. `agent.state.cite('read:N')`
resolves the handle to the canonical artifact URI accepted by evidence-linked working
notes. Deterministic exact/partial/changed-source and completion-evidence checks pass;
the six-task live rerun produced 42 catalog hits and reused 4,871 selected source lines,
but failed quality at 0/6 versus 1/6 before. Ofetch exposed an absolute-versus-normalized
path-key miss. Catalog lookup now normalizes requests through the configured workspace
boundary and has a deterministic absolute-path regression. A bounded Ofetch canary then
reused 1,047 selected lines across ten positive reuse receipts, including 872 lines from
`src/fetch.ts`, but again produced no patch and scored 0/47 F2P. The sample used relative
paths, so live absolute-path uptake remains unisolated. No run used citation handles or
grounded working-note findings. See
`docs/audits/ptc-read-catalog-live-2026-09-13.md`; defaults remain unqualified.
