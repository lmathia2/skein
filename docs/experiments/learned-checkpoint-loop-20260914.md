# Learned checkpoint loop diagnostic

## Question

Does requesting evidence-linked learned state at actual workflow boundaries cause the
model to create and reuse useful findings, while preserving completed-evidence quality
and reducing repeated acquisition or reasoning work?

## Frozen first diagnostic

- Case: `reuse_config_3`, one fresh-state repetition.
- Arms: `no_recall` and `findings`, dispatched concurrently.
- Model: OpenRouter `openai/gpt-5.6-luna`, reasoning `max`.
- Limits: 24 calls, 350,000 cumulative input tokens, 8,192 output tokens and 900 seconds
  per trial; no retries or selective replacement.
- Cached command image:
  `sha256:c50fcbacd80c6e4b42e18fedf0f8f4bcb2c591ef9a04e4dba39e588e095d4b8e`.
- Output: `.artifacts/learned-checkpoint-loop-live-v1`.
- Historical directional comparison:
  `.artifacts/checkpoint-feedback-live-20260913-v1` (`reuse_config_3` used 19 control
  calls/$0.03058721 and 13 findings calls/$0.02609231).

## Gates

Both arms must reach independent verification with completed decisive evidence and no
unknown effects, accounting gaps or prefix mutation. Inspect every checkpoint request,
note revision, finding kind/citation, retained-handle consumption receipt, catalog reuse,
new source selection and post-cut reread. Report model calls, input/uncached/output/
reasoning tokens, cost and active wall time.

This is a consumed-case diagnostic, not held-out qualification. Stop after the pair if
the mechanism is not exercised, either arm fails verification, or findings increase cost
without reducing calls/reacquisition. Do not promote defaults or expand to DeepSWE from
this pair alone.

## Result

The concurrent pair ran at commit `34c6d21`. The findings arm reached first independent
verification with all three answers source-supported. Its phase-entry request was exposed
at review, the model wrote note version 3 from completed evidence, and the following PTC
cell consumed `read:1`, `read:2`, and `read:3`. It made no same-version source reread after
the final cut. The control also consumed the three answer handles, but first reread the
unchanged five-line `environments.toml` source.

| Arm | Accepted | Calls | Input | Uncached | Output | Reasoning | Cost | Wall | Same-version reread |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_recall` | no: claim-ID protocol error | 17 | 187,321 | 38,815 | 16,046 | 10,110 | $0.03192652 | 233.5s | 5 lines |
| `findings` | yes: first verification | 15 | 186,085 | 45,372 | 14,239 | 9,511 | $0.03124181 | 211.8s | 0 lines |

The findings arm therefore used 2 fewer calls, 1,236 fewer input tokens, 1,807 fewer
output tokens, 599 fewer reasoning tokens, $0.00068471 less provider cost and 21.6s less
wall time. Uncached input was 6,557 tokens higher. These are paired observations, not an
effect estimate from one consumed case.

Promotion remains **hold**. The control artifacts are correct, but its final completion
claim contained duplicate, unknown or stale criterion IDs and failed before the verifier;
therefore the pair is not a clean quality comparison. The run demonstrates live checkpoint
uptake, evidence-linked note revision and retained-value consumption, plus directional
reread/call improvement. It does not demonstrate held-out reliability, a stable cost win,
or causal semantic dependence on memory.

Artifacts: `.artifacts/learned-checkpoint-loop-live-v1/results.json`, `summary.json`, and
the two arm directories containing `result.json` and `state/ledger.jsonl`. No paid run
remains active.
