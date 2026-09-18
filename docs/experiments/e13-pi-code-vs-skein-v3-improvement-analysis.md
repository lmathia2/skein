# Closing the remaining Pi Code Tool gap

## Verdict

The broad executor gap is now small and uncertain. Pi Code Tool scored 21/24 in the original E13 run; Skein PTC v3 produced 19 passes from 23 verified trials, with one interrupted Scriggo trial. On the seven tasks completed by every arm, Pi Code Tool scored 19/21 and v3 scored 18/21. Repriced cost was $3.479 versus $3.496, and median agent latency was 15.63 versus 16.08 minutes. Those are one-pass, 0.5%-cost, and 2.8%-latency differences, respectively.

The fresh four-task checklist experiment reinforces the uncertainty. Checklist-off v3 scored 9/12, exactly the historical Pi Code Tool pass count on Ink, Obsidian, Scriggo, and Textual combined. These are independent samples rather than paired seeds, so equality does not prove equivalence. It does show that a large, stable PTC execution penalty is no longer supported by the data.

The remaining failures are dominated by verification and stopping behavior:

- numeric edge cases were “manually verified” with probes that did not assert the held-out values;
- broad existing suites passed while requested interactions remained wrong;
- one checklist-on Scriggo rollout explicitly finalized with known gaps despite a prompt forbidding that;
- another claimed interface dispatch worked while returned strings were empty;
- example layout was exercised functionally without asserting positive viewport geometry.

The next version should preserve resident CPython and compact traces. It should improve helper ergonomics and add a bounded, enforceable evidence review. Transcript replay is not needed.

## What v3 already fixed

These are no longer leading explanations for the quality difference:

| Earlier defect | v3 status | Evidence |
|---|---|---|
| Shell stderr/exit could be omitted | Fixed | Combined output plus mandatory nonzero/stderr notices; 38 notices in the stopped v3 run. |
| Shell timeout killed the worker | Fixed | Shell deadline precedes cell deadline; zero hard worker timeouts in 23 saved v3 trials. |
| Failed cells could leave ambiguous namespace state | Fixed | Snapshot rollback is configured at the shared worker boundary. |
| Prompt omitted helper signatures and result keys | Fixed | Signatures and schemas are generated from the broker contract. |
| Wrong keys/arguments reached runtime | Mostly fixed | Conservative stdlib preflight blocks known bad keys, arguments, names, and simple type mismatches. |
| JSON result envelope polluted every observation | Fixed | The parent receives plain text; telemetry stays in tool details. |
| Mutation cadence lagged Code Tool | Closed | V3 records 447 edits and 172 writes in 23 traces, comparable to Code Tool's 393 and 202 in 24 traces. |
| PTC did less testing | Not supported | V3 and earlier Skein runs issue at least as many recognized test commands on the affected tasks. |

V3's model-facing projection is already separated from machine telemetry in `scripts/pi_code_tool_harbor.py`. The extension returns `value.text` to Pi and stores status, state, broker outcomes, and result IDs in `details`. The worker keeps state outside the transcript, avoiding Pi Code Tool's 21.6 GB event-log expansion.

## Remaining implementation differences

### 1. Plain observations still sit behind structured helper values

The parent sees plain text, but code inside the cell still receives nested dictionaries:

```python
print(bash("pytest -q")['data']['output'])
print(read("src/app.py")['data']['text'])
```

Pi Code Tool exposes helpers as plain Python functions returning strings. Its Harbor `bash` string happens to contain JSON, so it is not perfectly clean, but it still avoids `['data'][...]` traversal. V3 preflight prevents many mistakes, yet the latest traces still contain 46 source-validation rejections across the checklist arms; 42 are blocked imports and the remainder include bad keys, arguments, and names. Every rejected cell consumes a model round even when it prevents a side effect.

Use ergonomic direct aliases while retaining structured `agent.*` operations:

```python
read(path, offset=1, limit=400) -> TextResult
bash(command, timeout_seconds=120) -> ShellResult
edit(...) -> MutationResult
write(...) -> MutationResult
```

`TextResult` and `ShellResult` should print like their useful text while exposing metadata as attributes (`complete`, `next_offset`, `exit_code`, `timed_out`). This keeps provenance and preflight typing without requiring nested key access. `agent.fs.*` can continue returning dictionaries for advanced workflows and compatibility.

This is the closest remaining executor-contract match to Pi Code Tool. It should be implemented before changing state or batching behavior.

### 2. The prompt is compact but underspecifies blocked imports

The prompt says effectful imports are blocked but names only the preloaded modules. In the stopped v3 traces, 34 source-validation failures are blocklist violations. The checklist arms add 42 more, mostly attempts to import `os`, `pathlib`, or `subprocess`.

Add one precise sentence generated from the real guard:

> No `os`, `pathlib`, `subprocess`, or direct file/process APIs; use `read`, `bash`, `edit`, and `write`.

Do not expand this into a long sandbox manual. The exact examples address the observed errors. Keep the generated signatures as the source of truth.

### 3. Prompt-only completion advice is not a gate

The checklist experiment is direct evidence. Appending completion advice changed 9/12 passes to 8/12, while lowering uncached input by 14%. It did not prevent either Scriggo failure. One failed rollout ended with a literal “Known gaps” section; another claimed interface dispatch worked after testing only broader cases.

Replace optional prose with one bounded review turn. When the model first tries to finish, the Pi extension should queue at most one follow-up if either condition holds:

- the final response declares a known gap, remaining failure, unimplemented requirement, or failing probe;
- no successful verification evidence was recorded after the last workspace mutation.

The follow-up should be short:

> Reconcile each requested behavior with exact observed evidence. Resolve any declared gap. For behavior not covered by an existing test, run a minimal assertion with expected and actual values. Then finish once.

Pi extensions can queue a follow-up message after `agent_end`; this makes the review an actual additional model turn rather than ignorable tool-description text. Cap it at one turn so it cannot loop. This should be a separate arm because it adds tokens and latency.

The mutation/test condition needs a small evidence ledger. Record successful `bash` verification commands and the workspace revision/diff identity they verified. Direct `edit` and `write` mark the ledger dirty. For shell commands, start conservatively: recognize test/typecheck/lint commands already used by the analysis scripts and mark unknown shell calls as potentially mutating. Avoid a general workflow engine.

### 4. Verification needs expected values, not more test volume

The failures are not caused by absent testing:

- Ink failures passed 23/25 tests and repeatedly claimed fixed/`fr`/`auto`/`minmax` checks. The missing evidence was exact allocation at boundary values.
- Textual passed follow-state behavior but failed the example's viewport-height condition.
- Scriggo passed broad `go test ./...` runs but missed method-expression and interface-return semantics.

Add a `verify` helper as a thin wrapper around `bash`, not a new subsystem:

```python
verify(command, must_contain=(), must_not_contain=(), timeout_seconds=120)
```

It should return/print the same `ShellResult`, raise on a nonzero exit or unmet literal expectation, and append a compact evidence receipt to the ledger. Literal expectations cover the observed failure modes (`width=15`, `error: something failed`, positive geometry) without embedding benchmark knowledge. The model still authors the probe from the task specification.

Do not require `verify` for every command. Require one post-mutation verification receipt before the bounded review can accept completion. Existing test commands with exit zero can count automatically; `must_contain` is for new behavior lacking a test.

### 5. Result retrieval is correct but effectively unused

V3 retains 256 KB for the last 32 cells and shows a `result_id` when details are omitted. In the stopped run the model never paged retained results; the checklist experiment also has zero retrievals despite 719 truncation flags across both arms. Increasing retention or replaying transcripts will not help if the model never follows the route.

Keep the store, but make omitted evidence actionable in the observation:

```text
[omitted: 18,430 bytes; tail contains 3 failure lines]
more('r_...', offset=...)
```

Expose `more` as the short alias for the existing retrieval path. When shell output is truncated, preserve both a head and tail before offering pagination, because compiler/test summaries are normally at the end. This is more useful than raising the cap and keeps token use bounded.

No failing trial has yet been proven to depend on hidden truncated output, so this is below the evidence gate in priority.

### 6. State durability is adequate for current failures

Resident state remains more fragile than transcript replay after a hard process loss, but v3 observed zero hard worker timeouts. Snapshot rollback handles ordinary failed cells, shell timeouts preserve the worker, and continuous Pi traces now survive parent timeout/cancellation. None of the remaining verifier failures is linked to lost variables.

Do not add transcript replay or a general checkpoint journal. If real worker-loss frequency rises, persist only the latest successful pickle-safe snapshot plus the small state manifest. Restore that snapshot after worker replacement. This preserves bounded state without replaying every cell or serializing it into every Pi event.

### 7. Batching and variable reuse are not priority gaps

V3 batching remained around 11.6% of cells in the stopped run, close to earlier arms. In the checklist experiment, multi-helper cells were 7.2% off and 5.7% on. Binding reuse was low by the conservative metric, but the metric intentionally misses functions, loops, branches, and aliases. More importantly, no failure maps to absent batching or lost intermediate values.

Keep the current one-line batching guidance. Do not add verbose state footers or force artificial cross-cell reuse. Optimize batching only if host-call latency becomes a measured bottleneck.

## Ranked implementation plan

| Rank | Change | Expected benefit | Risk / cost | Validation |
|---|---|---|---|---|
| P0 | Ergonomic string-like direct aliases; structured `agent.*` remains | Fewer contract errors and less cell boilerplate | Small worker/preflight change | Offline replay plus focused helper-contract tests |
| P0 | Exact blocked-import sentence generated from guard | Removes the largest avoidable rejection class | Tiny prompt change | Replay 76 observed blocklist attempts; prompt snapshot |
| P1 | One bounded evidence-review turn at first completion | Prevent declared-gap and no-post-edit-verification exits | Extra model turn only when triggered | Scriggo, Ink, Textual three trials each; measure trigger precision/cost |
| P1 | Minimal `verify` wrapper and evidence ledger | Converts informal probes into explicit assertions | Small broker/extension state | Unit test unmet expectations; inspect receipts in traces |
| P2 | Head+tail truncation and `more(...)` alias | Better access to long compiler/test output | Observation formatting change | Synthetic long output; one targeted usability run |
| P3 | Latest snapshot recovery after actual worker loss | Durability without replay | Serialization edge cases | Fault-injection test, not a paid benchmark first |

Do not bundle P0 and P1 into one paid arm. First run offline replay and focused contract tests for aliases/import guidance. Then compare current v3 against the evidence-review arm. If both are bundled, a quality change cannot be attributed.

## Recommended experiment

The cheapest useful next paid test is not another 20-task benchmark. Run the evidence-review arm only on the three diagnostic tasks where verification failed: Ink, Scriggo, and Textual, three trials each, against frozen checklist-off v3. That is 18 trials total. Obsidian no longer diagnoses the problem: both checklist arms passed 3/3.

Primary outcomes:

- exact pass count and mean F2P/P2P;
- proportion of first completions that trigger review;
- fraction of triggers that lead to a new mutation or stronger assertion;
- finalization with declared gaps;
- post-mutation verification receipt present;
- uncached/output tokens and added review-turn latency;
- provider-wait time separated from agent work.

Promotion criteria should be practical: at least two additional passes across 18 trials, no P2P regression, no more than 15% higher repriced cost per pass, and zero final responses that still declare a required known gap. If it fails, remove the review gate and keep only the ergonomic aliases.

## Bottom line

Skein PTC no longer needs to become Pi Code Tool internally. It already runs as a Pi extension with the same agent loop, model, provider, and task setup. Resident CPython gives much smaller traces and cheap calls; transcript replay would discard that advantage without addressing the observed semantic failures.

Close the remaining gap at two narrow boundaries: make direct helper results as easy to use as strings, and turn completion evidence into one enforceable review turn. Everything else—more batching prose, richer state footers, larger result caps, mypy, or transcript replay—has weaker evidence and should wait.
