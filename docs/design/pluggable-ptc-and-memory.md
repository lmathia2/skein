# Pluggable PTC and memory experiment

Skein now separates two independent choices:

| Axis | Option | Model surface | State and history |
| --- | --- | --- | --- |
| PTC | `skein_notebook` | `execute_code` | Persistent CPython heap within the run, write-ahead cells, trace-derived notebook |
| PTC | `prime_repl` | `execute_code` | Trusted native Python, JSONL lifecycle evidence, bounded dill snapshots |
| Memory | disabled | unchanged | Existing operational events and bounded work packets only |
| Memory | `trace_native` | reserved `memory` commands when active | Versioned programs over the canonical JSONL/DuckDB ledger |
| Memory | `pi` | unchanged | Pi's structured checkpoint prompt over ADK session history, with token-triggered compaction and a retained raw tail |

The PTC implementations are ADK tools, not harnesses. Skein notebook PTC receives
brokered `read`, `bash`, `edit`, and `write` functions, preserving policy, approval,
receipt, redaction, and verification. Prime is deliberately a trusted native arm;
its direct effects are recorded at cell granularity as `native_untracked`.

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

## Native configuration presets

Use these blocks under `harness.config` in an existing composition. Omitted
`serialization` and `state` settings default to `native`, preserving the implementation's
existing behavior.

```yaml
notebook_ptc:
  enabled: true
  implementation: skein_notebook
  serialization: notebook
  state: replay_safe
```

```yaml
notebook_ptc:
  enabled: true
  implementation: prime_repl
  serialization: jsonl
  state: snapshot
  prime_native_execution: true
```

Unsupported serialization/state combinations and Prime conversation continuity raise
`NotImplementedError` at load time. The trusted Prime preset never triggers a substitute
implementation. The removed `adk_code_mode` key also raises a migration error.
