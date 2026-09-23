# Skein implementation specification

This is a map of the contracts enforced by the current code. It is intentionally
short; historical experiments remain under `docs/audits/` and `docs/experiments/`.

## Runtime contract

- Google ADK is the only model/tool runtime.
- `SkeinHarnessFactory` builds one isolated `App` and one coding worker.
- Notebook PTC is enabled by default and exposes one model-facing `code` tool.
- Direct `read`, `bash`, `edit`, and `write` mode is retained only for controlled
  comparison.
- The workflow may run multiple bounded work batches, but it does not execute native
  model tool calls itself.
- A task is complete only when deterministic verification passes.

## Input and output

The root workflow accepts user text. `parse_task_request` also accepts the existing
JSON task-request representation at the boundary for runner compatibility; that does
not impose structured output on the model.

The model normally returns prose. The only model-authored JSON required by the prompt
is an optional blocker:

```json
{"status":"blocked","message":"...","question":"..."}
```

The host returns a typed `HarnessOutcome` with one of `complete`, `answered`,
`blocked`, `failed`, or `cancelled`. `complete` requires a passing
`VerificationReport`.

## Prompt contract

The stable prefix is assembled in `app/agent/config.py` from the configured worker
instruction, the selected tool surface, and trusted project instructions. PTC mode
appends the v4.1 helper contract and declares only `code`.

The dynamic packet is assembled by `build_coding_packet` and contains complete required
control state plus optional bounded conversation. Required content over budget fails
before model dispatch. Each emitted projection records a hash and source watermark.

## Tool contract

`code` accepts exactly one of:

- `code`: a Python cell to execute;
- `more`: an existing result identifier to page, with `offset` and `limit`.

The persistent worker preloads `read`, `bash`, `edit`, `write`, and `verify`. Nested
helpers cannot bypass confinement, approval, redaction, receipt, timeout, or output
bounds. A rejected cell did not run. A failed cell does not commit a dirty epoch.

Provider-native ADK function calls and responses stay native. Model-facing results
exclude internal `data` and `ui_details`; full evidence can be retained in artifacts
and event records.

## State contract

| State | Persistence | Purpose |
| --- | --- | --- |
| ADK session | ADK-managed | Conversation and native tool messages |
| Task event stream | Append-only JSONL by default | Operational evidence and ledger rebuild |
| `TaskLedger` | Deterministic reduction | Current goal, criteria, progress, files, validation |
| Notebook | Durable projection | Submitted cells, selected outputs, provenance |
| Python heap | Worker lifetime | Fast intermediate computation |
| PTC checkpoint | Explicit, JSON-safe subset | Safe value restoration |
| Artifact store | Content-addressed | Large results and verification details |

No derived view becomes a second source of historical truth. Optional trace-native
memory computes views from addressed evidence at a recorded watermark.

## Execution contract

All workspace mutations and commands flow through `harness/execution`. The boundary
must:

- resolve paths inside the configured workspace;
- enforce command policy and approvals;
- redact known secrets from model-visible output and traces;
- bound output and retain references to omitted bodies;
- issue receipts for effects and record unknown outcomes;
- preserve optimistic concurrency fields for edits and writes.

Direct tools, PTC helpers, and verification share these primitives.

## Verification contract

Mutation marks verification as required. `bash` does not satisfy final verification;
the PTC prompt directs the model to use `verify`. The host independently runs the
configured validation and compares workspace state. Failure is fed into the next
bounded work batch. Unknown effects or exhausted budgets prevent `complete`.

## Configuration contract

Behavior comes from validated YAML. Environment variables bind invocation-specific
identity and paths only. Configuration may select known providers, budgets, memory,
tracing, and the direct-tool ablation; it cannot create an arbitrary model topology or
bypass the broker and verifier.

## Runnable checks

```bash
./install.sh
make lint
make test
```

Harbor evaluation commands and required credentials are documented in
[evaluation-harbor.md](evaluation-harbor.md). The active architecture is described in
[architecture.md](architecture.md); ADRs record why its boundaries exist.
