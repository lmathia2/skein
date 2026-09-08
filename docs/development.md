# Skein development

## Fast path

From this checkout:

```bash
./install.sh --dev
./start.sh run --workspace /absolute/path/to/project
```

To exercise the notebook-native PTC path with isolated state:

```bash
./install-ptc.sh --dev
./start-ptc.sh /absolute/path/to/project
```

The PTC launcher selects one persistent `execute_code` tool and enables canonical JSONL
memory. It does not install DuckDB or LanceDB. Those are optional development extras:

```bash
uv sync --locked --extra memory-duckdb
uv sync --locked --extra memory-search
```

Use `./install.sh --plan` to inspect install paths without changing anything. The
install scripts recreate only this checkout's `.venv`; runtime state and credentials
live outside it.

## Pi terminal client

The default installer builds the protocol-only Pi-toolkit client. For client work,
use Node.js 22.19+ and build from this checkout:

```bash
npm ci --prefix clients/terminal
npm run build --prefix clients/terminal
```

Start the usual server in terminal 1:

```bash
skein-start server --workspace /absolute/path/to/project
```

From this checkout in terminal 2:

```bash
npm start --prefix clients/terminal -- --state-root "$HOME/.local/state/skein"
```

For a custom server, also pass `--server ws://127.0.0.1:PORT/v1/agent` and use its
state root. The client reads `SKEIN_TOKEN` when set, otherwise
`STATE_ROOT/server/auth-token`; provider credentials stay on the server. `/resources`
shows server paths, `/login` and `/model` control the provider, `/resume` restores
history, and `/approvals` reviews waiting commands. Escape defers an approval dialog;
Escape from the editor requests cancellation. See the migration design for remaining
live comparison gates; installation alone is not a claim of full Pi parity.

The default prompt streams eligible conversational Markdown using a typed control
header. Custom prompts returning the older JSON `message` format still work but
remain buffered. Code-completion replies appear only after verification. Sensitive
spans may wait until the reply ends so credentials split across chunks are redacted.
Public partial replies are replayable from the server run database; cancelling a
reply does not label it complete. No additional provider call is used for display.

## Setup and checks

Follow [installation and TUI startup](../README.md). Development setup:

```bash
./install.sh --dev
.venv/bin/python -m compileall -q app harness
.venv/bin/pytest tests/unit tests/integration
.venv/bin/ruff check app harness tests
.venv/bin/pyright --pythonpath .venv/bin/python app harness
npm test --prefix clients/terminal
```

Python 3.12+, uv, Git, and Node.js 22.19+ are required for the full developer stack.
Tests do not need cloud credentials. Live behavior requires separately authorized
provider access. Do not interpret deterministic smoke tests as model benchmarks.

## Configuration and entrypoints

Copy `harness/config/default.yaml` and pass `serve --config FILE` for custom
behavior. The launcher `skein-start run` defaults to the Codex subscription
configuration and handles server/TUI state handoff.

The optional Google Agents CLI is not installed by the minimal installer.
If already installed, it can load `app.agent.application`; this entrypoint uses
the same YAML factory as the server. See `.env.example` for its identity bindings:
`SKEIN_CONFIG`, `SKEIN_WORKSPACE`, `SKEIN_STATE_DIR`, task/workspace
IDs, and `SKEIN_TRUST_PROJECT`. Former model/workflow/learning environment
settings are not the behavior API: migrate to YAML.

`skein tuning-export --config FILE` emits the optimizer-facing subset of
that YAML. It includes the baseline behavior hash, prompt/model/generation settings,
context and tool-output budgets, progress thresholds, cache/compaction controls, and
the existing outcome/trace observation contract. Safety, authority, topology,
verification, persistence, and redaction are deliberately absent from its parameters.
Treat one candidate as one behavior hash; compare candidates on held-out tasks and
retain pass/test guardrails before optimizing cost, uncached input, or wall time.

Do not run multiple server workers against one state directory. Back up local state
before upgrading a behavior configuration; do not resume old runs across a behavior
hash change.

### Runtime profiles

The supported profiles differ only at explicit configuration seams:

| Profile | Configuration | Purpose |
| --- | --- | --- |
| Four-tool default | `notebook_ptc.enabled: false`, `memory.enabled: false` | Backward-compatible coding harness |
| PTC + JSONL | `notebook_ptc.enabled: true`, `memory.enabled: true`, `ledger: jsonl` | Persistent CPython, notebook workbench, dependency-free canonical ledger |
| PTC + DuckDB | PTC enabled, memory enabled, `ledger: duckdb` | Same authority model with SQL analytics |

The corresponding fully annotated, standalone files live in
`harness/config/profiles/`: `four-tool.yaml`, `notebook-ptc-jsonl.yaml`, and
`notebook-ptc-duckdb.yaml`. They intentionally duplicate the complete strict schema;
there is no inheritance layer that could hide the actual behavior hash.

The Codex `--notebook-ptc` launcher generates the PTC + JSONL profile. For other
combinations, copy `harness/config/default.yaml` and use
`skein serve --config FILE`. `retrieval: lance` is rejected until a versioned embedding provider is
wired; Lance is currently a tested disposable projection API, not a live prompt source.

Per-run notebooks and canonical ledgers live below `STATE_ROOT/runs/RUN_ID/`. The
notebook is a deterministic projection of ledger events, not the source of truth.
Completed replay-safe cells can restore live Python state; incomplete or unknown-effect
work remains explicit and is not replayed as successful work.

Inspect a PTC workbench without starting Jupyter:

```bash
RUN_STATE="STATE_ROOT/runs/RUN_ID"
skein notebook --state-root "$RUN_STATE" --task-id RUN_ID
skein notebook --state-root "$RUN_STATE" --task-id RUN_ID --cell-index -1
```

The command rematerializes the notebook from the event stream first, then delegates all
notebook document reads to the required `nb-cli` binary via `nb read --no-output`.
Skein does not parse `.ipynb` JSON as a browsing fallback. Do not run `nb execute`:
the harness CPython worker, capability broker, and ledger terminal events are the
execution authority.

## Search and skills

The four tools remain `read`, `bash`, `edit`, and `write`. Search is a reserved
virtual `bash` command, never passed to a shell:

```text
search grep --pattern TEXT [--path REL] [--mode literal|regex]
            [--case-sensitive] [--context 0..2] [--limit 1..50]
search grep --cursor TOKEN
search find --pattern TEXT [--path REL] [--limit 1..50]
search find --cursor TOKEN
search health
```

Pages default to 20 results, grouped across files and bounded in bytes. Cursors are
workspace/query/content-bound and reject stale or cross-workspace use. File
mutations refresh the index; external shell changes can briefly lag its watcher.

Skills live in trusted directories with `SKILL.md` and optional `scripts/` and
`references/`. Project `.agents/skills` requires `--trust-project`; additional
trusted roots belong in YAML. Explicit `$skill-name` selection takes precedence,
and selected content is bounded and hashed. Editing a skill is an operator action;
there is no automatic promotion system.

## Approvals, verification, and traces

YAML safety settings apply to tools and deterministic verification. Network,
dependency installation, history mutation, publishing, and unknown commands are
gated; destructive commands are denied by default. Verification receives an
explicit executor and cannot silently choose a different backend from the environment.
Prepare the target project's dependencies before running its offline checks.

Inspect and decide pending approvals in the connected TUI with `/approvals`. Do not broadly allow unknown
commands to make a test pass.

```bash
skein trace-export --state-root /path/to/run-state --task-id TASK_ID
```

Exports are already-redacted records. Metadata/off/redacted modes are configured
in YAML. Treat trace content and artifacts as sensitive even after redaction.

## Change discipline

Keep core code importable without credentials; place ADK wiring in `app/` or
`harness/adk/`. Keep volatile data out of static instructions. Test contracts
deterministically and commit coherent, verified changes independently.

Avoid adding a public configuration field until a running path consumes it.
Prefer a direct primitive over a compatibility loader, parallel model schema,
unused provider bridge, or speculative orchestration layer.

## Remaining gates

The unchecked entries in `docs/TODO.md` are the authoritative remaining work:

1. Provide an explicit, versioned embedding provider for live Lance retrieval.
2. Prove byte, cache, and correctness behavior before serving ledger views in live
   prompt construction and compaction.
3. Run a paired four-tool versus notebook-PTC quality, token, latency, and cache-hit
   ablation before changing the default tool surface.

Do not implement DuckLake, a Jupyter server, or another execution daemon without a
measured requirement. DuckDB remains the canonical analytical backend, Lance remains
a disposable search projection, and CPython remains the live executor.
