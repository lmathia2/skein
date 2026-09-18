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

The completed 48-case OpenRouter continuity cohort reduced exact-line duplicate
emissions with findings but did not reduce source rereads, cost 13% more than the
metadata arm, and failed the missing-range case. DeepSWE expansion and default
promotion remain held. See [the continuity audit](audits/ptc-memory-continuity-2026-09-12.md)
for the separate cohort results and subsequent coverage diagnostic.

Deterministic unit and integration tests establish code contracts, not model quality.
See [Package layout](package-layout.md), [Architecture](architecture.md), and
[Harbor evaluation](evaluation-harbor.md).
