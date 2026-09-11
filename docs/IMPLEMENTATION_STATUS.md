# Skein implementation status

## Supported product boundary

Skein is a one-shot Harbor/Pier coding-agent harness built on Google ADK. It ships
two evaluated profiles:

- `four-tool.yaml`: model-visible `read`, `bash`, `edit`, and `write`.
- `notebook-ptc-jsonl.yaml`: one persistent `execute_code` tool with brokered
  capabilities and canonical JSONL evidence.

The interactive terminal, WebSocket service, launcher variants, Prime PTC, ADK Code
Mode, context experiment profiles, and custom pre-Harbor graders are not supported
product surfaces and have been removed.

## Retained contracts

- `app/agent` composes one ADK coding worker and owns the model/tool loop.
- `harness/core` owns validated configuration, typed task state, bounded context,
  skill selection, and deterministic orchestration.
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
- Complete nested capability results and truncated stdout/stderr are immutable,
  content-addressed artifacts. Internal result artifacts stay out of model responses;
  bounded task-scoped load/list operations recover them, while explicit publish adds
  normalized host-facing metadata without forwarding data.
- Conservative replay-safe recovery remains the shipped default. A bounded
  primitive/container snapshot policy is available only as an experimental opt-in.
- `harness/verification` owns acceptance checks and the final complete/retry/blocked
  decision. Model completion claims are never authoritative.
- `harness/adapters` contains external boundary code only: Google ADK integration,
  supported model providers, and the Pier task-environment adapter.
- `evals` contains frozen manifests, campaign execution, and result analysis. It is
  deliberately outside the harness package.

## Empirical status

Four tools remain the default because notebook PTC has not cleared the matched quality
gate. Notebook PTC remains available for controlled evaluations. The reference-six
audit and subsequent two-task comparison are retained under `docs/audits/`.

Deterministic unit and integration tests establish code contracts, not model quality.
See [Package layout](package-layout.md), [Architecture](architecture.md), and
[Harbor evaluation](evaluation-harbor.md).
