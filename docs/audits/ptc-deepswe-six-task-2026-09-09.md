# Notebook versus Prime PTC: six-task DeepSWE comparison

Status: completed matched provider run; efficiency result only, not a quality promotion.

## Contract

Both candidates ran the same first six DeepSWE tasks from the frozen confirm manifest
at Skein revision `d0e03a5`. Each task had one attempt, no wrapper or Pier retries,
two-task candidate concurrency, a 2,000,000 cumulative input-token ceiling, and a
32,768 output-token ceiling. Both used OpenRouter
`meta/muse-spark-1.3-contributor` with `xhigh` reasoning. The checked-in profiles
were identical outside the PTC configuration: Skein notebook-native execution versus
Prime JSONL plus snapshot execution in the authoritative Harbor task container.

Official DeepSWE verifier reward defines quality. Skein terminal status is retained
separately as a reliability diagnostic. Active agent time excludes environment and
verifier overhead; end-to-end time includes it.

## Results

| Metric | Prime PTC | Skein notebook | Notebook change |
| --- | ---: | ---: | ---: |
| Official reward | 0/6 | 0/6 | tie |
| Infrastructure failures | 0 | 0 | tie |
| Active time, total | 2,367.1 s | 1,844.9 s | -22.1% |
| Active time, median | 385.7 s | 236.0 s | -38.8% |
| End-to-end time, total | 3,091.3 s | 2,569.0 s | -16.9% |
| Input tokens | 12,161,968 | 10,500,814 | -13.7% |
| Cache-read tokens | 11,521,812 | 9,972,493 | -13.4% |
| Uncached input tokens | 640,156 | 528,321 | -17.5% |
| Output tokens | 226,868 | 181,778 | -19.9% |
| Reasoning tokens | 184,798 | 142,294 | -23.0% |
| Recorded cost | $0.1324 | $0.1091 | -17.6% |
| Tasks reaching input ceiling | 6/6 | 4/6 | -2 tasks |
| Skein terminal state | 6 failed | 4 failed, 2 blocked | diagnostic only |

Notebook used less active time on five of six paired tasks and less input on four of
six. The largest difference was `bandit-structured-nosec-directives`: notebook stopped
blocked after 183.4 seconds and 518,861 input tokens, while Prime reached the input
ceiling after 488.1 seconds and 2,023,239 input tokens. Neither result passed the
official verifier.

## Decision

Do not promote either PTC implementation on quality: the sample is 0/6 for both, so it
cannot estimate a quality difference. Keep four tools as the default. Notebook is the
more efficient PTC candidate under this contract, but the next experiment should first
diagnose why both modes fail to produce accepted completion before buying a larger run.
In particular, separate model task failure from Skein's completion/verification loop
and reduce repeated context growth; do not raise the common token ceiling merely to
hide exhaustion.

The retained scored artifacts are under
`/Users/mathiasl/skein-eval-results/deepswe-6-d0e03a5-r2/{prime,notebook}`. The earlier
`deepswe-6-b8dd5aa` directory is excluded because a multi-line JSONL serialization bug
made resume duplicate a completed row; revision `d0e03a5` fixed that bug before this
clean restart.
