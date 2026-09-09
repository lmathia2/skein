# Skein

<p align="center">
  <img src="docs/assets/skein-logo.png" width="720" alt="Skein: threads of execution, woven memory, thought to action">
</p>

Skein is a trace-native coding agent built on Google ADK. It runs one coding worker
inside a minimal deterministic harness for execution, memory, policy, and verification.

Read [Skein architecture design](docs/adr/skein_architecture_design.md) for why code mode, notebook documents,
append-only traces, and versioned memory programs belong together, with links to the
current architecture decisions.

## Key features

- **One-tool PTC:** optional notebook mode exposes one persistent `execute_code` tool that
  composes guarded file, shell, CLI, and MCP capabilities.
- **Memory as programs:** versioned programs derive bounded, reproducible views from
  append-only traces; JSONL is dependency-free, with DuckDB and LanceDB optional.
- **Replayable execution:** notebooks, events, receipts, approvals, steering, and
  checkpoints preserve evidence across restarts.
- **Verified completion:** task state, context budgets, safety policy, and verification
  remain outside model control.

The four-tool worker (`read`, `bash`, `edit`, and `write`) remains the default baseline;
notebook PTC and canonical memory are opt-in.

## Why Skein

A **skein** (pronounced **“skayn”**) is a loosely wound length of thread: execution
traces are the threads, memory programs weave them, and the minimal harness connects
thought to action.

## Quick start

Install once, then start the default four-tool harness in any repository:

```bash
./install.sh
./start.sh run --workspace /absolute/path/to/repository
```

Start notebook PTC instead:

```bash
./install-ptc.sh
./start-ptc.sh /absolute/path/to/repository
```

Both install helpers install the same application; `install-ptc.sh` is a memorable
alias. `start-ptc.sh` selects the persistent CPython/notebook path, enables canonical
JSONL memory, and keeps its state separate at
`~/.local/state/skein-ptc`. Set `SKEIN_PTC_STATE_ROOT` to
override that location.

In the terminal, run `/login` if prompted, `/model` to choose a model, and then enter
a task. Add `--trust-project` only after reviewing the target repository's instructions
and skills.

| Mode | Start command | Model-visible tools | Durable history |
| --- | --- | --- | --- |
| Default | `./start.sh run --workspace DIR` | `read`, `bash`, `edit`, `write` | Existing JSONL/SQLite stores |
| Notebook PTC | `./start-ptc.sh DIR` | One persistent `execute_code` tool | Notebook transcript plus canonical JSONL ledger |

## Install details

On macOS, install [Homebrew](https://brew.sh) and make sure `brew` is on your PATH.
Git is needed to clone the repository; if it is missing, run `brew install git` first.

For a new checkout:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/lmathia2/skein.git "$HOME/src/skein"
cd "$HOME/src/skein"
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

If you already have the checkout, skip cloning. Stop its running server/TUI before
reinstalling:

```bash
cd "$HOME/src/skein"
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer detects macOS, installs missing uv/Git/Node.js through Homebrew, creates
Python 3.12+ in this checkout's `.venv`, syncs the locked default dependencies,
installs the pinned `pynb-cli` notebook browser, and builds the
Pi-toolkit terminal **by default**. It links `skein`, `skein-tui`, `skein-start`, and
`nb` into the specified command directory. You do not need to activate
the virtual environment, install Pi or Magnitude, or pass a workspace to installation.
Indexed search comes from the pinned `fff-search` dependency; no separate rg/fd
installation is needed for that search backend.

**Every installation deletes and recreates this checkout's `.venv`.** Credentials
and task state outside it are preserved. Keep the checkout in place: command links
point into it. The installer refuses a symlinked environment or a non-symlink
command collision.

Add `export PATH="$HOME/.local/bin:$PATH"` to your shell startup file (`~/.zshrc`
for zsh, or the appropriate bash profile) to make the commands available in future
terminals. The startup examples below also set PATH explicitly.

Node.js 22.19+ is required. If an older Homebrew Node.js is already installed, run
`brew upgrade node` and rerun the installer; existing outdated Node.js is not auto-upgraded.
On Linux, install uv, Git, and Node.js 22.19+ before running the same script. The first
installation needs internet access for dependencies.

Optional installer flags (choose only what you need):

| Flag | Effect |
| --- | --- |
| `--plan` | Show paths and the plan without installing |
| `--dev` | Also install pytest, Ruff, and Pyright |
| `--eval` | Also install Harbor 0.22 evaluation dependencies |
| `--minimal` | CLI only; skip the TUI |
| `--bin-dir DIR` | Choose where the command links are placed |

## Start the coding agent

Choose **one** of the following launch modes. The examples use the existing
`$HOME/src/coding_tools` repository; replace that path with the repository you want
the agent to work on. The server works directly in that workspace, so review its
changes as you would any local edits.

### One terminal: server and TUI together

```bash
export PATH="$HOME/.local/bin:$PATH"
skein-start run --workspace "$HOME/src/coding_tools"
```

This starts a managed server, opens the TUI, and stops that server child when you
exit the TUI with `/quit`. Do not also start a separate server on the same port.

### Two terminals: server and TUI separately

Terminal 1 — start the server and leave it running:

```bash
export PATH="$HOME/.local/bin:$PATH"
skein-start server --workspace "$HOME/src/coding_tools"
```

Terminal 2 — connect the TUI:

```bash
export PATH="$HOME/.local/bin:$PATH"
skein-start tui
```

The launcher reads the token and sets the TUI environment automatically. No token
copy/paste or manual `STATE_ROOT` setup is needed. `/quit` exits only the TUI in this
mode; press Ctrl+C in Terminal 1 to stop the server.

From the harness checkout, `./start.sh run`, `./start.sh server`, and
`./start.sh tui` are equivalent launcher commands (installed CLI/TUI commands must
still be on PATH). `--server` and `--tui` are also accepted as mode aliases.

Use `./start-ptc.sh /absolute/path/to/repository` for the one-tool PTC profile. It is
equivalent to `./start.sh run --notebook-ptc --workspace DIR` with an isolated state
root. Each Python call becomes a durable notebook cell; completed replay-safe cells
restore CPython state after a worker restart. File, shell, CLI, and registered MCP
capabilities still pass through the harness broker, policy, receipts, output bounds,
and redaction. Failures, blocks, retries, exceptions, and timeouts remain ledger events
rather than being rewritten as successes.

PTC does not remove Bash capability or add a second shell runtime. Python programs call
`agent.shell.run(...)`, so compound CLI/Bash work uses the same confined Bash adapter as
the four-tool profile while retaining Python variables across calls. Use the default
profile to measure the familiar direct-tool baseline; use PTC to compose and reuse
multi-call programs.

Inspect the durable session notebook by run/task ID. Server runs keep operational
state under `STATE_ROOT/runs/RUN_ID`, and the run ID is also the harness task ID.
The command rematerializes from the append-only event stream, then delegates notebook
document browsing to the required `nb-cli` command via `nb read --no-output`:

```bash
RUN_STATE="$HOME/.local/state/skein-ptc/runs/RUN_ID"
skein notebook --state-root "$RUN_STATE"
skein notebook --state-root "$RUN_STATE" --task-id RUN_ID
skein notebook --state-root "$RUN_STATE" --task-id RUN_ID --cell-index -1
```

The notebook interleaves task/user/assistant/compaction Markdown with PTC code and
selected outputs. Timestamps and ledger sequence remain cell metadata. Do not use
`nb execute` to resume a harness session: the long-lived CPython worker owns live state,
and only replay-safe code cells are restored automatically.

### First login and model selection

The launcher uses this harness's Codex subscription adapter, not a local model or
an OpenAI API key. Login uses the harness's own credential store; do not assume that
being signed into the Codex app or Pi has authenticated this harness.

In the TUI:

1. Enter `/login` if needed and follow the displayed browser/device-code instructions.
2. Enter `/auth` to check authentication.
3. Enter `/model` to choose a model from your account's catalog.
4. Press Enter to select for the next turn; Ctrl+S also saves the server default.
   The active turn keeps its frozen model and does not need a server restart.
5. Enter your coding request. The TUI reports the
   configured coding model; merely starting the server is not a model-response test.

Use `/help` for commands. Guidance entered during execution is delivered at the next
safe boundary; `/cancel` requests cancellation. `/benchmark` is optional and makes
live model requests—it is not required for installation or startup.

Add `--trust-project` to `run` or `server` only after reviewing the workspace's
`AGENTS.md` and `.agents/skills`. Without it, project instructions and skills are not
loaded. Prepare the target repository's own test/build dependencies separately;
the harness installation does not install them.

### State and environment

The launcher announces the resolved paths and environment handoff without printing
tokens. Defaults:

| Data | Location |
| --- | --- |
| State root | `~/.local/state/skein` |
| Codex credentials | `STATE_ROOT/auth/openai-codex.json` |
| Saved model selection | `STATE_ROOT/auth/model-selection.json` (older `openai-codex-selection.json` is read for migration) |
| Generated Codex configuration | `STATE_ROOT/server/openai-codex.yaml` |
| WebSocket bearer token | `STATE_ROOT/server/auth-token` |
| Combined-launch server log | `STATE_ROOT/server/foreground.log` |
| Per-run state and artifacts | `STATE_ROOT/runs/` |

`SKEIN_STATE_ROOT` overrides the launcher's default state directory.
`SKEIN_SERVER_URL` overrides the TUI URL, not the server listener.
`SKEIN_TUI_COMMAND` can select an alternate compatible terminal executable;
the checkout's pinned Pi-style terminal is used by default.
The TUI process receives `SKEIN_TOKEN` from the token file and
`SKEIN_STATE_ROOT` from the resolved directory. Tokens are not command-line
arguments. The default endpoint is `ws://127.0.0.1:8765/v1/agent`; it is not a browser UI.

For a different state directory, pass the **same** `--state-root /absolute/path`
to both `server` and `tui` (or once to `run`). This also selects which saved login
and model selection the TUI uses. Run only one server process per state directory.

### Startup troubleshooting

- **Command not found:** set PATH as shown above; use the directory supplied to
  `--bin-dir` if it differs from `~/.local/bin`.
- **Missing auth token:** start Terminal 1 first and ensure both terminals use the
  same state root. This WebSocket token is separate from your Codex login.
- **Managed server exited / address already in use:** stop your existing harness
  server before using `run`. Inspect `STATE_ROOT/server/foreground.log` for the cause.
- **Browser shows 404 at port 8765:** connect with the TUI; there is no web frontend.
- **Removed Magnitude commands/options:** use the commands above, not
  `serve-magnitude`, `magnitude --setup`, or installer `--workspace`/`--magnitude` flags.

## Configure the harness

Copy [the default YAML](harness/config/default.yaml), then change the model,
instruction file, skill roots, budgets, steering, tracing, or local/Docker command
settings. Start a custom configuration with:

Terminal 1:

```bash
skein serve --config /absolute/path/to/harness.yaml \
  --workspace /absolute/path/to/repository \
  --state-root "$HOME/.local/state/skein"
```

Terminal 2:

```bash
skein-start tui
```

Built-in providers are native ADK/Gemini (`google_adk`), Codex subscription
(`openai_codex`), and OpenRouter's OpenResponses API (`openrouter`). Gemini needs
its normal ADK credentials; OpenRouter reads only the configured API-key environment
variable and always uses OpenRouter's fixed HTTPS endpoint.
The closed `HarnessFactory` and model-provider registries permit explicit
Python extensions without changing the server protocol or TUI.

YAML configures executable settings; loop topology belongs to the harness
implementation. Unknown/removed options fail validation.
The worker prompt lives in the YAML behavior bundle, so copied configurations stay portable.
Volatile state stays out of the stable instruction prefix.

Export the current safe optimization surface as deterministic JSON:

```bash
skein tuning-export --config /absolute/path/to/harness.yaml > tuning.json
```

The export pins the baseline behavior hash, current values, parameter domains,
outcome/cost/cache diagnostics, and the redacted trace-export command. Only listed
paths are optimizer-owned; tool names/topology, safety, verification, persistence,
server identity, and trace redaction remain fixed invariants. Candidate values are
still applied through the strict harness YAML, so invalid combinations fail closed.

The default remains backward compatible with the four-tool harness and its existing
stores:

```yaml
notebook_ptc:
  enabled: false
memory:
  enabled: false
  ledger: jsonl
  retrieval: lexical
```

Complete annotated standard configurations are available for the supported profiles:

- [`four-tool.yaml`](harness/config/profiles/four-tool.yaml): four-tool worker with
  the existing operational stores.
- [`notebook-ptc-jsonl.yaml`](harness/config/profiles/notebook-ptc-jsonl.yaml): PTC
  plus the dependency-free canonical JSONL ledger.
- [`notebook-ptc-duckdb.yaml`](harness/config/profiles/notebook-ptc-duckdb.yaml): PTC
  plus the optional analytical DuckDB ledger (`memory-duckdb` extra required).

Pass any profile directly to `serve --config` or `tuning-export --config`.

For notebook PTC with a canonical dependency-free ledger:

```yaml
notebook_ptc:
  enabled: true
memory:
  enabled: true
  ledger: jsonl
  retrieval: lexical
```

Set `ledger: duckdb` for SQL-backed canonical history after installing the
`memory-duckdb` extra. The base install and `install-ptc.sh` deliberately use the
dependency-free JSONL backend. Install an optional backend explicitly from the
checkout when developing it:

```bash
uv sync --locked --extra memory-duckdb
uv sync --locked --extra memory-search
```

Lance projections remain an optional programmatic API until a live embedding provider
is configured; `retrieval: lance` therefore fails closed at server startup instead of
being silently ignored.

Skills are read from trusted directories, selected deterministically, and disclosed
within a byte/token budget. Redacted interaction traces remain available:

```bash
skein trace-export --state-root /path/to/run-state --task-id TASK_ID
```

## Safety and verification

The host-local command adapter is a development tool, **not a security sandbox**.
Network, publishing, destructive, and unknown commands are not enabled by default.
Docker command execution uses the same mounted workspace; `serve --production`
rejects the host-local command adapter. File tools still run in the harness process,
so Docker alone does not isolate the entire harness.

Files use confined paths, atomic replacement, optional hash preconditions, and
mutation receipts. Tool output is bounded and large logs are recoverable as artifacts.
The ADK worker owns the complete model/tool loop; the deterministic workflow—not
model prose—decides whether a coding task is verified.

Structured requests can specify `goal`, `acceptance_criteria`, and
`verification_requirements`. Executable changes require behavioral checks by
default; `verification_level: syntax` is an explicit weaker contract.

## Pier evaluations

Install the optional evaluation dependencies first:

    ./install.sh --dev --eval

The Pier runner executes the frozen Harbor-compatible samples sequentially and preserves each full job directory: raw agent trajectories, Skein events and traces, command artifacts, verifier output, stdout/stderr, metadata, and file hashes. Completed task keys are skipped on restart; interrupted jobs use Pier job resume; failed jobs get separate retry directories.

    .venv/bin/python scripts/run_harbor_eval.py --suite smoke --plan
    .venv/bin/python scripts/run_harbor_eval.py --suite smoke --task-id modernize-scientific-stack
    .venv/bin/python scripts/run_harbor_eval.py --suite smoke
    .venv/bin/python scripts/run_harbor_eval.py --suite broader
    .venv/bin/python scripts/run_harbor_eval.py --suite full

Use `--task-id` or `--limit` for a preflight, `--jobs-dir` to choose persistent
artifacts, `--retries 0` to disable retries, or `--stop-on-error` to halt. A
watchdog allows the manifest runtime plus 30 minutes by default; override it
with `--timeout-seconds`. Reusing a jobs directory with a changed model,
reasoning setting, harness configuration, sample, or Git revision is rejected.

## Development

```bash
./install.sh --dev
.venv/bin/python -m compileall -q app harness
.venv/bin/pytest tests/unit tests/integration
.venv/bin/ruff check app harness tests
.venv/bin/pyright --pythonpath .venv/bin/python app harness
npm test --prefix clients/terminal
```

Deterministic tests use fake model streams and temporary repositories. They do not
require cloud credentials. Live model quality/speed must be measured separately;
old evaluation reports are not evidence for the simplified runtime.

## Key capabilities and current limits

- Deterministic, cache-stable context compilation and bounded tool output.
- Resumable local sessions, steering, approvals, replay, and independent completion
  verification.
- Optional notebook-native PTC with persistent CPython state and a deterministic
  nbformat workbench containing message, compaction, code, output, timestamp, and
  ledger-provenance cells; required `nb-cli` inspection replaces direct `.ipynb` JSON
  parsing.
- One append-only canonical event schema over JSONL or optional DuckDB, including
  incomplete, failed, blocked, retried, and timed-out work.
- Deterministic history, progress, open-execution, time, task-memory, and dream/failure
  views; optional immutable Lance hybrid-search projections retain canonical event IDs.

The remaining implementation gates are intentionally small and measurable:

1. Wire an explicit versioned embedding provider before live Lance prompt retrieval.
2. Ablate ledger-backed prompt/compaction readers for byte stability, cache behavior,
   and correctness before cutting them into the live path.
3. Run the paired four-tool versus notebook-PTC quality, token, latency, and cache-hit
   evaluation before considering PTC as the default.

Notebook PTC is for trusted local workspaces; its Python source guard is defense in
depth, not a production sandbox. See [the implementation TODO](docs/TODO.md) for the
authoritative checklist and [the current status](docs/IMPLEMENTATION_STATUS.md) for
the supported boundary.

The standalone [design notebooks](examples/notebooks/README.md) use mocked events to
demonstrate PTC state restoration, structured cache-aware compaction, versioned memory
programs, and byte-stable prompt assembly without provider credentials or optional
database dependencies.

## Removed features

The simplification removes Magnitude and LiteLLM, remote/Kubernetes command
adapters with disconnected file workspaces, the unwired semantic/LSP/Moderne bridge,
automatic skill synthesis/promotion/trials, automatic project memory, advisory
reviewer agents, and experiment-only comparison CLIs. Directory skills, traces,
verification, core evaluation graders, and the server/TUI remain.

Persistence is local-only (JSONL/SQLite/files or in-memory ADK services). PostgreSQL,
Vertex/GCS services and multi-worker control leases are removed. Run one server
process per state directory. The server and optional Agents CLI entrypoint now use
the same YAML factory; behavior environment overrides must be migrated to YAML.

Existing global Magnitude binaries/models and runtime credentials are untouched.
Old YAML must drop `learning`, `reviewer`, the reviewer model/agent, and
`workflow.entry/nodes`; retain `workflow.max_iterations`. Regenerate Codex
configuration by starting `serve-codex`. Removed code remains recoverable in Git.

See [architecture](docs/architecture.md) and
[simplification notes](docs/simplification.md). The original
[design brief](docs/design/pi-inspired-adk-coding-harness.md) and old evaluation
reports describe the larger pre-simplification system.
