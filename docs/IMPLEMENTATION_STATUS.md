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

Deterministic unit and integration tests establish code contracts, not model quality.
See [Package layout](package-layout.md), [Architecture](architecture.md), and
[Harbor evaluation](evaluation-harbor.md).
