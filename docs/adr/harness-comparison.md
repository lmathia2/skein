# Skein compared with Codex, OpenCode, and Pi

Status: architectural comparison, September 2026

This note positions the current simplified Skein implementation against three
influential coding harnesses. It is not a feature checklist or a claim that Skein is
a better end-user product. Codex, OpenCode, and Pi are mature interactive systems;
Skein is a research harness for testing a narrower architectural thesis:

> One programmable action surface plus one trace-native evidence log can support a
> long-running coding agent when memory is computed as versioned views and completion
> remains under deterministic host control.

The comparison uses Skein's [design analyses](../design/), the
[current architecture](../architecture.md), and public primary documentation for
[Codex](https://learn.chatgpt.com/docs/codex/cli),
[OpenCode](https://opencode.ai/docs/), and
[Pi](https://github.com/earendil-works/pi/tree/main/packages/coding-agent).

## Executive comparison

| | Codex | OpenCode | Pi | Skein |
| --- | --- | --- | --- | --- |
| Primary goal | Complete coding product across local, IDE, app, and cloud workflows | Provider-neutral coding platform with rich tools, agents, permissions, and integrations | Minimal terminal harness that delegates workflow choices to extensions and ordinary CLIs | Controlled experiment in programmable tool use, trace-native memory, and verified completion |
| Default interaction | Product-managed coding agent with shell, patching, skills, MCP, sandboxing, approvals, and task coordination | TUI/CLI/server with many built-in tools, primary agents, subagents, MCP, LSP, skills, and permission rules | Small interactive loop with `read`, `write`, `edit`, and `bash`; extensions supply optional workflow | One ADK worker; four-tool baseline or one `execute_code` tool |
| Extensibility | Configuration, `AGENTS.md`, skills, MCP, hooks, SDK, app server | Custom agents, tools, plugins, MCP, LSP, SDK, server | Extensions, skills, prompts, themes, packages, SDK, RPC | Closed code-owned registries and validated experiment profiles |
| Durable session model | Product task/thread history with record-and-replay support | Persisted sessions and messages across clients | Append-only JSONL session tree with branching and compaction markers | Canonical task/effect trace; ADK session, notebook, heap, indexes, and caches have separate authority |
| Long-context strategy | Product-managed context and compaction | Provider/session context management and configurable agents | Lossy structured compaction with retained recent tail; full JSONL remains on disk | Versioned programs compute bounded prompt views from evidence at an explicit watermark |
| Safety model | OS sandbox modes, approvals, project trust, and network policy | Per-tool `allow`/`ask`/`deny` rules, including granular command/path patterns | Intentionally leaves permission UI and isolation to extensions or the surrounding environment | One broker contract for confinement, authorization, redaction, bounded output, receipts, and reconciliation |
| Completion authority | Agent/product workflow | Agent/application workflow | Agent/user workflow | Model completion is only a proposal; deterministic verification owns terminal status |
| Optimization stance | Optimize a production agent experience | Maximize capability and configurability | Keep the core small and let users compose the rest | Keep topology fixed, expose controlled treatment axes, and require matched ablations before promotion |

## Codex: the integrated product baseline

Codex is the strongest comparison for a production coding environment. Its public
surface spans terminal, IDE, desktop, cloud, SDK, app-server, non-interactive, and
multi-agent workflows. It supports project instructions through `AGENTS.md`, skills
and MCP integration, configurable sandbox and approval modes, worktrees, and explicit
record-and-replay tooling. Project-local configuration is loaded only after trust is
established. See the official [Codex CLI](https://learn.chatgpt.com/docs/codex/cli),
[configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
[sandbox](https://learn.chatgpt.com/docs/sandboxing), and
[record-and-replay](https://learn.chatgpt.com/docs/extend/record-and-replay)
documentation.

Skein should not claim novelty for sandboxing, approvals, project instructions,
skills, resumable work, tool-call recording, or programmatic tool use. Codex already
offers polished forms of those capabilities and a much broader operating envelope.

The difference is research focus. Codex exposes an integrated product contract;
Skein exposes the internal experimental variables. Skein can hold the task, model,
budgets, verifier, and effect policy fixed while changing only the model-facing
execution mode or memory strategy. Its trace and view receipts are designed to answer
which exact evidence, program version, watermark, and prompt shape affected a run—not
merely to reproduce or inspect the interaction.

## OpenCode: the configurable platform baseline

OpenCode emphasizes breadth and user configuration. Its documented tool set includes
file operations, shell, search, patching, skills, todos, questions, web access, and an
experimental LSP tool; custom tools and MCP servers extend it further. Primary agents
and subagents may have separate prompts, models, and permissions. Permission rules can
allow, ask, or deny actions globally or by command/path pattern. See OpenCode's
[tools](https://opencode.ai/docs/tools/),
[agents](https://opencode.ai/docs/agents/), and
[permissions](https://opencode.ai/docs/permissions/) documentation.

Skein should not claim novelty for multi-provider support, configurable tools,
subagents, MCP, LSP-backed navigation, granular permissions, or an embeddable server.
OpenCode is broader on all of those axes.

Skein intentionally moves in the opposite direction. Its model-visible topology is
small and closed, because every additional tool changes prompt cost, tool selection,
policy surface, and the meaning of an evaluation. Configuration may select a reviewed
provider, PTC implementation, ledger, or context policy; it may not construct an
arbitrary agent topology. This makes Skein less flexible as a product but more useful
as a controlled harness.

## Pi: the closest philosophical ancestor

Pi most directly influenced Skein's minimal model interface. Pi defaults to four
general coding tools, keeps its system prompt small, loads skills progressively, uses
ordinary shell programs for composition, and stores sessions as append-only JSONL.
Its session tree supports branching. Compaction keeps a recent tail and replaces older
active context with a structured summary while preserving the full session file. Pi's
stated philosophy is to omit built-in subagents, permissions, MCP, planning, and todos
when extensions or existing tools can supply them. See Pi's
[coding-agent documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md),
[SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md),
and [compaction design](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md).

Skein copies the useful discipline: a small stable prompt, shell-first composition,
progressive disclosure, bounded outputs, an append-oriented history, and one main
agent loop. The four-tool profile remains the compatibility baseline precisely because
Skein's own matched evaluations have not justified replacing it by default.

Skein diverges where a managed, replayable experiment needs stronger authority. Pi's
session log is primarily conversation and interaction history; Skein's canonical trace
records task state, cell attempts, authorization, receipts, failures, timeouts,
verification, and unresolved effects. Pi compaction is a practical lossy context
handoff; Skein treats each memory result as a versioned program over addressed evidence
at a watermark. Pi deliberately leaves permissions and verification outside its core;
Skein makes both non-optional host responsibilities.

## What is genuinely novel in Skein

None of Skein's ingredients is novel in isolation. Code execution, notebooks,
event-sourced logs, compaction, retrieval, capability brokers, and deterministic tests
all predate it. The interesting contribution is the combination and the unusually
strict boundaries between them.

### 1. The ledger, notebook, and live heap are three different kinds of state

Many code-mode systems blur transcript, notebook, and runtime state. Skein assigns
each one a single job:

- The append-only ledger answers what happened.
- The notebook is a readable, reconstructable working document.
- The Python heap holds disposable live values for one kernel epoch.

Cell and attempt identities connect the three, but none substitutes for another. A
notebook cell is not proof that an effect completed; a live value is not durable; a
ledger event is not automatically useful model context. This separation is implemented
in `harness/ledger`, `harness/notebook`, and `harness/repl`.

### 2. Memory is a versioned program with an evidence receipt

Skein does not define memory as a mutable prose field or a vector-store lookup. A
memory view identifies the program name and version, parameters, authorized evidence,
historical watermark, budgets, evidence addresses, and result hash:

```text
view = program@version(evidence at watermark, parameters, budgets)
```

That turns progress reports, history pages, summaries, counts, retrieval, and prompt
selection into inspectable computations. Deterministic views are disposable and
rebuildable. Candidate programs can run in shadow against the same frozen history
before they influence context. This contract lives in `harness/memory` and
`harness/ledger`.

### 3. One programmable tool does not receive ambient authority

The notebook profile gives the model one `execute_code` surface, but nested file,
shell, and registered capabilities still cross the same host broker as direct tools.
Code mode changes the syntax and composition power available to the model; it does not
change authorization, confinement, deadlines, redaction, output limits, receipts, or
reconciliation. Every nested operation remains separately attributable in the trace.

This is a stronger claim than “the model can run Python.” It asks whether programmatic
tool composition can reduce turns and prompt bytes without creating an opaque,
all-powerful interpreter.

### 4. Failed cells are treated as transactional evidence boundaries

Skein records cell intent before execution and records success, failure, timeout, or
unknown effect afterward. A failed brokered cell discards its dirty kernel epoch before
more work is accepted; restoration is limited to explicitly classified replay-safe
cells. Mutation-capable uncertainty blocks continuation until reconciled. The design
therefore preserves both the failed attempt as evidence and a clean boundary for later
reasoning.

### 5. Completion is derived from environmental evidence

The model may claim it is done, but Skein's control plane checks acceptance criteria,
workspace scope, required commands, baseline-relative regressions, and open effects.
The terminal state is computed from that evidence. Verification results return to the
trace and may drive another bounded model turn, but prose cannot mark itself verified.

### 6. The harness is designed as an ablation instrument

Skein makes model surface, PTC implementation, memory strategy, context policy, and
ledger implementation explicit treatment axes while keeping authority and verification
fixed. Configurations fail closed on unsupported combinations. Promotions require
matched evidence across quality, token use, cache behavior, latency, retries, effects,
and verification—not a compelling demo or one cheaper run.

Together these properties make Skein less an alternative terminal agent than an
instrument for asking a precise question: can a model do better long-horizon work when
it computes through one narrow interface and receives reproducible views over its own
evidence?

## Claims the current implementation does not make

The simplified implementation matters more than the aspirational design:

- Four direct tools remain the default because the completed PTC comparison did not
  clear the quality gate.
- Skein ships one notebook PTC implementation; discarded experiment adapters are not
  retained as product surface.
- JSONL is sufficient for the default path. DuckDB is optional, and semantic retrieval
  is not active without an explicit embedding provider.
- The local environment adapter is not a production security sandbox.
- Deterministic contract tests establish behavior, not superiority over Codex,
  OpenCode, or Pi. Product-quality and live-task claims require matched evaluations.

These limits are features of the comparison, not footnotes. Skein's credible novelty
is a falsifiable architecture with explicit authority and evidence—not the assertion
that every proposed mechanism is already better.
