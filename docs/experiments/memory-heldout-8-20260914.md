# Current memory versus PTC control: fresh DeepSWE 1.1 eight-task cohort

Date: 2026-09-14/15 UTC. Runtime revision:
`c41ae3ae0a80810c25667c6f02ad56b1943e17fc`.

## Contract

This is a frozen system-level comparison of the checked-in
`notebook-ptc-memory.yaml` profile against the checked-in
`notebook-ptc-jsonl.yaml` PTC control. It is not a one-variable memory ablation:
the current profile also changes context-window policy, recent-event budget, PTC
work-batch limits, and verification-attempt configuration.

- Eight DeepSWE 1.1 tasks with no earlier paid/live result directory were selected
  before dispatch: `actionlint-action-pinning-lint`,
  `arktype-json-schema-refs-dependencies`,
  `awilix-async-container-initialization`,
  `drizzle-orm-window-function-builders`, `httpx-streaming-json-iteration`,
  `ipython-session-bundle-replay`, `narwhals-rolling-window-suite`, and
  `prometheus-typed-label-sorting`.
- The panel covers Go, Python, and TypeScript; easy, medium, and hard published
  difficulty; API, streaming, persistence, schema, parser/runtime, CLI, and
  sorting behavior.
- Both arms used OpenRouter `meta/muse-spark-1.3-contributor`, reasoning `xhigh`,
  32,768 output tokens, a 1B cumulative-input ceiling, 1,000 workflow iterations,
  one attempt, zero retries, and the pinned confirmation manifest
  `f30a3bb6245a360820be5684ed98d001cea460169168633c18487ef28d48fad4`.
- The arms ran concurrently at three trials each, for global concurrency six.
- No implementation, prompt, fixture, limit, retry, or task selection changed
  during the cohort.

Artifacts:

- PTC control: `/Users/mathiasl/skein-eval-results/memory-heldout-8-ptc-control-20260914`
- Current memory: `/Users/mathiasl/skein-eval-results/memory-heldout-8-current-20260914`

## Quality

Both arms scored **4/8** under the isolated Harbor verifier. There were two
memory-only wins, two control-only wins, two shared passes, and two shared
failures. The exact paired McNemar result is therefore neutral (`b = 2`, `c = 2`;
two-sided `p = 1.0`).

| Task | PTC control | Current memory |
| --- | ---: | ---: |
| actionlint | 1 | 1 |
| arktype | 0 | 1 |
| awilix | 0 | 0 |
| drizzle-orm | 0 | 0 |
| httpx | 0 | 1 |
| IPython | 1 | 1 |
| narwhals | 1 | 0 |
| prometheus | 1 | 0 |

Completion calibration also failed in both arms. Control reported `complete` on
arktype although Harbor scored it zero; current memory reported `complete` on
narwhals although Harbor scored it zero. Conversely, three control passes and all
four memory passes ended locally as `blocked`. The benchmark verifier remains the
quality authority for this report.

## Cost, tokens, calls, and latency

| Metric, eight tasks | PTC control | Current memory | Change |
| --- | ---: | ---: | ---: |
| Official passes | 4 | 4 | 0 |
| Model calls | 554 | 401 | -27.6% |
| Tool calls | 555 | 407 | -26.7% |
| Input tokens | 50,117,616 | 45,963,452 | -8.3% |
| Uncached input tokens | 2,050,233 | 3,369,132 | +64.3% |
| Output tokens | 360,987 | 265,682 | -26.4% |
| Reasoning tokens | 229,397 | 164,949 | -28.1% |
| API-equivalent cost | $0.37335547 | $0.47523824 | +27.3% |
| Cost per official pass | $0.09334 | $0.11881 | +27.3% |
| Sum of task active wall time | 4,448.8s | 3,574.4s | -19.7% |
| Median task active wall time | 546.1s | 436.6s | -20.1% |
| Sum of peak context tokens | 1,585,859 | 2,105,931 | +32.8% |

The lower call, total-input, output, reasoning, and wall totals do not translate to
lower cost. Current memory has 64.3% more uncached input and a lower aggregate cache
read ratio (92.7% versus 95.9%). Its larger context/navigation exposure is the most
direct measured explanation. On the two tasks that both arms passed, current memory
cost 97.4% more despite 16.3% fewer calls.

Different stopping points remain a confound: for example, current memory stopped the
failed drizzle and narwhals tasks much earlier. On the four same-reward pairs, current
memory still cost 23.6% more and used 59.6% more uncached input while using 35.4% fewer
calls.

## PTC reuse and repeated reads

The following measurement covers canonical completed `fs.read` receipts with exact
path, source hash, and line ranges. Shell excerpts and transformed Python output are
not exhaustively mapped and remain unknown.

| Mapped read metric | PTC control | Current memory | Change |
| --- | ---: | ---: | ---: |
| `fs.read` operations | 325 | 228 | -29.8% |
| Requested source lines | 34,969 | 28,008 | -19.9% |
| Same-version repeated-overlap lines | 10,891 | 6,996 | -35.8% |
| Lines served from the live PTC catalog | 10,236 | 6,470 | -36.8% |
| Lines acquired from source | 24,733 | 21,538 | -12.9% |
| Explicit retained-handle uses | 22 | 32 | +45.5% |

Current memory requested less repeated overlap on six of eight tasks, but acquired
fewer source lines on only three of eight. The aggregate source-line reduction is
substantially influenced by early stopping on two memory failures. Both arms already
have the broker-level PTC read catalog, so control also avoided most repeated physical
reads. This is evidence of fewer repeated read requests and greater retained-handle
uptake, not a reliable cohort-wide reduction in source acquisition.

## Was learned memory exercised?

No task compacted: peak contexts remained below the current profile's 80% of
1,048,576-token threshold. The current arm exposed one checkpoint opportunity per
task and wrote a note in seven of eight tasks. Only the drizzle and prometheus notes
contained evidence-linked observations; most other notes were unsupported next-action
or hypothesis text. Arktype, one of the two memory-only wins, wrote no note. There
were no explicit memory-retrieval events.

Therefore the tied quality outcome and the two memory-only wins cannot be attributed
to reliable learned-memory recovery. This cohort primarily exercised active
navigation messages, working-note preparation, live PTC catalog reuse, and different
work-batch/context policy—not post-compaction or prior-run recall.

## Decision

Do not promote the current full profile from this result. It preserves aggregate
quality and reduces repeated `fs.read` requests and calls, but does not demonstrate
reliable learned-memory use, reliable physical reread reduction, lower cost, or
better completion calibration. The next experiment should isolate the uncached-input
increase and make evidence-linked checkpoint construction/reuse observable before
another paid broad cohort. Do not tune or retry these eight tasks; they are now
consumed diagnostics.
