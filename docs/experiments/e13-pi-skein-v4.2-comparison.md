# Pi + Skein PTC v4.2 live comparison

V4.2 reduced redundant verification, but the live campaign did not produce a valid
60-trial quality result. OpenRouter returned 402 credit errors in 22 rollouts and Pi
exited successfully after those provider errors, so Harbor graded partial or unchanged
workspaces. One separate Pwntools verifier timed out. The 25/60 raw pass count must not
be compared with the complete v4.1 and Code Tool panels.

- Manifest: `tests/eval/experiments/e13-code-mode-20-v2.json`
- V4.2 result root: `.artifacts/e13-muse-20-v4.2-pi-skein-ptc`
- V4.1 result root: `.worktrees/e13-ptc-isolation/.artifacts/e13-muse-20-v4.1-pi-skein-ptc`
- Code Tool result root: `.worktrees/e13-ptc-isolation/.artifacts/e13-muse-20-v3-pricing-fixed-pi-code-tool`
- Model: `meta/muse-spark-1.3-contributor`, `xhigh`
- Trials: three per task, concurrency six, no retries
- V4.2 source: `562bc65c7ea28555000c0d976628fc6057c75053`
- V4.2 `runs.jsonl` SHA-256: `de07627f02f78ee8b7174dbebd52402700567ee3333e4546e44d92c6860ef7b8`
- Source diff SHA-256 recorded by the runner: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (clean)

## Run validity

| Outcome | Trials |
|---|---:|
| Provider-clean and graded | 37 |
| OpenRouter 402 interruption | 22 |
| Provider-clean, verifier timeout | 1 |
| Total attempted | 60 |

The 402s began while other requests were still in flight and then became hard
insufficient-credit failures. Five final tasks received no usable model work in any
trial. One interrupted Numba rollout still passed after doing useful work before the
402; it is excluded from the clean quality view. The Pwntools error was a 1,800-second
verifier timeout in `test_recv_timeout`; its agent phase completed normally.

Pi records provider failures as assistant messages with `stopReason: "error"`, but
its process exits zero. The current adapter therefore reports a successful agent run
and lets Harbor grade it. This is a runner validity defect, not a PTC quality failure.

## Quality on usable evidence

Across all 37 provider-clean graded rollouts, v4.2 passed 24 (64.9%). Weighting each
historical arm's per-task result by the same clean-v4.2 task mix gives expected pass
rates of 64.0% for v4.1 and 65.8% for Code Tool. This is descriptive because the
trials are stochastic and not seed-paired.

The most defensible balanced slice is the nine tasks with three clean v4.2 trials,
27 trials per arm:

| Metric | PTC v4.2 | PTC v4.1 | Code Tool |
|---|---:|---:|---:|
| Official passes | 18/27 (66.7%) | 19/27 (70.4%) | 19/27 (70.4%) |
| Mean F2P | **98.446%** | 93.856% | 98.254% |
| Mean P2P | 99.947% | 99.921% | **99.961%** |
| Mean partial credit | **99.789%** | 98.863% | 99.730% |

The one-pass difference does not establish a quality regression. V4.2 improved the
continuous metrics over v4.1 and approximately matched Code Tool on this clean slice.
Claude Code by Agents improved from one v4.1 pass to two v4.2 passes, while Arktype
lost one. Bandit and Clack remained near-complete binary failures.

## Efficiency on the balanced clean slice

| Metric | PTC v4.2 | PTC v4.1 | Code Tool |
|---|---:|---:|---:|
| Cost per trial | $0.193 | $0.154 | **$0.136** |
| Input tokens per trial | 10.81M | 10.30M | **8.04M** |
| Uncached input per trial | 1.62M | 1.22M | **1.10M** |
| Output tokens per trial | 65.1K | 65.5K | **57.1K** |
| Median agent latency | 18.95 min | 16.13 min | **13.39 min** |
| Mean model steps | 113.0 | 107.8 | **85.5** |
| Code cells per trial | 110.9 | 106.3 | **84.5** |
| Helper calls per trial | **131.9** | 132.8 | n/a |
| `bash()` calls per trial | 75.9 | 49.7 | n/a |
| `verify()` calls per trial | **1.74** | 18.41 | n/a |
| Invalidated successful verifications | 11 total | n/a | n/a |

The revision gate worked: `verify()` calls fell 90.5%, and read-only exploration no
longer invalidated final evidence. It did not reduce total interaction volume. Helper
calls were flat, code cells rose 4.3%, and model steps rose 4.9%. The prompt moved
intermediate checking from `verify()` to `bash()`; `bash()` calls rose 52.7% instead
of disappearing or being batched.

V4.2 cost 25.8% more than v4.1 and 42.4% more than Code Tool on this slice. Uncached
input rose 32.3% versus v4.1 even though total input rose only 5.0%, so weaker cache
reuse amplified the extra turns. Median latency rose 17.5% versus v4.1. The workspace
fingerprint also performs repository scans after every cell; its time is not separately
instrumented, so its share of the latency increase cannot be isolated from these traces.

## Per-task validity and passes

| Task | Clean v4.2 | v4.2 clean passes | 402 | Verifier errors | v4.1 | Code Tool |
|---|---:|---:|---:|---:|---:|---:|
| actionlint | 3 | 3/3 | 0 | 0 | 3/3 | 3/3 |
| anko | 3 | 3/3 | 0 | 0 | 3/3 | 3/3 |
| arktype | 3 | 1/3 | 0 | 0 | 2/3 | 1/3 |
| bandit | 3 | 0/3 | 0 | 0 | 0/3 | 1/3 |
| clack | 3 | 0/3 | 0 | 0 | 1/3 | 1/3 |
| claude-code-by-agents | 3 | 2/3 | 0 | 0 | 1/3 | 1/3 |
| httpx | 3 | 3/3 | 0 | 0 | 3/3 | 3/3 |
| meriyah | 3 | 3/3 | 0 | 0 | 3/3 | 3/3 |
| narwhals | 3 | 3/3 | 0 | 0 | 3/3 | 3/3 |
| numba | 2 | 2/2 | 1 | 0 | 2/3 | 3/3 |
| oxvg | 2 | 0/2 | 1 | 0 | 1/3 | 0/3 |
| pebble | 2 | 2/2 | 1 | 0 | 1/3 | 2/3 |
| pwntools | 2 | 2/2 | 0 | 1 | 2/3 | 2/3 |
| quill | 1 | 0/1 | 2 | 0 | 0/3 | 1/3 |
| scc | 1 | 0/1 | 2 | 0 | 2/3 | 1/3 |
| skrub | 0 | — | 3 | 0 | 2/3 | 0/3 |
| sql-formatter | 0 | — | 3 | 0 | 3/3 | 3/3 |
| tomlkit | 0 | — | 3 | 0 | 3/3 | 3/3 |
| true-myth | 0 | — | 3 | 0 | 3/3 | 3/3 |
| updo | 0 | — | 3 | 0 | 1/3 | 0/3 |

## Decision and next steps

V4.2 preserves v4.1 quality on the clean evidence available, but it misses the stated
interaction-reduction objective. Do not promote or reject it from the raw 60-trial
score.

Before spending on another run:

1. Treat any terminal Pi assistant message with `stopReason: "error"` as an agent
   error, retain the provider message, and prevent Harbor from grading it as a normal
   rollout.
2. Resume only the 22 provider-interrupted trials plus the one Pwntools verifier error
   after sufficient credits are available. Preserve their task and attempt slots; do
   not replace already clean results.
3. Change the compact prompt from “use bash for exploration” to “batch exploration and
   intermediate checks in as few cells as practical; use one final verify.” The first
   wording caused substitution rather than consolidation.
4. Compute the workspace fingerprint only after a cell containing a potentially
   mutating helper (`write`, `edit`, or `bash`), and record fingerprint duration. Reads
   and pure Python cells cannot change the workspace through the PTC contract.
5. Keep the revision-gated evidence contract. It achieved the intended verification
   reduction without transcript replay or worker-state changes.
