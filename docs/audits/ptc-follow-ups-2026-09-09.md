# PTC follow-ups: one list for verification, prompt, search, and kernel work

Status: consolidated next steps as of 2026-09-09. Nothing here is landed. This
document supersedes the "Recommended next units of work" table in
`ptc-deepswe-reference-six-2026-09-10.md` where the two differ, and it carries the
canonical proposed prompt text from `ptc-prompt-comparison-2026-09-09.md`.

Evidence base: the reference-six lanes under `/Users/mathiasl/skein-eval-results/
deepswe-reference-six-{four-tool,notebook-ptc,prime-ptc}-7c8ddcc`, code at
`7c8ddcc`, Prime Agent at `bf8894a`, pi at `853a80d`, pi-ptc at `b567c89`.

## Principles these units share

- Verification control flow first, then prompt shape, then runtime ergonomics.
  This is the order agreed on 2026-09-07 and nothing in the reference-six traces
  argues against it.
- The model never installs packages during code execution. The kernel is fixed
  per profile, declared to the model, and project commands run in the project's
  own environment through the broker.
- The model must know what the kernel provides. Every capability, preloaded
  module, and search primitive is discoverable through `agent.help()` and stated
  once in the prompt, so the model picks the right tool without probing.
- Each unit has deterministic evidence before any paid run, and cell-shape gates
  before reward gates.

## Units

### V. Verification (quality)

| Unit | Change | Evidence and gate |
| --- | --- | --- |
| V1 Clause rows | DeepSWE requests have no acceptance criteria, so `TaskRequest` falls back to the whole goal as one row (`harness/models/task.py:59`). Make clause rows first-class with stable ids: decompose the goal at initialization, or adopt the model's own claim rows on the first review. | Fixture: the Koota goal yields separate rows for `Added`, `Removed`, `onAdd`, `onRemove`; the review prompt renders rows, not the 1.6k-character goal marked MISSING. |
| V2 Claim matching by id | `_verify_task` keys claims by exact criterion text (`app/agent/workflow.py:465`); the Koota model wrote four clause claims and all were dropped, so the report showed empty claimed evidence. Match by row id. | Test: a step with clause claims yields non-empty `claimed_evidence` on the matching rows and nothing on others. |
| V3 Per-row evidence binding | `build_report` attaches every passing reference to every row (`harness/verification/runner.py:199`). Bind a command reference only to the row it was selected to prove; unmatched rows stay unsatisfied. Gate on the notebook profile first. This inverts `test_environmental_evidence_does_not_require_model_completion_prose`; state the contract change explicitly. | Fixture: complete→incomplete passes, "never complete" row unresolved, internal `done` refused. Gate: three paired Koota attempts, notebook pass rate at or above four-tool. |
| V4 Transition probes | Require a precondition-unmet probe for every transition clause, not temporal keywords. The four-tool Koota smoke tested "Removed no false positive"; the notebook claim row for Removed was one positive check. | Same Koota fixture as V3. |
| V5 Preserve kernel after pre-execution rejection | Five of seven notebook cell failures were SyntaxError or static PermissionError raised in `_validate_source` before exec; `app/agent/ptc.py:626` still resets the worker. Have the worker report the failure stage and keep the epoch for parse and validate failures with no broker call. | Extend `tests/unit/test_repl.py` and `test_notebook_ptc_integration.py`: syntax and policy rejection preserve values and epoch; runtime failure after assignment still discards. No paid run. |
| V6 Uniform result envelope | Both `KeyError('data')` failures came from failed reads of nonexistent paths; error and blocked envelopes carry only `status` and `model_text`. Every `agent.*` result, including per-item parallel results, exposes `status`, `model_text`, `effect`, truncation and artifact references, and a `data` mapping that is empty when there is no typed detail. `_AGENT_RESULTS` becomes the single source and is table-tested against the real broker. | Zero result-shape exceptions in deterministic scenarios and in the next live trace. |
| V7 Work-batch yield tuning | Only after V1-V6. Compare `24/48` with `12/36` on Koota and Testem; a yield returns unresolved rows and the latest validation failure. Today a yield reviews one MISSING row, so tuning before V1 measures noise. | Reduce median cells or active time without lowering reward or raising uncached input; otherwise keep `24/48`. |

### P. Prompt shape

| Unit | Change | Evidence and gate |
| --- | --- | --- |
| P1 Shared doctrine | Replace `NOTEBOOK_PTC_INSTRUCTION` and the inline Prime paragraph with one 106-word doctrine appended for both implementations. Text below. | Prefix-hash tests updated once; both PTC lanes share doctrine bytes. |
| P2 Runtime appendices | About 40 words each, notebook and Prime, text below. Signatures, blocked-API lists, and replay warnings move out of the prefix. | Stable prefix per PTC lane about 300 words, from 633. |
| P3 Tool descriptions | Replace the one-sentence descriptions in `app/agent/ptc.py:847` and `app/agent/prime_ptc.py:222` with the texts below, each with one executable example. | The example runs under `tests/unit/test_example_notebooks.py` against the real broker. |
| P4 Dynamic phase hints | The first yield sets the phase to `review`, so in Koota the whole 48-cell implementation batch ran under the review rule. Remove phase-conditional text from the prefix; emit a one-line hint in `next_action` when the phase changes or a batch yields. Keep `batching_instruction` as an override field but stop appending it. | Hint text matches the ledger phase in the trace at every yield. |
| P5 Kernel manifest line | The prompt states what the kernel provides and that nothing may be installed. Text in K3. | Model never issues an install command in the next live trace. |
| P6 Cell-shape gate | Measure before reward on Koota and Testem, three paired attempts. | Single-call cell share at or below 40% (from 71%); capability calls per cell at or above 2.0 (from 1.38); read-only cells over 12k bytes of stdout at or below 5 (from 16); no rise in median uncached input. |

### S. Search

| Unit | Change | Evidence and gate |
| --- | --- | --- |
| S1 Document the virtual search | The bash tool already intercepts `search grep` and `search find` and serves them from the fff index; `agent.shell.run` reaches it. No model-facing text shows the grammar, and no run in either lane used it. Add the grammar to the notebook appendix and the bash docstring. | Search receipts appear in the next live trace; naive `grep -rn` disappears. |
| S2 Structured `agent.search` | Add `agent.search.grep(pattern, path=None, mode="literal", limit=20, cursor=None)` and `agent.search.find(...)` to the worker proxy and `agent.help()`, routed through the broker with effect none and allowed in `agent.parallel`. Return `data.matches` as records with path, line, column, definition, and content sha256, which `_MatchRecord` already carries. | Table test through the real broker; a failed search never forces reconciliation. |
| S3 Warm the index | The finder is lazy; the first query pays the scan, up to 5 s on large repos. Build it at worker start or during container preparation; the watcher keeps it fresh. | First search in a trace reports `cold_index: false`. |

Benchmark on this repository, single process: fff cold 33 ms, warm 4 ms, scoped
under 1 ms; `grep -rn` with excludes about 1 s; naive `grep -rn` including `.venv`
8 s. Per-call broker overhead of 200 to 300 ms still dominates, so the gain is
bounded pages and structured matches, not raw speed.

### K. Kernel

| Unit | Change | Evidence and gate |
| --- | --- | --- |
| K1 Declared kernel | Add `notebook_ptc.kernel` to `NotebookPtcConfig`: `python` (interpreter path, default current), `preload` (modules bound into the namespace at worker start), and `manifest` (rendered for the prompt and for `agent.help("kernel")`). Add a `ptc-kernel` dependency group in `pyproject.toml` and install it in the task image. Kernel packages are separate from the project environment. | Worker start binds the preload set; `agent.help("kernel")` lists modules with versions. Restart cost measured before and after. |
| K2 No installs during execution | In-kernel installs are already impossible (`importlib`, `subprocess`, `sys` blocked). Add a command-policy rule in the notebook profile that denies `pip install`, `uv pip install`, `npm install -g`, and equivalents through `agent.shell.run`, with a blocked envelope that says the kernel is fixed and names the manifest. Project-local installs that a task image requires stay a separate, explicit allowance. | Policy test per command family; zero install commands in the next live trace. |
| K3 First preload set | In-memory, cheap to import: `re`, `regex`, `json`, `difflib`, `textwrap`, `collections`, `itertools`, `dataclasses`, `rapidfuzz`, `orjson`, `tree_sitter` with a language pack. Retrieval is the fff index through `agent.search`, not a vector store in the kernel. Keep numpy, a dataframe library, and the K5 media tiers behind opt-in flags: libraries with their own file I/O bypass the broker's read policy and produce no receipts, because the guard only removes `open` from the cell's builtins. | Import time of the preload set under 300 ms; no ImportError cells in the next trace. |
| K4 Bypass decision | Decide explicitly whether the trusted notebook profile accepts library-level file reads (Prime's position) or fences them. Until decided, the preload set stays in-memory only. | Written decision in an ADR. |
| K5 Multimodal tier | A data-science agent that processes images, video, and audio cannot stay on stdlib. Define tiers, enabled per profile and named in the manifest: tier 0 text (K3); tier 1 numeric and images: `numpy`, `Pillow`; tier 2 video and audio: `av` (PyAV, bundles ffmpeg) or `imageio` with `imageio-ffmpeg`, `opencv-python-headless` for frame operations, `soundfile` for PCM audio, `librosa` optional because it pulls numba. Return visual results through the existing MIME display path: a cell whose final expression is a mapping keyed by MIME type becomes `display_data`, oversized bundles become artifacts. Tier 1 and 2 libraries read files themselves, so they depend on K4; until then media enters through a new `agent.fs.read_bytes` capability or through `agent.shell.run("ffmpeg ...")` in the project environment, which keeps receipts. | Image size and worker start time measured per tier; `agent.help("kernel")` names the active tier and the display protocol; a fixture cell loads a PNG, crops it, and emits `image/png` under 16k inline. |

## Proposed text

### Shared base (unchanged, 150 words)

Keep `coding_worker.instruction` from `four-tool.yaml` verbatim in every profile.

### Shared PTC doctrine (P1, both implementations)

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

### Notebook appendix (P2, P5, S1)

```text
Capabilities: agent.fs.read, agent.fs.write, agent.fs.edit, agent.shell.run,
agent.search.grep, agent.search.find, agent.mcp.call, and agent.parallel([...])
for up to four independent reads or searches. agent.help() returns exact
signatures; agent.help(name, details=True) returns one result shape;
agent.help("kernel") lists the preloaded modules. The kernel is fixed: {manifest}.
Never install packages. Direct filesystem, process, and network APIs are blocked.
For .ipynb files use nb read or nb search through agent.shell.run.
```

`{manifest}` renders from K1, for example `re, regex, json, difflib, textwrap,
collections, itertools, dataclasses, rapidfuzz 3.x, orjson 3.x, tree_sitter 0.2x`,
plus `numpy, Pillow` or `av, cv2, soundfile` when a K5 tier is enabled.
Until S2 lands, replace the two `agent.search` names with: "search files with
agent.shell.run('search grep --pattern X [--path REL] [--mode regex]') or
'search find --pattern X'".

### Prime appendix (P2)

```text
bash, asyncio, and emit are prebound; use await bash(command) for shell
commands and top-level await freely. Serializable variables are snapshotted
after each cell; open resources and background tasks may not restore. A failed
cell keeps earlier assignments. The kernel is fixed; never install packages.
```

### `execute_code` tool description, notebook (P3)

```text
Run one Python cell in a persistent CPython worker. Variables, functions, and
imports persist across cells. Capability calls return a mapping with status,
model_text, and data; data is empty on error or blocked. A failed cell discards
its own assignments.

Example: find call sites, read two known files, decide in Python, print facts.
    hits = agent.search.grep(pattern="createRemoved", path="packages/core/src", limit=20)
    a = agent.fs.read("src/config.py", limit=200)
    b = agent.fs.read("tests/test_config.py", limit=200)
    ok = [r for r in (a, b) if r["status"] == "ok"]
    lines = {r["data"]["path"]: [i for i, line in enumerate(r["data"]["text"].splitlines(), r["data"]["offset"]) if "timeout" in line] for r in ok}
    print([m["path"] for m in hits["data"]["matches"]], lines, [r["model_text"] for r in (a, b) if r["status"] != "ok"])
```

Before S2, drop the `hits` lines from the example.

### `execute_code` tool description, Prime (P3)

```text
Run one Python cell in a persistent native kernel. Variables, functions, and
imports persist across cells. Use await bash(command) for shell commands; it
returns exit_code and output. Print only the facts the next decision needs.

Example:
    r = await bash("pnpm -F core test run 2>&1 | tail -n 30")
    print(r.exit_code, r.output[-2000:])
```

### Dynamic phase hints (P4)

```text
understand: Batch independent bounded reads and searches with known inputs, then print a compact summary.
implement:  Group the reads that support one decided change; an already-decided edit and its already-selected check may share a cell when the cell stops on failure.
review:     For each unresolved criterion row, run its positive, negative, and transition probes together, including histories where the prerequisite never held.
verify:     Run already-selected formatter, type, and targeted-test commands in one cell, serially, and print every exit status.
```

### Blocked-install envelope (K2)

```text
status: blocked
model_text: Package installation is disabled. The kernel is fixed; see
agent.help("kernel"). Run project commands in the project's own environment.
```

## Sequence

1. V5, V6, S1, K2. All deterministic, no paid run, small diffs. S1 and K2 are
   text and policy only.
2. V1, V2, V3, V4 together with P1-P5, because the review hint and the doctrine
   both refer to clause rows and to `data` being always present.
3. S2, S3, K1, K3 as one runtime slice, with K5 tiers as a separate opt-in profile, with `agent.help("kernel")` and
   `agent.search` visible in the prompt only once they exist.
4. P6 cell-shape gate on Koota and Testem, three paired attempts per mode.
5. Reference-six staged confirmation: Koota first, then Testem and Textual,
   then Ofetch and Wazero as regression sentinels, notebook and Prime under
   identical doctrine. Only then consider changing the default profile.
6. V7 and K4 only if the traces from step 5 still show Testem-style churn or
   library-level file reads.

Do not change container isolation, notebook serialization, the memory backend,
or import Prime's full rlm prompt from these results.
