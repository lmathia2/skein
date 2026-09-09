# Four-tool, notebook, and Prime: six-task DeepSWE comparison

Status: completed matched provider run; efficiency result only, not a quality promotion.

## Contract

All three candidates ran the same first six DeepSWE tasks from the frozen confirm
manifest. The PTC runs used Skein revision `d0e03a5`; the later four-tool baseline used
`1fdb18`, whose only intervening change was this audit and its TODO link. Each task had
one attempt, no wrapper or Pier retries,
two-task candidate concurrency, a 2,000,000 cumulative input-token ceiling, and a
32,768 output-token ceiling. All used OpenRouter
`meta/muse-spark-1.3-contributor` with `xhigh` reasoning. The two PTC profiles were
identical outside the PTC configuration: Skein notebook-native execution versus Prime
JSONL plus snapshot execution in the authoritative Harbor task container. Four tools
used its checked-in default profile, including disabled memory and managed context.

Official DeepSWE verifier reward defines quality. Skein terminal status is retained
separately as a reliability diagnostic. Active agent time excludes environment and
verifier overhead; end-to-end time includes it.

## Results

| Metric | Four tools | Prime PTC | Skein notebook | Notebook vs baseline |
| --- | ---: | ---: | ---: | ---: |
| Official reward | 0/6 | 0/6 | 0/6 | tie |
| Infrastructure failures | 0 | 0 | 0 | tie |
| Active time, total | 2,231.9 s | 2,367.1 s | 1,844.9 s | -17.3% |
| Active time, median | 351.0 s | 385.7 s | 236.0 s | -32.7% |
| End-to-end time, total | 2,949.4 s | 3,091.3 s | 2,569.0 s | -12.9% |
| Input tokens | 12,036,929 | 12,161,968 | 10,500,814 | -12.8% |
| Cache-read tokens | 11,384,589 | 11,521,812 | 9,972,493 | -12.4% |
| Uncached input tokens | 652,340 | 640,156 | 528,321 | -19.0% |
| Output tokens | 246,504 | 226,868 | 181,778 | -26.3% |
| Reasoning tokens | 201,004 | 184,798 | 142,294 | -29.2% |
| Recorded cost | $0.1373 | $0.1324 | $0.1091 | -20.5% |
| Tasks reaching input ceiling | 5/6 | 6/6 | 4/6 | -1 task |
| Skein terminal state | 5 failed, 1 blocked | 6 failed | 4 failed, 2 blocked | diagnostic |

Notebook used less active time on five of six paired tasks and less input on four of
six. The largest difference was `bandit-structured-nosec-directives`: notebook stopped
blocked after 183.4 seconds and 518,861 input tokens, while Prime reached the input
ceiling after 488.1 seconds and 2,023,239 input tokens. Neither result passed the
official verifier.

## Decision

Do not promote either PTC implementation on quality: the sample is 0/6 for all three,
so it cannot estimate a quality difference. Keep four tools as the default. Notebook
is the most efficient candidate under this contract. Prime was 6.1% slower by total
active time and used 1.0% more input than four tools, although it cost 3.5% less.
The next experiment should first
diagnose why both modes fail to produce accepted completion before buying a larger run.
In particular, separate model task failure from Skein's completion/verification loop
and reduce repeated context growth; do not raise the common token ceiling merely to
hide exhaustion.

The retained scored artifacts are under
`/Users/mathiasl/skein-eval-results/deepswe-6-d0e03a5-r2/{four-tool,prime,notebook}`.
The earlier
`deepswe-6-b8dd5aa` directory is excluded because a multi-line JSONL serialization bug
made resume duplicate a completed row; revision `d0e03a5` fixed that bug before this
clean restart.
