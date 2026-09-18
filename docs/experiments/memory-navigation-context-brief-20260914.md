# Memory and navigation context: current evidence and next steps

Date: 2026-09-14. Evaluated runtime revision:
`c41ae3ae0a80810c25667c6f02ad56b1943e17fc`.

## Executive conclusion

The current memory profile did not improve aggregate DeepSWE quality over PTC
control (both passed 4/8). It reduced total calls and repeated `fs.read`
requests, but increased uncached input by 64.3%, cost by 27.3%, and aggregate
peak context by 32.8%.

The strongest measured systemic issue is not compaction: no evaluated task
compacted. It is the construction of large, changing work-batch suffixes. On
every outer iteration the harness appends a new work packet containing task
state, selected skills, repository manifest, recent events, and, in the memory
arm, a newly rendered evidence-navigation snapshot. Those newly appended bytes
cannot have been cached on their first request. Direct yield attribution shows
that nearly all outer boundaries were caused by the one-cell review protocol,
not the profile's smaller maximum PTC batch cap.

The next implementation should make ordinary continuation packets small deltas,
reserve full recovery snapshots for actual recovery boundaries, and align PTC
batch limits between arms before another paid comparison.

## Experiment contract

This was a frozen system-level comparison of:

- PTC control: `harness/core/config/profiles/notebook-ptc-jsonl.yaml`
- Current memory: `harness/core/config/profiles/notebook-ptc-memory.yaml`

Both arms ran the same eight previously unused DeepSWE 1.1 tasks using OpenRouter
`meta/muse-spark-1.3-contributor`, reasoning `xhigh`, one attempt, no retries,
and global concurrency six. The task panel was actionlint, arktype, awilix,
drizzle-orm, httpx, IPython, narwhals, and prometheus.

This was not a one-variable memory ablation. Among other differences, the memory
profile enabled active trace-native memory, working notes, prior-run recall, and
window management, while using 24 maximum PTC cells per batch and a 12-cell
no-progress boundary. Control used 48 and 24 respectively.

## Result and trace locations

Primary comparison report:

- `docs/experiments/memory-heldout-8-20260914.md`

All relative `.artifacts/...` paths below resolve beneath
`/Users/mathiasl/src/skein/`. A `~/skein-eval-results/...` path resolves beneath
`/Users/mathiasl/skein-eval-results/`.

Raw result roots:

- PTC control:
  `/Users/mathiasl/skein-eval-results/memory-heldout-8-ptc-control-20260914`
- Current memory:
  `/Users/mathiasl/skein-eval-results/memory-heldout-8-current-20260914`

Each task result contains the following evidence under
`<result-root>/<task>/<task>/<trial>/agent/skein-state/runs/<run-id>/`:

- `metrics.db`: provider-reported token, cache, cost, latency, request-byte, and
  provider-request-region measurements in `model_usage`.
- `events/<task-hash>.jsonl`: canonical harness events, including ledger changes,
  memory notes, evidence-navigation snapshots, reads, tool outcomes, and
  verification events.
- `managed-tools.db`: canonical tool receipts used to measure read paths, hashes,
  requested ranges, physical acquisition, and retained-handle reuse.
- `traces.db`: structured trace spans.
- `ledger.jsonl`: run-local task ledger history.
- `artifacts/sha256/`: content-addressed outputs referenced by receipts and memory.

The paired arms use the same run IDs for each task:

| Task | Run ID |
| --- | --- |
| actionlint | `38a74cf9d25b388cb192e35ec84ef56e` |
| arktype | `26306bb9d4d1380741612e2133f40005` |
| awilix | `af9a6583b766e0614e9ba678224c3dde` |
| drizzle-orm | `ebb743855b2e48574966ed5b573155fc` |
| httpx | `3df7cc3bc56c74b48570dce95dbb99e0` |
| IPython | `bb22b3d2169ef7dc19824d51d221f382` |
| narwhals | `b72ecf4694c186a142fc7271c2edbd59` |
| prometheus | `c73b607a47e872e7573393041a372f66` |

The event filename is identical between paired arms for a task. The trial
directory suffix differs and is discoverable directly beneath each task root.

## Measured results

| Metric, eight tasks | PTC control | Current memory | Change |
| --- | ---: | ---: | ---: |
| Official passes | 4 | 4 | 0 |
| Model calls | 554 | 401 | -27.6% |
| Input tokens | 50,117,616 | 45,963,452 | -8.3% |
| Uncached input tokens | 2,050,233 | 3,369,132 | +64.3% |
| API-equivalent cost | $0.373355 | $0.475238 | +27.3% |
| Sum of task active wall time | 4,448.8s | 3,574.4s | -19.7% |
| Sum of peak context tokens | 1,585,859 | 2,105,931 | +32.8% |
| Cache-read ratio | 95.9% | 92.7% | -3.2 points |

Per-call measurements explain why fewer calls still cost more:

| Per-call metric | PTC control | Current memory | Change |
| --- | ---: | ---: | ---: |
| Mean input tokens | 90,465 | 114,622 | +26.7% |
| Mean uncached input tokens | 3,701 | 8,402 | +127.0% |
| Mean provider request bytes | 439,213 | 519,277 | +18.2% |
| Median dynamic-suffix estimate | 3,953 | 6,973 | +76.4% |

The cache configuration itself was stable. Across all calls in each arm there
was one static-prefix hash, one provider instruction-region hash, and one prompt
cache-key hash. This rules out changing static instructions as the primary cache
buster.

## PTC reuse and learned-memory use

For canonical completed `fs.read` receipts only:

| Read metric | PTC control | Current memory | Change |
| --- | ---: | ---: | ---: |
| `fs.read` operations | 325 | 228 | -29.8% |
| Requested source lines | 34,969 | 28,008 | -19.9% |
| Same-version repeated-overlap lines | 10,891 | 6,996 | -35.8% |
| Lines served from live PTC catalog | 10,236 | 6,470 | -36.8% |
| Lines acquired from source | 24,733 | 21,538 | -12.9% |
| Explicit retained-handle uses | 22 | 32 | +45.5% |

This demonstrates fewer mapped read requests and more explicit retained-handle
uses. It does not demonstrate reliable cohort-wide physical reread reduction:
source acquisition fell on only three of eight tasks, and early stopping on two
memory failures materially affects the aggregate.

No task compacted. Seven tasks wrote a working note, but only drizzle-orm and
prometheus produced evidence-linked observations. There were no explicit memory
retrieval events, and one memory-only win wrote no note. The live cohort therefore
did not demonstrate reliable learned-memory recovery or a causal quality benefit
from learned memory.

## Why the navigation context is oversized and uncached

### 1. A complete work packet is rebuilt at every outer boundary

`app/agent/workflow.py` calls `build_work_packet` on every workflow iteration.
The packet includes task state, conversation, selected skills, repository
manifest, compacted history or evidence navigation, recent events, and steering.
Several of those sections repeat information that the model already received.

The repeated text is placed in the dynamic suffix, after model-generated history.
Even byte-identical repeated prose at a new position is new provider input on its
first request. Stable prefix caching can cache it only on later calls that retain
that exact conversational prefix.

### 2. Memory adds a near-budget navigation snapshot to almost every packet

The memory arm emitted 146 `context.evidence_navigation_created` events. Their
model-visible `content` totalled 1,721,333 bytes; median size was 11,867 bytes,
with a 4,987-to-12,000-byte range. At a four-byte token estimate, navigation alone
introduced approximately 430,333 newly appended tokens across the cohort before
counting repeated task, skill, manifest, and recent-event sections.

Every navigation snapshot had a unique content hash. The generator keys snapshots
by invocation and work-batch iteration and includes a changing task hash and source
watermark. Some content changes are legitimate, but the current design republishes
the full current snapshot even when the useful semantic delta is small.

### 3. Repeated one-cell review creates most outer boundaries

Canonical ledgers reached 154 aggregate outer iterations in the memory arm versus
126 in control, despite the memory arm making fewer total model calls. `max_cells`
ended only three memory batches and no control batch. `review_cell_limit` ended 131
of 145 memory batches and 99 of 108 control batches. With one review cell per batch,
each review cell returns to orchestration and appends another complete work packet.

Review batches account for 68% of memory uncached input and 69% of control uncached
input. The first call after a review boundary used 10,493 median uncached tokens in
memory versus 6,222 in control; an ordinary later call used approximately 748 and
1,285 respectively. Control pays the same structural review tax, while memory adds
about 4,300 uncached tokens per boundary and encountered about 30% more boundaries.
Five of eight memory tasks reached the iteration limit versus one control task.

This is also why reread reduction and cost can move in opposite directions: the PTC
catalog can prevent a physical source read while the harness still sends another
large work packet to ask the model what to do next.

### 4. Compaction and the 32K tail setting did not cause this cohort's regression

No compaction event fired. The `recent_event_tokens: 32000` setting would affect
the exact retained tail after a cut, but there was no cut. In ordinary work packets,
recent events were also bounded to the last 12 events and each payload was truncated
to 1,000 characters. The setting should still be normalized in a clean ablation,
but it is not the direct cause measured here.

## Current issues

1. **Recovery data is on the hot path.** Full evidence recovery/navigation is sent
   during ordinary continuation, even while the PTC worker and its bindings remain
   live.
2. **Snapshots are full-state, not changed-only.** Small changes to the watermark,
   task ledger, focus, or evidence state create a new full payload.
3. **Work packets repeat durable context.** Skills, manifest, task framing, and
   overlapping recent events are republished at every outer iteration.
4. **Review and stopping policy amplify packet cost.** One review cell causes a new
   outer packet, and repeated claim/review cycles continue to the iteration cap.
   Unequal PTC limits remain an experimental confound even though they did not cause
   most boundaries in this cohort.
5. **Working notes are usually not learned evidence.** Five of seven notes contained
   plans or hypotheses without completed evidence and therefore added little safe
   reusable state.
6. **Completion remains miscalibrated.** Each arm falsely completed one Harbor
   failure, while many official passes ended locally blocked.

## Recommended implementation stages

### Stage 1: terminate review loops and remove full packets from ordinary continuation

After one bounded counterexample-review cell, require a host-visible structured
decision: verify, make a concrete fix, or block on a concrete unresolved criterion.
Do not enter another review batch on an unchanged workspace. If another model turn is
required, append only changed control state rather than another complete packet.

Reuse the live PTC catalog and retained bindings while the worker epoch is live and
there is no unresolved effect. At an ordinary work-batch boundary send only a small
control delta: current criterion, new or invalidated evidence IDs, changed paths,
validation status, and the next action. Do not include the full evidence manifest,
working set, or live-binding catalog again.

Send a full recovery snapshot only when at least one of these occurs:

- context was actually cut;
- worker epoch changed or a safe restore is pending;
- an execution effect is unresolved;
- a source dependency was invalidated;
- verification failed and exposed a concrete evidence gap;
- an explicit phase change makes a checkpoint necessary.

The existing canonical events, content hashes, worker epochs, and working-set
projection are sufficient. No new storage system is needed.

### Stage 2: make recovery append-only and changed-only

Represent each packet concern as deterministic comparison data: task contract,
criterion and next action, evidence entries, live bindings, unresolved effects,
validation status, note, selected skills, manifest, and recent events. Send immutable
sections once, and advance recent events from the last exposed event sequence.

When recovery is required, publish the full content-addressed snapshot once. On
later boundaries publish only additions, invalidations, or status changes keyed to
the prior snapshot hash. Do not mutate or regenerate an older message. This keeps
provider prefix caching valid and makes the newly uncached portion proportional to
new information.

Exclude `work_batch_id`, `task_hash`, and the current watermark from semantic diff
comparison, but retain them in a non-model-visible trace envelope with base/result
hashes, worker epoch, and source event sequence. Reset the model-visible baseline
after a cut, epoch change, recovery, or loss of the prior baseline message.

Checkpoint notes only when new evidence-backed findings exist. Do not promote an
unsupported hypothesis or next action into durable learned memory.

### Stage 3: remove the experiment confound

Use the same review/iteration policy, PTC cell limits, no-progress limits,
verification attempts, context window, and recent-tail policy in both arms. The sole
treatment difference should be whether evidence-backed memory is constructed and
conditionally exposed.

### Stage 4: verify cheaply before another broad live run

Use deterministic tests only to verify serialization, replay, provenance, safety,
and baseline-reset contracts. Do not use replay or token projections as evidence of
value. Evaluate value only with paired live model runs.

Run a small live mechanism panel covering intact-worker reuse, worker-loss recovery,
changed-source freshness, and a review-heavy coding task. Proceed to a new diverse
held-out cohort only if this live panel maintains quality and
meets all of these gates:

- at most one full navigation snapshot per unchanged worker epoch and phase;
- ordinary navigation delta no larger than 512 tokens;
- first-call uncached input at a review boundary no larger than the matched ordinary
  new-turn baseline plus the navigation-delta allowance;
- no increase in uncached input or cost versus aligned PTC control;
- no increase in model calls;
- no repeated review boundary without an intervening workspace change, newly
  identified criterion gap, or verification result;
- mapped source acquisition and repeated-read opportunities reported separately;
- every completion claim supported by completed evidence and the independent
  Harbor result reported as the quality authority.

Non-regression gates also require the established behaviors to remain intact: direct
retained-value use with a live worker, selective reacquisition of changed or missing
source, source-backed freshness across no-change shell receipts and scoped writes,
fail-closed unresolved effects, and no answer accepted without completed evidence.

## What has been tried

The table distinguishes an implemented mechanism from an outcome demonstrated in a
live run. A narrow mechanism success is not evidence that the complete memory profile
is ready as a default.

| Intervention | Live evidence | What worked | What did not work / remains unproved | Status |
| --- | --- | --- | --- | --- |
| Correct provider-token accounting, phase-aware cuts, complete call/result retention, and incremental ADK-history capture | `docs/experiments/memory-fixed-20-20260911.md`; `~/skein-eval-results/memory-fixed-canary-v{1,2,3}-20260911` | Removed deterministic compaction crashes and the old reserialization telemetry balloon. A canary cut from 56,251 to 2,684 estimated tokens and continued with the same live worker. | Forced cuts still caused substantial rediscovery and call amplification. The initial 20-task compaction arm tied quality at 4/20 but used 2,388 versus 1,288 calls and cost $0.991 versus $0.660. | Mechanism fixed; system benefit failed. |
| Evidence manifest in deterministic handoff | `docs/experiments/memory-fixed-20-20260911.md`; Luna Obsidian and Koota/Oxvg result roots recorded there | The cut carried path/hash/range evidence, preserved worker continuity, and stopped exact repeats of the newest exposed ranges. | A latest-eight flat list omitted older ranges. Koota/Oxvg still made 45/47 post-cut reads on prior paths and 36/47 on the same hashes. | Partial; representation was too shallow. |
| Eager per-cell retained-binding locators | `docs/experiments/live-worker-reuse-20260913.md`; `.artifacts/live-worker-reuse-iteration-{1,2,3}-live` | Locators were visible and usable. | They could not improve a zero-reread baseline, cost 16.2% more across three cohorts, used 30.5% more input, and twice contributed to lost live-worker integrity. | Rejected; remains opt-in. |
| On-demand live-worker state and retained-value reuse | `docs/experiments/live-worker-reuse-20260913.md`; `.artifacts/live-worker-reuse-iteration-1-live` through iteration 4 | Across all four iterations, 16/16 intact trials and 38/38 eligible delayed questions reused completed values with zero avoidable source rereads. Changed sources and missing ranges were acquired correctly. | This does not test worker loss, context cuts, semantic notes, or prior-run recall. | Demonstrated for an intact worker; keep as default. |
| Recovery recipes in ordinary handoffs | `docs/experiments/ordinary-read-recovery-20260913.md`; `.artifacts/ordinary-read-recovery-live-v1` | Both arms independently verified, all six handoffs carried recovery instructions, and provider-prefix checks passed. | Findings reread 285 tracked lines versus control's 284. Merely telling the model how to load artifacts did not make it recover instead of reread. | Failed as a behavioral fix. |
| Common continuity fixes: persistent checkpoint reminders, clean container state, and malformed tool-call handling | `docs/experiments/continuity-common-fixes-20260913.md`; `.artifacts/continuity-common-fixes-live-v1` | Removed the specific deterministic harness failures and both arms verified. | Findings had the same 171 tracked reread lines and cost 147.2% more. | Harness fixes worked; memory efficiency failed. |
| Evidence-linked checkpoint request and next-cell retained-handle consumption | `docs/experiments/learned-checkpoint-loop-20260914.md`; `.artifacts/learned-checkpoint-loop-live-v1` | In one consumed case, findings wrote a cited note, consumed `read:1..3`, made zero final-cut rereads, used two fewer calls, and cost slightly less. | Control failed the claim-ID protocol, and findings still used 6,557 more uncached tokens. This was not a clean or held-out comparison. | Positive mechanism signal only. |
| Six-pair learned-memory qualification | `docs/experiments/learned-memory-qualification-20260914.md`; `.artifacts/learned-memory-qualification-live-v1` | Findings reduced post-cut same-version overlap 82 to 40 lines, preserved changed/conflicting-source correctness, produced no stale answer, and used 11.6% fewer reasoning tokens. | Both arms verified only 4/6. Findings used two more calls, 16.5% more input, 18.2% more uncached input, and 6.4% more cost. | Reread and safety gates passed; terminal and efficiency gates failed. |
| Remove redundant review-entry checkpoints and bound counterexample review to one cell | `.artifacts/review-checkpoint-diagnostic-live-v1`; `.artifacts/bounded-review-fresh-canary-live-v1` | Removed the diagnosed note-repair loop and bounded review execution. | Findings still missed terminal verification in the fresh canary because total dynamic input remained too large. | Narrow protocol fix; not a system win. |
| Correct memory freshness for reserved memory commands and successful no-change shell receipts | `.artifacts/managed-memory-freshness-canary-live-v1`; `.artifacts/receipt-backed-freshness-canary-live-v1` | Stopped reserved memory commands from self-invalidating findings. Receipt-backed freshness and usable learned transformations were demonstrated in individual runs. One canary reduced calls 33.3%, cost 17.1%, and actual post-cut rereads 20 to 14. | The first canary still reread more than control; the stronger canary did not actually exercise the intended ordinary-shell receipt join and reread during final review. Uncached input remained higher. | Freshness bugs fixed; broader behavior mixed. |
| Preserve source identity through artifact loads and retire successful write uncertainty by exact changed path | `.artifacts/targeted-receipt-review-canary-live-v1`; `.artifacts/source-identity-canary-live-v1`; `.artifacts/scoped-write-canary-live-v1` | Fixed artifact-reference shadowing and task-wide false invalidation from successful writes. The final targeted canary kept all learned dependencies fresh through three worker-loss cuts and answer writes, verified in 17 calls, and made zero post-cut source rereads. | Across four mechanism iterations only two findings trials independently verified. In the final canary uncached input rose 62.6%, reasoning 40.5%, and cost 14.0%; control stopped earlier, so efficiency was not cleanly comparable. | Learned-memory recovery demonstrated once under a targeted protocol; reliability/cost unproved. |
| Current full memory profile on eight diverse fresh DeepSWE tasks | `docs/experiments/memory-heldout-8-20260914.md`; raw roots in this brief | Quality tied 4/8. Mapped read requests fell 29.8%, repeated-overlap lines 35.8%, and explicit retained-handle use rose 45.5%. Calls and active wall time also fell in aggregate. | No task compacted, no prior memory retrieval occurred, most notes lacked evidence, physical source acquisition improved on only 3/8, uncached input rose 64.3%, cost 27.3%, and peak context 32.8%. | Broad default-promotion gate failed. |

## What is established now

- PTC can reliably reuse retained source values while its worker remains intact. This
  is the strongest completed result and does not need more eager prompt notices.
- The harness can preserve a worker across a context cut and can construct source-
  backed findings that survive shell checks, worker loss, and scoped writes.
- Conservative provenance bugs that falsely invalidated memory have been identified
  and fixed one by one.
- Memory can reduce rereads in controlled cases, including one final targeted run
  with zero post-cut source reacquisition.

## What is not established

- Reliable learned-memory use across diverse agentic coding tasks.
- A quality improvement over aligned PTC control.
- Lower aggregate cost, uncached input, or model calls from memory.
- Useful prior-run recall in isolated benchmark tasks.
- Natural large-window compaction behavior: the latest diverse cohort never cut.
- Correct completion calibration: both arms still disagree with Harbor outcomes.

The current implementation has enough storage, provenance, recovery, and PTC reuse
machinery. Adding another memory representation is not the next move. The dominant
new measured cost is repeated dynamic work-packet construction, so the next change
should reduce full navigation exposure frequency and make ordinary continuation
changed-only.
