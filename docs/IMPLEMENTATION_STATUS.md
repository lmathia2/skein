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

Deterministic unit and integration tests establish code contracts, not model quality.
See [Package layout](package-layout.md), [Architecture](architecture.md), and
[Harbor evaluation](evaluation-harbor.md).
