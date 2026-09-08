# PTC execution refinement

Status: proposed implementation plan, reviewed 2026-09-08 against Skein
`267d4ac`. This refines the recent PTC optimization discussion; it does not
change the supported runtime, activate a profile, or authorize a benchmark run.

## Decision

Keep the persistent `python` tool, guarded capability broker, canonical trace,
and notebook projection. Improve their data and execution contracts before
considering a replacement runtime. A rewrite is permitted, but the reviewed
sources do not establish that one is necessary for the desired gains.

The target is **more useful computation between model decisions, with less
irrelevant data entering those decisions**. Concurrency is a separate latency
optimization. Neither a tool rename nor a minimum calls-per-cell quota proves
PTC is effective.

## Corrections to the earlier recommendation

1. Sequential loops, conditional calls, and local filtering already qualify as
   PTC. Async execution is useful, not its defining property. Anthropic describes
   both intermediate-result isolation and programmatic orchestration; its 37%
   token reduction is a research-task result, not a coding-agent guarantee.
   [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use).
2. The PTC cookbook's saved comparison reports 4 versus 4 API requests,
   110,473 versus 15,919 tokens, and 35.38 versus 34.88 seconds. Requests that
   resume server-side execution are not necessarily fresh inference turns.
   This is an illustrative workload, not evidence of an equivalent DeepSWE
   quality/latency improvement.
   [PTC cookbook](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/programmatic_tool_calling_ptc.ipynb).
3. The cost cookbook explicitly says its short-task compaction threshold did
   not fire. Its failed repeat cannot establish compaction caused a regression.
   It also distinguishes stable-prefix caching from shortening context; we need
   to measure both, not assume fewer input tokens always means faster requests.
   [Cost cookbook](https://github.com/anthropics/claude-cookbooks/blob/main/cost_optimization/cost_optimization.ipynb).
4. Replace “never batch edits with decisions” with “return to the model when
   new semantic judgment is needed.” An already-decided patch followed by a
   targeted check is valid. A program that invents subsequent repairs from
   unfamiliar failures is not the intended batching pattern.
5. Grouping commands in one cell does not mean running them concurrently.
   Formatters, builds, and tests can mutate files or shared caches. Do not call
   them read-only or parallel-safe merely because they are validation commands.
6. Remove projected savings summed across overlapping categories and hard
   density targets such as 2.5–3 capabilities per cell. A shell call can already
   contain a whole workflow; a zero-capability cell can perform valuable analysis.
   Report critical-path time and useful outcomes, not a quota the model can game.

## What the reference implementations contribute

The local clones were reread at these revisions. These are design references,
not comparative performance evidence.

| Reference | Useful mechanism | Do not copy without need |
| --- | --- | --- |
| [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent/tree/bf8894afa55832f7cfa2094c8a0d041bc680a691) | Persistent IPython state, top-level await, host-reported process completion, complete interaction boundaries for compaction | A second orchestration framework or assumptions that every heap value survives compaction |
| [Open-PTC](https://github.com/daly2211/open-ptc/tree/2f2ef0ce4087d338a2cd1646c8ef18cc3f57b1de) | Explicit input/output contracts and selected console output; brokered execution resumption | Replacing Skein's persistent worker with its per-execution Deno subprocess or copying its chain-budget behavior |
| [pi-ptc](https://github.com/cegersdoerfer/pi-ptc/tree/b567c8902d751fcc6edac9ffcfda527375d1c539) | Correlated RPC responses, pending futures, asynchronous host dispatch | Assuming async syntax alone gives cancellation, effect ordering, or replay safety |
| [llmvm](https://github.com/9600dev/llmvm/tree/2939932cb03e17df5c7ca66d3b874c465e24e8a0) | Persistent execution locals and separation of program execution from reasoning | Its short-code-block prompting and nested LLM extraction/map-reduce as a latency policy |
| [UTCP Code Mode](https://github.com/universal-tool-calling-protocol/code-mode/tree/e5fdc319bfae1ceec57e7bce37048337635ffb51) | Generated input/output interfaces, namespaced tools, explicit returned value versus diagnostic logs, isolate memory limits, and tests for hung calls | Its fresh isolate per chain, loading every tool interface, blocking tool bridge, or abandonment as a substitute for cancellation/reconciliation |

Reviewed seams: Prime's `docs/rlm.md`, `docs/compaction.md`, and RLM prompt;
Open-PTC's sandbox executor, REPL tool builder, and orchestrator; pi-ptc's Python
RPC runtime, host RPC handler, and executor; llmvm's continuation controller and
Python execution prompt; UTCP Code Mode's TypeScript/Python runtimes, generated
interfaces, bridge lifecycle, and timeout/resource tests. Local repositories live
under `/Users/mathiasl/src`.

UTCP Code Mode does not change the architecture decision. Its TypeScript runtime
creates and disposes an isolate for every tool chain, while Skein deliberately
retains a notebook kernel. Its generated functions call `applySyncPromise`, which
blocks the isolate thread until each host call settles; wrapping those calls in
`Promise.all` therefore does not create the read fan-out proposed below. On chain
timeout it races host calls against chain completion so the isolate thread can be
released, but the underlying host promise can still settle later. That is a useful
resource-leak defense, not an effect-cancellation guarantee. The Python variant
also documents that a timed-out worker thread cannot be forcibly stopped.

## Implementation sequence and acceptance checks

Use focused commits and existing configuration/receipt mechanisms. No new
framework, extra top-level tool, separate historical store, or per-feature paid
ablation is required. Each slice below must pass its deterministic checks before
the next depends on it; one combined live screen follows the completed bundle.

### 1. Give programs data, not rendered tool transcripts

Current seam: `harness/tools/coding.py:execute_read` renders numbered lines and
returns only `model_text`; `app/agent/builders.py:_CellBroker` forwards this
model-oriented result into Python. Reads are limited to 400 lines, and shell
conversion also collapses structured stdout/stderr into display text. Existing
tests already prove nested results can remain outside model context: preserve
that property while making the data useful to programs.

- Extend the shared execution-result contract with machine-readable file
  content/range, structured search matches/cursors, and separate stdout/stderr.
  Render the four-tool view from the same underlying result. Do not create a
  second implementation of authorization, redaction, or file mutation.
- Keep the program's selected returned value separate from diagnostic logs and
  the host-generated operation summary. This adopts UTCP Code Mode's clean
  result/log distinction without replacing Skein's existing last-expression and
  MIME handling.
- Expose explicit completeness, truncation, original-content hash, and artifact
  or continuation references. Preserve exact text/newlines. Large data stays
  bounded and artifact-backed; add a brokered bounded retrieval path where the
  existing artifact resolver is insufficient. A 400-line preview is not a full
  file just because display truncation is false.
- Keep runtime ingress and model egress budgets separate. Never provide
  unlimited tool output to the kernel or silently bypass redaction. Redacted
  text must not masquerade as original bytes for a subsequent replacement.
- Keep a small host-generated cell outcome alongside selected Python output:
  execution status, failed/blocked/unknown operations, truncation and provenance
  references. Dropping bulky metadata must not hide a failed operation.
- Capture actual serialized OpenRouter request sizes and region hashes at the
  transport boundary, alongside usage and timing. Do not persist raw prompts or
  credentials by default. Distinguish retries/HTTP requests, model responses,
  cells, capabilities, and selected egress bytes.

Checks: extend `test_harness_factory.py`, `test_repl.py`, and
`test_openrouter_responses.py`. A scripted 20-call loop reads uniquely marked
large results, filters them, then makes a second mocked provider request.
Assert only selected output reaches that request, including after compaction;
test partial data, redaction, failed operations, and exact text/hash behavior.
Increasing nested data must not proportionally increase provider payload size.

### 2. Teach phase-aware programs using executable examples

Change `NOTEBOOK_PTC_INSTRUCTION`, `agent.help()`, and the existing
`NotebookPtcConfig.batching_instruction`; keep the `python` tool name. Document
actual return types, not just function signatures. Version/hash the stable
examples using existing behavior configuration so their strategy can later be
optimized without introducing a new optimizer now.

Generate compact, machine-readable argument/result contracts from Skein's typed
models and expose them through targeted `agent.help(name)` lookup. Do not inject
all interfaces into every request: Skein has four compact built-ins, and registered
MCP capabilities should be discovered or inspected only when selected. Cache the
generated contract by the existing behavior hash, not as mutable notebook state.

Provide three short, executable examples:

- Discovery: gather known-path reads/searches, parse/filter locally, retain
  reusable values, print a compact comparison with evidence addresses.
- Implementation: apply one already-chosen change with its expected hash;
  inspect status in code; run the already-selected check only on success;
  return its exit status and relevant failure excerpt. No blind repair loop.
- Review: collect evidence for weak explicit requirements, including negative
  cases/state transitions; run already-selected checks; return gaps and stop
  exploring when required evidence is present. Independent verification still
  decides completion; self-authored assertions do not establish correctness.

Keep policy choices learnable in the existing behavior surface, but safety,
effect reconciliation, and completion gates remain deterministic. Do not add
an LLM call just to create a batching plan, audit, or compaction note.

Checks: execute examples against a scripted broker and assert operation order,
data selection, and stop-on-failure. Do not assert on natural-language model
output. Existing no-progress/max-cell limits remain safety bounds, not desired
batch sizes.

### 3. Add bounded parallel reads without replacing the interpreter

Implement an additive broker batch primitive, `agent.parallel(operations)`,
using the existing operation names and argument contracts. Each item contains
an operation name and keyword arguments; results are returned in input order.
Start with a configurable bound of four concurrent operations and admit only
broker-certified read-only operations. Reject unsupported/effectful batches
before starting any item. Dependent stages remain ordinary sequential Python.

This gives independent fan-out one broker round trip without introducing
top-level await, a second async API for every capability, or multiple cells
running against the same heap. Full correlated async RPC remains an alternative
if staggered execution is actually needed; it is not a prerequisite for PTC.

- Preallocate immutable child operation IDs in submission order. Record each
  intent before dispatch. Keep one ledger-writer owner; record terminal outcomes
  as observed and return results in stable input order. Do not close over the
  mutable `_CellBroker.call_index` in concurrent callbacks.
- Return a result for every started child, including failures/cancellation.
  Do not retry the whole batch after partial success. Keep normal shell calls,
  builds, tests, writes, and approval-requiring operations serialized initially.
- Fix the existing timeout seam: `PersistentPythonWorker.execute` calls the host
  broker inline, so it cannot enforce its cell deadline while that call blocks.
  Propagate remaining deadlines into execution adapters. Cancelling an await or
  a future is not proof a subprocess stopped. Race every broker wait against cell
  termination so a hung call cannot strand the worker, but quarantine late
  settlement and record the operation as unknown until the executor proves it
  stopped or reconciliation observes the outcome. Prevent subsequent mutations
  until safe; do not report rollback of files when only the Python heap was
  discarded. This is intentionally stronger than UTCP Code Mode's abandonment
  warning, because Skein brokers workspace effects rather than arbitrary read-only
  APIs.

Checks: barrier-based overlap/cap tests, stable result order, denied batch with
zero execution, unique receipts, partial failure, expiry during a broker call,
and no post-timeout mutation/replay. Reuse `test_repl.py` and notebook integration
tests. No flaky millisecond speed assertions are needed for correctness.

### 4. Remove redundant verification and snapshot work

Current verification already reuses some `execution.validation_observed`
receipts. Extend and tighten that path rather than adding another cache.
`app/agent/workflow.py:_ensure_validation_baseline` still runs tests before
coding; replace unconditional baseline execution with an on-demand comparison.

Implementation note (2026-09-08): receipt reuse, dirty-path fingerprinting, and
the shorter baseline timeout are safe and implemented. The unconditional baseline
is deliberately retained. Running it after a failure in the already-mutated
authoritative workspace is not a baseline, and neither the local nor Harbor runtime
currently exposes an isolated initial workspace with equivalent dependencies. Do
not remove the pre-mutation check until that execution primitive exists.

- Preserve the initial workspace identity before edits, including initial dirty
  files. Run modified-workspace checks first; obtain the matching isolated
  initial-workspace baseline only for failures that require comparison. Never
  reset the live workspace. A baseline timeout is inconclusive, not a pass;
  record the blocker rather than relaxing the verifier.
- Reuse only complete, eligible check receipts with matching command, cwd,
  environment/toolchain identity, check semantics, and workspace fingerprint.
  An arbitrary model-run command is not automatically an equivalent required
  check. Reapply current test-count/strength requirements when consuming evidence.
- Reduce repeated Git/subprocess/container work in the execution adapters, not
  in the Harbor grader. A Git-aware fingerprint may hash changed/untracked files
  rather than the entire tree, but must cover deletions, modes, symlinks, index
  and base identity. Revalidate dirty content at verification fences; invalidating
  a cache only after broker writes misses external edits. Fail closed on errors.
- Reuse notebook projections at the same ledger watermark; avoid rebuilding
  unchanged history around every cell. Preserve byte-equal rematerialization and
  immutable final snapshots. Keep ledger/receipt writes synchronous and durable.

Checks: reused versus executed verification equality; changed environment,
external edits, dirty initial workspace, truncated output, timeout, and missing
baseline rejection; fingerprint equivalence fixtures; notebook bytes unchanged
at a fixed watermark. Record exact blocking check and reason for diagnosis.

### 5. Bound history at cache-stable epochs

Use the existing context program and compaction machinery in
`harness/adk/context.py`. It currently rebuilds handoff details for each request;
after a cut this can change the header before the retained history.

- Freeze the compacted handoff at its recorded epoch/watermark. Append new
  effects, steering, and state-loss notices in the suffix; do not let a frozen
  handoff hide newer control information. Preserve stable prefix bytes.
- Separate trigger budget from target retained size, reserving headroom for the
  next bounded result and model output. Prefer semantic phase boundaries; retain
  a complete-cell overflow fallback so a long phase cannot prevent compaction.
- Retain exact active requirements/steering, decisions and invariants, unresolved
  failures/effects, evidence addresses, and a meaningful bounded recent tail.
  Keeping only the latest pair plus a generic summary is not sufficient by
  construction. Never split a tool call/result interaction.
- Reuse existing advisory notes; no extra summarizer model. Notes can guide
  continuation but cannot authorize effects or prove completion. Keep the live
  heap through ordinary compaction, and explicitly report kernel loss on failure
  or restart. Do not replay effects or promise arbitrary Python-value restoration.

Checks: byte-stable epoch header across successive requests, new steering and
effects visible, exact requirement retention, complete pairs, bounded provider
payloads, safe recovery, and no reference to lost values as live state. Trigger
compaction deterministically with small test budgets rather than paying for a
long synthetic conversation.

## Affordable acceptance and reporting

Run the focused tests per slice, then applicable unit/integration, lint, and
type checks. Use scripted broker/provider fixtures for data isolation, batching,
timeouts, replay, and compaction. Those prove mechanics, not model quality.

Once the bundle is implemented, use one live smoke and then the same six
DeepSWE instances, one attempt each, with **Muse Spark 1.3 Contributor**, matching
the previous task definitions, provider settings, budgets, and official grader.
Record exact model/provider/config/revision and any unavoidable drift. Do not
silently substitute Luna, launch repeat sweeps, change the grader, or rerun only
the successes. Keep eval credentials outside the agent's task environment.

Compare against the existing PTC, four-tool Skein, and mini-swe artifacts per
task: official quality and individual old/new tests, harness terminal reason,
cost per solved task, total/cached/uncached/output tokens, actual request bytes,
model decisions, retries, cells, capabilities, egress, and critical-path wall
time. Separate model wait, tool execution/queueing, verification, and trace work;
do not add overlapping spans as if they were independent recoverable latency.

Stop promotion on a newly lost pass, lost old test, false completion, duplicate
effect, or unresolved timeout. Passing old tests alone does not establish the
requested new behavior. One successful six-task screen is limited evidence, not
proof of “no quality loss” or optimality. Keep the four-tool fallback; do not
silently promote PTC globally on the strength of this design review.

Deferred deliberately: tool renaming, tool search for four compact capabilities,
general background-job infrastructure, arbitrary heap persistence, async trace
writes, and copying another agent framework. Add them only for a demonstrated
requirement that the retained execution path cannot meet.
