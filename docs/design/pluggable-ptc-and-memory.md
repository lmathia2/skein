# Pluggable PTC and memory experiment

Skein now separates two independent choices:

| Axis | Option | Model surface | State and history |
| --- | --- | --- | --- |
| PTC | `skein_notebook` | `python` | Persistent CPython heap within the run, write-ahead cells, trace-derived notebook |
| PTC | `adk_code_mode` | `execute_code` | Turn-scoped Docker Python; workspace effects still call Skein's four brokered capabilities |
| Memory | disabled | unchanged | Existing operational events and bounded work packets only |
| Memory | `trace_native` | reserved `memory` commands when active | Versioned programs over the canonical JSONL/DuckDB ledger |
| Memory | `pi` | unchanged | Pi's structured checkpoint prompt over ADK session history, with token-triggered compaction and a retained raw tail |

The PTC implementations are ADK tools, not harnesses. They receive the same
brokered `read`, `bash`, `edit`, and `write` functions, so policy, approval,
receipt, redaction, and verification remain shared. The vendored Code Mode arm
disables its artifact helper tools and tool-result artifact wrapper to avoid a
second effect or persistence path. Its sandbox image must be pinned explicitly.

The memory choice is optional and independent of PTC. The `pi` summary policy is
ported from `earendil-works/pi` commit `853a80d`; ADK owns the event range and
retained-tail persistence in this Python adaptation. The trigger preserves Pi's
16,384-token default reserve; ADK retains a configured event tail rather than Pi's
20,000-token tail. `trace_native` remains the
only strategy allowed to provide context programs, cross-run recall, working
notes, conversation notebook continuity, or safe-auto recovery. The `pi` arm is
intentionally smaller and delegates summary generation and event-range replacement
to ADK's native compaction mechanism.

A matched comparison changes only `notebook_ptc.implementation` or
`memory.implementation`, keeps the model/task/budgets fixed, and records the
existing pass, cost, token, cache, latency, retry, effect, and verification metrics.
No default changes until that ablation passes.
