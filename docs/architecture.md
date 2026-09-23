# Skein architecture

Skein is a coding harness built on Google ADK. ADK owns the model call, native
function-call continuation, session, streaming, cancellation, and resume machinery.
Skein adds a deliberately small coding surface, a brokered effect boundary, durable
evidence, bounded context, and host verification.

The default model-facing surface is one PTC v4.1 tool:

```text
code(code="...")
code(more="result-id", offset=0, limit=51200)
```

Inside the persistent Python worker, `read`, `bash`, `edit`, `write`, and `verify`
are preloaded. `code` is the public ADK tool; `execute_code` is only the internal
Python callable name. The four direct coding tools remain available only as an
evaluation ablation.

## Request lifecycle

```text
task + trusted project instructions
        |
        v
bounded coding packet ----> ADK LlmAgent ----> code
        ^                      |                 |
        |                      | native loop     v
verification feedback         |          persistent Python worker
        |                      |                 |
        +---- Skein workflow <-+          brokered capabilities
                    |                            |
                    +---- events, receipts, artifacts
                    |
                    +---- host verifier ----> complete / retry / blocked
```

1. `SkeinHarnessFactory` validates YAML configuration and creates an isolated ADK
   `App`.
2. `settings_from_composition` builds the stable worker instruction. With notebook
   PTC enabled it declares only `code` and appends the PTC v4.1 contract.
3. The outer `Workflow` creates or restores a task ledger and renders a bounded
   Markdown coding packet.
4. ADK runs the `LlmAgent` and continues native tool calls until the worker emits a
   final assistant message.
5. The PTC tool runs cells in a persistent CPython worker. Nested helpers pass through
   the same execution broker as direct tools.
6. Ordinary prose means “ready for host inspection.” A small JSON blocker envelope is
   accepted only for a genuine human dependency.
7. The host verifier checks the workspace. A failure becomes feedback for another
   work batch; only a passing report can produce `HarnessOutcome(status="complete")`.

The outer workflow is not a second model/tool loop. It owns task boundaries, budgets,
steering, recovery markers, and verification around ADK's loop.

## Authority boundaries

| Concern | Authority |
| --- | --- |
| Model calls, tool continuation, sessions, streaming, cancellation | Google ADK |
| Coding tactics and tool selection | Model inside the ADK worker |
| Filesystem and command effects | Skein execution broker |
| Task progress projection | Reduced `TaskLedger` |
| Conversation history | ADK session |
| Operational evidence | Append-only task `EventStore` |
| PTC working values | Live worker heap; selected JSON-safe values may be checkpointed |
| Completion | Skein host verifier and `HarnessOutcome` |

These boundaries avoid duplicate authorities. The notebook is a workbench, not the
conversation log. The event stream is evidence, not a replacement ADK session. The
model may claim completion, but cannot grant it.

## Prompt and context assembly

The stable instruction contains the worker role, the selected tool contract, and
bounded trusted project instructions. It is hashed so trace data can distinguish a
stable-prefix change from dynamic task state.

`build_coding_packet` creates the dynamic suffix from:

- the goal, acceptance criteria, and constraints;
- selected skills and newest steering;
- changed files and the next action;
- latest verification and evidence navigation;
- optional recent complete turns.

Required control sections are never silently split. If they exceed the configured
budget, dispatch stops with `ContextBudgetExceeded`. Optional conversation is the only
section truncated to fit. Each projection records its source watermark, byte count,
and content hash.

ADK still owns provider-facing message conversion and tool messages. Skein does not
serialize native function calls into a custom structured-response protocol.

## Programmatic tool calling

The PTC worker is persistent for a conversation, so Python variables and functions can
carry mechanical state across cells. Direct filesystem, process, and network imports
are blocked. Workspace effects must use the injected capabilities:

- `read(path, offset, limit)` returns bounded text;
- `bash(command, timeout_seconds)` runs a brokered command;
- `edit(...)` and `write(...)` support optimistic concurrency checks;
- `verify(command, timeout_seconds)` runs an explicit final check and raises on failure.

Results are bounded and pageable. Large bodies are retained as artifacts or result
pages instead of being injected wholesale into the next prompt. Failed cells do not
commit dirty runtime state. Recovery restores only checkpointable values and safe
replay inputs; it never replays arbitrary effects.

## Evidence, tracing, and recovery

Every task has an append-only event stream covering task creation, context projections,
tool artifacts, recovery boundaries, PTC cells, workspace observations, steering,
verification, and termination. ADK lifecycle tracing and metrics are attached through
plugins. When trace-native memory is enabled, a ledger-backed store mirrors supported
events for analytical queries; the JSONL task stream remains sufficient for normal
operation.

Recovery boundaries are recorded before model calls and around effectful work. Tool
receipts and workspace fingerprints let the host distinguish a safe retry from an
unknown effect. An unknown effect blocks automatic completion until reconciled.

Learning is offline only. `verified_learning_episode` can project a reproducible
episode from a clean, host-verified trace; it does not mutate runtime prompts or tools.

## Source map

| Area | Implementation |
| --- | --- |
| Composition and ADK plugins | `app/agent/factory.py` |
| Stable prompt and PTC contract | `app/agent/config.py` |
| ADK worker and tool adapters | `app/agent/builders.py` |
| Work batches and verification loop | `app/agent/workflow.py` |
| Coding packet | `harness/core/orchestration/core.py` |
| PTC session and worker | `app/agent/ptc.py`, `harness/ptc/` |
| Effect boundary | `harness/execution/` |
| Events, receipts, ledger, tracing | `harness/evidence/` |
| Completion contract | `harness/core/models/outcome.py` |
| Verification | `harness/verification/` |

## Supported simplifications

- One ADK worker; no planner/reviewer model topology.
- PTC v4.1 is the default; direct four-tool mode is an ablation.
- Ordinary final prose; no required structured agent response.
- One host verifier decides completion.
- No online self-modifying memory or learned prompt mutation.

The detailed contracts live in the ADRs:

- [ADK-native PTC v4.1 core](adr/adk-native-ptc-v4.1-core.md)
- [Programmatic tool calling](adr/programmatic-tool-calling.md)
- [Context and memory](adr/context-and-memory.md)
- [Execution and recovery](adr/execution-and-recovery.md)
- [Pi and Skein core primitives](adr/harness-comparison.md)
