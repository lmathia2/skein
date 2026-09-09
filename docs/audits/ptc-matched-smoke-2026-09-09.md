# Four-mode PTC matched smoke, 2026-09-09

Status: directional one-task provider smoke; not a promotion result.

## Contract

All four trials used the same clean Git fixture, task, tests, model, reasoning,
iteration limit, cumulative input-token limit, wall deadline, and disabled memory:

- provider/model: OpenRouter `meta/muse-spark-1.3-contributor`, `xhigh`;
- task: implement an integer-only iterable sum, including generators and explicit
  rejection of booleans;
- verification: the unchanged stdlib `unittest` suite plus Skein's independent
  verifier;
- limits: 8 workflow iterations, 200,000 cumulative input tokens, 300 seconds;
- execution: sequential fresh workspaces and state roots; one attempt per mode.

The first four-tool attempt is excluded because the initial fixture imported pytest,
which was unavailable to the clean verifier interpreter. The corrected fixture uses
only the standard library. During the first ADK trial, an unchanged host workspace
could incorrectly pass static-only evaluation. Commit `0346d48` now normalizes every
evaluation request to coding mode with behavioral verification and clarifies that
container-local files are artifacts, not project mutations. The table uses the fresh
post-fix ADK trial.

## Result

| Mode | Outcome | Wall | Model calls | Input / cached tokens | Total request bytes | First → last request | Tool calls | Cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Four tools | verified | 37.6 s | 15 | 58,997 / 50,975 | 262,179 | 5,883 → 26,671 | 18 | $0.001710 |
| Skein notebook | verified | 32.7 s | 13 | 124,704 / 110,924 | 484,000 | 8,685 → 57,187 | 14 | $0.002162 |
| Prime native | verified | 25.5 s | 9 | 57,469 / 46,216 | 240,843 | 5,374 → 47,772 | 10 | $0.001877 |
| ADK Code Mode | failed at 200k input budget | 61.5 s | 15 | 204,536 / 181,038 | 928,808 | 6,766 → 112,097 | 26 | $0.003911 |

Every workspace ended with only `calc.py` changed and its two repository tests
passing. The ADK run changed the authoritative workspace through the broker, but it
exhausted the matched cumulative input budget before independent terminal verification;
it is therefore correctly scored as failed.

## Decision

Keep four tools as the default. Prime is the best result in this smoke on wall time,
model calls, and serialized request bytes, but one trivial task cannot establish coding
quality or recovery behavior. Notebook PTC reduced calls and wall time but more than
doubled input tokens. ADK Code Mode's request growth is a blocking efficiency issue.

Do not extract a shared coordinator/runtime/serializer/state layer from this result.
The modes still differ materially in execution authority and failure behavior, and the
ADK path needs context investigation before a broader matched run. The next paid gate
remains the frozen representative task comparison; Prime also needs a local-workspace
adapter because its native effects cannot be treated as Harbor-brokered operations.
