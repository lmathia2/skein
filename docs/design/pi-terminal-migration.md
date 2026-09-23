# Pi terminal migration

Goal: match Pi's CLI/TUI interaction for conversational and coding tasks while
retaining the configurable ADK harness. The terminal must not run Pi's agent loop,
execute tools itself, or own provider credentials.

## Delivery gates

1. Prove the pinned standalone Pi terminal toolkit can render a small remote-session
   interface: editor, Markdown, compact tools, expansion, Unicode, resize and history.
2. Separate human replies, activity, and internal workflow control. Support ordinary
   conversation without forcing code changes; preserve deterministic coding verification.
3. Preserve threads across turns and reconnects. Wire steering, queued follow-ups,
   cancellation, model selection and authentication through authenticated controls.
4. Complete the Pi-style client, capability-aware commands, resource discovery and
   launcher/installer integration. No dependence on a developer's Pi checkout.
5. Compare representative prompts and terminal behavior, exercise replay and a second
   harness, run regressions, remove the superseded Go client and document state/log paths.

Commit each verified coherent unit separately. Keep the old client until the new one
passes the migration gates; do not claim full parity from rendering fixtures alone.

The initial reference is local Pi 0.84.4 (commit 853a80d26). Runtime reuse is through
the MIT-licensed `@earendil-works/pi-tui` package pinned in the client lockfile, not
copied Pi AgentSession or InteractiveMode implementation.

## Current live comparison findings (2026-08-31)

Both the stock Pi CLI and the remote ADK terminal ran with `gpt-5.6-luna`, low
reasoning, in separate empty temporary Git workspaces. Pi extensions, skills and
prompt templates were disabled for this baseline. Existing credentials were read
server-side; isolated run state did not replace the user's running server.

- Greeting and a list-versus-tuple explanation completed in both terminals with
  short prose and no tools. ADK streamed public prose without control JSON or
  server warnings.
- Creating `hello.py` and five unittest cases exposed a real ADK verifier gap:
  the worker's tests passed, but independent discovery selected only syntax/diff
  checks in the metadata-free workspace. Verification rejected completion and the
  model repeated work. The run was explicitly cancelled with Escape. This is a
  failed coding-parity case, not a passing task; Pi finished its five tests.
  The refinement discovers adjacent unittest modules even without project metadata,
  requires at least one executed test, and keeps sandbox HOME/bytecode caches under
  run artifacts rather than making them appear as source changes.
  A fresh rerun of the identical prompt then completed: the worker and independent
  verifier both ran five tests, the durable run status was `completed`, and no
  sandbox-home/cache path appeared in the source diff. This closes that specific
  coding case, not the remaining representative-task and migration gates.
- Presentation now reuses Pi's actual animated `Loader`. It adds no transcript
  entries, keeps informational notices separate, and stops on approval waits,
  disconnect, pause, completion and disposal. The footer has two width-bounded
  rows for workspace and model/status, rather than wrapping their concatenation.
  Empty notices/queues no longer reserve blank rows. Forty-two terminal tests pass,
  including animation lifecycle and Unicode/narrow-width checks. The rebuilt
  client was inspected in a real PTY at 80 columns.

The sections below record how those initial gaps were closed. Parity here means the
agreed conversation/coding interaction set and protocol boundary; it does not mean
that this project embeds every Pi command, extension, theme or session feature.

### Refined live results

After the verifier correction, the isolated ADK terminal completed these additional
`gpt-5.6-luna`/low turns. Durations are from durable server timestamps, not terminal
polling; they are single observations and therefore not a performance benchmark.

| Prompt class | Outcome | Tools / verification | Wall time |
| --- | --- | --- | ---: |
| `hello.py` plus five unittest cases | `completed` | worker tests + independent five-test run | 16.35 s |
| specified Unicode RLE codec | `completed` | four unittest methods, 200 seeded round trips, independent verifier | 51.93 s |
| specified JSON Pointer parser/resolver | `completed` | five unittest methods, exact-command approval, independent verifier | 53.07 s |
| three-bullet verifier explanation | `answered` | no tools; first public text at 2.05 s | 2.77 s |

An independent RLE oracle then passed five valid and fifteen invalid vectors. The
JSON Pointer implementation also passed independent root, escaped-key, mapping-key,
array-index and malformed-input vectors. Its model used an absolute `cd && test`
command, so the conservative policy paused at a default-Deny dialog; approving only
that fingerprint resumed the run once and verification completed.
The
stock Pi and ADK terminal had already completed the identical `hello.py` prompt with
five passing tests; only the corrected ADK run adds independent completion gating.
No cost/speed superiority is claimed from these runs.

### Live terminal recovery

A `slugify.py` coding turn was started and the terminal exited while the server run
continued. A newly launched client listed the run as active under `/resume`, restored
its partial user/tool transcript, presented the outstanding default-Deny approval,
and completed after the exact test command was approved. Durable evidence contains
one run record, one ADK invocation, two non-replayed writes, one non-replayed shell
call, five normal model-loop samples, and six passing slug tests. A final relaunch
restored the completed transcript and `/session` retained `Pi-inspired ADK coding
harness`, model, run and conversation identity.

The first live restore exposed a presentation-only defect: historical reducer state
replaced the negotiated harness label with `waiting for server`. The client now
preserves the live server harness across restore, with a deterministic regression
test. No model/tool action was duplicated by either restore.

An additional live turn was interrupted with Escape after three read calls. Its
durable record contains one run ending `cancelled`, three successful non-replayed
reads, no writes, and no requested output files. The activity indicator stopped and
the terminal remained usable. Entering a prompt before `server.hello` is also safe:
the terminal retains one idempotent local start request and sends it after negotiation
instead of clearing the editor and reporting that input was not sent.

## Final delivery gate

The migration was rechecked from a clean installer-created environment. The macOS
installer removed and recreated the isolated `.venv`, synchronized the locked Python
dependencies, installed the pinned Node dependencies, built the terminal, and created
working `skein-start` and `skein-tui` launchers in a temporary command directory.
Both launchers provide standalone help without requiring a token or running server.

The final deterministic gate contains 521 Python unit/integration cases and 46 Node
terminal cases. All pass. Ruff, Pyright over `app`/`harness`, Python compilation and
`git diff --check` are clean. The tracked Go client is absent, production code has no
Magnitude references and the Pi-terminal delivery checks were completed before this design was archived.

The live acceptance set covers short non-coding replies without tools; `hello.py`,
Unicode RLE, JSON Pointer and slugification coding tasks with independent tests;
default-Deny approval; exit/resume/replay; and active cancellation. The scripted
cross-language set additionally covers multi-turn queues, model selection, auth,
resources/skills, approvals, streaming reconnect, startup queuing and an alternate
registered harness. This proves the scoped interaction contract, not benchmark
superiority or every feature of the upstream Pi application.

## Prototype evidence

`npm test --prefix clients/terminal` passes four focused tests using the actual Pi
editor and renderers: read collapse/expansion, bash previews/errors, Markdown with
terminal-cell width checks at 20/40/80/120 columns, input history and bracketed paste.
The rendering-only demo was also run in a real PTY: `/tools` showed a one-line read,
Ctrl+O exposed its 30 lines, and Ctrl+D restored the terminal and exited successfully.
This passes the toolkit-reuse gate, not the server integration or full UX gates.

## Output boundary

The assembly opts into explicit public messages. The generic ADK mapper still
streams tools and errors, but accepts prose/results only from events tagged by
the workflow. The worker's optional `message` field is published only after its
typed result is reduced; completion replies wait for passing verification.
Private control text and child-node outputs remain in ADK history, not the public
transcript. The newer typed-header streaming path is described below; legacy JSON
responses retain this buffered behavior.

## Conversation gate evidence

The real ADK Runner, real worker and real tools pass scripted greeting, read-only
explanation and adversarial write-then-answer fixtures. Greeting takes one model
call, no tools and no verification; a read can finish as `answered`, explicitly
`verified: false`. A write followed by a model `answer` is withheld and invokes
verification. Explicit coding contracts, acceptance criteria, verification strength,
completion claims and workspace changes also disallow the direct-answer path.
All shell calls conservatively require verification: command classification alone
is not proof that a shell expression has no side effects.

The full Python unit/integration suite, Ruff, Pyright and compilation pass, as do
the terminal contracts. Live Codex turns above add conversational, coding, approval,
resume and cancellation evidence; their single-run timings are not benchmarks.

## Conversation continuity

Sequential completed turns can reuse one authenticated WebSocket. Admission rejects
overlapping runs in the same ADK session and prevents changing its workspace/harness
binding. Task budgets, skill selection and effect markers reset for a new task but
not a same-task resume. The reset is flushed before child nodes run: pending ADK
parent-state deltas otherwise mask a child's newly written effect markers.

ADK's workflow isolation does not itself carry prior turns into the coding worker.
The harness retains that isolation and supplies up to 24 prior user/public-message
events through the existing bounded context compiler (`context.conversation_tokens`,
default 2000). It excludes private node inputs, thought content and the current
invocation. A two-turn real ADK test verifies prior conversation reaches the next
request without stale budgets/skills; no alternate session store or agent loop is added.

## Remote terminal transport

The new terminal now uses authenticated loopback WebSockets with a deterministic
view reducer, contiguous replay cursors, bounded display buffers, reconnection,
heartbeat and acknowledged steering/cancellation. The public control ID echoes
the caller's idempotency key even when a harness returns a separate internal receipt.
Internal state and workflow JSON are not transcript entries.

The cross-language integration gate builds the terminal and runs its real Node
client against Uvicorn, the production WebSocket coordinator and ADK Runner with
a scripted model. Two turns retain conversation context; a real read tool is shown
as a compact card. Rendering at 40/80/120 columns remains bounded and excludes
workflow diagnostics. This is stronger than separate fake-socket tests. Live PTY
runs and the focused sections below cover model/auth selectors, durable follow-ups
and the launcher.

The same cross-language gate registers an alternate strict harness factory whose
configuration has no Pi `agents` shape. It runs one ordinary ADK agent through the
production server and unchanged terminal, which displays `Alternate fixture harness`
and its reply. This exposed and fixed a behavior-hash assumption that previously
prevented non-Pi factories from reaching the Runner.

## Durable follow-ups

The server now advertises `sessions` controls. Conversation summaries/history are
queried from the existing run registry; follow-up and continuation receipts live
in that same SQLite database. Alt+Enter queues a new turn; Enter remains steering.
The terminal shows up to three queue previews and a count, with `/queue`,
`/queue continue` and `/queue clear` controls while full selectors are completed.

Success drains the queue in order. Blocked, failed and cancelled turns retain
pending messages. Shutdown does not silently run them; an explicit continuation
can restart the pending queue. A continuation retry remains tied to the original
item, and a crash after run creation cannot invoke that run again. User/workspace/
harness identity is checked before session access or queue dispatch.

The cross-language test now exercises three real ADK turns, with two follow-ups
queued while the first model response is gated. The terminal follows each successor
run, not merely the newest run, so fast completions cannot skip transcript entries.
Unit tests cover queue ordering, retries, cancellation, blocked/error results,
restart persistence, ownership and redacted previews. Later gates cover the
session/model/login selectors, full transcript loading and live visual/model checks.

## Recovery refinements

Server startup terminalizes interrupted queued/running records with a replayable
`server_restarted` error, without reinvoking models or draining pending follow-ups.
Cancellation before an asyncio task first enters also closes its allocated Runner
and terminalizes its run. Completed attachments can be released from the durable
run status even when a reconnect cursor is already past the final event. Focused
tests cover all three cases; these changes do not claim automatic task resumption
after an unknown-effect process crash.

## Shared terminal dialogs

The terminal uses Pi's Input, SelectList and fuzzy filtering for a shared inline
selector. `/help` discovers currently supported commands, `/queue` manages pending
turns, and the real Pi editor completes slash commands without scanning client
files. Selection and saving a default are separate callbacks for the upcoming
model picker. Escape closes a dialog and restores the draft before it can reach
the run-interrupt handler.

Deterministic tests cover filtering by labels rather than opaque IDs, arrow keys,
Enter/Ctrl+S, empty results, Unicode width bounds, command availability and draft
preservation. A real PTY fixture exercised `/help`, search/selection, Tab completion,
Escape and clean Ctrl+D exit. Login/model/session pickers still need their backend
actions; the selector component alone is not evidence those features work.

## Provider authentication controls

The server advertises `provider_controls` only when its local provider service is
wired. `provider.request` / `provider.result` provide status, login, cancel-login
and logout outside the run transcript. The existing Codex device flow runs in a
worker thread with cooperative cancellation. Only instructions, masked account
status and the credential path reach clients, never token values or provider
error bodies. Credentials continue to use the private atomic server-side store.

One pending login is shared across local clients. Login receipts and attempts are
bounded and process-local; clients must reconcile `/auth`, not automatically replay
authentication mutations after a disconnect or restart. Logout cancels pending
login and removes the local credential file; it neither revokes the remote account
session nor recalls an already-authorized model request. Invalid saved credentials
do not prevent inspecting status or removing the invalid cache.

Ten focused tests exercise a real credential store and synthetic HTTP transport,
including success, cancellation before token exchange, shutdown, logout retry,
ownership, secret-safe failures and an authenticated production-server socket that
still answers ping during login. This synthetic transport evidence does not constitute
fresh live-provider authentication evidence.

## Terminal login experience

The new terminal advertises `/login`, `/auth` and `/logout` when the server supports
provider controls. A searchable selector opens an inline login dialog showing the
verification URL, code and status; Escape cancels, including when the initial reply
has not arrived. Confirmed login shows the server credential path. Logout requires
explicit confirmation. Delayed status replies cannot revive a dismissed dialog.

Authentication requests are correlated but deliberately not replayed on reconnect;
unconfirmed operations direct the user to `/auth`. A server-owned pending login can
be reattached via `/login`. The terminal never reads or writes provider credentials.
Closing the terminal requests cancellation of its active login dialog before closing
the socket, but only an acknowledged cancellation is reported as confirmed.

A cross-language test drives the actual Pi dialogs and
remote session against the production authenticated Python server, with only the
provider HTTP transport mocked. It verifies login, cancellation, logout, preserved
drafts, width bounds and zero auth messages in the conversation transcript. The
rendering-only demo's `/login` was exercised in a real PTY through selector, code
display, Escape and clean exit. Model/session selectors and installation are covered
below; these tests are not a live-account sign-in claim.

## Model selection

The optional `model_selection` capability exposes `model.request` status/catalog/
select controls. The searchable Pi picker refreshes the catalog without blocking
typing or the server receive loop. Enter selects for the current conversation's
next turn; Ctrl+S additionally saves the local server owner's default. Active turns
retain their original model. Selection receipts persist alongside the run registry,
and retrying a prior selection cannot revert a newer acknowledged choice.

The server records model identity and effective behavior hashes at run admission,
then applies that immutable configuration through an optional harness-factory seam.
Configured models remain selectable when catalog discovery is unavailable. Discovery
currently uses the existing Codex adapter; other providers retain configured choices.
Listing/configuring a model is not evidence that it has loaded or responded.

Conversation preferences are scoped to user, workspace and harness. Shared defaults
live at `STATE_ROOT/auth/model-selection.json`, atomically replaced with mode 0600;
the CLI reads legacy `openai-codex-selection.json` only if the new file is absent.
Explicit YAML/launch model choices win unless `server.use_saved_model_default` is
enabled; an ordinary generated Codex launch enables it. Ctrl+S enables the saved
default for new conversations in the current process. Existing conversations retain
their model. No client-side provider credentials or YAML mutation is introduced.

The default file and conversation database are individually atomic, not a distributed
transaction. A process/disk failure between them can leave only the default saved.
Unconfirmed selections are never automatically replayed; reopen `/model` to reconcile
the current and saved choices. Escape during an in-flight save discloses the same
uncertainty instead of claiming that a server mutation was cancelled.

The cross-language fixture drives the actual
Pi picker → authenticated WebSocket → real ADK Runner: alpha stays active, a queued
turn uses beta with prior conversation context, and a new conversation uses saved
gamma. Reconnect reconciles the conversation model without replaying selection.
Width checks cover 20/40/80/120 columns. A rendering-only PTY also exercised `/model`,
filtering, Ctrl+S, Escape and clean exit. Live runs above confirm the selected Codex
model responds; catalog presence alone is not treated as availability proof.

## Historical transcript contract

`session.request` operation `events` reads one run's public event history. A page
returns its frozen high-water sequence and exclusive continuation cursor, at most
100 events and 512 KB of event JSON. New live events do not extend an in-progress
snapshot; normal attachment catches up afterward. Inputs and events are redacted,
and both the conversation and run must match the authenticated user/workspace/
harness. History reads neither attach nor acknowledge, dispatch queues, or invoke
models/tools. Focused tests cover Unicode byte limits, snapshot retries and growth,
ownership, invalid cursors and zero execution.

## Resume and history UI

The `session_history` capability gates `/resume` and `/history`; `/session` shows
conversation/run identity and queue status. The picker lists server-scoped recent
conversations, restores up to 20 turns through the same reducer used for live
events, and attaches after the last restored sequence. A fresh terminal can reopen
saved conversations without creating a new model invocation. Pending follow-ups
remain server-owned; opening a stopped conversation does not continue them.

`/history` browses older pages in a read-only dialog. Escape restores the live view
and editor draft; Ctrl+O expands tool details. The 400-entry/64-KB-per-entry display
bounds still apply, while full public events remain in server storage. This is not
Pi's session branching, renaming or cross-workspace switching; those are not exposed
as placeholder commands. A run ending without a tool result says so instead of
leaving a historical card marked `running`.

The production-server/ADK bridge drives the real resume picker and history dialog,
then reopens the conversation from a second client. Model invocation counts stay
unchanged. Deterministic terminal tests additionally cover active-run catch-up,
cancelled delayed loads, chronological replay, malformed cursors, earlier-page
navigation and widths of 20/40/80/120 columns. Live exit/resume and reinstall evidence
are recorded in this document.

## Resource discovery and selection

The `resources` capability exposes read-only `resource.request` / `resource.result`
metadata. Discovery runs off the WebSocket receive loop, so slow filesystem reads
do not block ping or cancellation. Each connection permits one discovery at a time;
the terminal coalesces concurrent refreshes and ignores dismissed dialog callbacks.
Inventory is redacted, limited to 128 entries and a 512-KB response, and explicitly
reports truncation. Alternate factories may omit the resource hook without breaking
the protocol; their workspace/state metadata remains available with a warning.

`/resources` shows server workspace, state, configuration and history locations,
project trust, prompts, tools and skill roots. `/skills` is a searchable selector;
`/skill:NAME` expands to the existing explicit skill request without another model
tool. Ctrl+O reveals compact resource metadata. Available resources are not labelled
loaded: actual selected skill names are emitted before the model call, separately
from the directory inventory. Instruction bodies and skill hashes remain private.

The production WebSocket/ADK fixture checks resource paths, sends a skill request,
observes selection while the model is gated, and verifies the body reached the
worker but not the terminal. Tests also cover trust, configured roots, disabled
budgets, invalid manifests, bounded discovery, responsive ping, safe errors,
dialog dismissal and terminal widths. Live model and installation checks are recorded
above; parity remains scoped to the agreed interaction set.

## Interactive approvals

The new terminal opts in with `interactive_approvals: "true"` task metadata; queued
turns inherit it. Clients without that opt-in retain immediate blocked tool results.
An optional assembly approval primitive joins the existing SQLite requests to
cancellable async waits, shared by worker shell calls and deterministic verification.
Verification commands now run off the server event loop; verdict rules are unchanged.
The coordinator advertises its own implemented cancellation and replay capabilities
independently of the chosen harness descriptor.

`approval.request` supports bounded listing and exact-request decisions, with owner,
workspace, harness and fingerprint checks. SQLite decisions remain authoritative;
the existing approval CLI can still resolve a live wait. A decision retry cannot
dispatch a command: only the original tool/verification continuation can do that.
Timeout/cancellation expires pending requests; finished runs reject decisions.

The Pi client shows a persistent approval indicator and opens a dialog when it will
not displace another dialog. Deny is selected initially. Escape defers and preserves
the draft; `/approvals` reopens it, while Escape from the editor interrupts the run.
Approval scope, command, risk, deadline and fingerprint are visible. Oversized
previews cannot be approved. Completed/cancelled requests close stale dialogs.
An uncertain mutation is never resent on reconnect and never labelled cancelled
merely because its dialog closed.

The cross-language fixture uses the production authenticated WebSocket server,
real ADK Runner, actual Pi widgets and local shell. It resumes a waiting run from a
fresh terminal, approves its command, denies another, cancels a third, then approves
a deterministic verification command and reaches `verified: true`. Execution counts
prove denied/cancelled commands did not run. Focused tests cover expiry, duplicate
decisions, wrong owner/binding/fingerprint, responsive sockets and verification,
secret-safe failures, draft restoration and width bounds. This remains scripted-model
evidence, not live-provider quality or complete visual-parity evidence.

The final validation gate supersedes the counts recorded at this milestone.

## Public Markdown streaming

The default worker emits a complete, single-line AgentStep control header, then a
newline and Markdown. Empty optional fields are omitted; `message` belongs only
to the parsed internal representation. The rest of the reply is never reparsed as
control data. A legacy JSON object containing `message` still works, buffered.
This changes the default static prompt/prefix hash, not the four-tool surface,
provider, verification rules or volatile-state placement.

An ADK callback with invocation-isolated state validates the entire header before considering
publication. Only `answer` with no coding obligations, completion claims, tool
effects or workspace changes can stream. Once public text starts, that work batch
cannot execute another tool or make another model call. A contradictory final
aggregate, invalid result, or changed workspace fails the run instead of granting
completion. Steering arriving mid-reply closes that reply and continues at the next
work-batch boundary. Cancellation retains partial text without claiming success.
Verification-required and coding completion replies still wait for passing checks.

The callback leaves ADK content untouched and attaches only a redacted public
delta. The generic server maps it to ordinary AG-UI text events; the terminal has
no AgentStep parser or ADK dependency. Word endings and incomplete known secrets
are buffered across chunks. Sensitive labels/private-key spans conservatively hold
the remaining reply until its final redaction, so this is not unconditional token-
by-token delivery. Bounds are 16,000 characters each for header and Markdown.

Deterministic evidence uses a gated scripted model: public text must reach the
client before generation can finish. A fresh Pi client reopens that still-active
conversation, replays its partial reply, and receives the final outcome without
duplicated text or another model call. Rendering is checked at 20/40/80/120 columns.
Additional tests cover secret splits, legacy responses, immutable control, forbidden
post-reply tools, cancellation cleanup and coding verification gates. No extra
presenter model call, simulated typing or sleep is used by the implementation.

Connection setup also now retries only requests that existed before the greeting;
new model/resource discovery requests are sent once, avoiding intermittent busy
errors. The live comparison, quiet Pi loader, installer migration and legacy-client
removal are recorded above.

Streaming state is released on normal
exit, failure and cancellation; concurrent invocations have separate authorization
and buffers. These are deterministic contract results, not live-model benchmarks.

## Live catalog compatibility

The 2026-08-31 live check found a 400 error for a missing `client_version` query
parameter. Discovery now supplies a pinned catalog compatibility version (also
overrideable by its Python caller), while retaining the harness's own client
identity. The same saved login then returned eight catalog entries. Unit tests
cover default/override query and header values. This verifies account catalog
access, not model response quality or availability for every listed model.

Pi's checked-out Codex provider uses bundled model metadata; it does not make this
same account-catalog request. The direct backend route remains a compatibility
adapter, not the [documented App Server model/list interface](https://learn.chatgpt.com/docs/app-server#list-models-modellist).
