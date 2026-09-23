# ADR: Context and memory

Status: accepted and implemented

## Decision

Skein keeps stable instructions separate from dynamic task state, treats the ADK
session as conversation authority, and constructs bounded model-facing projections
from task evidence. Optional memory is a reproducible view, not mutable hidden prompt
state.

## Stable prefix

`settings_from_composition` assembles the configured worker instruction, tool contract,
and bounded trusted project instructions. PTC mode appends the v4.1 contract and
declares `code`. The prefix hash and token estimate are recorded for metrics and cache
analysis.

Task progress, verification output, steering, and recent conversation do not enter this
prefix.

## Dynamic coding packet

Before each bounded work batch, `build_coding_packet` renders:

1. task goal, criteria, and constraints;
2. selected skills;
3. latest steering;
4. continuation state and changed paths;
5. optional recent complete turns;
6. latest verification;
7. evidence navigation or compaction summary.

Required sections are included whole. If they alone exceed the budget, dispatch fails
with `ContextBudgetExceeded`. Optional conversation is appended only with remaining
space and receives an explicit truncation marker.

Each projection records the exact content, source watermark, content hash, and byte
count in the task event stream.

## Message ownership

ADK owns conversation history and provider-native function call/response messages.
Skein may transform the bounded context presented at a model boundary through ADK
plugins, but it does not rewrite native tool exchanges into an alternate transcript.

## State separation

| Store | Role |
| --- | --- |
| ADK session | conversation and native tool messages |
| JSONL `EventStore` | append-only task evidence |
| reduced `TaskLedger` | current task projection |
| notebook | durable PTC workbench |
| worker heap | transient Python values |
| artifact store | large immutable bodies |
| optional analytical ledger | trace-native queries and memory programs |

Default operation does not require trace-native memory. When enabled, a memory result
identifies its source events and watermark so it can be rebuilt and audited. Summaries
remain advisory and cannot override task instructions, receipts, or verification.

## Skills and learning

Skill bodies are selected under explicit byte and count budgets. Trusted project skill
roots are disabled unless project trust is enabled.

Runtime self-modification is out of scope. `verified_learning_episode` is an offline
projection that accepts only clean traces with passing host verification and no unknown
effects. It produces evidence for later analysis; it does not alter prompts during a
task.

## Consequences

- prompt growth is bounded independently of trace growth;
- native ADK message semantics remain observable;
- repeated runs can identify the evidence used for a projection;
- the system maintains several stores, but each has one non-overlapping authority;
- memory features are optional and must justify their context and operational cost.

## Implementation

- `app/agent/config.py`
- `harness/core/orchestration/core.py`
- `harness/adapters/adk/context.py`
- `harness/evidence/state/`
- `harness/evidence/memory/`
- `harness/evidence/learning.py`
