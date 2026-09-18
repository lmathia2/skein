# E13 PTC isolation: final comparison

All arms used `meta/muse-spark-1.3-contributor`, OpenRouter, `xhigh`, and `max_output_tokens=32768`. Each arm has 8 tasks × 3 fresh trials. The successful Scriggo replacement is included as Pi + Skein trial 24; the timed-out launcher attempt is excluded from outcome and latency aggregates because it produced no trial result or captured usage.

## Aggregate results

| Arm | Pass | Task pass@3 | Mean partial | Median agent time | P90 | Steps median | Input/cache/output | Cache ratio | Repriced cost | Cost/pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Pi + Code Tool | 21/24 (87.5%) | 8/8 | 0.998 | 17.8m | 31.1m | 119 | 424.4M / 388.9M / 2.0M | 91.6% | $4.726 | $0.225 |
| Pi + Skein PTC | 17/24 (70.8%) | 8/8 | 0.995 | 17.7m | 30.8m | 122 | 400.8M / 362.0M / 2.1M | 90.3% | $5.026 | $0.296 |
| Skein + Skein PTC | 9/24 (37.5%) | 6/8 | 0.841 | 13.4m | 20.3m | 66 | 196.0M / 189.2M / 1.3M | 96.6% | $1.307 | $0.145 |

The strongest result is the host-loop effect. Pi + Skein PTC passes 17/24 while Skein + the same PTC runtime passes 9/24, a 33-point gap. The outer loop, prompt/state policy, and termination behavior therefore explain more of Skein's deficit than persistent CPython alone.

Pi + Code Tool also leads Pi + Skein PTC by +16.7%, but their mean partial scores are nearly identical (0.998 versus 0.995) and the task-cluster bootstrap interval crosses zero (-4.2% to +37.5%). This suggests a real reliability edge worth testing, not proof that the Skein runtime is intrinsically less capable.

The observed Pi + Skein minus Skein pass-rate difference is +33.3%; its task-cluster bootstrap 95% interval is +16.7% to +50.0%. With only eight task clusters, this remains directional rather than a precise population estimate.

## Per-task outcomes

Each cell is `passes/3 · mean partial · mean agent minutes · task cost`.

| Task | Pi + Code Tool | Pi + Skein PTC | Skein + PTC |
|---|---:|---:|---:|
| `ink-grid-box-layout` | 2/3 · 0.991 · 22.0m · $0.519 | 1/3 · 0.986 · 22.6m · $0.453 | 1/3 · 0.892 · 15.2m · $0.189 |
| `koota-pair-relation-tracking` | 3/3 · 1.000 · 22.9m · $0.646 | 1/3 · 0.990 · 23.9m · $0.766 | 1/3 · 0.910 · 14.1m · $0.231 |
| `obsidian-linter-scoped-ignore-markers` | 2/3 · 0.991 · 26.6m · $0.984 | 3/3 · 1.000 · 16.8m · $0.389 | 2/3 · 0.999 · 15.5m · $0.199 |
| `query-persist-restored-query-state` | 3/3 · 1.000 · 11.1m · $0.343 | 3/3 · 1.000 · 10.4m · $0.385 | 2/3 · 0.993 · 18.4m · $0.119 |
| `scriggo-method-declarations` | 2/3 · 0.999 · 36.4m · $1.247 | 1/3 · 0.996 · 42.5m · $1.773 | 0/3 · 0.955 · 16.7m · $0.278 |
| `tengo-destructuring-bindings` | 3/3 · 1.000 · 15.0m · $0.518 | 3/3 · 1.000 · 15.8m · $0.733 | 1/3 · 0.710 · 11.5m · $0.167 |
| `testem-per-launcher-reports` | 3/3 · 1.000 · 12.9m · $0.207 | 3/3 · 1.000 · 11.9m · $0.199 | 2/3 · 0.973 · 8.4m · $0.071 |
| `textual-richlog-follow-state` | 3/3 · 1.000 · 14.0m · $0.262 | 2/3 · 0.987 · 14.7m · $0.327 | 0/3 · 0.295 · 5.7m · $0.053 |

## What changes in the traces

Pi + Code Tool and Pi + Skein execute long iterative loops: median 119 and 122 model/tool steps. Skein uses a median 66 model calls across 9.5 work batches. This makes Skein much cheaper and usually faster, while giving it fewer outer-loop opportunities to reassess failures and redirect the implementation.

Skein's own terminal state disagrees with the external verifier frequently: {"blocked": 19, "complete": 3, "failed": 2}. Eight of nine externally passing submissions are internally marked `blocked`, while 11 blocked runs fail externally; two runs stop `failed`. `Blocked` therefore mixes successful and incomplete work and is not a reliable completion decision. The Pi-hosted arms continue their turn loop and let Harbor verify the final workspace. This termination-policy mismatch is the clearest mechanism consistent with the accuracy difference.

Skein is dramatically more economical: $1.307 versus $5.026 for the same PTC runtime, and its median latency is 13.4 minutes versus 17.7. Pi buys more repair/retest cycles with much more context traffic. Pi + Skein has a 90.3% cache-read ratio, but repeated cached context still dominates nominal input volume; caching lowers cost without lowering request size or latency proportionally.

The quality loss is concentrated in feature completion: Skein's mean F2P is 0.584, versus 0.976 for Pi + Skein, while P2P remains 0.966. Skein usually avoids regressions but more often stops before the requested behavior is fully implemented.

Patch scope also differs. Mean changed files / line churn are Pi + Code Tool 8.0/822, Pi + Skein PTC 8.2/815, Skein + Skein PTC 5.8/469. Larger patches are not inherently better, but they indicate that the Pi loops explore and revise more of the repository before stopping.

## Prompt, state, and batching differences

**Pi + Code Tool** gives Pi a compact `code` contract, explicit persistence, generated helper signatures, clear print/return behavior, and catchable Python exceptions. The Pi transcript retains the code that created variables, while serialized interpreter state rides in tool-result details. There is no separate Skein work-packet or `AgentStep` completion protocol.

**Pi + Skein PTC** keeps Pi's same outer loop and swaps only the runtime. Its extension explicitly says that variables persist and advertises `agent.state.list/describe/reuse`. Each response also exposes `state_count`, `state_delta`, `state_preserved`, and `failure_stage` in model-visible JSON. This arm isolates the resident CPython implementation from Skein's orchestration.

**Skein + Skein PTC** adds a large notebook instruction covering capability envelopes, citations, recovery, freshness, artifact paging, mutation rules, and structured `AgentStep` output. Much of that is useful but competes with the task for attention. The full runtime computes namespace deltas and manifests, then `compact_tool_result` removes `state_count` and `state_delta`; the tested profile leaves `emit_state_updates: false`. After a work-batch boundary, the model receives a reconstructed packet without a concise list of live bindings. This is a real interface defect, but it is not evidence that persistence is unused: an AST heuristic finds prior-binding references in 1212/1739 Skein cells (69.7%). Treat namespace visibility as a targeted efficiency/recovery fix, not the primary cause of the 33-point host-loop gap.

Batching differs too. The tested Skein profile allows 48 cells per batch, yields after 24 read-only/no-progress cells, and permits only one review cell before forcing a structured decision. Pi has no equivalent outer work-batch protocol and continues one code turn at a time. Skein's median run used 66 model calls across 9.5 batches, versus roughly 120 Pi turns. The savings are substantial, but the forced boundary combines lossy state handoff with an unreliable `blocked` decision.

## Task-level reading

- `ink-grid-box-layout`: Pi Code leads 2/3 to 1/3 for both Skein-runtime arms. The Pi host does not remove the gap here, so this task is evidence for a runtime/interface reliability difference; full Skein also has lower partial credit and fewer repair steps. Mean steps were 104 / 121 / 73.
- `koota-pair-relation-tracking`: Pi Code is 3/3 while both Skein-runtime arms are 1/3. Pi + Skein spends the same large step budget as Pi Code, so under-iteration alone cannot explain this task; the Code Tool contract/runtime is the leading difference. Mean steps were 168 / 168 / 100.
- `obsidian-linter-scoped-ignore-markers`: Pi + Skein is the only 3/3 arm and is much faster and cheaper than Pi Code. The Skein runtime is fully capable here; full Skein's 2/3 result points to host-loop variance rather than an executor ceiling. Mean steps were 245 / 104 / 68.
- `query-persist-restored-query-state`: Both Pi arms are 3/3. Full Skein is 2/3 with 0.993 partial credit and is slower than either Pi arm, so its lower overall cost/latency pattern does not hold on this task. Mean steps were 96 / 104 / 72.
- `scriggo-method-declarations`: Pi Code / Pi + Skein / Skein score 2/3, 1/3, 0/3. Pi + Skein's 0.996 partial score shows near-complete failures around a strict verifier edge; it also has the longest latency and highest cost, so extra iteration is not sufficient by itself. Mean steps were 298 / 326 / 103.
- `tengo-destructuring-bindings`: Both Pi arms are 3/3, while full Skein is 1/3 with 0.710 partial credit. Holding the runtime fixed, the Pi loop recovers the whole gap; Skein stops with broad new-feature semantics unfinished. Mean steps were 102 / 132 / 78.
- `testem-per-launcher-reports`: Both Pi arms are 3/3 and full Skein is 2/3 with 0.973 partial credit at a fraction of the cost. This is the closest case to a favorable Skein efficiency tradeoff. Mean steps were 69 / 72 / 53.
- `textual-richlog-follow-state`: Pi Code / Pi + Skein / Skein score 3/3, 2/3, 0/3. Full Skein stops after 5.7 mean minutes with only 0.295 partial credit, the clearest trace of premature termination or insufficient repair cycles. Mean steps were 132 / 129 / 54.

## Recommended next experiments

1. Keep the Pi extension arm. It is the clean isolation boundary: Pi + Code Tool versus Pi + Skein PTC measures the runtime, and Pi + Skein versus Skein + PTC measures the host loop. This experiment shows that moving Skein PTC into Pi is useful and already implemented.
2. Make namespace awareness the first fix: expose bounded `state_delta` after every cell and a compact live-binding summary at every batch handoff. The data already exists; do not replay full cell output. Test this as one isolated toggle (`emit_state_updates: true`) before redesigning memory.
3. Fix Skein's termination contract: a tool/runtime failure should return control to the model with a compact error, and `blocked` without a concrete human question should route to replan/verify rather than terminate. Re-run the task strata where Pi+Skein beats Skein.
4. Shorten the always-on notebook prompt. Keep persistence, capability signatures, print discipline, mutation safety, and failure semantics in the static prompt; move citation/recovery recipes behind targeted `agent.help` calls.
5. Tune work-batch size as the efficiency knob. Compare 8-12 cell batches with the current 48-cell ceiling and add a targeted-test checkpoint after mutations. The goal is to retain most of Skein's cost advantage while adding repair opportunities.
6. Use at least 20 diverse tasks and three trials for the confirmatory run. Report task-cluster intervals and partial credit alongside pass rate; do not interpret 24 trials from eight tasks as 24 independent task samples.

## Data notes

Repriced cost uses $0.10/M uncached input, $0.002/M cache reads, and $0.20/M output. Provider-reported cost is retained in the JSON but omitted from the headline because it does not match this experiment's comparison tariff. The original Pi + Skein Scriggo job timed out after two valid trials and left one stale `running` marker; it emitted no third trial result or agent usage, so its wasted cost cannot be measured from retained artifacts.
