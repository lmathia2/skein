# PTC quality and efficiency improvement plan

> Status: proposed
>
> Scope: Skein notebook PTC only
>
> Evidence baseline: [matched DeepSWE reference-six audit](audits/ptc-deepswe-reference-six-2026-09-10.md)

## Outcome

Make notebook PTC at least as reliable as the four-tool baseline while preserving
its lower latency, token use, and cost. Four tools remain the default until a
matched Harbor evaluation clears both the quality and efficiency gates below.

This plan improves the model's ability to choose the right capability, retain useful
state, cover behavioral edge cases, and combine mechanical work. It does not add a
model-visible tool or weaken Skein's effect, trace, recovery, or verification
boundaries.

## Current implementation

Skein ships notebook PTC as the opt-in `notebook-ptc-jsonl.yaml` profile. The default
`four-tool.yaml` profile remains the quality baseline. The former Prime and ADK Code
Mode implementations have been removed; this plan concerns the retained Skein
runtime only.

### Model interface and prompt

The model sees one ADK function tool:

```python
execute_code(code: str, timeout_seconds: int = 120)
```

Each call submits one Python cell. The stable PTC instruction currently explains
the broker signatures, result handling, batching, notebook authority, replay rules,
and three orchestration examples. The tool description itself is only one sentence.
This gives the model the mechanics, but duplicates information available from
`agent.help()` and does not describe the actual kernel, CLI, or MCP inventory.

The profile also retains the same base coding instruction, provider request-prefix
contract, context budgets, safety policy, and independent verification flow as the
four-tool mode. PTC changes how the model composes actions, not what it may do.

### Persistent CPython worker

`PersistentPythonWorker` starts one child process with Python's `multiprocessing`
`spawn` context and communicates through a duplex pipe. The child owns a persistent
namespace for one kernel epoch. Assignments, imports, functions, and intermediate
results remain available across successful cells without entering model context.

Cell execution uses the standard library AST, `compile`, `exec`, and evaluation of
the trailing expression. It is synchronous: top-level `await` and background tasks
are not supported. Standard output, standard error, and the trailing value are
bounded. A final MIME-keyed mapping is treated as display data, and oversized rich
output is stored as a content-addressed artifact by the parent path.

The worker is disposable. A timeout or transport failure terminates it. The current
application path also discards the epoch after every Python error, including syntax
and source-policy failures that occurred before user code ran.

### Guarded Python environment

The worker prebinds an `agent` proxy and guarded builtins. Static validation and the
import hook block direct access to dangerous primitives such as `open`, `eval`,
`exec`, dunder introspection, and modules including `os`, `pathlib`, `subprocess`,
`socket`, `requests`, and `urllib`.

This is defense in depth for trusted local workspaces, not an OS sandbox. The
supported contract is that intended filesystem, process, and network effects use
the broker. Container or host isolation remains an outer deployment concern.

### Brokered capabilities

The prebound proxy currently exposes:

```text
agent.fs.read(path, offset=1, limit=400)
agent.fs.write(path, content, expected_sha256=None, expected_absent=False)
agent.fs.edit(path, old_text, new_text, expected_sha256=None)
agent.shell.run(command, timeout_seconds=120)
agent.mcp.call(capability, arguments)
agent.parallel([independent fs.read operations])
agent.state.list()
agent.state.describe(name)
agent.help(name=None, details=False)
```

`agent.fs.*` and `agent.shell.run` reuse the same confined execution adapters,
command policy, approvals, redaction, output bounds, artifact handling, and receipts
as the four direct tools. Registered MCP handlers are selected by
name in the parent. `agent.parallel` accepts only bounded independent reads and
returns results in input order; effectful operations remain serial.

Every broker call receives a stable operation identity. The parent records intent
before execution and then records completed, failed, blocked, timed-out, or unknown
outcomes. A broker deadline that expires discards the worker and requires effect
reconciliation rather than assuming that cancellation stopped the external work.

`agent.help()` currently describes the fixed built-in signatures and selected result
shapes. It does not receive metadata from registered MCP handlers and does not expose
a verified Python or CLI manifest. `agent.state.*` exposes bounded metadata about live
bindings—name, type, size, originating cell, and replay class—without copying values
back into the prompt.

### Notebook, ledger, and recovery

Before execution, Skein appends a durable submitted-cell event with stable notebook,
cell, attempt, invocation, and kernel identities. It then appends the terminal cell
result and every nested capability outcome. The append-only ledger answers what
happened and remains the historical authority.

The `.ipynb` file is rebuilt deterministically from ledger events. It contains cell
source, selected output, Markdown context, and provenance, but it is neither an
execution authority nor proof that an effect completed. `nb-cli` is the supported
inspection boundary. Closing a worker stores the logical notebook as an immutable
artifact; Skein does not snapshot the arbitrary Python heap.

After a discarded epoch, Skein reconstructs only previously completed cells whose
AST is classified as replay-safe. Capabilities are disabled during reconstruction.
The current policy deliberately accepts only simple, self-contained data
construction; imports, calls, definitions, loaded-name dependencies, attribute
access, and subscript access require reconciliation. A timed-out cell with an
unknown effect blocks later execution until the effect is reconciled.

### Configuration and operating limits

`NotebookPtcConfig` controls enablement, implementation, serialization and state
modes, output size, timeouts, parallel-read bounds, continuity, and work-batch
limits. The shipped profile uses the Skein notebook implementation and canonical
JSONL evidence. Configuration validation rejects removed implementations and
unsupported combinations.

The worker enforces a 128,000-byte cell-source limit, configured output bounds, a
default and maximum timeout, bounded parallel fan-out, maximum cells per batch, and
a no-progress yield. The application yields control to the model or verifier at
deterministic boundaries rather than allowing an unbounded inner Python loop.

### Features already proven by deterministic tests

- Persistent values across successful cells within one kernel epoch.
- Guarded direct filesystem, process, and network access.
- Brokered reads, atomic writes and edits, shell execution, MCP dispatch, and
  bounded parallel reads.
- Write-ahead cell and capability events with stable identities.
- Output truncation, redaction, artifact externalization, and rich MIME results.
- Timeout termination and unknown-effect reconciliation.
- Transactional recovery through epoch discard and replay-safe reconstruction.
- Deterministic notebook materialization and conversation-scoped continuity.
- Metadata-only live-state inspection.
- Independent verification of model completion claims.

These tests establish execution contracts. They do not establish that notebook PTC
is more capable than four tools on coding tasks; that decision remains evaluation
gated.

## Baseline and diagnosis

On the six matched DeepSWE tasks, four tools passed 3/6 and notebook PTC passed 2/6.
Notebook PTC used 10.0% less active time, 8.8% fewer input tokens, and 28.3% less
recorded cost, but its lower pass rate made cost and input tokens per accepted task
worse.

The trace points to four actionable problems:

1. **Incomplete behavioral coverage.** The Koota implementation missed one negative
   state-transition case after passing the visible suite. The task entered review as
   one large goal rather than a stable set of testable clauses.
2. **Weak PTC composition.** Seventy-one percent of cells contained exactly one
   capability call, so Python often acted as a verbose wrapper around the four-tool
   loop. Only one bounded parallel read was used across all six tasks.
3. **Unnecessary state loss.** Five of seven failed cells were rejected before user
   code executed, yet each rejection discarded the kernel epoch.
4. **Capability uncertainty.** The prompt documents built-in signatures but does not
   present a verified inventory of the active kernel, useful project CLIs, or
   registered MCP capabilities. The model must probe or guess.

## Design constraints

- `execute_code` remains the only model-visible PTC tool.
- All filesystem, command, and MCP effects pass through the existing broker.
- The append-only ledger remains historical authority; the notebook remains a
  projection; the live Python heap remains disposable.
- Stable instructions contain only invariant doctrine. Task and environment facts
  stay in the dynamic suffix or behind `agent.help()`.
- Failed or interrupted effectful cells are never replayed automatically.
- Harbor reward measures quality. Skein's verifier and deterministic tests measure
  harness correctness, not benchmark success.
- Each unit lands and is tested independently. A paid run follows only after the
  deterministic gates pass.

## Work plan

### Phase 1: remove deterministic friction

These changes are small, offline, and should precede prompt experiments.

| Unit | Implementation | Deterministic gate |
| --- | --- | --- |
| F1 Uniform results | Give every nested success, error, blocked, timeout, and parallel result the same bounded envelope, including an empty `data` mapping when no typed detail exists. Generate `agent.help(..., details=True)` result contracts from that source. | Table-test every terminal status through the real broker; a missing-file read followed by `result["data"]` must not raise. Preserve redaction, artifacts, and receipts. |
| F2 Failure stages | Report `parse`, `source_validation`, `execution`, or `transport` as a trusted worker failure stage. Preserve the kernel only for parse and source-validation failures with no broker call and no effect. | Syntax and static-policy failures preserve the epoch and existing values. Runtime mutation followed by failure still discards the epoch and restores only replay-safe cells. |
| F3 Compact errors | Return the exception type, bounded message, cell-local source line, short source excerpt, failure stage, and whether state was preserved. Strip worker implementation frames. | Snapshot tests cover syntax, policy, broker, and runtime failures without natural-language assertions. |

### Phase 2: make available capabilities obvious

Follow Prime Agent's useful pattern: advertise stable facilities, list extensible
capabilities compactly, and disclose detailed contracts only on demand.

Add a parent-built, immutable catalog to the worker:

```python
agent.help()                              # compact category index
agent.help("kernel")                     # Python version and preloaded modules
agent.help("cli")                        # verified, supported commands
agent.help("mcp")                        # registered capability names
agent.help("mcp.github.search", details=True)  # one exact contract
```

| Unit | Implementation | Deterministic gate |
| --- | --- | --- |
| C1 Capability metadata | Register each MCP handler with its name, description, argument schema, result schema, effect class, and approval policy. Reuse upstream schemas when present. | Every callable advertised to the model is registered and invocable; disabled handlers are absent; catalog ordering and serialization are stable. |
| C2 Kernel manifest | Configure a small preload set and verify imports when the worker starts. Expose only verified module names and meaningful versions through `agent.help("kernel")`. Do not scan or advertise the whole environment. | Worker startup fails closed on a required preload; optional missing modules are omitted; manifest bytes are deterministic. |
| C3 CLI manifest | Verify a curated command list in the authoritative Pier task environment. Include repository-detected commands only when their manifests or executable paths exist. | Host and Pier inventories cannot be confused; unavailable commands are never advertised. |
| C4 Prompt split | Replace duplicated signatures and placeholder examples in the stable PTC instruction with a short doctrine and one executable example in the `execute_code` description. Put the bounded environment summary in the dynamic work packet. | Prefix snapshot changes once; subsequent environment changes do not alter stable-prefix bytes. The example executes against the real broker. |

The prompt should teach strategy rather than enumerate APIs:

```text
Persistent Python is your control plane. Combine capability calls whose inputs are
already known, retain intermediate values, check status, filter results in Python,
and expose only the facts needed for the next judgment. Inspect unfamiliar runtime,
CLI, or MCP contracts with agent.help(). Run project checks in the project's own
environment. Skein's verifier owns completion.
```

Package installation remains disabled during a run. The kernel manifest describes
the computation environment, while repository commands execute through the broker
in the project's environment.

### Phase 3: improve requirement coverage

This is the quality-critical phase.

| Unit | Implementation | Deterministic gate |
| --- | --- | --- |
| Q1 Stable criterion rows | When a benchmark supplies no explicit criteria, retain the original goal as the parent and adopt a bounded first-review decomposition into stable, hashed child rows. The model proposes structure; it does not decide satisfaction. | A Koota fixture produces separate `Added`, `Removed`, `onAdd`, and `onRemove` rows without dropping the parent requirement. IDs remain stable across replay. |
| Q2 Claims by ID | Address completion claims by criterion ID rather than exact prose. Reject unknown, duplicate, and stale IDs. | Rewording a claim cannot lose its evidence; a claim cannot satisfy a different row. |
| Q3 Evidence by row | Bind each validation receipt only to the rows it was selected to test. Unmatched rows remain unresolved. | A positive transition test cannot satisfy a negative or precondition-unmet row. |
| Q4 Transition matrix | For state-transition requirements, require positive transition, negative, and precondition-unmet probes regardless of whether the goal contains words such as “never.” | The historical Koota positive probe remains insufficient until the missing false-positive case is demonstrated. |

Criterion rows and unresolved evidence appear in the dynamic suffix and notebook
projection. They never enter the stable prompt prefix.

### Phase 4: improve composition efficiency

Tune behavior only after the model receives correct contracts and useful unresolved
criteria.

| Unit | Implementation | Measured gate |
| --- | --- | --- |
| E1 Phase hints | Emit one current-phase hint at a phase transition or batch yield. Remove phase-dependent prose from the stable prefix. | Hint phase always matches the ledger; complete tool-call/result pairs remain in context. |
| E2 Search discovery | Document the existing brokered search grammar in `agent.help()` and the CLI manifest. Do not add another top-level tool. | Search fixtures preserve path confinement, bounds, and receipts; the model no longer needs `find`/`grep` probing to discover syntax. |
| E3 Batch tuning | Compare the current `24/48` no-change/max-cell policy with `12/36`. A yield returns unresolved rows and the latest failed validation rather than restarting exploration. | On Koota and Testem, reduce median cells or active time without reducing reward or increasing uncached input. Otherwise keep `24/48`. |

Target cell-shape indicators, evaluated before buying a broader reward run:

- Cells with exactly one capability call: at most 40%, from 71%.
- Mean capability calls per cell: at least 2.0, from 1.38.
- Read-only cells emitting more than 12,000 bytes: at most five across the selected
  comparison, from sixteen across the reference six.
- No increase in median uncached input per task.

These are diagnostics, not optimization targets in isolation. A model may correctly
use one call when the next action depends on its result.

### Phase 5: evaluate bounded state rollback

Only pursue this after F1-F3. Pre-execution preservation removes most observed
kernel churn without introducing serialization risk.

Separate notebook serialization from runtime recovery in configuration:

```yaml
notebook_ptc:
  serialization: notebook
  state: replay_safe  # none | replay_safe | bounded_snapshot
```

Implement `bounded_snapshot` as an experimental profile. Snapshot eligible user
values before a cell, restore the committed snapshot after runtime failure, and
discard state after timeout or unknown effect. Exclude broker objects, modules, open
resources, tasks, generators, and oversized values. Keep replay-safe recovery as the
supported default until the snapshot mode improves matched outcomes.

Gate snapshot activation on:

- atomic restore after an injected runtime failure;
- deterministic exclusion and size-limit behavior;
- no replay of brokered effects;
- bounded startup and per-cell overhead;
- a measured reduction in recovery cells without a reward regression.

Top-level `await` and background tasks are deferred. They add interruption,
attribution, shutdown, and snapshot complexity without evidence that they address a
current Harbor failure.

## Evaluation sequence

1. Land F1-F3 and replay deterministic notebook/broker failure fixtures.
2. Land C1-C4 and Q1-Q4; freeze the new prefix, behavior hash, catalog, and profiles.
3. Run three paired four-tool/notebook attempts on Koota. Notebook must recover the
   lost pass.
4. Run three paired attempts on Testem and Textual to measure churn and strategy
   variance.
5. Rerun Ofetch and Wazero as regression sentinels.
6. Only after the quality gate passes, test E3 and bounded snapshots as isolated
   ablations.
7. Expand to the reference six, then the next frozen Harbor tier before considering
   notebook PTC as the default.

Record exact revision and diff hash, profile and behavior hashes, task image, model
settings, attempt count, retries, reward, verifier partial score, model/tool/cell
counts, nested calls, state resets, wall and active time, input/output/reasoning and
cache tokens, recorded cost, output bytes, and terminal reason.

## Promotion gates

Notebook PTC may replace four tools as the default only when the same frozen tasks
and provider conditions show:

1. pass rate no lower than four tools across multiple attempts;
2. no loss of the existing Ofetch and Wazero passes;
3. no unresolved effects or verifier-authority regressions;
4. median uncached input no higher than four tools;
5. recorded cost per accepted task no higher than four tools; and
6. either lower median active time or fewer model turns without worse tail latency.

If quality improves but efficiency regresses, retain PTC as an opt-in mode and use
the traces to isolate the expensive unit. Do not compensate by weakening verification
or increasing hidden retries.

## Explicit non-goals

- Import Prime Agent's full runtime, prompt, daemon, skills product, or native OS
  authority.
- Add an exhaustive package or executable scanner.
- Allow package installation during benchmark execution.
- Add background Python tasks, unrestricted filesystem access, or another notebook
  execution path.
- Change the ledger backend, container isolation, or model provider during the PTC
  comparison.
- Optimize call density at the expense of necessary model judgment.

## Code map

| Concern | Primary location |
| --- | --- |
| Stable PTC instruction | `app/agent/config.py` |
| Capability registration and broker | `app/agent/builders.py`, `app/agent/ptc.py` |
| Worker, `agent.help()`, failure stages | `harness/ptc/repl/worker.py` |
| PTC configuration | `harness/core/config/models.py`, `harness/core/config/profiles/notebook-ptc-jsonl.yaml` |
| Criterion and completion flow | `app/agent/workflow.py`, `harness/verification/` |
| Trace-derived metrics | `harness/evidence/`, `evals/` |
| Deterministic contracts | `tests/unit/test_repl.py`, `tests/unit/test_notebook_ptc_integration.py` |

The implementation order is **F1 → F2 → F3 → C1 → C2 → C3 → C4 → Q1 → Q2
→ Q3 → Q4 → staged Harbor evaluation → E3/snapshot ablations**. This fixes known
deterministic waste first, then the observed quality miss, and only then adds runtime
complexity.
