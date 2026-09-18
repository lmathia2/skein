# Pi + Skein PTC v4.1 versus Pi Code Tool

Final results for the saved 20-task, three-trial DeepSWE panel.

- Manifest: `tests/eval/experiments/e13-code-mode-20-v2.json`
- PTC v4.1: `.artifacts/e13-muse-20-v4.1-pi-skein-ptc`
- Code Tool baseline: `.artifacts/e13-muse-20-v3-pricing-fixed-pi-code-tool`
- Model: `meta/muse-spark-1.3-contributor`, `xhigh`
- Trials: 60 per arm; no retries or invalid trials

## Provenance

- The single `PTC v4.1` commit containing this report records both immutable `runs.jsonl` hashes in its commit message, binding the results to that commit without adding a provenance-only commit.
- Source-equivalent pre-squash implementation commit: `f019d1c841760498e604dbb284b3504420becefc`
- Both runs recorded base revision `55a6856827b628f638356a22292d27066ef7070f` plus a dirty-tree digest in `run-metadata.json`.
- PTC v4.1 dirty-tree SHA-256: `198dc46f5334658c75742340dfd910d7784f14bf01e00a5c4fef020b5fdaf854`
- PTC `runs.jsonl` SHA-256: `f00309f789f24c9017f30568014f5da55b2276a5536fc7b0511a54ac4bd7c385`
- Code Tool dirty-tree SHA-256: `be219d8254a4dc0ddb95a53dd308b1c697b59f83870f635549f0d1a7b4af025f`
- Code Tool `runs.jsonl` SHA-256: `cd6ba9e216435b0e6c7193bfd74ec3c3fffc77f93a75a5d6b340607db7227fc6`

The arms ran sequentially with different concurrency: Code Tool used 3 and PTC v4.1 used 6. This does not change official rewards, token counts, or provider pricing, but host contention can affect per-trial latency. Treat the latency comparison as operational rather than a controlled same-concurrency estimate.

## Aggregate results

| Metric | PTC v4.1 | Code Tool | PTC difference |
|---|---:|---:|---:|
| Official reward | **39/60 (65.0%)** | 37/60 (61.7%) | +3.3 pp |
| Mean F2P | 94.897% | **96.428%** | -1.531 pp |
| Mean P2P | 99.964% | **99.980%** | -0.017 pp |
| Mean partial credit | 99.028% | **99.410%** | -0.382 pp |
| Actual OpenRouter cost | $10.440 | **$9.122** | +14.5% |
| Cost per trial | $0.174 | **$0.152** | +14.5% |
| Cost per official pass | $0.268 | **$0.247** | +8.6% |
| Input tokens | 622.80M | **560.14M** | +11.2% |
| Output tokens | 3.98M | **3.74M** | +6.4% |
| Median agent latency | 1,103.9s | **888.3s** | +24.3% |
| Mean agent latency | 1,128.0s | **921.9s** | +22.4% |
| Mean model steps | 107.9 | **92.9** | +16.1% |

The two-pass difference is not statistically persuasive on this panel. A naive independent 95% interval for the pass-rate difference is approximately -13.9 to +20.6 percentage points. At task level, PTC won five tasks, Code Tool won four, and eleven tied; the exact sign test on the nine non-ties is not significant.

## Per-task official passes

| Task | PTC v4.1 | Code Tool | Winner |
|---|---:|---:|---|
| actionlint | 3 | 3 | Tie |
| anko | 3 | 3 | Tie |
| pebble | 1 | 2 | Code Tool |
| scc | 2 | 1 | PTC |
| updo | 1 | 0 | PTC |
| true-myth | 3 | 3 | Tie |
| sql-formatter | 3 | 3 | Tie |
| arktype | 2 | 1 | PTC |
| clack | 1 | 1 | Tie |
| claude-code-by-agents | 1 | 1 | Tie |
| meriyah | 3 | 3 | Tie |
| quill | 0 | 1 | Code Tool |
| narwhals | 3 | 3 | Tie |
| tomlkit | 3 | 3 | Tie |
| numba | 2 | 3 | Code Tool |
| httpx | 3 | 3 | Tie |
| skrub | 2 | 0 | PTC |
| pwntools | 2 | 2 | Tie |
| bandit | 0 | 1 | Code Tool |
| oxvg | 1 | 0 | PTC |

The largest remaining continuous-quality regression is `claude-code-by-agents`: PTC averaged 52.4% F2P versus Code Tool's 90.5%, despite both scoring one official pass. Bandit and Pebble also favored Code Tool. OxVG and Updo favored PTC.

## Trace findings

| Trace measure | PTC v4.1 | Code Tool | Difference |
|---|---:|---:|---:|
| Code cells | 6,375 | 5,514 | +15.6% |
| Helper calls | 7,680 | 6,791 | +13.1% |
| Mean model steps | 107.9 | 92.9 | +16.1% |
| `verify()` calls | 1,418 | n/a | 23.6 per trial |
| Worker discards | 0 | 0 | — |
| Checkpoint restores | 0 | n/a | — |

V4.1 closed the binary-quality gap, but did so with more model turns, code cells, helper calls, input tokens, and wall time. The counts move together closely enough to identify interaction volume as the main cost and latency cause.

The authoritative verification contract overcorrected. The model often used `verify()` for inspection commands as well as the final test. The evidence gate also treats every `bash()` call as a possible mutation, encouraging another verification after read-only shell work. At 1,418 calls, verification is now a repeated workflow primitive rather than one final evidence boundary.

The compact checkpoint path was never exercised: there were no worker discards or restores. It removed the known state-loss risk without adding transcript replay, but it did not affect this run's quality or efficiency.

## Recommended next change

Optimize interaction count without changing the v4.1 result projection or checkpoint design:

1. Tell the model to use `bash()` for exploration and exactly one `verify()` for the final required check.
2. Stop marking read-only shell calls as mutations. Record a compact workspace revision after each cell and require another final verification only when that revision changes.
3. Add a telemetry counter for verification calls before the final mutation so the next ablation directly measures redundant verification.
4. Re-run only the diagnostic tasks where the arms differed—Pebble, SCC, Updo, Arktype, Quill, Numba, Skrub, Bandit, and OxVG—before another full panel.

The next target is efficiency at matched quality. Transcript replay is still unnecessary, and the checkpoint feature should remain dormant unless a worker is actually replaced.
