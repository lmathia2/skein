# Pi and Skein: core harness primitives

Status: implementation comparison, September 22, 2026

This comparison is grounded in Skein `main` and the local Pi checkout at commit
`3c75b2747`. Pi is a complete, extensible coding agent. Skein is a narrower research
harness for ADK-native PTC, effect evidence, and host-verified coding tasks. The goal is
to identify transferable simplicity, not declare a winner.

## Primitive-by-primitive comparison

| Primitive | Pi | Skein | Design consequence |
| --- | --- | --- | --- |
| Loop owner | `packages/agent/src/agent-loop.ts` directly streams the assistant, executes native calls, appends results, handles steering/follow-ups, and stops | ADK `LlmAgent` owns that native loop; Skein's `Workflow` only brackets bounded work batches with task state and verification | Skein must not grow a second tool-call loop |
| Default tool surface | Four direct tools: `read`, `bash`, `edit`, `write` | One public `code` tool; the Pi-shaped four helpers plus `verify` are nested inside PTC | Pi is immediately legible; Skein spends one extra conceptual hop to reduce model round trips |
| Prompt assembly | `buildSystemPromptSections` renders tool-aware sections, project context, skills, docs, and cwd; extensions can patch sections | `settings_from_composition` builds a stable instruction; `build_coding_packet` supplies bounded task state per work batch | Skein has a stricter stable/dynamic split; Pi is more extension-friendly |
| Project instructions | Loaded into a tagged system-prompt section | Loaded only for trusted projects, byte-bounded, then placed in the stable instruction | Both support repository guidance; Skein makes trust and byte limits explicit |
| Tool declarations | The transcript records tool loadout changes and replay derives the current executable set | ADK owns native function declarations; default composition declares only `code` | Pi makes dynamic loadouts first-class; Skein fixes topology for comparable runs |
| Message model | `AgentMessage[]` is transformed to provider messages at the LLM boundary; native tool results are appended to the same context | ADK session and provider adapters own native content/function calls; Skein records separate task evidence | Both preserve native tool semantics; Skein deliberately avoids a custom structured-response transcript |
| Context management | A context transform can run before each request; automatic compaction summarizes older work, keeps a recent tail, and retains the full session | Required task control is rebuilt from `TaskLedger`; optional conversation is truncated; optional trace-native memory supplies addressed views | Pi optimizes an interactive conversation; Skein optimizes reproducible task projections |
| Session history | Durable session entries support replay, branching, prompt/tool-state changes, and compaction | ADK session is conversation authority; JSONL events rebuild task state; notebook and heap are separate | Skein has more stores, so their authority must stay explicit |
| Tool-call scheduling | The loop validates and executes native calls, including batches, errors, steering, hooks, and stop policy | ADK schedules the public `code` call; Python code may call nested helpers sequentially or programmatically | Pi exposes scheduling in the loop; Skein delegates outer scheduling to ADK and inner composition to Python |
| Tool errors | Invalid, missing, blocked, aborted, and thrown calls become tool-result messages; truncated calls are not executed | Builder adapters convert exceptions to bounded recoverable results; PTC cell failure discards dirty state | Same feedback principle, with Skein adding worker-epoch semantics |
| Output bounds | Coding tools truncate output and session/UI code controls presentation | Results are bounded, pageable, redacted, and may retain full artifacts plus model-facing projections | Skein pays extra bookkeeping for trace analysis and recovery |
| Effects and safety | Core tools operate in the selected environment; project trust, hooks, extensions, and surrounding sandbox shape policy | One broker enforces confinement, approvals, redaction, optimistic writes, receipts, and fingerprints for all paths | Skein intentionally owns more host policy |
| Verification | The agent or user decides when the requested work is done; projects can add checks through prompts/extensions | Mutation requires verification; only the host can emit a complete outcome | Skein is less conversational but produces benchmark-grade terminal evidence |
| Recovery | Durable sessions, branches, replayable prompt/tool state, retries, and compaction support continuation | ADK resumability plus event reduction, recovery boundaries, receipts, workspace fingerprints, and safe PTC checkpoints | Skein refuses automatic replay when effect outcome is unknown |
| Tracing | Agent events and durable session entries make the loop inspectable; telemetry and extensions can add observation | ADK trace/metrics plugins plus task events, context hashes, receipts, PTC events, artifacts, and verification | Skein's trace is heavier because it is an experimental data product |
| Extensibility | Extensions can add tools, hooks, commands, UI, providers, prompt sections, and session behavior | Code-owned composition and validated YAML select known providers and policies | Pi favors a product ecosystem; Skein favors controlled ablations |
| Learning | Readable loop, extensions, skills, and ordinary CLI composition make behavior easy to teach and modify | Skills are bounded; verified traces can become offline learning episodes; runtime self-modification is disabled | Skein borrows progressive disclosure but keeps learned changes out of live authority |
| Terminal result | An assistant response and session events | Ordinary assistant prose is parsed as a work-batch signal; host returns typed `HarnessOutcome` | Model-facing simplicity and host-facing rigor are separate contracts |

## What Skein learns from Pi

1. Keep one obvious model/tool loop. In Skein that loop is ADK's; the workflow must
   remain a thin host boundary.
2. Keep the prompt inspectable. Stable instructions, tools, project guidance, and
   dynamic work state should have named source functions rather than a middleware maze.
3. Make tool failure ordinary feedback. A bad call should stay inside the loop unless
   the host can no longer establish safety.
4. Prefer a small general surface. Skein uses one programmable tool by default and
   retains Pi's four-tool shape inside it.
5. Keep progressive disclosure. Skills, large outputs, and old evidence should be
   loaded only when the next decision needs them.
6. Make the common path readable before adding extension points. Skein's fixed topology
   is a feature of an evaluation harness, not missing scaffolding.

## Where Skein intentionally differs

Skein keeps ADK because its event loop, session lifecycle, provider integrations,
streaming, cancellation, resume behavior, and native tracing are requirements. It keeps
the PTC worker because programmable composition is the primary experiment. It keeps the
broker, evidence stream, and verifier because effect provenance and independently proven
completion are the useful Skein concepts.

Those choices mean Skein cannot be as small as Pi internally. The simplicity target is
instead:

```text
one ADK loop + one public tool + one effect boundary + one completion authority
```

Everything else must justify itself as evidence, recovery, or evaluation support.

## Source anchors

Pi:

- `/Users/mathiasl/src/pi/packages/agent/src/agent-loop.ts`
- `/Users/mathiasl/src/pi/packages/coding-agent/src/core/system-prompt.ts`
- `/Users/mathiasl/src/pi/packages/coding-agent/src/core/tools/index.ts`
- `/Users/mathiasl/src/pi/packages/coding-agent/src/core/compaction/compaction.ts`
- `/Users/mathiasl/src/pi/packages/coding-agent/src/core/session-manager.ts`

Skein:

- `app/agent/config.py`
- `app/agent/builders.py`
- `app/agent/workflow.py`
- `harness/core/orchestration/core.py`
- `harness/evidence/state/`
- `harness/execution/`
- `harness/core/models/outcome.py`
