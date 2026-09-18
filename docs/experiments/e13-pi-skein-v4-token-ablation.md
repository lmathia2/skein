# Pi + Skein PTC v4 token ablation

Eight E13 tasks, one fresh trial per task and arm, Muse Spark 1.3 Contributor, `xhigh`, and six total concurrent workers. Control was frozen v4. The treatment shortened generated signatures, removed routine new-variable footers, and reduced model-facing observations from 50 KB to 24 KB with head+tail retention. Errors, shell diagnostics, mutation receipts, and evidence review were unchanged.

| Arm | Exact passes | Mean F2P | Median input | Median output | Median active latency | Repriced total |
|---|---:|---:|---:|---:|---:|---:|
| v4 control | **8/8** | **100%** | **9.50M** | **66.4K** | **15.76 min** | **$1.469** |
| compact treatment | 6/8 | 95.94% | 12.20M | 77.1K | 19.15 min | $2.357 |

The treatment fails the promotion gate. Median input increased 28.4%, latency increased 21.5%, and repriced cost increased 60.4%. It failed Ink at 20/25 F2P and Scriggo at 42/48 F2P; all P2P tests passed. Control passed every task.

The treatment produced longer trajectories: 1,173 cells versus 976, with median cells per trial rising from 102.5 to 115. The largest expansion was Scriggo, 396 versus 262 cells. Neither arm paged retained results. Reducing observation bytes did not reduce total context because the model needed more turns. The treatment code was removed; frozen v4 remains the default.

## Trace diagnosis

The two arms returned almost the same amount of model-facing tool text: about 2.59 MB for control and 2.58 MB for compact. Compact nevertheless used 137.3M input tokens versus 96.8M for control. The difference is transcript replay across extra turns, not observation volume. Compact used 1,173 cells versus 976 and made most calls one helper at a time. Scriggo alone grew from 262 to 396 cells and from 31.1M to 61.7M input tokens.

The 24 KB cap was not the direct cause of either failure. Ink truncated two results: a Yoga crash whose exit and stack tail remained visible, and lint output. Scriggo truncated one paired source read. Neither trial requested the retained continuation, but the omitted text did not contain the failing grader behavior.

Ink's implementation chose a Yoga measure-node design that detaches grid children. It exercised column sizing extensively but did not assert constrained `gridTemplateRows` behavior. The five grader misses were all row sizing or parent/child constraint interactions: row `fr`, two `minmax` row cases, mixed row tracks, and a grid nested in flexbox. The final claim that these cases were verified was stronger than the trace evidence. Control instead applied grid layout after Yoga had computed the surrounding constraints, and passed all 25 feature tests.

Scriggo's trace exposed its failure before finalization. Its own interface probe printed a zero value instead of 42 and continued to report `iface-ptr RUN FAIL: reflect: call of reflect.Value.Interface on zero Value`. The probe returned exit 0 because it only printed errors, so the harness counted it as successful verification. The final response then claimed pointer-interface dispatch worked. The grader failures match that evidence: pointer and struct receivers panicked, while string/error interface calls lost receiver values and returned empty strings. Control used a dedicated runtime binding path and passed all 48 feature tests.

The current evidence-review trigger therefore has a narrow blind spot: it treats any recognized test command with shell status 0 as proof after the latest mutation. It cannot detect a custom probe that prints failure or the wrong value but exits successfully. `verify()` was not used in either arm.

## Recommended next steps

1. Keep frozen v4, including the 50 KB head+tail observation cap. Do not promote any part of the compact treatment.
2. Stop tuning observation size on E13. V4 already has the best cost per exact pass on the 24-trial comparison, and this run shows that fewer bytes can increase total tokens by increasing turns.
3. Make one small verification change before the larger panel: add a compact instruction that custom probes must assert expected output and exit nonzero on mismatch. Do not add another automatic review turn to every task; that would increase transcript replay and latency.
4. Run the fresh, disjoint 20-task manifest on frozen v4 versus Pi Code Tool. Use at least three trials per task if estimating pass-rate differences; use one trial first only as an operational/cost check. The current eight tasks have now influenced implementation decisions and should not be the generalization set.
5. Optimize only after the 20-task traces identify a repeated cause. The viable token target is fewer model turns. Observation truncation, prompt shortening, and footer removal are too small to matter unless they reduce turns, and this combined treatment moved turns in the wrong direction.

This was one stochastic trial per task and the treatment changed three variables together, so it does not identify which compact change caused the longer trajectories. It is sufficient to reject the combined treatment, not to assign causality among its three parts.

Raw ledgers:

- Control: `.artifacts/e13-v4-token-ablation/control/runs.jsonl`
- Compact: `.artifacts/e13-v4-token-ablation/compact/runs.jsonl`
