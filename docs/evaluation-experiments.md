# Fixed-intelligence experiment workflow

The experiment code holds intelligence constant at subscription-backed
`openai_codex` / `gpt-5.6-luna` / `max`. It deliberately has no model sweep,
fallback model, Web-session bridge, request camouflage, account rotation, or
retry around subscription limits.

## Phase 3: finish the model freeze

The checked Phase 4 specification at
`tests/eval/experiments/phase4-ablation-v1.json` starts in `planning` state and
fails the live-ready check. After credentials and usage are approved:

1. Run `skein codex status`, `skein codex models`, and select Luna/max.
2. Record a 16-character hash of the approved account/workspace identifier,
   the enabled model snapshot, and Codex client version. Never record tokens,
   cookies, headers, or credential paths.
3. Replace each `PENDING` candidate revision with the exact 40-character commit.
4. Set the experiment status to `frozen` and regenerate the matrix.
5. Run one Luna/max attempt on every six-task smoke-manifest task before the
   ablation queue. Stop on provider/tool-protocol, redaction, or resume failure.

The authorization field must remain `pending` unless the intended custom
provider and evaluation volume are approved. Subscription-covered model calls
have zero incremental model spend; `api_equivalent_cost_usd` is a portability
estimate, not an invoice.

## Phase 4: Skein ablation

Generate the required 36-trial four-tool versus notebook-PTC+JSONL matrix:

```sh
skein eval-plan \
  --experiment tests/eval/experiments/phase4-ablation-v1.json \
  --output phase4-matrix.json \
  --require-live-ready
```

The command exits 2 and lists every blocker until the contract is frozen.
Once live-ready, ask for one incomplete task at a time:

```sh
skein eval-next --matrix phase4-matrix.json --results phase4-results.jsonl
```

The returned `argv` is an argument array, not shell text. It runs exactly one
task at Pier concurrency 1, uses the immutable Harbor task digest, and passes no
credential into the task container. On a subscription interruption, record the
interruption and stop until the documented reset.

After Harbor finishes, import its `result.json` under the assigned key:

```sh
skein eval-import \
  --matrix phase4-matrix.json \
  --trial-key TRIAL_KEY \
  --harbor-result HARBOR_TRIAL/result.json \
  --results phase4-results.jsonl
```

The importer accepts the official verifier's `reward` as the score; agent
metadata cannot override it. It normalizes provider, subscription, verifier,
infrastructure, timeout, safety, and ordinary task outcomes, captures available
token/cost/time/step metrics, and appends idempotently. A conflicting rerun is
rejected and must receive a new predeclared trial key.

JSONL and DuckDB remain a mechanical storage decision. Existing deterministic
tests prove byte-equal canonical events and equivalent notebook event capture.
Do not spend model calls comparing them unless a future code change makes the
model-visible work packet differ.

## Phases 5 and 6: pilot and confirmation

After Phase 4, create a pilot spec with the same schema, the
`evaluation-pilot-v1` manifest/hash, one attempt, and every explicitly selected
harness. Freeze each native revision/config and the same Phase 3 model
contract. The matrix will contain 42 assignments per harness.

Advance no more than two operationally valid candidates. Create the
confirmatory spec from `evaluation-confirm-v1`, two attempts, and those exact
unchanged finalists. Its matrix must contain 420 assignments. No pilot or
confirmatory spec is checked in early because finalist identities and revisions
do not exist yet; inventing placeholders would weaken the freeze.

After all assignments have a result record, reproduce the paired analysis:

```sh
skein eval-analyze \
  --matrix phase6-matrix.json \
  --results phase6-results.jsonl \
  --output phase6-analysis.json
```

Analysis validates every trial identity and refuses incomplete ledgers. It
reports task-normalized benchmark and equal-weight composite scores, separate
intent-to-run and capability views, paired wins/losses/ties, exact McNemar
tests, task-then-attempt bootstrap intervals, infrastructure rates, and
median/p90 operational metrics. Repeats are nested inside tasks; they are never
treated as independent tasks.
