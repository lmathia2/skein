# v3 checklist comparison, 2026-09-17

Completed: all 24 trials finished. See [the comparison](e13-pi-skein-v3-checklist-comparison.md). The following funding snapshot was recorded before launch. OpenRouter balance checked live: $3.728729466. Expected cost approximately $5.70; budget range $5–8, based on the four tasks' saved v3 uncached input, cached input and output repricing. Scriggo's two completed trials are extrapolated to three. This is an estimate, not a provider quote; account debit remains the billing authority. At least $4.28 additional credit is needed to cover the upper estimate, excluding other account activity.

## Setup fixed

Pi stdout and stderr are now written directly to trace files while the subprocess runs. Timeout/cancellation no longer discards already-written trace output. Execution timeout remains 6,900 seconds for each trial. The new opt-in outer watchdog is attempts × (execution timeout + 1,800 seconds setup/verifier allowance): 26,100 seconds for a three-attempt task job. Legacy runs retain their existing timeout semantics. This prevents three attempts sharing the old 7,200-second watchdog; it is not a guarantee against host/process failure or unlimited verifier time.

The v3 worker, preflight, helpers and base prompt remain unchanged. Only the completion prompt component differs between arms. Each arm has four tasks × three fresh trials. Both use Muse Spark 1.3 Contributor, OpenRouter xhigh, 32,768 maximum output tokens, zero retries and three concurrent workers, for six total on an otherwise idle host. Do not run the launcher twice or alongside another campaign. The manifest records exact task artifacts and verifier images. Run metadata records the checklist flag and execution budget.

Run `bash scripts/run_e13_v3_checklist.sh --plan` to inspect both commands. Run without `--plan` only after the account can fund the campaign. All results use new `.artifacts/e13-v3-checklist/checklist-off` and `checklist-on` roots. Historical results are unchanged.

## Independent failure reproductions

Saved model patches were applied inside disposable pinned verifier containers with networking disabled. No model calls were made. Logs are in `.artifacts/e13-v3-reproductions/`.

| Task / failed trial | Reproduction | What it establishes |
|---|---|---|
| Ink / Wce9nfw | 4 grid tests fail | Span/placement/fractional sizing arithmetic remains wrong in the submitted patch. |
| Obsidian / kHdbMoF | 17 scoped-rule tests fail; 32 pass in the selected suite | Scoped protection fails for the named rule integrations. The selected suite includes more tests than the official F2P denominator. Exact verifier preparation resets test-owned files before applying the test patch. |
| Scriggo / dtmsWPj | 3 interface-satisfaction subtests fail | Interface calls return empty strings for error(), repeated calls and passage through a function; other interface subtests pass. |
| Textual / oAviU22 | example integration test fails | The example cannot give every log a positive viewport at the tested screen size, although append actions execute. |

These reproduce symptoms, not a counterfactual causal fix. Trace/code inspection points to Ink track arithmetic, Obsidian ignore preprocessing/alias handling, Scriggo return-value dispatch, and Textual vertical space allocation. Exact code-level attribution still needs one-change before/after probes; do not report those hypotheses as proved. No benchmark solution changes have been incorporated into the agent.

## Interpretation and follow-up

This is a diagnostic subset selected because v3 failed, not an unbiased benchmark sample. Compare task-level F2P and P2P fractions, binary reward, latency, uncached/cache/output tokens, actual debit versus repricing, preflight errors, shell failures, compactions and evidence of requirement-level checks. Preserve partial trials as unknown, never zero reward. Inspect whether the checklist changes verification behavior before attributing any reward difference to it. Three trials per task are exploratory; close differences require more diverse tasks and paired uncertainty estimates.

Focused runner/trace tests passed, and both launcher arms validated in plan-only mode. Funding was subsequently added and the paid comparison completed; no further run is pending.
