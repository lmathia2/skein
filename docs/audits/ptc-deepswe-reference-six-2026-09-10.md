# Matched DeepSWE reference-six PTC comparison

Status: complete. Keep four tools as the default; improve notebook verification and
REPL ergonomics before buying a larger quality run.

## Result

On the exact six tasks from the earlier `remaining-6` notebook experiment, the
current matched run scored as follows. Each task cell is **official reward
(verifier partial score)**, then active latency, recorded cost, and input tokens.
The aggregate row reports means per task and, on its second line, quality-normalized
cost and input tokens per accepted task.

| Use case | Four tools | Skein notebook PTC | Prime JSONL PTC |
| --- | --- | --- | --- |
| **Aggregate mean** | **3/6 (50%)**<br>820.1 s · $0.0392 · 5.14M input / task<br>$0.0784 · 10.28M input / pass | **2/6 (33%)**<br>738.4 s · $0.0281 · 4.69M input / task<br>$0.0843 · 14.06M input / pass | **1/6 (17%)**<br>818.2 s · $0.0309 · 4.11M input / task<br>$0.1856 · 24.65M input / pass |
| Kombu dead lettering | 0 (.9940)<br>914.8 s · $0.0587 · 7.76M input | 0 (.9940)<br>692.0 s · $0.0324 · 5.87M input | 0 (.9913)<br>497.6 s · $0.0232 · 2.95M input |
| Koota composite aspects | **1 (1.0)**<br>1,151.3 s · $0.0616 · 11.79M input | 0 (.9955)<br>863.1 s · $0.0509 · 11.06M input | 0 (.9865)<br>1,481.5 s · $0.0606 · 11.59M input |
| Ofetch circuit breaker | **1 (1.0)**<br>693.2 s · $0.0272 · 1.70M input | **1 (1.0)**<br>416.0 s · $0.0140 · 1.26M input | 0 (.2167)<br>543.0 s · $0.0112 · 0.85M input |
| Testem bail on failure | 0 (.9896)<br>1,103.1 s · $0.0463 · 5.78M input | 0 (.9931)<br>1,535.9 s · $0.0374 · 6.53M input | 0 (.9879)<br>1,033.3 s · $0.0447 · 4.88M input |
| Textual Kitty key phases | 0 (.9000)<br>699.1 s · $0.0282 · 2.82M input | 0 (.8875)<br>431.2 s · $0.0200 · 2.49M input | 0 (.9875)<br>793.6 s · $0.0274 · 3.23M input |
| Wazero snapshots | **1 (1.0)**<br>359.0 s · $0.0130 · 0.99M input | **1 (1.0)**<br>492.1 s · $0.0138 · 0.91M input | **1 (1.0)**<br>559.9 s · $0.0185 · 1.15M input |

Notebook PTC used 10.0% less total active time, 26.6% less median active time,
8.8% fewer input tokens, 25.3% fewer output tokens, 17.1% fewer reasoning tokens,
and 28.3% less recorded cost than four tools. It nevertheless lost one accepted
task, so its quality-normalized efficiency was worse: $0.0843 and 14.06 million
input tokens per pass, versus $0.0784 and 10.28 million for four tools.

End-to-end time, which includes environment and verifier overhead, was 5,200.8 s
for four tools, 4,707.5 s for notebook PTC, and 5,188.9 s for Prime. Median
end-to-end time was 848.5 s, 657.6 s, and 728.7 s respectively. Cache-read ratios
were 96.3%, 97.6%, and 96.5%. The three lanes together recorded $0.589246078 of
cost; OpenRouter key usage rose by the same amount, from $118.935079886 to
$119.524325964. The key endpoint exposed no spending limit or remaining balance.

This is a six-task, single-attempt result. It supports a concrete debugging plan,
not a statistically reliable model-quality ranking.

## Experimental contract

All lanes used:

- Git revision `7c8ddccc6f2a21bedc45ed5ef29f100dc38543a1` with an empty diff digest
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
- OpenRouter model `meta/muse-spark-1.3-contributor` with provider-default
  reasoning and output limits (`reasoning: null`, `max_output_tokens: null`);
- manifest `/Users/mathiasl/src/skein/tests/eval/manifests/evaluation-ablation-v1.json`,
  declared contract digest
  `ae5b84f02955344b57430426283febb4f1c6794084b533049e3d046651253563`
  and file SHA-256
  `9912b030b6091e0f89b1fb99a1d930b6af94ea340057ebc09c30dd7664f6efdc`;
- suite `broader`, one attempt, candidate concurrency one, Pier concurrency one,
  no wrapper retry, no Pier retry, and fail-fast scheduling;
- `max_iterations=24`, cumulative task input ceiling `1,000,000,000`, agent wall
  time `5,400` seconds, and per-task runner timeout `7,200` seconds;
- a fresh Harbor container per task, with the immutable task image cached rather
  than rebuilt between tasks.

Only the checked-in profile changed:

| Mode | Exact profile | SHA-256 | Effective module choice |
| --- | --- | --- | --- |
| Four tools | `harness/config/profiles/four-tool.yaml` | `c386f00a6de88be5503ba206f0952ece766d488f53dbd568345b3e6b34921b91` | PTC off; memory off; direct `read`, `bash`, `edit`, `write` |
| Notebook PTC | `harness/config/profiles/notebook-ptc-jsonl.yaml` | `d033dd726416357cf2769c6ccdf49366357c8a5fd7a90d16bdf66fb03e329d25` | `skein_notebook`; canonical JSONL memory; exact PTC history |
| Prime PTC | `harness/config/profiles/prime-ptc-jsonl.yaml` | `914a91ba77704827ea5b0ee1dce1c854d5fe9e8ea3b5e01873ead36ed9d1b642` | `prime_repl`; JSONL cells; snapshot state; canonical JSONL memory |

The authoritative resolved configuration for every trial is retained at
`<trial>/agent/skein-state/evaluation/config.yaml`. The exact Pier argv is retained
at `<task-attempt>/commands.jsonl`. To reproduce a profile byte-for-byte, use
`git show 7c8ddccc6f2a21bedc45ed5ef29f100dc38543a1:<profile-path>` rather than the
working-tree copy.

The runner command contract was:

```text
.venv/bin/python scripts/run_harbor_eval.py \
  --suite broader --attempts 1 --concurrency 1 --retries 0 --stop-on-error \
  --provider openrouter --model meta/muse-spark-1.3-contributor \
  --provider-defaults --max-task-input-tokens 1000000000 \
  --config <exact-profile> \
  --task-id kombu-virtual-queue-dead-lettering \
  --task-id koota-composite-trait-aspects \
  --task-id ofetch-per-origin-circuit-breaker \
  --task-id testem-bail-on-test-failure \
  --task-id textual-kitty-key-phases \
  --task-id wazero-multi-module-snapshots \
  --jobs-dir <exact-lane-root>
```

The lanes ran sequentially to avoid Docker and CPU contention in latency results.
Provider conditions can still drift over their several-hour wall-clock span.

## Quality by task

Official Harbor verifier reward, not Skein terminal status, is the quality result.
The consolidated table under **Result** includes the verifier's partial score in
parentheses and the paired efficiency measurements for every task.

Skein's own terminal state is diagnostic only. For example, four-tool Wazero had
`runtime_failed` while Harbor awarded 1, and notebook Koota was internally
`complete` while Harbor awarded 0. Internal repository tests cannot substitute for
the isolated benchmark verifier.

## Trace-aligned task review

The phase proxies below are deterministic trace facts: outer model reservations,
PTC cells, nested capability requests, patch size, local validation, and isolated
verifier outcome.

### Kombu: common semantic miss

- Exploration: four tools used five outer model calls; notebook used three calls,
  80 cells, 110 brokered capability requests, and one no-progress yield; Prime used
  three calls and 49 cells.
- Implementation: patches were 826, 753, and 712 lines respectively.
- Verification: four tools and notebook both passed 67/76 feature tests and all
  1,412 preservation tests. Their nine failures were the same TTL/dead-letter
  behaviors.
- Conclusion: notebook was faster and cheaper without changing quality. The miss is
  task reasoning/test coverage shared with the baseline, not evidence of notebook
  persistence failure.

### Koota: the actionable notebook quality delta

- Exploration: four tools used two outer model calls. Notebook used three calls,
  100 cells, 166 capability requests, and two forced work-batch yields. Prime used
  two calls and 113 cells.
- Implementation: four tools changed 19 files in a 2,057-line patch. Notebook
  changed 11 files in 1,386 lines. Prime changed 21 files in 1,797 lines.
- Verification: all three passed the repository's visible suite. Notebook also ran
  several ad hoc `tsx` probes for `Not`, `Changed`, `Added`, `Removed`, callbacks,
  nesting, relations, and distribution. Its `Removed` probe covered the
  complete-to-incomplete transition but never tested an entity which had only one
  constituent and was therefore never complete.
- Isolated result: notebook passed 50/51 feature tests. The sole failure was
  `Removed modifier > should not match entity that never had all constituents`.
  Four tools passed 51/51. Prime missed three modifier cases.
- Conclusion: the notebook implementation was nearly correct. The evidence points
  to an acceptance-test matrix gap, not lost notebook state or an inability to edit
  the repository.

The decisive notebook evidence is in the Koota notebook and canonical event trace
listed under **Artifacts**. `nb search --scope source --cell-type code <notebook> removed`
finds the transition probes; there is no source match for `never had`.

### Ofetch: notebook efficiency win with equal quality

- Notebook used three outer calls, 30 cells, and 44 capabilities versus four outer
  calls for the baseline.
- Notebook's 301-line patch was smaller than the baseline's 575-line patch.
- Both passed all 47 feature and 13 preservation tests. Notebook used 416.0 active
  seconds and 1.26 million input tokens versus 693.2 seconds and 1.70 million for
  four tools.
- Prime ended with `run_total_timeout`, produced no patch, and failed all feature
  tests. This is a Prime runtime/control-flow failure, not a notebook result.

### Testem: churn around an incomplete shared solution

- Notebook used four outer calls, 87 cells, 101 capabilities, three failed cells,
  and two work-batch yields. Four tools used three outer calls; Prime used three
  outer calls and 59 cells.
- All three missed the same four feature behaviors: bail exit count, dot/TAP
  summaries, and `getBailReport`. Notebook passed all 489 preservation tests while
  four tools and Prime each introduced two preservation failures.
- Notebook took 1,535.9 active seconds, 39.2% longer than four tools, despite the
  same official result. This is the clearest trace for tightening the no-progress
  review and forcing a requirement-to-test reconciliation before more edits.

### Textual: strategy variance dominates

- Four tools and notebook made similarly sized three-file patches; notebook used
  two outer calls, 46 cells, and 54 capabilities.
- Notebook passed 14/23 feature tests versus 15/23 for four tools. Prime passed
  22/23 and missed only shifted alternate-key metadata.
- Because both PTC implementations moved in opposite directions, this task does not
  support a general PTC-surface conclusion. It does support replaying the successful
  Prime approach as a diagnostic input for a future notebook prompt/verification
  experiment.

### Wazero: stable success

- All modes passed 78/78 feature and 2/2 preservation tests with comparable
  two-file patches.
- Notebook used 27 cells and 34 capabilities. One rejected direct `open()` call
  caused a clean kernel restart, after which the task still passed.
- This is positive evidence that notebook isolation, brokered filesystem access,
  editing, verification, and persistence can complete a repository-scale task.

## Notebook execution findings

Across six notebook runs, the canonical traces contain 370 cells and 509 capability
requests: 173 successful reads, 176 successful shell calls, 128 successful edits,
and 23 successful writes. Seven cells failed:

| Failure | Count | Consequence |
| --- | ---: | --- |
| Python syntax error from over-escaped generated code | 3 | No effect, but the kernel epoch was discarded |
| Guarded direct `pathlib` import or `open()` call | 2 | Rejected before execution, but the epoch was discarded |
| Capability result-shape `KeyError('data')` | 2 | Read-only nested calls completed, then Python failed |

Those failures created four kernel epochs in Koota, four in Testem, and two in
Wazero. The other three tasks stayed in one epoch. There is no cross-task state
leakage: every task used a fresh Harbor container, distinct run/notebook identity,
and separate ledger.

## Recommended next units of work

1. **Make PTC verification criterion-shaped.** Before notebook PTC can return
   `done`, require a durable acceptance matrix which maps each behavioral clause to
   positive, negative, and transition evidence. Seed temporal words such as
   `Added`, `Removed`, `before`, `after`, and `never` into the matrix. The Koota
   regression should become the deterministic fixture: a Removed aspect must not
   match an entity that was never complete. Keep this as a PTC prompt/view
   experiment first; promote it to shared verification only if it improves both
   quality and cost on replay.
2. **Do not restart on proven pre-execution failures.** Syntax and source-policy
   validation happen before user code executes and have `effect: none`. Preserve
   the existing kernel epoch for those two classes, while retaining discard and
   reconciliation for runtime failures or uncertain effects. A focused test must
   prove the namespace is unchanged. This would have avoided five of seven kernel
   resets in this run without weakening transaction safety.
3. **Finish the common nested-result envelope.** The trace shows models alternating
   between `result["data"]` and `result["model_text"]`, causing two KeyErrors.
   Make every `agent.fs.*` and `agent.shell.*` result expose the same compact fields
   (`status`, `model_text`, `effect`, truncation and artifact references), retain
   typed detail under one predictable field, and include one short executable
   example in the PTC prefix. Do not add another top-level tool.
4. **Replan earlier on read-only churn, experimentally.** Koota and Testem each hit
   the 24-cell no-change yield and later the 48-cell batch limit. Compare the current
   `24/48` policy with `12/36`, with the acceptance matrix included. Do not simply
   lower the limit globally: Ofetch and Wazero show that compact uninterrupted PTC
   loops can be effective.
5. **Rerun only the discriminating tasks.** First rerun Koota, Testem, and Textual
   for three seeds per mode. Koota measures the lost baseline pass, Testem measures
   long-loop reconciliation, and Textual measures strategy variance. Require
   notebook to recover the Koota pass without losing Ofetch/Wazero and without
   increasing median uncached input or cost per pass before expanding the suite.

Do not change container isolation, notebook serialization, or the memory backend
from this result. The traces show no leakage or persistence corruption, and changing
those variables would confound the next test.

## Artifacts

Lane roots:

- Four tools: `/Users/mathiasl/skein-eval-results/deepswe-reference-six-four-tool-7c8ddcc`
- Notebook PTC: `/Users/mathiasl/skein-eval-results/deepswe-reference-six-notebook-ptc-7c8ddcc`
- Prime PTC: `/Users/mathiasl/skein-eval-results/deepswe-reference-six-prime-ptc-7c8ddcc`

Each root contains `run-metadata.json`, append-only `runs.jsonl`, one task-attempt
directory per row, the exact `commands.jsonl`, Pier logs, model patch, isolated
verifier reports, and file hashes. The canonical trace for every task is found with:

```text
find <lane-root> -path '*/agent/skein-state/runs/*/events/*.jsonl' -type f | sort
```

The exact notebook and run-ledger documents are found with:

```text
find <notebook-root> -path '*/agent/skein-state/runs/*/notebooks/*.ipynb' -type f | sort
find <ptc-root> -path '*/agent/skein-state/runs/*/ledger.jsonl' -type f | sort
```

The quality-discriminating Koota evidence is:

- four-tool trace:
  `/Users/mathiasl/skein-eval-results/deepswe-reference-six-four-tool-7c8ddcc/002-koota-composite-trait-aspects-attempt-01/002-koota-composite-trait-aspects-attempt-01/94ac4d52d173cc22827714c56d74f098__GCxUbmM/agent/skein-state/runs/1de61fd3f55ecef07fef1e6d0df8cb2d/events/7bb383a7d19d743072d0de373c35ac681d00ac5f9d3f2013a38007bbc26fda5a.jsonl`
- notebook trace:
  `/Users/mathiasl/skein-eval-results/deepswe-reference-six-notebook-ptc-7c8ddcc/002-koota-composite-trait-aspects-attempt-01/002-koota-composite-trait-aspects-attempt-01/94ac4d52d173cc22827714c56d74f098__jpths8v/agent/skein-state/runs/1de61fd3f55ecef07fef1e6d0df8cb2d/events/7bb383a7d19d743072d0de373c35ac681d00ac5f9d3f2013a38007bbc26fda5a.jsonl`
- notebook document:
  `/Users/mathiasl/skein-eval-results/deepswe-reference-six-notebook-ptc-7c8ddcc/002-koota-composite-trait-aspects-attempt-01/002-koota-composite-trait-aspects-attempt-01/94ac4d52d173cc22827714c56d74f098__jpths8v/agent/skein-state/runs/1de61fd3f55ecef07fef1e6d0df8cb2d/notebooks/7bb383a7d19d743072d0de373c35ac68.ipynb`
- Prime trace:
  `/Users/mathiasl/skein-eval-results/deepswe-reference-six-prime-ptc-7c8ddcc/002-koota-composite-trait-aspects-attempt-01/002-koota-composite-trait-aspects-attempt-01/94ac4d52d173cc22827714c56d74f098__JkkGhYD/agent/skein-state/runs/1de61fd3f55ecef07fef1e6d0df8cb2d/events/7bb383a7d19d743072d0de373c35ac681d00ac5f9d3f2013a38007bbc26fda5a.jsonl`

Use `nb read --no-output` and `nb search` for notebook inspection; notebook JSON is
not an execution or historical authority. The event logs and Harbor verifier reports
remain the authoritative evidence for this audit.
