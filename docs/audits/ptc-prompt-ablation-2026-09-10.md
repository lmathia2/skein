# Notebook PTC prompt ablation — 2026-09-10

## Decision

Reject the compact C4 prompt and retain the prior notebook prompt. Keep the
independently tested result-envelope, failure-stage, compact-error, and
capability-budget changes.

## Matched Koota result

Both lanes used `meta/muse-spark-1.3-contributor` through OpenRouter on
`koota-composite-trait-aspects`, sequentially, with no retries and the same
notebook PTC profile.

| Prompt | Completed | Reward | Feature tests | Preservation tests | Input tokens | Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Prior prompt (`e82f33c`) | 3 | 3/3 | 153/153 | 516/516 | 47,028,216 | $0.7272 |
| Compact C4 (`4686983`) | 2 | 1/2 | 101/102 | 344/344 | 62,154,154 | $0.7033 |

The compact lane regressed reward and consumed more input tokens in two
completed trials than the baseline consumed in three. Its third trial was
stopped at the user's direction; it is excluded from the comparison.

## Follow-up

Do not attempt another broad prompt rewrite. Any future prompt change should
be one narrow behavioral edit, tested against this baseline before landing.

