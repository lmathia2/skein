# ADR: Programmatic tool calling

Status: accepted and implemented

## Decision

Skein's default coding interface is a persistent CPython tool exposed to the model as
`code`. It follows the PTC v4.1 prompt and helper contract used by the current
implementation, while all effects remain mediated by Skein.

## Public interface

The ADK tool has two mutually exclusive operations:

```text
code(code="python source")
code(more="result-id", offset=0, limit=51200)
```

The first executes a cell. The second pages a retained result. Invalid combinations are
rejected before execution and returned as recoverable tool errors.

The internal callable remains named `execute_code` in Python. That name is not part of
the model-facing surface.

## Worker contract

The worker keeps Python variables and functions for the lifetime of the conversation.
It preloads `json`, `math`, `re`, and five capabilities:

- `read` for bounded file reads;
- `bash` for brokered commands and searches;
- `edit` for one exact replacement;
- `write` for a complete file write;
- `verify` for a check that raises on failure.

`os`, `pathlib`, `subprocess`, direct network access, and equivalent escape paths are
blocked. This is capability restriction, not an attempt to use Python as the security
boundary; the execution broker remains authoritative.

## Results and model projection

Nested capabilities return useful Python values to the cell. The direct ADK tool result
is a bounded model projection. Internal `data` and `ui_details` are removed from direct
model responses, while full results may be stored as artifacts and evidence events.
Large results receive identifiers and can be paged with `more`.

This separation lets code filter, join, count, and validate large observations before
the model sees them. It also keeps trace detail available without inflating context.

## Failure and continuity

- Syntax, policy, timeout, and runtime failures are tool results, not harness crashes.
- A rejected cell did not execute.
- A failed cell discards its dirty worker epoch.
- JSON-safe selected values can be checkpointed.
- Recovery never replays arbitrary effectful cells.
- Every mutation makes host verification mandatory.

The notebook records cells and selected output as a durable workbench. It is not the
conversation authority and it is not identical to the live Python heap.

## ADK relationship

ADK still owns function-call messages, the assistant/tool continuation loop, and
provider translation. Skein supplies one ADK function tool whose implementation enters
the PTC worker. There is no parallel structured tool-call loop.

## Evaluation adapters

`scripts/pi_code_tool_harbor.py` and related parity scripts are benchmark adapters.
They preserve strict comparison shapes where an experiment needs them; they do not
define Skein's runtime architecture.

## Implementation

- `app/agent/config.py`: model instruction
- `app/agent/builders.py`: ADK function and bounded projection
- `app/agent/ptc.py`: registered capabilities and notebook session
- `harness/ptc/`: worker, protocol, notebook, checkpoints, result paging
- `harness/execution/`: brokered nested effects
