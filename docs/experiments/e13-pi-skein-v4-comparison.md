# Pi + Skein PTC v4 live comparison

Run date: 2026-09-17. V4 used the same eight pinned E13 tasks and configuration as v3 and the historical Pi Code Tool baseline: three trials per task, Muse Spark 1.3 Contributor through OpenRouter, `xhigh`, 32,768 output tokens, six concurrent workers, zero campaign retries, and the four-tool Harbor profile. V3 has 23 verified trials because one Scriggo trial was interrupted; v4 and Code Tool have 24.

| Arm | Exact passes | Mean F2P | Mean partial | Median active latency | Median input | Median output | Repriced total | Repriced / pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Pi Code Tool | 21/24 (87.5%) | 95.50% | 99.762% | 17.75 min | 12.48M | 71.5K | $4.726 | $0.225 |
| Skein PTC v3 | 19/23 (82.6%) | 96.48% | 99.519% | 16.63 min | 10.29M | 76.7K | $4.362 | $0.230 |
| Skein PTC v4 | **22/24 (91.7%)** | **99.44%** | **99.794%** | **15.73 min** | 12.23M | 75.8K | $4.754 | **$0.216** |

Repricing uses $0.10/M uncached input, $0.002/M cache reads, and $0.20/M output. Provider trace `cost_usd` is much larger and is not actual Contributor billing. No isolated account-balance snapshot was taken around this run, so this report does not claim an actual debit.

| Task | Code Tool | v3 | v4 |
|---|---:|---:|---:|
| Ink grid box layout | 2/3 | 2/3 | 2/3 |
| Koota pair relation tracking | 3/3 | 3/3 | 3/3 |
| Obsidian scoped ignores | 2/3 | 2/3 | **3/3** |
| Query persisted state | 3/3 | 3/3 | 3/3 |
| Scriggo method declarations | 2/3 | 1/2 | **3/3** |
| Tengo destructuring | **3/3** | **3/3** | 2/3 |
| Testem launcher reports | 3/3 | 3/3 | 3/3 |
| Textual RichLog follow state | 3/3 | 2/3 | **3/3** |

The v4 contract changes were used. Legacy `result['data']` accesses fell from 2,403 in 2,948 v3 cells to 319 in 3,142 v4 cells. Source-validation failures fell from 37 to 13, and blocked-import failures from 24 to 3. The model did not call `verify()` or page retained results with `more`; the one-shot evidence review fired in 5/24 trials, of which four passed and the Ink failure reached 92% F2P. These observations support the simpler direct-result contract and explicit import guidance. They do not isolate the review turn as the cause of the quality gain.

V4 beat Code Tool by one exact pass, used 11.4% lower median active latency, and had 4.0% lower repriced cost per pass. Its median input was only 2.0% below Code Tool and 18.8% above v3, so v4 did not preserve v3's full token advantage. The higher pass rate makes total tokens per successful result better than Code Tool, but the direct efficiency gap is now small.

This is an independent three-trial sample, not paired random seeds. A one-pass difference is not enough to claim that v4 is intrinsically more accurate than Code Tool. The result does show that the earlier large Skein PTC accuracy regression is absent in this panel.

Raw ledgers:

- V4: `.artifacts/e13-pi-skein-v4/runs.jsonl`
- V3: `.artifacts/e13-pi-skein-v3/runs.jsonl`
- Pi Code Tool: `.artifacts/e13-ptc-isolation-pi-code/runs.jsonl`
