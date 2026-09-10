# PTC prompt comparison: Skein, Prime Agent, pi, pi-ptc

Status: analysis and proposed text. Nothing here is landed. The canonical follow-up list and prompt text now live in `ptc-follow-ups-2026-09-09.md`. Pair with
`ptc-deepswe-reference-six-2026-09-10.md`, whose N1-N5 units this note sequences
against.

## Sources

| Stack | Revision | Model-facing text examined |
| --- | --- | --- |
| Skein four-tool | `7c8ddcc` | `coding_worker.instruction` in `harness/config/profiles/four-tool.yaml`; tool schemas from `harness/tools/adk_adapter.py` |
| Skein notebook PTC | `7c8ddcc` | same base + `NOTEBOOK_PTC_INSTRUCTION` (`app/agent/config.py`) + `NotebookPtcConfig.batching_instruction` (`harness/config/models.py`); tool description in `app/agent/ptc.py` |
| Skein Prime PTC | `7c8ddcc` | same base + inline adapter paragraph in `app/agent/config.py`; tool description in `app/agent/prime_ptc.py` |
| Prime Agent upstream | `bf8894a` (pinned; prompt files unchanged at `d4bc773`) | `packages/coding-agent/src/core/prompts/rlm.ts`, `tools/ipython.ts`, `system-prompt.ts` |
| pi coding agent | `853a80d` | `packages/coding-agent/src/core/system-prompt.ts`, `tools/{read,bash,edit,write}.ts` |
| pi-ptc extension | `b567c89` | `src/index.ts` `code_execution` description, `README.md` |

Word counts are of the stable instruction only. Skein counts exclude project
instructions, tool schemas, skills, and the dynamic work packet.

## Comparison

| Stack | Stable prompt | Where mechanics live | Batching doctrine | Executable example |
| --- | ---: | --- | --- | --- |
| Skein four-tool | 150 words | tool schemas | none; targeted reads and bounded search | none |
| Skein notebook PTC | 633 words (150 + 348 + 135) | system prompt; tool description is one sentence | explicit, phase-conditional | three snippets with placeholders (`known_paths`, `weak_criteria`, `collect_known_evidence`) |
| Skein Prime PTC | 233 words (150 + 83) | system prompt | none beyond "concise printed results" | none |
| Prime Agent | variable, several hundred words | `ipython` tool description (~70 words) plus rlm prompt mental model | implicit: Python is the orchestration language, name and reuse results, nonblocking `bash()` handles | none in prompt; `edit` skill usage snippet |
| pi | ~100 words + one-line tool snippets | tool descriptions (truncation, offset continuation, edit uniqueness) | none | none |
| pi-ptc | pi base + ~150-word tool description | tool description | none | one runnable loop |

Skein's four-tool and notebook profiles share a byte-identical base. The
evaluated Prime lane used Skein's base plus the 83-word adapter and never saw
Prime's rlm prompt, so the reference-six Prime lane compared runtimes under
unequal prompts. Prime's own code labels its rlm prompt the "trained
buildRlmPrompt prefix", which means its effect is coupled to Prime's model
training and should not be assumed to transfer to another harness.

Every external design places the how-to in the tool description with at most one
runnable example and keeps the system prompt about role and guidelines. Skein's
four-tool base copied that shape from pi. The notebook profile inverted it: the
`execute_code` description is "Executes Skein notebook PTC in one persistent
CPython tool." and all mechanics sit in the cached system prompt.

## What the notebook prompt achieved in the reference-six traces

Measured over the 370 notebook cells in
`/Users/mathiasl/skein-eval-results/deepswe-reference-six-notebook-ptc-7c8ddcc`:

| Measure | Value |
| --- | ---: |
| Capability calls per cell | 1.38 |
| Cells with exactly one capability call | 263 of 370 (71%) |
| Cells with zero capability calls | 7 |
| Cells with three or more calls | 26 |
| Single shell one-liner cells | 122 |
| Single edit cells | 41 |
| `agent.parallel` uses across six tasks | 1 |
| Read-only cells that print raw file text | 94 |
| Read-only cells with 12k+ bytes of stdout | 16 of 88 |

The model used `execute_code` as a wrapper around one tool call per cell and
often printed the whole read. That is the four-tool loop with Python syntax and
without the batching benefit the 483 words of doctrine describe.

Three findings that shape the rewrite:

1. **The phase doctrine is mis-keyed, not dead.** The ledger phase reaches the
   model inside the TASK JSON of the work packet. But the first work-batch yield
   sets the phase to `review`, so in Koota the whole 48-cell implementation batch
   ran under the review rule and the implement rule applied to almost no cell.
2. **The `data` advice matches the documented envelope.** Both `KeyError('data')`
   failures came from reads of nonexistent paths, whose error envelope carries only
   `status` and `model_text`. The prompt's own example checks `status` first; the
   model skipped it. The envelope fix (reference-six N3) removes the trap; prompt
   text alone does not.
3. **Signatures in the prefix duplicate `agent.help()`.** The prompt lists four
   signatures, then tells the model `agent.help()` lists all of them. The prefix
   also repeats the notebook-authority warning that the ledger already enforces.

## Proposed text

The base instruction stays as it is in every profile. The PTC additions become one
shared doctrine of 106 words plus a runtime appendix of about 40 words, with mechanics moved
into the tool description and `agent.help()`. Resulting stable size per PTC lane is
about 300 words (150 base, 106 doctrine, 43 or 37 appendix) against 633 today, and the two lanes differ only in the appendix.

### Shared base (unchanged, 150 words)

Keep `coding_worker.instruction` from `four-tool.yaml` verbatim. It carries the
AgentStep contract the outer workflow depends on.

### Shared PTC doctrine (append for both `skein_notebook` and `prime_repl`)

```text
Persistent Python is your control plane. Your only tool is execute_code(code).
In one cell, combine capability calls whose inputs are already known and
independent. Bind every result to a variable, check its status before reading
its data, filter in Python, and print only the facts the next decision needs.
Stop the cell before any repair or semantic choice that depends on a result you
have not seen. If the next cell would only repeat a call whose inputs you already
hold, merge it into this one. Run builds and tests in the project's own
environment, serially, and keep every exit status. Skein's independent verifier
owns completion.
```

### Notebook appendix (`skein_notebook` only)

```text
Capabilities: agent.fs.read, agent.fs.write, agent.fs.edit, agent.shell.run,
agent.mcp.call, and agent.parallel([...]) for up to four independent reads.
agent.help() returns exact signatures; agent.help(name, details=True) returns
one result shape. Direct filesystem, process, and network APIs are blocked. For
.ipynb files use nb read or nb search through agent.shell.run.
```

### Prime appendix (`prime_repl` only)

```text
bash, asyncio, and emit are prebound; use await bash(command) for shell
commands and top-level await freely. Serializable variables are snapshotted
after each cell; open resources and background tasks may not restore. A failed
cell keeps earlier assignments.
```

### `execute_code` tool description, notebook

Replaces the one-sentence description in `app/agent/ptc.py`.

```text
Run one Python cell in a persistent CPython worker. Variables, functions, and
imports persist across cells. Capability calls return a mapping with status,
model_text, and data; data is empty on error or blocked. A failed cell discards
its own assignments.

Example: read two known files, decide in Python, print only facts.
    a = agent.fs.read("src/config.py", limit=200)
    b = agent.fs.read("tests/test_config.py", limit=200)
    ok = [r for r in (a, b) if r["status"] == "ok"]
    hits = {r["data"]["path"]: [i for i, line in enumerate(r["data"]["text"].splitlines(), r["data"]["offset"]) if "timeout" in line] for r in ok}
    print(hits, [r["model_text"] for r in (a, b) if r["status"] != "ok"])
```

The example must survive `tests/unit/test_example_notebooks.py` against the real
broker so it stays executable as the envelope changes.

### `execute_code` tool description, Prime

Replaces the description in `app/agent/prime_ptc.py`.

```text
Run one Python cell in a persistent native kernel. Variables, functions, and
imports persist across cells. Use await bash(command) for shell commands; it
returns exit_code and output. Print only the facts the next decision needs.

Example:
    r = await bash("pnpm -F core test run 2>&1 | tail -n 30")
    print(r.exit_code, r.output[-2000:])
```

### Dynamic phase hint (replaces the phase-conditional prefix text)

Delivered in the ledger `next_action` when the workflow changes phase or yields a
work batch, so it lives in the dynamic suffix and follows the actual phase.

```text
understand: Batch independent bounded reads and searches with known inputs, then print a compact summary.
implement:  Group the reads that support one decided change; an already-decided edit and its already-selected check may share a cell when the cell stops on failure.
review:     For each unresolved criterion row, run its positive, negative, and transition probes together, including histories where the prerequisite never held.
verify:     Run already-selected formatter, type, and targeted-test commands in one cell, serially, and print every exit status.
```

### `agent.help()` contract change

`_AGENT_RESULTS` in `harness/repl/worker.py` becomes the single source of result
shapes and must match the broker. Every envelope, including error, blocked, and
per-item parallel results, exposes `status`, `model_text`, and a `data` mapping
that is empty when there is no typed detail. This is reference-six N3.

## Where each piece lands

| Piece | Location | Replaces |
| --- | --- | --- |
| Shared doctrine | new constant in `app/agent/config.py`, appended for both implementations | `NOTEBOOK_PTC_INSTRUCTION` body and the inline Prime paragraph |
| Appendices | two short constants in `app/agent/config.py` | signatures, blocked-API list, replay warnings |
| Tool descriptions | `app/agent/ptc.py:847`, `app/agent/prime_ptc.py:222` | one-sentence descriptions |
| Phase hints | `_criterion_review_action` and the yield handler in `app/agent/workflow.py` | `NotebookPtcConfig.batching_instruction` default; keep the field for overrides but stop appending it to the prefix |
| Result shapes | `_AGENT_RESULTS` and broker envelopes | documented-versus-actual drift |

## Sequencing and gates

1. Land N3 first so the doctrine can promise `data` unconditionally.
2. Land the prompt change together with N1 clause rows, because the review hint
   refers to criterion rows that do not exist until then.
3. Replay `tests/unit/test_example_notebooks.py` and the prefix-hash tests; the
   stable prefix bytes change once, and both PTC lanes must share the doctrine
   bytes.
4. Gate on cell shape before reward, on Koota and Testem, three paired attempts:
   single-call cell share at or below 40% from 71%, capability calls per cell at
   or above 2.0 from 1.38, read-only cells above 12k bytes of stdout at or below
   5 from 16, no increase in median uncached input per task.
5. Then run the reference-six N5 staging for reward, with notebook and Prime
   receiving identical doctrine so the runtime ablation is fair.

Do not import Prime's full rlm prompt. Its subagent, continual-harness,
asynchronous-process, and native-access guidance describes capabilities Skein
notebook PTC does not provide, and its trained-prefix coupling does not transfer.
