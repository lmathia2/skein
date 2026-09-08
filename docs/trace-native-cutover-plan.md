# Skein: review findings and trace-native cutover plan

> Status: proposal, 2026-09-06. Reviewed at `main` @ `afcf3cd`; live PTC
> execution update recorded 2026-09-07 through `48e2e3b`.
> Targets: [trace-native harness ADR](adr/trace-native-harness.md),
> [context and memory ADR](adr/context-and-memory.md),
> [trace-native design](design/trace-native-repl-agent.md) tenets T1-T17.
> Evidence: DeepSWE-8 traces for Skein (`skein-eval-results/skein-muse-spark-1.3-contributor-deepswe-8-sequential-20260906`)
> and mini-swe-agent (`skein-eval-results/mini-swe-agent-muse-spark-1.3-contributor-deepswe-8-sequential-20260905-uncapped`).

## 1. Summary

The ADR's thesis is that one programmable code-mode tool plus one trace-native
store is the sufficient model-facing substrate, with the host keeping policy,
approvals, sandbox, cancellation, verification, and durability. The repository
already contains every primitive the ADR names. The problem is direction: the
JSONL task stream and nine SQLite stores are the authorities today, while the
canonical ledger, the view runtime, and the prompt manifest are shadows that
nothing live reads.

The DeepSWE-8 traces show that the harness, not the model, is where Skein's time
goes. Quality equals mini-swe-agent (2/8 each, same two tasks), cost is lower
($0.66 vs $1.00) because the cache-read ratio is higher (95.8% vs 90.3%), but
wall time is 2.55x longer (16,227 s vs 6,354 s). About 12,900 of those seconds
were spent after the first implementation pass had finished, re-entering the
worker against a verifier that could not pass.

Skein's central problem is not patch quality or cache behavior. It is
control-flow discipline around verification, followed by conversation
discontinuity. The plan therefore has three parts, and only the first two are
committed:

1. **Now.** Fix the command-policy bypasses, replace the stagnation guard with
   an independent verification counter, make verification baseline-relative
   and memoized, accept targeted tests as behavioral evidence, repair claim
   propagation. No architecture change. Rerun DeepSWE-8 before anything else.
2. **Next, after that measurement.** Run the worker as one continuous ADK
   conversation with program-driven compaction. This explains the repeated file
   discovery and the 1,574 versus 705 model-call gap, but it must follow
   verification repair or it optimizes an invalid loop and makes the ablation
   uninterpretable.
3. **Only with independent evidence.** The authority inversion (ledger as the
   only harness write path, views as the only derivation, one broker, notebook
   as the session document). Each of those slices is gated on its own
   correctness or performance evidence, not on the plan's existence. Do not
   start the ~15,000-line cutover on the strength of this document.

## 2. Baseline health

### 2.1 Live notebook-PTC execution update (2026-09-07)

The corrected six-task four-tool run and the clean PTC+JSONL v4 run used the
same `meta/muse-spark-1.3-contributor` provider defaults, task set, one attempt,
zero retries, and sequential execution. The PTC run is at `e63c24f`; its raw
artifacts are in
`skein-muse-spark-1.3-contributor-deepswe-remaining-6-ptc-jsonl-v4-20260907`.

| Measure | Four tools | PTC+JSONL v4 | Change |
| --- | ---: | ---: | ---: |
| Official rewards | 4/6 | 2/6 | -2 |
| Model calls | 479 | 429 | -10.4% |
| Model-visible tool calls | 533 | 442 | -17.1% |
| Agent wall time | 5,180 s | 4,921 s | -5.0% |
| Model latency | 3,108 s | 2,994 s | -3.7% |
| Input tokens | 29.1M | 43.2M | +48.1% |
| Uncached input tokens | 926k | 1.66M | +78.8% |
| Output tokens | 305k | 301k | -1.2% |
| Provider cost | $0.210 | $0.309 | +47.0% |

PTC is operational: all six tasks completed their notebook lifecycle, nested
capabilities shared the normal broker, failed cells did not dirty later epochs,
notebooks snapshotted, and two tasks received full official reward. It also
reduced aggregate calls. It does **not** pass the promotion gate: reward fell,
input/cost rose, and no compaction occurred while per-task peak context reached
81k-228k tokens. The four-tool profile therefore remains the default.

The first PTC run exposed a separate harness defect that invalidated the old
claim that trace persistence was always negligible. `LedgerBackedEventStore.read`
repaired the complete operational JSONL stream into the canonical JSONL ledger
on every notebook projection. Canonical append idempotency itself rescans JSONL,
so the combined path was quadratic. Commit `e63c24f` repairs each task once and
keeps subsequent appends dual-written. Matched late-cell measurements were:

| Task | PTC v3 last-10 materialization | PTC v4 last-10 | Improvement |
| --- | ---: | ---: | ---: |
| kombu | 2,763 ms | 29.6 ms | 93x |
| koota | 12,249 ms | 49.0 ms | 250x |
| ofetch | 1,799 ms | 25.0 ms | 72x |

Across all v4 tasks, final last-10 means were 20-49 ms and maxima were 26-107
ms at 255-647 events. The notebook reducer, canonical encoding, atomic write,
and fsync were not the bottleneck; making them asynchronous would retain the
bad algorithm and weaken durability. One-time read repair is the root fix.

There is no existing flag that directly reduces inner PTC model calls.
`context.window_management` bounds reconstructed outer work packets; it does
not compact the ADK tool-call history accumulated inside one worker invocation.
PTC call reduction comes from composing already-known capability operations in
one Python cell. Stronger prompt pressure reduced Wazero from 40 calls to 26 and
32 in two repeats, but both repeats lost the same two regression tests and
scored zero. That wording was backed off in `48e2e3b`; only exact result-shape
guidance and the explicit `open()` prohibition remain.

The next isolated treatment is PTC-only bounded inner history: retain recent
exact Python call/response pairs, replace older selected output bodies with a
deterministic ledger/notebook reference, and measure quality before enabling it.
Do not enable the broad `context-ptc.yaml` bundle as a shortcut because it
changes retrieval, notes, reconstruction, and windows together and cannot
attribute a result.

| Check | Result |
| --- | --- |
| Ruff | clean |
| pytest (700 tests) | pass locally |
| pyright `app harness` | 12 errors, all missing optional extras (`pier`/`harbor`, `lancedb`, `pyarrow`) |
| CI `deterministic-tests` on main | failing on every push since 2026-09-01 |
| CI `Integration` on main | passing |

CI first failed on pyright for the optional `lancedb` import, and now fails
earlier at collection because `tests/unit/test_harbor_adapter.py` imports the
`harbor` extra that CI never installs.

Production Python is 30,328 lines in 27 packages; the terminal client is 1,512
lines of TypeScript. Top-level dead code is small (`ProgramCatalog`,
`SummaryCache`). The reduction opportunity is in opt-in feature branches that are
off by default and whose promotion gates the TODO says have not run.

## 3. What the traces say

Eight DeepSWE tasks, one attempt each, same model
(`meta/muse-spark-1.3-contributor` via OpenRouter). Skein ran the four-tool
profile at revision `23cd682` with `max_iterations=24` and a 5,400 s wall cap;
mini-swe-agent 2.4.6 ran with one `bash` tool and no cost cap.

### 3.1 Totals

| Measure | Skein | mini-swe-agent |
| --- | ---: | ---: |
| Tasks passed (reward 1) | 2 (koota, ofetch) | 2 (koota, ofetch) |
| Agent wall time, all tasks | 16,227 s | 6,354 s |
| Model calls | 1,574 | 705 |
| Time in model calls | 8,708 s | ~4,450 s |
| Time in tools + verification | 12,773 s | ~1,870 s |
| Input tokens | 81.6M | 79.4M |
| Cache-read tokens (share) | 78.2M (95.8%) | 71.7M (90.3%) |
| Uncached input tokens | 3.4M | 7.7M |
| Output tokens | 787k | 455k |
| Reasoning tokens | 317k | 234k |
| Provider cost | $0.66 | $1.00 |
| Median model-call latency | 2.7 s | 2.7 s |
| Tool calls | 1,983 (983 bash, 473 read, 190 edit, 50 write, 151 verify) | 705 (all bash) |

### 3.2 Per task

| Task | S reward | M reward | S wall s | M wall s | S iters | S calls | M calls | S verify s | S bash s | S out tok | M out tok | S ending |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| boa | 0 | 0 | 5,400 | 1,582 | 6 | 218 | 185 | 1,816 | 2,620 | 71k | 74k | timeout |
| helm | 0 | 0 | 2,268 | 1,301 | 2 | 132 | 111 | 0 | 1,283 | 68k | 66k | complete |
| kombu | 0 | 0 | 2,732 | 477 | 24 | 451 | 65 | 10 | 303 | 191k | 48k | iteration cap |
| koota | 1 | 1 | 1,468 | 1,270 | 2 | 166 | 162 | 41 | 331 | 96k | 106k | complete |
| ofetch | 1 | 1 | 3,237 | 467 | 24 | 253 | 43 | 595 | 675 | 188k | 40k | iteration cap |
| testem | 0 | 0 | 5,400 | 493 | 21 | 234 | 76 | 2,638 | 1,833 | 86k | 48k | timeout |
| textual | 0 | 0 | 681 | 425 | 2 | 67 | 41 | 7 | 131 | 53k | 44k | complete |
| wazero | 0 | 0 | 441 | 339 | 2 | 53 | 22 | 1 | 94 | 34k | 30k | complete |

Cost of the first iteration versus the second (the counterexample-review pass):

| Task | Iter 1 s | Iter 1 calls | Iter 2 s | Iter 2 calls | Iter 2 out tok |
| --- | ---: | ---: | ---: | ---: | ---: |
| boa | 2,063 | 110 | 599 | 42 | 14k |
| helm | 1,965 | 106 | 299 | 26 | 11k |
| kombu | 694 | 74 | 183 | 25 | 13k |
| koota | 1,161 | 128 | 262 | 38 | 18k |
| ofetch | 377 | 36 | 241 | 19 | 16k |
| testem | 744 | 65 | 316 | 14 | 6k |
| textual | 564 | 53 | 95 | 14 | 7k |
| wazero | 263 | 29 | 164 | 24 | 11k |

### 3.3 Root causes, ranked by measured cost

**1. The verifier demands something the model cannot produce, and the loop
cannot notice.** (~12,900 s, ~870 model calls)

- When the workspace changed, required strength is `behavioral`
  (`harness/verification/contracts.py:41`), which only a `test` or `custom`
  category command satisfies, and the command is the repository's discovered
  whole-suite runner, which must exit zero.
- ofetch: `npm run test` runs `pnpm lint && vitest --coverage`; 3 tests fail on
  every one of 23 runs while the benchmark's own verifier scores 47/47.
- testem: one mocha test fails 19 times.
- kombu: no test command discovered; strongest possible check is `py_compile`;
  unpassable from iteration one.
- boa: `cargo clippy --all-targets --all-features` fails at baseline.
- There is no baseline run, so pre-existing failures, coverage gates, and lint
  debt are indistinguishable from regressions.
- `recommended_next_action` is "Add and pass a trusted behavioral verification
  command", which the model cannot do because trusted commands come from
  discovery.
- `claimed_evidence` is `[]` in all 96 verification reports ("No model
  completion claim recorded"), so completion claims never reach the verifier.
- The repeated-failure guard added in `afcf3cd` resets on any novel tool call
  (`harness/state/progress.py:107`), so nothing stops the loop before
  `max_iterations`.

**2. Every iteration is a fresh conversation.**

- The worker runs as a workflow node in ADK `single_turn` mode, which sets
  `include_contents='none'` (`google/adk/workflow/_llm_agent_wrapper.py:387`).
- Input tokens drop to ~4k at each iteration boundary and climb back to
  33k-85k as the model re-reads files. The packet carries a progress list and
  file names, not evidence.
- mini-swe-agent keeps one continuous conversation for 185 calls with cache
  hits throughout.

**3. Verification re-runs the full suite on an unchanged workspace.** (5,107 s)

- ofetch ran `npm run test` 23 times; iterations 2-7 carried the identical
  workspace fingerprint `a346b5e…`.
- testem spent 2,638 s and boa 1,816 s in verification.

**4. The counterexample review is a second full iteration with a cold
context.** 95-599 s and 14-42 calls per task; 25-60% of wall time on the four
clean tasks. It changed no outcome on these eight tasks.

**5. Twice the tool calls for the same work.** Part of that is cause 2; part is
four narrow tools versus one shell the model composes in. This is the one-tool
ablation the ADR asks for. It should not run until causes 1-3 are fixed, or it
will measure the loop rather than the tool surface.

Projection, not a result: removing the unpassable loop and memoizing
verification takes ~12,000 s out of the eight-task total, which would put Skein
at or below mini-swe-agent's wall time with the same reward and lower cost. The
continuity change is what then reduces output tokens and model calls.

### 3.4 Where per-call harness time goes (measured 2026-09-07)

Question asked: can logging and writing be made async to speed the harness up?
The original four-tool measurements below correctly show that individual event,
receipt, and trace appends are not worth making asynchronous. The later PTC run
found a different algorithmic problem in canonical read repair, documented in
section 2.1; fixing that repeated full-stream work produced the material gain.
The remaining synchronous cost in the four-tool path is workspace fingerprinting
and, under Harbor, container round trips.

Local profile on a 384-file clone of this repo (`create_adk_tools`, real
receipts/traces/metrics stores, cProfile over ten `bash` calls):

| Operation | Median | Notes |
| --- | ---: | --- |
| `read` (no receipt) | 0.8 ms | |
| `bash "true"` without `operation_id` | 4.5 ms | shell spawn |
| `bash "true"` through `_mutate` | 112 ms | 96 ms is two `fingerprint()` calls = six git subprocesses |
| `edit` / `write` through `_mutate` | 91-99 ms | same two fingerprints |
| `fingerprint()` alone | 45 ms | `git rev-parse` 10 ms + `git diff --binary HEAD` 24 ms + `git ls-files --others` 11 ms + reading every untracked file |
| receipt `begin` + `finish` | 0.7 ms | fresh SQLite connection each, still sub-ms |
| trace span append | 0.3 ms | |
| event-store append at 240 / 1,000 / 3,000 events | 0.4 / 1.5 / 4.4 ms | O(n) re-read, harmless at these sizes |
| `redact_text` 1 MB | 42 ms | runs on full output before truncation |
| FFF `refresh()` after a mutation | ~0 ms | on this repo |

Per-tool durations in the eval (Harbor, all eight tasks, `tool_usage`):

| Tool | n | p10 | median | p90 | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `bash` | 983 | 253 ms | 528 ms | 11.5 s | 7,269 s |
| `verify:test` | 49 | 15.4 s | 34.9 s | 166 s | 3,962 s |
| `verify:lint` | 50 | 6.8 s | 9.5 s | 57 s | 1,132 s |
| `edit` | 190 | 957 ms | 981 ms | 1.2 s | 196 s |
| `read` | 473 | 325 ms | 360 ms | 386 ms | 169 s |
| `write` | 50 | 509 ms | 567 ms | 777 ms | 31 s |

Under Harbor every `read` is a `download_file` round trip (~350 ms floor) and
every `_mutate` does two `_snapshot()` calls, each one container exec that
runs `sha256sum` over every tracked and untracked file
(`harness/evals/harbor.py:302-311`). An `edit` is therefore roughly download,
`is_file`, upload, and two full-tree hashes: ~1 s. Fixed per-call harness
overhead in the eval is about 650 s of 16,227 s, or 4%. Verification is 31%,
model-driven `bash` (builds and tests the model chose to run) about 43%, model
latency the rest. The per-call overhead is worth fixing but it is not the lever;
the loop and the repeated verification are.

Concrete per-call fixes, in order of value:

1. **Fingerprint once per mutating call, not twice, and cache across calls.**
   Within a task only the harness's own tools and the verifier mutate the
   workspace, so `workspace_before` is the previous call's `workspace_after`.
   Keep the last fingerprint in memory, invalidate on any mutation-capable
   call, and compute `workspace_after` only for `edit`, `write`, and `bash`.
   Six git processes per call become one, or zero for repeated reads.
   (`harness/tools/adk_adapter.py:643, 682`)
2. **Cheaper fingerprint.** Replace `git diff --binary HEAD` (renders the
   entire diff) with `git status --porcelain=v2 -z` plus hashing only the
   modified and untracked files it lists. One process instead of three, and
   cost proportional to the change, not the repository.
   (`harness/environment/runtime.py:55-66`)
3. **Harbor snapshot proportional to change.** Same idea in the container:
   `git status --porcelain -z` and hash only listed files, in one exec. Cuts
   `edit` from ~1 s to ~0.4 s and `bash` fixed cost by about half.
   (`harness/evals/harbor.py:302`)
4. **Defer `workspace_after` off the critical path.** The model waits on the
   tool result, not on the receipt. Return the result, compute
   `workspace_after` in the background, and have the next tool call await it
   before its own `workspace_before`. This is the one place where "make it
   async" saves wall time: ~50 ms locally, ~300 ms under Harbor, per call.
5. **Redact after truncation with a margin.** Redact the kept window plus a
   few KB on each side instead of the full output; keeps correctness for
   secrets straddling the cut and removes the 42 ms/MB on large outputs.
6. **Batch Harbor file operations.** `atomic_write` does `is_file`, download,
   upload as separate round trips; one exec with a small shell script does
   all three.

Not worth doing: async SQLite writes (sub-ms), async JSONL append (4 ms at
3,000 events), batching trace spans, pooling connections. These would save
under 2 ms per tool call.

#### Why git is in the execution path at all

The harness uses the working tree's git state as its oracle for three
questions: did anything change (`fingerprint()`: `rev-parse`, full
`diff --binary HEAD`, `ls-files --others`, plus reading every untracked file),
what files exist (`manifest()`: `ls-files`, `rev-parse`, `branch`, `status`,
plus a walk), and what the task changed (`changed_paths()`: `diff --name-only`,
`ls-files --others`). It asks the first question on every receipt
(`adk_adapter.py:643, 682`) and four to six more times per iteration
(checkpoint, validation memo before/after, baseline validity, answer fence,
verify trigger, init divergence). The receipt fingerprints are consumed only by
`validate_recovery_evidence` (`harness/state/recovery.py:59-68`), reachable
under `adk.recovery: safe_auto` or same-invocation crash resume; in the default
profile they are written and never read.

Design change, not a micro-optimization:

- The harness already knows what `edit` and `write` changed (path and hashes
  are on the receipt). Only `bash` and validation commands can change arbitrary
  files, so change detection is needed after those, not after every call.
- One oracle: `git status --porcelain=v2 -z` (one process, ~10 ms) plus
  hashing only the listed files. Cost proportional to the change.
- One cached fingerprint per task, invalidated when a mutation-capable tool or
  a validation command completes; every workflow check reads the cache.
- Receipt `workspace_before/after` populated only when a consumer exists
  (`safe_auto`); otherwise the receipt carries the edit/write hashes it already
  has and a single post-`bash` fingerprint from the cache.
- `manifest()` computed once per task and refreshed only when the cached
  fingerprint changed since the last manifest.

Result: from ~6 git processes per tool call plus ~20 per iteration to one
`git status` after each `bash` or validation command.

## 4. Confirmed defects (code review)

Each item was verified against the code; the policy bypasses were reproduced by
running `ApprovalPolicy().decide`.

| # | Severity | Defect | Location |
| --- | --- | --- | --- |
| 1 | HIGH | Command policy auto-runs network, publish, and destructive commands: `git -C . push origin main`, `ls & curl …`, `echo $(curl …)`, `find . -exec rm -rf {} +`, `find .. -delete` all classify `read_only`. Git subcommand is read from `tokens[1]`; splitter ignores bare `&` and substitutions. | `harness/safety/approval.py:129-168` |
| 2 | HIGH | An expired approval permanently blocks that command for the task: `INSERT OR IGNORE` on `(task_id, fingerprint)`, and both executors treat `expired` as terminal. | `harness/approvals/store.py:96`, `harness/tools/adk_adapter.py:565`, `harness/verification/managed.py:107` |
| 3 | HIGH | Repeated-verification guard resets on any novel tool call because it shares `no_progress_count` with tool fingerprints. | `app/agent/workflow.py:803`, `harness/state/progress.py:97-113` |
| 4 | HIGH | Verifier has no baseline; whole-suite commands that fail at base revision make completion unreachable (section 3.3). | `harness/verification/contracts.py`, `managed.py`, `discovery.py` |
| 5 | MED | First-event liveness retry always fails under the default config: the retry recreates the execution with status `running`, which the factory treats as recovery and rejects. | `harness/server/runtime.py:373-377, 964` |
| 6 | MED | O(n²) event reads: `JsonlEventStore.append` re-parses the whole file; the workflow reads the stream ≥8 times per turn; with canonical memory on, every read re-imports every event. | `harness/state/event_store.py:66-73`, `harness/ledger/shadow.py:22-28` |
| 7 | MED | Sandbox timeout orphans grandchildren (`shell=True` without `start_new_session`). Correct `killpg` code exists unused. | `harness/sandbox/local.py:98`, `harness/environment/local.py:280-300` |
| 8 | MED | Scope and verification-level fields on `TaskRequest` are never populated by the server; `check_scope` is a no-op. | `harness/models/task.py:48-51`, `harness/server/protocol.py` |
| 9 | MED | Telemetry plugin is on the correctness path: stagnation detection and the input-token budget depend on it, and it swallows exceptions. | `harness/telemetry/adk_plugin.py:285-314, 399-403, 521, 540` |
| 10 | MED | Search spills are written into the user's repository (`<workspace>/.artifacts`) and change the workspace fingerprint. | `harness/environment/local.py:130`, `harness/tools/adk_adapter.py:421` |
| 11 | MED | `skein steer` writes to a database the server never reads. | `harness/cli.py:48`, `harness/server/runtime.py:372` |
| 12 | MED | Workspace confinement does not apply to `bash` (`cat ../../etc/passwd` is read-only); dependency gate inconsistent (`npx` auto-runs, `pip install` needs approval). | `harness/safety/approval.py:130, 323` |
| 13 | LOW | Interrupted validation resumes as an uncaught `ValueError`, not a blocked outcome. | `app/agent/workflow.py:452` |
| 14 | LOW | Dead ledger fields (`plan`, `current_step_id`, `completed_step_ids`, `validations`) are serialized into every checkpoint and rendered to the model every turn. | `harness/models/task.py:156-183` |
| 15 | LOW | TUI reports hello-timeout as auth failure and stops retrying (server uses close code 1008 for both). | `clients/terminal/src/remote-session.ts:80`, `harness/server/websocket.py:235, 721` |
| 16 | LOW | `CommandRisk.REQUIRE_APPROVAL` does not exist (`if False` guard); lexicographic ISO timestamp compare in recovery; importers fall back to local time (T6); erasure keys ADK sessions on `task_id`; `atomic_write` creates parents before conflict checks; REPL source guard misses `posix`. | various |

Docs/CI hygiene: `make typecheck` fails (pyproject includes `tests`, CI does
not); `make format` would rewrite 142 of 256 files; CI `uv sync` is not
`--locked`; `requires-python >=3.11` versus 3.12 everywhere else; `--extra eval`
installs `harbor` while the runner demands an unpinned `pier`; eval limits
diverge (20M tokens/5,400 s in the script vs 2M/1,800 s in the library);
`architecture.md` still says Bubble Tea TUI; ~7,700 lines of historical
design/ADR/audit docs sit in the current tree.

## 5. Over-engineering inventory

### 5.1 No live callers

| Component | Lines | Evidence |
| --- | ---: | --- |
| `harness/context/compaction.py` + `models/context.py` | 475 | zero callers; docstring says "legacy"; only `safe_artifact_uri` is live |
| `harness/memory/{prompt,summary,catalog}.py` + `runtime.PROGRAMS` | ~500 | `compile_prompt`, `SummaryCache`, `ProgramCatalog` have no callers |
| `harness/ledger/{archive,erasure,backfill}.py` | 450 | only the `ledger-backfill` CLI |
| `harness/evals/{experiments,real_repositories,cases,grader}.py` + `eval-*` CLI | ~1,500 | contradictory model contracts; `grade_case` never run |
| `harness/config/tuning.py` + `tuning-export` | 300 | live loop uses `behavior_sha256` from `config/models.py` |
| `harness/environment/local.py` run/env/store_artifact | ~90 | zero callers; execution goes through `sandbox/` |
| env-var fallbacks in `tools/adk_adapter.py`, `sandbox/factory.py` | ~100 | production passes everything explicitly |
| `harness/codex.py` benchmark, `CodexSelection`, `write_codex_config` | ~230 | only `discover_codex_models` is live |
| `verification/runner.run_validation_plan` + `models.py` shim | ~120 | tests only |
| protocol `task.pause`, `ping/pong`, `resource.request`, 4 unrendered AG-UI types, `ServerEnvelope.durable` | ~150 | TUI never sends them; `pause` always refuses |
| `harness/agent` registry + Protocols | ~170 | one real `HarnessFactory` |
| CLI `prepare/run/cleanup`, `steer`, `steering-status`, `codex models/select/benchmark` | ~400 | Agents CLI not installed; steer hits wrong DB |
| dead fields (`TaskRequest` scope/level, `TaskLedger` plan, `ModelConfig.base_url`, `AdkConfig.resumable`, `ServerConfig.protocol`, skill lifecycle/metadata) | ~200 | no writers or single-value Literals |
| misc (`register_action`, `HarnessRoute`, model shims, `map_adk_event`, `demo.ts`, `redact_secrets`) | ~200 | grep finds no callers |

### 5.2 Opt-in features with unmet gates

| Feature | Lines | Gate | Disposition in this plan |
| --- | ---: | --- | --- |
| Canonical ledger + importers + shadow + DuckDB + six `on_save` sinks | ~1,150 | `memory.enabled: false` | becomes the spine (section 7); importers/shadow deleted |
| Context programs (`memory/context`, `tools/memory`, `adk/context`, context-* profiles, context eval trio) | ~1,900 | `context_programs.mode: off` | views catalog keeps the contracts; ADK compaction replaces `ContextWindowPlugin`; `compute_context` (C901 50) is retired |
| LanceDB / semantic search | ~200 | fails closed | deferred, independently gated |
| Safe-auto recovery, prior-run recall, continuity | ~750 | default off | rebuilt later on views + ADK resumability |
| FFF virtual search + `search_command.py` + `fff-search` dep | ~1,100 | always on; model never told the grammar | cut; search composes through `rg` |
| Docker sandbox | ~200 | no shipped profile | cut |
| Notebook PTC (`repl`, `notebook`, PTC half of `builders.py`) | ~1,500 | `notebook_ptc.enabled: false` | becomes the primary profile on an ADK code executor |
| Agents CLI surface | ~250 | not installed | cut |
| Task worktrees (`workspace/manager.py`) | ~250 | CLI `run` only | decide; default cut |

### 5.3 Duplication in kept code

- Two steering mechanisms both on by default (`SteeringPlugin` before-model/
  before-tool, and the workflow work-batch boundary).
- Two input-budget systems (chars/4 reservation events; metrics-plugin actuals).
- Three effect brokers with three approval paths (`_ManagedTools`,
  `_CellBroker`, `ManagedValidationExecutor`).
- Two view runtimes (`memory/runtime.PROGRAMS`, `memory/catalog.ProgramCatalog`)
  and two prompt compilers (`build_work_packet` live, `compile_prompt` test-only).
- Four "immutable event" contracts (`HarnessEvent`, `LedgerEvent`, `TraceSpan`,
  `Checkpoint`) glued by importers.
- Four-layer registry append API plus a journal whose job is publish-after-commit.
- `RunCoordinator` Protocols contradicted by nine `isinstance` checks.
- Five copies of the ownership check.
- `HarnessSettings` (21 fields) duplicating config + bindings.
- Skills registry carrying lifecycle/precedence/metadata relics of the removed
  skill-learning feature.

## 6. ADK: same primitives by design, different implementations

Skein uses ADK 2.7.1 as its runtime substrate: `App`, `Runner`, `Workflow`,
`Agent` with `static_instruction` and `output_schema`, plugins and callbacks,
`SqliteSessionService`, artifact services, `ContextCacheConfig`, and
`ResumabilityConfig`. That is the right layer to lean on and should stay.

ADK also ships modules whose names match Skein subsystems. On inspection the
contracts differ, and in most rows the reusable part is the interface or the
plumbing, not the behavior. Replacing a Skein contract with the ADK one would
lose the property the design depends on. The table records the reuse level
honestly so nobody re-derives the wrong conclusion from the module list.

| Primitive | ADK 2.7.1 | Skein | Reuse level | Why not more |
| --- | --- | --- | --- | --- |
| Conversation compaction | `Event.actions.compaction` range + content, applied by the contents processor; `EventsCompactionConfig` with one `BaseEventsSummarizer` and a token-threshold trigger | versioned `context.compaction@N` program at explicit watermarks; every materialization retained and readable `as_of`; working notes as separate versioned events; `fresh` vs `handoff_tail` policies; triggers at phase, checkpoint, verification, steering | **transport only**: Skein appends its own compaction events carrying program output; ADK filters the range and keeps call/response pairs intact | ADK's summarizer sees an event list, not the ledger; one summary replaces the range; no notes, no windows, no `as_of` |
| Tool approval | `FunctionTool(require_confirmation)`, `ToolConfirmation`, pause the invocation, resume with a function response | deterministic policy classes, task-scoped fingerprints with expiry, in-process waiter that holds the worker, same store used by verification, deny-default dialog, `/approvals` visibility | **none for PTC, optional for the four-tool arm** | a nested call inside a running Python cell cannot pause the invocation without losing the kernel epoch; verification is not a tool call |
| Workspace primitives | `LocalEnvironment` (subprocess, read, write, path resolve); `tools/environment` read/write/edit/execute; `ExecuteBashTool` with timeouts and always-confirm | symlink-safe confinement, atomic write with expected-hash/expected-absent conflicts, receipts with argument and result hashes, bounded output with artifact spill, redaction, post-write diagnostics, command policy | **interface shape only** | every property above would live in the subclass; the ADK base contributes ~50 lines of behavior |
| Code execution | `BaseCodeExecutor` extracts fenced code from text; `CodeExecutorContext` keeps execution id and error counts in session state | one `python` FunctionTool; write-ahead cell protocol; cell and attempt ids; kernel epochs; notebook as the durable document; `nb-cli` boundary; nested capability ledgering | **tool-call plumbing only** | no cell, attempt, epoch, document, or ledger; would create a second authority for execution identity |
| Skills | `SkillToolset`: four model-visible tools (list, search, load, load_resource) | harness-selected skills injected into the packet, no schema cost, trusted roots | **none** | adds four tool schemas, against the four-tool / one-tool tenet |
| Delegation | `AgentTool` runs a child synchronously inside the invocation; `LongRunningFunctionTool` | ADR P1: async handles, budgets, result envelopes, ledger lifecycle, no recursion | **starting point for a sync child only** | async semantics, budgets, and envelopes are not there |
| Blocked / needs input | `_request_input_tool`, `get_user_choice_tool`: model-initiated pause | harness-decided `blocked` route; server marks the run terminal and manages follow-ups | **none** | different initiator and different lifecycle |
| Session state | `State` with `app:`/`user:`/`temp:` prefixes; `EventActions.state_delta` | string-keyed state passed between plugin and workflow | **genuine** | small, use it |
| Tool-call plumbing | `FunctionTool`, `ToolContext`, before/after callbacks, `output_schema` | same | **genuine** | already used |
| Tracing | OpenTelemetry spans, `AutoTracingPlugin`, `LoggingPlugin` | redacted, content-hashed, sequence-ordered, idempotent spans with erasure | **export sink only** | no redaction, hashing, ordering, or erasure contract |
| Evaluation / optimization | Gemini/Vertex eval sets, rubrics, GEPA prompt optimizer | Harbor benchmarks with deterministic verification; behavior-hash export | **none now** | different oracle and different evidence model |
| Memory service | `BaseMemoryService`, `load_memory`, `preload_memory` | ledger views with scope, watermark, and provenance | **none** | no scope or provenance contract; exact reads stay on the ledger |

Practical rule: reuse ADK for execution, sessions, artifacts, callbacks,
resumability, caching, state deltas, and tool-call plumbing. Keep Skein's
contracts for policy, approvals, effects, compaction content, notes, code
execution, skills, tracing content, and verification. Where a Skein subsystem
is over-built, trim it; do not swap it for the ADK module of the same name.

## 7. Target architecture

### 7.1 The inversion

```text
today                                     target

workflow ──► JsonlEventStore (authority)   ADK session (conversation) ──emits──┐
         ──► SQLite receipts                                                    ▼
         ──► SQLite checkpoints            workflow ──► LedgerWriter (only harness write path)
         ──► SQLite steering                          │
         ──► SQLite metrics                           ▼
         ──► SQLite traces                 ledger_events + artifacts
         ──► run registry events                      │
              │ on_save importers        views/ (programs, versioned, receipted)
              ▼                              ├─ task.control    ├─ history.model
   canonical ledger (shadow, opt-in)         ├─ execution.open  ├─ context.compaction ──► ADK summarizer
   memory programs (no live caller)          ├─ task.progress   ├─ verification.state
   compile_prompt (test only)                ├─ workspace.state ├─ budget.state
                                             └─ human.pending   └─ prompt.request
                                                      │
                                            packet, TUI, verifier, resume, notebook
```

### 7.2 Authority map

| Fact | Today | Target | Reuse |
| --- | --- | --- | --- |
| Model-visible conversation | ADK SQLite session + per-iteration packet | ADK session, continuous, compacted by ADK with a Skein summarizer | `persistence/observed_session.py` |
| What happened, in order | `state/event_store.py` JSONL + ledger shadow | ledger events, one sequence, one writer | `ledger/models.LedgerEvent`, `ledger/jsonl.py`, `ledger/store.py` |
| Tool and command effects | `state/receipts.py` SQLite | `capability.requested/completed/failed/blocked/timeout` events with status + effect | `builders._CellBroker` already emits these |
| Task goal, plan, progress, blockers | `TaskLedger` via `LEDGER_PATCHED` patches | typed control events reduced by `task.control@1`, `task.progress@1` | `state/events.reduce_events`, `models/task.TaskLedger` |
| Verification evidence, baseline, memo | `verification.completed` payloads, no baseline | `validation.baseline`, `validation.completed` keyed by (command, workspace fingerprint); `verification.state@1` | `verification/managed.py`, `discovery.py` |
| Open / effect-unknown work | receipts joined ad hoc | `execution.open@1` | `memory/runtime._execution_open` |
| Checkpoints | `state/checkpoints.py` SQLite | `checkpoint.created` + `resume.handoff@1` | payload shape |
| Model usage, budgets | telemetry SQLite | `model.usage` events + `budget.state@1` | `telemetry/metrics.py` aggregates |
| Traces | tracing SQLite spans | OpenTelemetry via ADK; bodies as artifacts | `tracing/plugin.py` redaction |
| Steering | `state/steering.py` SQLite lease queue | mutable lease table + `message.*` events + `human.pending@1` | lease/ack/idempotency |
| Approvals | `approvals/store.py` SQLite | mutable decision table (CAS) + `approval.*` events; one caller through the broker | `safety/approval.py`, `approvals/store.py`, `waiting.py` |
| Run lifecycle, public events | `server/registry.py` event table | `run.*` events on a run stream; CAS status column stays | `registry.terminalize` |
| Packet | `orchestration/core.build_work_packet` | initial `prompt.request@1`; thereafter ADK contents + compaction | `memory/prompt.compile_prompt`, `context/prompt.build_static_prefix` |
| Durable session document | `notebook/*` projection (opt-in with PTC) | the notebook is the primary durable workbench and document: PTC code cells, selected outputs, and task/user/assistant/compaction Markdown cells, rebuilt from the ledger at a watermark, snapshotted as a content-addressed artifact, read through `nb-cli` | `notebook/reducer.py`, `materializer.py`, `artifacts.py`, `skein notebook` |
| Live Python heap | `repl/worker.py` | unchanged: disposable, one kernel epoch, only self-contained data cells restore | `repl/worker.py`, `builders._replay_policy` |

### 7.3 Target packages

| Package | Contents | From | Est. lines |
| --- | --- | --- | ---: |
| `harness/ledger` | models, writer (flock, tail cache), jsonl, duckdb, replay | keep; drop importers/shadow/backfill/archive/erasure | ~700 |
| `harness/views` | models, runtime, catalog, `seeded/{task, execution, history, workspace, verification, time, budget, prompt, compaction, memory, notebook}` | move memory/*, state reducer, progress, packet, telemetry aggregates | ~1,600 |
| `harness/broker` | api, policy, approvals, fs, shell, mcp, artifacts, state, trace, redaction | move tools/adk_adapter core, environment file primitives, sandbox/local, safety/*, approvals/*, `_CellBroker` | ~1,400 |
| `harness/repl` + `harness/notebook` | `python` tool, worker, protocol, restore policy, cell broker; reducer, materializer, artifacts, snapshots | move builders PTC half | ~1,300 |
| `harness/context` | prefix, summarizer (ADK `BaseEventsSummarizer`), tokens | keep compiler/prompt; epochs cut | ~250 |
| `harness/control` | task models, steering (lease + delivery), verification with baseline/memo, workflow loop | move models/task, state/steering, verification/*, app/agent/workflow | ~1,700 |
| `harness/adk` | python tool, direct-tools compat, plugins (usage, receipts), observed session service | move builders four-tool half, telemetry plugin, persistence | ~600 |
| `harness/server` | protocol, registry (CAS + ledger streams), runtime, websocket, mapper, sessions, models | keep, trimmed | ~3,800 |
| `harness/ai`, `config`, `skills`, `cli` | providers, YAML, trimmed directory skills, six commands | keep, trimmed | ~2,300 |
| `app/agent` | factory, presentation, streaming | keep | ~900 |
| `harness/evals` | runner, optional harbor | keep | ~1,000 |
| removed | `state/`, `memory/`, `environment/`, `tools/`, `telemetry/`, `tracing/`, `workspace/`, `orchestration/`, `agent/` registry, `config/tuning`, `codex.py`, evals extras, FFF search | | |

Roughly 15,500 Python lines against 30,300 today. This is the end state if
every conditional slice earns its evidence; it is not a commitment.

## 8. Execution plan

Each slice is independently shippable, leaves the suite green, and is measured
on the same eight DeepSWE tasks before the next begins. Commit one component per
commit. Record the eight-task numbers (reward, wall, calls, in/out/cached
tokens, verify seconds, cost) in `docs/evaluation.md` after each slice.

### Slice 0: measurement harness (half a day)

Purpose: make every later gate a one-command check.

1. Add `scripts/compare_deepswe.py` (the comparison script used for section 3)
   that reads a Skein results directory and an optional mini-swe-agent directory
   and prints the per-task and total tables. Inputs: `result.json`, `reward.json`,
   `metrics.db`, `events/*.jsonl`, `mini-swe-agent.trajectory.json`.
2. Add `scripts/task_timeline.py` that prints the event timeline of one run
   (iteration boundaries, verification durations, workspace fingerprints, token
   growth per call).
3. Check both into `scripts/` and reference them from `docs/evaluation.md`.

Gate: both scripts reproduce the section 3 tables from the existing result
directories.

### Slice 1: the smallest correct verification fix (2-3 days)

Purpose: remove the ~12,900 s of unpassable iteration and close the HIGH
defects. No architecture change. This is the whole of what is committed before
the next measurement; resist adding anything else to it.

Files: `harness/safety/approval.py`, `harness/state/progress.py`,
`app/agent/workflow.py` (`_verify_task`, `_verification_transition`),
`harness/verification/{contracts,discovery,managed}.py`,
`harness/approvals/store.py`, `harness/sandbox/local.py`,
`.github/workflows/ci.yml`, `pyproject.toml`, `Makefile`.

Steps, in this order:

1. **Command-policy bypasses, immediately.** In `classify_command`: skip
   leading `-C DIR`, `-c k=v`, `--git-dir=` before picking the git subcommand;
   treat bare `&` as a separator; classify any segment containing `$(`,
   backticks, or `<(` as unknown; classify `find` with
   `-exec/-execdir/-delete/-ok` as unknown. Fix the dead
   `CommandRisk.REQUIRE_APPROVAL` expression. Seven regression tests in
   `test_safety.py`. Also: `ApprovalStore.request` reopens an `expired` row as
   `pending`; `LocalSandbox` uses `start_new_session=True` + `killpg`.
2. **Independent verification counter.** Remove the `register_action_batch`
   call from the verification path. Compute
   `consecutive_identical_verifications` from events: consecutive
   `verification.completed` with the same `verification_fingerprint(report)`
   AND the same workspace fingerprint, resetting on either change. Keep the
   tool-fingerprint stagnation counter untouched.
3. **Block after two.** Two consecutive identical failures on an unchanged
   workspace route to `blocked`, with the report's failing checks as the
   reason. Never let this loop reach `max_iterations`.
4. **Baseline-relative verification.** In `_initialize_run`, after discovery,
   run each discovered command once on the base revision before the model's
   first mutation. Append `validation.baseline` with
   `{command, exit_code, failing_tests[], duration_ms}` (parsers for pytest,
   vitest, mocha, cargo test, go test; baseline timeout recorded as
   `unknown`). In `build_report`, a command passes if `exit_code == 0` or its
   failing set is a subset of the baseline failing set; report
   `new_failures[]` and `fixed[]`. When required strength is unreachable from
   discovery, say so in `recommended_next_action` instead of asking for a
   trusted command the model cannot add.
5. **Memoize validation.** Key each validation by
   `(command_sha256, workspace_fingerprint)`; on a hit, append
   `validation.completed` with `cached=True` and the prior result, and skip
   the process.
6. **Targeted tests as behavioral evidence.** A `test`-category command the
   model ran through the broker with exit 0 that touches a changed path or a
   test file the model added counts toward `behavioral`. Build
   `ValidationCommand(source="model", targeted=True)` entries from receipts.
7. **Completion-claim propagation.** `claimed_evidence` is `[]` in all 96
   reports. Trace from `StructuredAgentStep` parsing through
   `workflow.py:792` to `_verify_task`; fix; add a test that a step with
   claims yields non-empty `claimed_evidence`.
8. **CI.** `pytest.importorskip("harbor")`; pyright ignore for the optional
   extras; `uv sync --locked` in both workflows; `requires-python` and pyright
   at 3.12; `make typecheck` matches CI.

Tests: `test_safety.py` bypass cases; `test_state.py` independent counter;
`test_verification.py` baseline subset, memo hit, targeted evidence, claims;
`test_conversation_runtime.py` edit→verify→identical-failure loop that
terminates in two verifications, and one where a novel tool call between
identical failures does not reset the counter.

Gate: **rerun DeepSWE-8 before changing conversation or storage
architecture.** No task ends by iteration cap on an unchanged workspace;
kombu, ofetch, testem, boa finish within 1.5x of their first-iteration time;
reward unchanged; verify seconds < 1,000 total; CI green on main.

### Slice 2: continuous conversation with ADK compaction (3-4 days)

Purpose: remove per-iteration rediscovery; cut model calls and output tokens.

Files: `app/agent/workflow.py`, `app/agent/factory.py`, `app/agent/builders.py`,
new `harness/context/summarizer.py`, `harness/config/models.py` (`ContextConfig`).

Steps:

1. Change the worker node from `single_turn` to a continuous agent. Options:
   set `mode='task'` with `include_contents='default'` on the workflow node, or
   move the loop into one `LlmAgent` invocation with the workflow as a
   before/after-agent boundary. Prototype both on one task; pick the one that
   keeps the stable prefix byte-identical (verify with the existing prefix guard).
2. Compaction is program-owned; ADK only applies it. At a boundary (token
   threshold, phase change, checkpoint, verification, steering, explicit
   request), the harness evaluates `context.compaction@N` at the current ledger
   watermark. The program's inputs are the ledger (not ADK's event list), the
   previous compaction view, the active working note, `execution.open`,
   `verification.state`, and `workspace.state`. Its output is the structured
   handoff of design §12.3 plus a retained-tail cursor. The harness records it
   as `view.computed` + `compaction.created` (program, version, watermark,
   content hash, source ranges) and then appends an ADK event whose
   `actions.compaction` carries that content and the covered range. ADK's
   contents processor does the filtering; no `BaseEventsSummarizer` is used for
   content. Earlier windows remain materialized and readable via
   `agent.trace.view("context.compaction", as_of=W)`.
3. Reconstruction policy is a parameter of the same program:
   `handoff_tail` keeps the last N raw events after the range end; `fresh`
   sets the range end at the latest event and republishes the required control
   state, pending steering, and note reference so nothing conversational
   survives except by retrieval. Both persist a new context epoch event before
   the next model request. The optional model narrative is a separate
   evidence-bound program call whose output is recorded, never authoritative.
4. Working notes stay outside compaction: `note.updated` events with expected
   prior version, rendered through `task.notes@1`, and placed in the P3
   region of the request (outside the stable prefix) by a before-model callback
   or the boundary message. A note is advisory and cannot change verification
   or completion.
5. Deliver verification results, the counterexample-review prompt, steering,
   and replan as user-role messages in the same conversation rather than
   workflow re-entry. The worker's `AgentStep` remains the turn terminator.
6. Move the input-token budget from the metrics plugin to an after-model check
   on recorded usage; on breach, evaluate the compaction program and publish.
7. Drop the per-iteration `execution.model_budget_reserved` events and the
   chars/4 reservation.

Tests: scripted-model integration test that drives three compactions and
asserts (a) prefix bytes unchanged, (b) function call/response pairs intact,
(c) an exact early file read is recoverable via the handoff, (d) verification
result arrives as a message and the next turn edits without re-reading.

Gate: P0 bytes identical; output tokens and model calls fall on helm, koota,
textual, wazero; cache-read ratio ≥ 95%; at least one compaction event on each
task that exceeds the threshold; reward unchanged.

### Slice 3: one event contract, one writer (3 days) — conditional

Purpose: the ledger becomes the only harness write path; fix O(n²) and
multi-process interleave.

Files: `harness/ledger/{models,jsonl,store}.py`, new `harness/ledger/writer.py`,
`harness/state/event_store.py` (becomes facade), `harness/state/{receipts,
checkpoints}.py`, `harness/telemetry/*`, `harness/tracing/*`,
`harness/server/registry.py`, `app/agent/factory.py`, `harness/server/bootstrap.py`.

Steps:

1. Extend `LedgerEvent` with `stream_id`, `stream_seq`, `visibility`,
   `artifact_refs`, `supersedes_event_id`, `retracts_event_id` (design §8.1).
   Map `HarnessEvent` fields onto it; `HarnessEvent` becomes a type alias +
   constructor helper.
2. `LedgerWriter`: single per-process writer with `fcntl.flock` on append,
   in-memory tail per stream keyed on `(mtime, size)`, gap-free `stream_seq`,
   idempotency by `(stream_id, idempotency_key)`.
3. `JsonlEventStore` → facade over `LedgerStore` with the same `append/read`
   signature so the workflow diff is minimal.
4. Migrate stores to event kinds: receipts → `capability.*`; checkpoints →
   `checkpoint.created`; model usage → `model.usage`; tool usage → already in
   receipts; trace spans → `model.requested/response` (redacted, bodies as
   artifacts) or drop in favour of OpenTelemetry; run events → `run.*` on a
   run stream; approvals → `approval.*`; steering → `message.*`.
5. Delete `ledger/importers.py`, `ledger/shadow.py`, `ledger/backfill.py`,
   `ledger/archive.py`, `ledger/erasure.py`, the six `on_save` sinks, the
   `memory.enabled` gate (the ledger is always on), `MemoryShadowPlugin`.
6. Keep SQLite only for CAS/lease tables: run status, steering lease, approval
   decision (until slice 4), workspace ownership. Each mutation emits an event.

Tests: byte-equal `history.model` and `task.state` on fixture streams versus
the old reader; two-process append test with interleaving; replay idempotency.

Gate: fixture equality; append p95 and 100k-event tail read recorded for JSONL
and DuckDB; suite green; DeepSWE-8 numbers unchanged.

### Slice 4: one broker (3-4 days) — conditional

Purpose: one policy/approval/receipt path for direct tools, PTC, and
verification. The broker is Skein's; ADK contributes `FunctionTool` and
`ToolContext` registration only.

Evidence required before starting: a defect or measurement that the three
current paths (`_ManagedTools`, `_CellBroker`, `ManagedValidationExecutor`)
disagree in a way slice 1 did not already fix, or the one-tool ablation
(slice 7) being scheduled, which needs direct-versus-nested equivalence.

Files: new `harness/broker/`, `harness/tools/adk_adapter.py` (shrinks to four
adapters), `app/agent/builders.py` (`_CellBroker` moves),
`harness/verification/managed.py`, `harness/approvals/*` (kept, one caller).

Steps:

1. `Broker.call(capability, arguments, *, operation_id, consumer, timeout)`:
   policy → approval (existing store and waiter, one caller) → execute →
   bound/redact → receipt event. Capabilities registered by name:
   `fs.read/write/edit`, `shell.run`, `api.call`, `trace.view/events`,
   `state.get/put/delete`, `artifacts.get`, `clock.now`,
   `capabilities.list/describe`.
2. Execution stays on Skein's confined environment (symlink-safe `resolve()` +
   `relative_to`, atomic write, expected-hash conflicts, `killpg` timeouts).
   Merge `environment/local.py` file primitives and `sandbox/local.py` into
   the broker; drop the dead `run`/env code and the env-var fallbacks.
3. Four direct tools = four adapters calling the broker. PTC `agent.*` = the
   same broker. `ManagedValidationExecutor` = `broker.call("shell.run", …,
   consumer="verification")`. Approvals are not ADK confirmations: a nested
   call inside a running cell must wait in-process without losing the kernel
   epoch.
4. Remove FFF search, `search_command.py`, `tools/memory.py` reserved routing,
   `sandbox/factory.py` env variant, Docker sandbox.
5. Route spills to `state_root/artifacts`; stop creating `<workspace>/.artifacts`.

Tests: existing tool/receipt/PTC/approval tests pass unchanged;
direct-versus-nested equivalence (same command → same decision and receipt
shape through both paths).

Gate: above tests; DeepSWE-8 unchanged or better.

### Slice 5: views catalog (2-3 days) — conditional

Purpose: every derived fact is a versioned program with receipts.

Files: new `harness/views/`, `harness/memory/*` (moves), `harness/state/
events.py` reducer (moves), `harness/state/progress.py` (moves),
`harness/orchestration/core.py` (packet moves), `harness/telemetry/metrics.py`
(aggregates move).

Steps:

1. `Program` model (name, version, language, source hash, allowed sources,
   budgets, status), `ViewRequest`/`ViewResult` extended with `lane`,
   `clock_event_id`; `RetrievalReceipt`.
2. Seeded programs as pure functions over `(events, watermark, params)`:
   `task.control`, `task.progress`, `execution.open`, `history.model`,
   `workspace.state`, `human.pending`, `task.memory` (lexical), `verification.
   state`, `budget.state`, `time.state`, `resume.handoff`, `context.compaction`,
   `notebook.state`, `prompt.request`.
3. Materialization cache keyed by `(program, version, params_hash, watermark,
   lane)`; `view.computed` and `retrieval.served` events.
4. The ADK summarizer from slice 2 calls `context.compaction`; the initial
   packet calls `prompt.request`; the TUI's session/history endpoints call
   `history.public`; the verifier calls `verification.state`.
5. Delete `memory/{prompt,summary,catalog,lance,semantic}.py`,
   `context/compaction.py`, `models/context.py`.

Gate: same request + watermark → same content hash and evidence; later
watermark is a different view even if bytes match; `prompt.compiled` manifest
emitted per request.

### Slice 6: typed goal control and steering delivery (2 days) — conditional

Files: `harness/control/task.py`, `harness/state/steering.py` (moves),
`harness/adk/steering.py` (deleted), `harness/server/{protocol,sessions}.py`,
`clients/terminal/src/{main,session-ui}.ts`.

Steps:

1. Control events: `task.created`, `task.goal_revised` (steering-only),
   `plan.revised`, `plan.item_status_changed` (pending/active/complete/blocked/
   dropped/superseded), `progress.claimed`, `workspace.changed`,
   `validation.completed`, `task.blocked`, `task.verification_requested`,
   `task.completed`. `LEDGER_PATCHED` removed; `task.control` derives remaining
   work from the active plan revision + acceptance coverage + evidence.
2. Steering `delivery` field (`current_now`, `current_next_boundary`,
   `after_current`, `inbox`); lifecycle events `message.queued/leased/
   delivered/acked/deferred/cancelled`; `human.pending` decides injection at
   the next boundary; `current_now` = cooperative cancel at the broker boundary.
3. Delete `SteeringPlugin`; the server follow-up queue becomes `after_current`;
   TUI adds a delivery selector.
4. `blocked` outcome via ADK's request-input tool.

Gate: original goal immutable; revisions attributable; the ADR's goal-control
eval list (late scope revision, out-of-scope refactor, dropped approach,
compaction between phases, unsupported claim, unrelated queued message,
no-progress replan, reopened criterion); no inbox leakage.

### Slice 7: PTC as the primary profile, then the one-tool ablation (4-5 days) — conditional

Files: `harness/repl/`, `app/agent/builders.py` (PTC half moves),
`harness/skills/*` (deleted), `app/agent/skills.py`, `app/agent/factory.py`.

Steps:

1. Keep the `python` FunctionTool and the write-ahead cell protocol
   (design §13.3) exactly: allocate cell and attempt ids, append
   `notebook.cell_added` + `repl.cell_submitted`, materialize the input cell,
   execute in the persistent CPython worker, ledger nested capability calls,
   append one terminal event, materialize outputs, append
   `notebook.materialized`. Do not route through ADK's `BaseCodeExecutor`;
   execution identity and error counts are ledger facts, not session state.
   Move `_replay_policy`, `_RestoreBroker`, `_CellBroker` out of `builders.py`
   into `harness/repl/`.
1b. The notebook is the session document. Project task, user, assistant,
   steering, verification, and compaction events as Markdown cells alongside
   code cells (already implemented); the compaction program from slice 2 also
   emits its handoff as a derived Markdown cell with program, version,
   watermark, and content hash in metadata (design §12.2). `skein notebook`
   and `nb read/search` through the broker remain the only inspection paths;
   the harness never parses notebook JSON as a fallback. Snapshot the notebook
   as a content-addressed artifact at compaction, checkpoint, and worker close.
2. `agent.capabilities.list/describe`, `agent.trace.view/events`,
   `agent.state.get/put/delete` (`state.put` appends an event; `state.latest`
   is a view), `agent.api.call` (MCP alias).
3. Keep Skein's harness-selected skills (no model-visible skill tools); trim
   `skills/registry.py` to name, description, body, references, and trusted
   roots, dropping lifecycle, precedence, and metadata relics.
4. Four-tool arm = direct-tools adapter over the broker (kept for the ablation).
5. Run the ablation on DeepSWE-8 (and the 18-task set if authorized): four-tool
   vs one-tool, slices 1-2 in both arms. Measure pass rate, cost per pass,
   uncached tokens, cache ratio, latency, tool errors.

Gate: quality holds within the declared margin; then flip the default and
remove the direct-tools adapter.

### Slice 8: later, with evidence

- Safe-auto recovery rebuilt on `execution.open` + `resume.handoff` + ADK
  resumability (Stage 4).
- Prior-run recall as source scope on `ViewRequest` (Stage 5).
- Delegation on `AgentTool` with `include_contents='none'` (P1).
- Process handles (P1, only if evals need dev servers).
- SQL, semantic retrieval, summary caching (independently gated).

### Effort and order

| Slice | Status | Days | Depends on | Evidence required to start | Primary measured effect |
| --- | --- | ---: | --- | --- | --- |
| 0 measurement | committed | 0.5 | - | - | gates become one command |
| 1 verification fix | committed | 2-3 | 0 | - | wall time -12,000 s (projected) |
| 2 continuous conversation | committed after 1's rerun | 3-4 | 1 measured | slice 1 rerun shows the loop is valid | model calls, output tokens |
| 3 event contract + writer | conditional | 3 | - | a measured O(n²) or interleave failure in a real run, or slice 5 scheduled | correctness, removes 5 stores |
| 4 one broker | conditional | 3-4 | 3 | path disagreement not fixed by slice 1, or slice 7 scheduled | one policy path |
| 5 views catalog | conditional | 2-3 | 3 | slice 2's compaction program needs `as_of` reads the current stores cannot serve | ADR contract live |
| 6 goal control + steering | conditional | 2 | 5 | drift or forgotten-work failures in evals | drift evals |
| 7 PTC as primary + ablation | conditional | 4-5 | 2, 4, 5 | slices 1-2 measured; ablation authorized | default decision |

Do not start slices 3-7 in parallel with 1-2. The rerun after slice 1 and the
rerun after slice 2 decide whether the architectural slices have a problem to
solve. Each conditional slice records the evidence that triggered it in its
first commit message.

## 9. Decisions to make

1. **Verification semantics.** Recommendation: baseline-relative, like the
   benchmark's fail-to-pass / pass-to-pass logic. A whole-suite command that
   fails at baseline is evidence about the repository, not about the change.
2. **Continuity mechanism.** Recommendation: Skein's versioned programs
   produce every compaction and note at explicit watermarks and keep all of
   them; ADK's `Event.actions.compaction` is only the transport that applies
   the chosen window to the request. ADK's own summarizer and its
   token-threshold trigger are not the compactor; the threshold may be one of
   several triggers.
3. **PTC document and executor.** Recommendation: the notebook stays the
   primary durable document and `nb-cli` the inspection boundary; the
   `python` FunctionTool with the write-ahead cell protocol stays the only
   execution path. ADK's `BaseCodeExecutor` is not used; it would move
   execution identity into session state and has no document model.
4. **JSONL or DuckDB as the eventual backend.** Recommendation: JSONL default
   until slice 3's numbers say otherwise; same interface either way.
5. **Keep the four-tool arm through slice 7.** Recommendation: yes, as a small
   adapter, because the ablation is the one measurement the design requires.
6. **Counterexample review.** Recommendation: keep as a warm in-conversation
   turn after slice 2 and measure; it changed no outcome on these eight tasks.
7. **Streaming replies.** Not a tenet; keeps the terminal feeling live.
   Recommendation: keep.
8. **Old run data.** No migration tool; old state roots stay readable by the
   old code in Git.

## 10. Verified-correct items (do not simplify away)

- Static prefix guard covers `system_instruction`, tools, and tool config;
  volatile state stays in the user packet. Model-declared completion always
  routes through `_verify_task`; direct answers are fenced by
  `can_answer_directly` plus a workspace fingerprint.
- Path confinement resolves then `relative_to`; `atomic_write` is
  mkstemp + fsync + replace; failed mutations record failed receipts;
  truncation preserves exit codes.
- Server `attach` subscribes before replay; `terminalize` commits event and
  status atomically with CAS; auth is loopback + bearer + origin allowlist with
  a 0600 `O_NOFOLLOW` token file; workspace lock released before `after_turn`.
- Codex credential store is 0600 with symlink refusal and cross-process flock;
  OpenRouter error path redacts the key; `prompt_cache_key` derives only from
  model, instructions, tools, text format with sorted keys.
- Verification fails closed on an empty command list; approvals keyed by task
  and fingerprint; executor resets approved fingerprints per instance.
- TUI required-field tables match `AgUiEvent.validate_event_shape`; approval
  shortcuts A/Y approve, D/N deny, Enter denies; 1 MiB frame cap both ways,
  400-entry transcript cap, 32 in-flight requests.
