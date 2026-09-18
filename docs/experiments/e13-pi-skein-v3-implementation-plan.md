# Pi + Skein PTC v3: implementation and evaluation plan

Status: implementation complete for Batch A plus user-requested stdlib preflight;
focused validation passed; campaign stopped with 23 verified trials and one job-budget interruption.
See `e13-pi-skein-v3-comparison.md` for results and limitations.

## Execution revision (user instructions supersede the staging below)

- Campaign: **Pi + Skein PTC v3**, exactly the same eight task artifacts as v2,
  three fresh trials each (24 total), Muse Spark 1.3 contributor, xhigh, 32,768
  output tokens, concurrency six, no automatic retries. New result root:
  `.artifacts/e13-pi-skein-v3`. The proposed 36-trial ablation is superseded.
- Prompt uses named composable components, Code Tool's common batching/routing
  wording, and concise Skein-specific contract/examples. Completion checklist is
  implemented as an opt-in component but **off** for these 24 trials; it remains
  a separate treatment, not an additional automatically launched arm.
- Per user follow-up, stdlib preflight moves into v3: real broker signatures and
  one result schema generate validation/help/prompt contracts. Straight-line
  helper calls, known result keys, unresolved references and provable primitive
  type errors are checked before effects. Dynamic calls/branches/comprehensions
  lose information conservatively. This is not a general Python type checker.
- Full-file acquisition and checkpoint recovery remain Batch B. No replay,
  process forking, mypy, or new dependencies are added.
- Backend finding: installed Pier Docker exec already redirects subprocess
  stderr to stdout. Thus v2 separate-stderr risk is backend-dependent and does
  not by itself prove hidden Go errors in these runs. v3 still reports nonzero
  exit, separately supplied stderr and timeout independent of model selection.
- Combined output preserves the backend stdout, adds labeled stderr if supplied,
  then an exit marker; arrival ordering between separate streams is unavailable.
- Pier's outer timeout kills its Docker client. v3 uses container GNU timeout
  (TERM, kill-after 5s), a transport budget 15s longer, and a worker margin 30s
  longer. Hard transport failure explicitly reports unknown effects.
- Outcome capture is limited to 64 calls per cell, rejecting additional calls
  before effects. Each retained field is bounded; observations reserve room for
  per-call diagnostic summaries. The record remains capped at 256 KB/32 cells.
- Safe final-expression rendering supports exact built-in data; unsupported
  objects are identified by type rather than invoking arbitrary repr hooks.
- Initial cost estimate: $4–$7, based on v2's $4.4394 repriced total. Account
  starting balance: $8.594547312. Trace provider cost fields are not actual debit.



Revision 2. This revision re-sequences the work around the evidence in
`e13-pi-skein-v2-regression-analysis.md`: the pass-rate gap traces to test
coverage and stopping discipline, and the two confirmed contract defects are
dropped shell diagnostics and the shell-timeout worker discard. Stages with no
failing trial behind them (full-file acquisition, durable recovery, preflight
checking) move to a second batch. Transcript replay is removed from the recovery
design. A comparison table at the end records what changes between Code Tool,
v2, and v3.

This plan covers the Pi extension tested in E13, with shared worker fixes where
appropriate. It complements, rather than replaces, the older notebook-host plan
in `docs/ptc_improvements.md`.

## Target contract

**Structured values belong inside Python. Projection is the model-facing
presentation.** Keep helper results usable as dictionaries for batching,
filtering, and persistent reuse. Give the parent concise Markdown, not arbitrary
dictionary repr output or an unsolicited dump of every intermediate result.

The projection contains:

1. Explicitly selected evidence: print output and the final expression.
2. Mandatory operational notices: nonzero shell exit, nonempty stderr, timeout,
   incomplete acquisition, failed effects, and lost/reset state.
3. Retrieval instructions only where relevant details have been omitted.

Successful intermediate tool calls remain silent unless selected. Preserve the
difference between a Python cell succeeding, a shell command succeeding, and the
task being verified. Nonempty stderr is a diagnostic, not automatically a failure.

The shell helper additionally returns a combined stream. `bash(...)['data']`
gains an `output` key holding backend stdout followed by labeled separate stderr with a
trailing `[exit N]` line, alongside the existing `stdout`, `stderr`, and
`exit_code`. The description's examples print `output`. This removes the
stdout-only habit at its source; the mandatory notices below are the safety net
when the model selects a narrower field anyway.

Example shell projection when the cell printed only stdout:

```text
<selected stdout>

Shell command failed [exit 1]
Command: go test ./...
stderr:
compiler.go:42: undefined: methodReceiver
Full output: code(result_id="r_…", offset=0)
```

Example selected mutation and read projections:

```text
Updated src/parser.go — 12 lines added, 4 removed.
Diff: code(result_id="r_…", offset=0)

src/parser.go — lines 1–400 of 1,592 (partial).
Continue: read("src/parser.go", offset=401, limit=400)
```

These locators serve different purposes: a result ID reads retained evidence from
that execution; `read(path, ...)` acquires current file contents, which may have
changed. Do not present them as interchangeable.

## Batch A: contract fixes for the first experiment

### 1. Capture broker outcomes independently of printed output

Primary location: `scripts/pi_code_tool_harbor.py`, `_PierPtcBroker` and the
per-cell dispatch boundary.

The broker runs in the parent process, so every outcome is already visible
there. The implementation is a per-cell list appended inside each broker method
and cleared at dispatch. No stable call IDs, journal, or storage dependency is
needed for this stage; those arrive only if stage 8 later needs a journal.

- Record operation, path or command, status, exit code, timeout flag, stream
  lengths, read completeness, and the retained evidence locator for every broker
  call in the cell, in call order.
- Keep the record when Python later raises or prints only stdout. Completed
  mutations stay recorded: namespace rollback is not filesystem rollback.
- Bound the per-cell list and collapse repeated identical notices without losing
  which calls failed.
- Emit the same records into the trace so the evaluation can count shell calls
  whose diagnostics the selected output omitted.

Checks: stdout-only selection with a nonzero exit and stderr; assignment-only
failure; two shell calls with different outcomes; broker failure followed by a
Python exception. Every mandatory notice must survive in the parent observation.

### 2. Mandatory notices and compact receipts at the parent boundary

Primary locations: `_ptc_response`, `_ptc_record`, and
`scripts/pi_skein_ptc_extension.mjs`.

- Append shell diagnostics from the captured outcomes after the selected output:
  exit code, command, and stderr for any nonzero exit or nonempty stderr. Skip a
  notice only when the cell demonstrably printed `output` or `stderr` for that
  call.
- Reserve observation space for mandatory notices before allocating it to
  selected output. A large success output must never push failure status off
  the end.
- Make write and edit results compact in the observation: changed or no-op,
  path, lines added and removed, and a retained diff locator. The full diff stays
  in the Python result and the retained record.
- Show the result-ID hint only when something was omitted: truncation, a
  compacted diff, or a shell notice that references full output.
- Label command, path, and log material as data; use safe code fencing and byte
  limits.

Deferred from this stage: rendering selected helper results as Markdown using
recorded operation identity. Attributing a printed dictionary to the call that
produced it requires identity tracking inside the child process. The notices
above deliver the evidence without it. Revisit only if traces show models
printing whole envelopes often enough to matter.

Checks: snapshots of shell, read, edit, and error projections plus a multi-call
cell; large stdout cannot hide stderr or exit status; structured Python filtering
still works; arbitrary dictionaries are preserved; no secret or raw metadata
enters the text.

### 3. Separate shell timeouts from hard cell deadlines

Primary locations: `harness/ptc/repl/worker.py` broker wait and
`_PierPtcBroker.bash`. Inspect all `PersistentPythonWorker.execute` callers
before changing the shared worker contract.

Contract:

- A shell timeout returns a structured timeout result inside the cell. The
  worker and every variable stay alive. The result carries `status: "timeout"`,
  the requested budget, and any partial stdout and stderr captured.
- The hard cell deadline still terminates the worker for stuck Python, dead
  transports, or a broker that fails to return within the outer deadline.
  Recovery from that path is stage 8.

Numbers:

- `bash` default stays 120 seconds. The ceiling rises to 600 seconds, matching
  `bash_max_timeout_seconds` in the four-tool profile. Validate requested values.
- The cell deadline is derived per broker call as the requested broker timeout
  plus a 30 second margin, never less than the configured cell timeout. Recompute
  the remaining budget for every broker call so a batch of several slow commands
  cannot exhaust a fixed budget mid-cell.
- Verify that Harbor's `environment.exec` terminates the container process when
  `timeout_sec` expires. If it does not, the broker issues an explicit kill before
  returning. A lingering test run corrupts the next command's measurements.
- If termination or side effects are uncertain, report unknown effects and
  require reconciliation. Never silently retry an effectful command.
- Keep late broker replies from being consumed by subsequent calls.

Both arms failed to run Ink's full `npx ava` inside 120 seconds, so the ceiling
matters as much as the discard fix.

Checks: short synthetic command timeout with a sentinel variable surviving into
the next cell; several commands exhausting a shared budget; unresponsive broker;
infinite Python loop; late reply; verified process termination. Extend the
existing timeout tests, retaining their hard-timeout expectations where valid.

### 4. Match the observation cap and make retrieval truthful

Primary locations: worker output capture, `_ptc_record`, and `read_result`.

- Raise the model-visible observation cap from 16 KB to Code Tool's installed
  default of 50 KB and 2,000 lines, verified against the constants in
  `packages/coding-agent/src/core/tools/truncate.ts`. The model never paged a
  retained result in v2, so the cap is the only thing it sees.
- Retain oversized final-expression output before model-facing truncation, using
  safe supported types or bounded rendering. Never execute arbitrary object
  hooks merely to serialize a result.
- Page the retained record's text rather than slicing JSON into an invalid,
  supposedly complete record.
- Expose original size, retained size, truncation layer, next offset, and expiry.
  Never promise full recovery once a storage ceiling has discarded data.
- Prefer the existing per-trial log directory for oversized retained evidence if
  needed. Specify retention and cleanup; do not build another store.

Checks: oversized print and final expression, Unicode paging, record cap, expired
ID, stable repeated retrieval, and a mutation diff recovered without re-execution.

### 5. Reduce state noise and document scratch behavior

- Show newly created names only, bounded, excluding a name that is rebound to
  the result of a broker call in the same cell. Report reset, loss, and deletion
  separately. In v2 the `[state: b]` footer appeared on nearly every cell of the
  Scriggo trials, and one Tengo trial ran 117 cells with no persistent state.
- Keep `agent.state.list/describe` available for the complete live namespace.
- Replace the name-overlap reuse metric with reads of a name bound in an earlier
  cell before any reassignment in the current cell. Document limitations for
  functions, branches, and aliases. Do not call rebinding useful reuse. This
  metric ships before the first experiment because the evaluation depends on it.
- Document workspace-only helper paths and one workspace scratch location in the
  tool description. Explain shell `/tmp` access separately; do not loosen helper
  confinement. Eight v2 cells were rejected for helper writes to `/tmp`.

Checks: repeated `b = bash(...)` produces no recurring state announcement; a
meaningful new binding is discoverable; resets are explicit; scratch writes
through the documented helper path succeed and path escape remains rejected.

### 6. Align the prompt with the implemented contract

Keep static API instructions stable and short. Put session state in projections,
not rewritten system instructions. Synchronize descriptions, help, examples, and
runtime behavior.

Contract arm (ships with stages 1 to 5):

- Explain structured values inside Python versus Markdown sent to the parent.
- Demonstrate `bash(...)['data']['output']` as the default way to inspect a
  command, independent read batching, meaningful reuse, and serial mutation and
  verification dependencies. Retain read-only `agent.parallel`.
- Explain shell diagnostics, timeout state, partial reads, scratch paths, and
  retrieval locators with minimal examples.

Completion arm (separate treatment on the same build):

- Require a short requirement-to-evidence checklist covering interactions and
  negative cases, not just each feature in isolation.
- Do not finalize with required behavior listed as a known gap. Keep failing
  probe tests until resolved or justified as invalid; do not remove tests to
  obtain a green suite. Distinguish baseline environment failures from new patch
  failures.
- Preserve outstanding gaps and exact failing commands across compaction. Do not
  replace Pi compaction merely because it occurred: all three Code Tool Scriggo
  trials compacted and two passed.

## Batch B: treatments that wait for Batch A results

### 7. Make full-file acquisition distinct from paged presentation

Primary locations: broker read contract, extension description and examples,
shared filesystem write and edit boundary if a protection applies across callers.

- Add explicit full-file acquisition, for example `read(path, limit=None)`, with
  a documented memory ceiling that errors rather than silently returning a
  prefix. Preserve paged reads for inspection and bounded parent projection.
- Keep completeness, exact range, and file hash in structured results. Project a
  partial-read notice independently of the model selecting only `data.text`.
- Replace the prompt's ambiguous read, transform, write example with full
  acquisition followed by validation, or `edit` with an exact old-text match.
- Require an assertion for model-authored string replacements before whole-file
  writes. Explain that `write` replaces the complete file and partial reads must
  never be used as its source.
- Do not claim hashes prove read completeness. Avoid broad shrinkage guards that
  reject legitimate deletions.

Checks: reproduce the 700 of 1,592 line Scriggo scenario and verify the intended
workflow leaves the suffix intact; missing replacement text fails before
mutation; the full-file size ceiling is explicit; intentional complete rewrites
remain possible.

### 8. Recover committed state after hard worker loss

Handled shell timeouts preserve the live worker (stage 3). Hard cell timeouts,
and process crashes take this path. Host restart recovery would require durable storage and is not covered by the in-memory design below. Neither namespace rollback nor
any recovery mechanism undoes external filesystem or shell effects.

Contract:

- Hard worker loss restores the last successful cell's bounded plain-data
  checkpoint into a fresh worker before any new dependent work runs.
- Functions, aliases, and other unsupported objects that were not restored are
  named explicitly so the model can recreate them.
- The model sees one concise recovery notice. Checkpoint contents stay out of
  the prompt, tool-result details, and event messages.
- Full recovery of arbitrary Python state waits until traces show it is
  necessary. Transcript replay and process forking are out of scope.

Implementation:

- The crash path is an adapter-only change. Construct the worker with
  `capture_committed=True` so every successful result carries
  `checkpoint_values` and `checkpoint_omitted_names`. The Pi adapter keeps the
  latest checkpoint and its `state_manifest` in memory, and after a discard calls
  `restore_plain(values, source_cell_id)` on the fresh worker. The timed-out
  cell's partial namespace is never committed.
- The unavailable list is the difference between the pre-crash manifest and the
  manifest `restore_plain` returns. The notice is one line: reason, count
  restored, and the names that need recreating. Details carry a checkpoint ID and
  byte count only.
- Checkpoint capture serializes name by name under a cumulative 1 MB ceiling and
  is O(names × size) per successful cell. Measure it on the Scriggo namespaces,
  which reached 140 bindings, before enabling it by default. Large values land in
  the omitted list, which is acceptable.
- Audit snapshot rollback alongside this: restoring selected data after an
  ordinary runtime error must not erase previously committed helper functions.
- Keep the stage 1 outcome record for the failed cell so completed or unknown
  external effects remain visible for reconciliation after restore.

Checks: committed scalar, list, and dict survive recovery; a helper function is
reported unavailable by name; hard timeout after a file write does not repeat
that write; corrupt checkpoint fails closed; ordinary rollback preserves prior
supported bindings; normal-call cost, checkpoint bytes, and recovery latency are
recorded over increasing session lengths. Small logs and cheap normal calls are
acceptance goals, not assumptions.

### 9. Validate helper signatures before any cell effects

V2 produced one or two source-validation errors per trial, each costing one
correction round, and the failing cases were wrong keyword names such as
`bash(..., workdir=...)`. The first stage is a signature check, nothing more.

- Generate prompt signatures, help, and the validator from one description of
  the four direct helpers and their `agent.fs.*` and `agent.shell.run` aliases:
  keyword names, required arguments, and simple argument types.
- Reject a demonstrably invalid direct call before executing any statement.
  Return a short diagnostic with the cell line, the invalid argument, the valid
  alternatives, and `No code in this cell executed`. Leave state unchanged.
- Out of scope for this stage: result-key inference, scope analysis, undefined
  name detection, and any external checker. Static validation cannot prove
  arbitrary Python safe; runtime checking remains for everything else.

Checks: a cell with an initial write followed by an invalid direct call causes
zero broker operations; valid persistent variables, user functions, and dynamic
dictionaries are not spuriously rejected; false positives and preflight latency
are recorded. Evaluate as a separate contract treatment.

## What changes: Code Tool, v2, and v3

| Aspect | Pi Code Tool | Skein PTC v2 | Skein PTC v3 | Stage |
|---|---|---|---|---|
| Interpreter | Monty sandbox in Node | Resident CPython child | Unchanged | |
| State persistence | Transcript replay with cached calls | Live namespace | Live namespace plus in-memory plain-data checkpoint (Batch B) after each success | 8 |
| Failed cell | Snippet not committed | Snapshot rollback of plain data | Unchanged, with audit that helper functions survive rollback | 8 |
| Hard worker loss | Impossible; state rebuilt from transcript | All state discarded | Checkpoint restored into a fresh worker; unavailable names listed | 8 |
| Shell timeout | Python error, state kept | Kills the cell and worker at 120 s | Structured timeout inside the cell; ceiling 600 s; cell deadline derived per call | 3 |
| Shell result | One combined string with exit code | `{exit_code, stdout, stderr}` | Same dict plus combined `output`; parent appends exit and stderr notices regardless of what was printed | 1, 2 |
| Mutation receipt | Compact string | Full diff in every result | Changed, path, line counts; diff retained behind a result ID | 2 |
| Read contract | Whole file as string | 400-line pages with `next_offset` | Pages plus explicit full acquisition with a size ceiling; partial-read notice always projected | 7 |
| Pre-execution check | Full static type check | AST blocklist | Blocklist plus helper signature check with zero effects on rejection | 9 |
| Observation cap | 50 KB, 2,000 lines | 16 KB with paged record | 50 KB, 2,000 lines, matched for comparison | 4 |
| Retained record | None | 256 KB JSON, last 32, never used | Sizes and truncation layer exposed; hint shown only when something was omitted | 4 |
| State footer | None | Every cell, including `b` rebinding | New names only; resets reported separately | 5 |
| Scratch files | Read-only mount, `/tmp` via shell | Helper writes outside workspace rejected | Documented workspace scratch path; rule stated in the description | 5 |
| Prompt | Batching plus routing guidance | One batching line | Contract examples; completion checklist as a separate arm | 6 |
| Logs | 21.6 GB for 24 trials | 0.14 GB | Unchanged; checkpoint IDs only in events | 8 |

## Changesets and completion evidence

| Changeset | Batch | Dependency | Completion evidence |
|---|---|---|---|
| Broker outcome capture + mandatory notices + combined `output` | A | None | Failures survive stdout-only and assignment-only cells |
| Shell timeout inside the cell; 600 s ceiling; derived cell deadline | A | Outcome capture for telemetry | Handled timeout retains sentinel; hard timeout still terminates; process verified stopped |
| Compact receipts + 50 KB cap + truthful retrieval | A | Outcome capture | Batching intact; omitted details recoverable; cap matches Code Tool |
| State-noise fix + reuse metric + scratch guidance | A | Stable contracts | No rebinding noise; metric ships before the campaign; workspace scratch works |
| Contract prompt text | A | Stages 1 to 5 | Description, help, and examples match runtime behavior |
| Completion-checklist prompt | A, separate arm | Contract prompt | No known-gap finalization; failing probes retained |
| Full acquisition + safe editing examples | B | Projection notices | Partial-read reproduction no longer damages the file |
| Checkpoint restore after hard worker loss | B | Outcome record + timeout semantics | Supported state restored; unavailable names listed; no repeated effects |
| Helper signature preflight | B | Shared signature source | Known invalid cells perform zero effects; valid dynamic code unaffected |

Use focused tests in the existing worker test module and one adapter/projection
test module if needed. Run Python and Node syntax checks and relevant unit
checks. No new dependencies, general rendering framework, or rewrite of the
parent loop. Commit only owned files; preserve the unrelated modified metrics
artifact.

## Evaluation and rollout

1. Run deterministic contract reproductions first. They demonstrate diagnostic
   delivery, state preservation, timeout handling, and retrieval without model
   cost.
2. Freeze code revisions and configuration hashes for Code Tool, current v2, and
   each treatment. Identical model, xhigh setting, output reserve, task inputs,
   tool limits where applicable, and trial budgets. Label intentional
   differences.
3. First batch, three arms on Ink, Scriggo, Tengo, and Testem with three fresh
   trials each: current v2 as control, v3 contract (stages 1 to 6, contract
   prompt), and v3 contract plus the completion-checklist prompt. Measured E13
   per-task costs put one four-task arm near three dollars repriced, so the
   batch is roughly nine dollars. Add Koota to the completion arm. Preserve prior
   results as historical; do not mix them into fresh denominators or treat trial
   numbers as paired seeds.
4. Batch B treatments run one at a time against the v3 contract build only if
   the first batch leaves a gap they could explain. A combined candidate can
   measure product improvement but cannot attribute it to one component.
5. Record exact pass, F2P and P2P, model steps, cached, uncached, and output
   tokens, observed billing and separately repriced cost, model, tool, and
   end-to-end latency, timeout and reset counts, lost bindings, partial reads,
   visible output bytes, truncation, retrieval, and known gaps at termination.
   Report by task and trial, not only pooled averages.
6. Count broker calls with nonempty stderr or nonzero exit whose selected output
   omitted them, then verify the projection supplied that evidence. The v2
   stdout-only counts describe risk, not confirmed hidden failures. Record
   pipeline commands: shell-level exit zero is not proof every pipeline component
   passed.
7. Estimate each batch from measured per-task costs and trial count, including
   compaction and a bounded retry allowance. State the remaining balance and the
   expected cost range before launching. Distinguish provider-reported usage
   prices from actual OpenRouter debits. Existing run authorization is not a
   reason to launch every ablation automatically.
8. Report uncertainty with task-level comparisons; do not use an arbitrary
   promotion threshold. Require deterministic contract fixes to pass, investigate
   any new quality regression, and check that saved latency is not premature
   exit. Validate the combined candidate on held-out manifest tasks before broad
   claims.

The diagnostic panel is informed by observed failures and is therefore a
development set. A larger diverse evaluation follows successful contract checks
and diagnostic evidence; it cannot substitute for isolating the mechanisms above.

## Validation evidence for this implementation

- Existing worker suite plus v3 contract checks: 34 tests. They cover zero-effect
  rejection for the four requested error classes, conservative dynamic code,
  per-call shell budgets, preserved sentinel, hard timeout, compact receipts,
  Unicode retained values, all 64 failure notices, and output byte/line bounds.
- Docker reproduction: partial output captured, timed-out command PID gone,
  sentinel retained in the live worker. GNU timeout verified in all eight images.
- Offline v2 replay: 3,463 execution cells (3,464 code calls including retrieval);
  16 flags, all originally errors, including syntax failures and one bad result
  key. Cross-cell names reconstructed; types deliberately unknown. No evidence
  here supports adding mypy. No historical code or effects were executed.
- Prompt assembly check: checklist absent by default and present only when
  explicitly enabled. Main component text approximately 1,518 characters before
  runtime-generated signatures/schemas. Node syntax and Ruff checks passed.
- The original worker snapshot rollback can still lose unsupported bindings on
  an ordinary exception. v3 now names deleted/unavailable bindings; full snapshot
  audit/recovery remains the explicitly deferred Batch B.
- Exact matching compacts printed mutation envelopes; nested or transformed
  envelopes keep their selected representation. No object-identity framework.
- State reuse telemetry is a conservative straight-line AST measure, not a
  claim of dynamic dataflow coverage through functions, branches or aliases.
