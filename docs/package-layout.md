# Package layout

The repository is split by authority, not by feature variant.

| Path | Owns |
| --- | --- |
| `app/agent/` | Concrete Google ADK composition, coding worker, PTC registration, streaming, and the outer verification workflow |
| `harness/core/` | Validated configuration, task and outcome models, skills, bounded context, and orchestration helpers |
| `harness/ptc/` | Persistent Python worker, protocol, notebook projection, result paging, and safe checkpoints |
| `harness/execution/` | Filesystem and command capabilities, path confinement, sandbox policy, approvals, redaction, and workspace runtimes |
| `harness/evidence/` | Task events, receipts, artifacts, tracing, metrics, optional memory views, and offline learning episodes |
| `harness/verification/` | Host-owned validation and completion evidence |
| `harness/adapters/` | ADK plugins, provider selection, and Pier boundary translation |
| `evals/` | Frozen Harbor manifests and campaign analysis; no runtime authority |
| `scripts/` | Thin developer and evaluation entry points |

The common runtime path is:

```text
app/agent -> ADK -> code -> harness/ptc -> harness/execution
     |                                      |
     +-> harness/core                       v
     +-> harness/verification <- harness/evidence
```

Important ownership rules:

- ADK, not `harness/core`, owns the native model/tool event loop.
- `app/agent/workflow.py` may repeat bounded coding and verification batches, but it
  must not interpret or execute native tool calls.
- Direct tools and nested PTC helpers meet at `harness/execution`.
- ADK sessions own conversation; evidence stores own task facts; notebooks and the
  Python heap are projections or working state.
- Evaluation adapters may reshape a benchmark boundary, but cannot redefine the
  production model-facing surface.

New code should join the package that already owns its authority. Do not add a new
top-level package for a provider, benchmark arm, storage projection, or tool-surface
variant.
