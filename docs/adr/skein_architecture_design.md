# Skein architecture design

Skein explores a simple idea: give a capable model one programmable way to act,
retain the evidence of its work, and compute the context it needs from that evidence.
In the notebook profile, the agent's working document contains code, selected results,
and narrative. Its memory consists of versioned programs that turn an append-only trace
into useful views. Google ADK supplies the surrounding model execution and session
machinery.

These choices address a recurring problem in long-running agents. Useful information
accumulates faster than the model can keep it in its active context. Repeated tool
calls consume turns; raw results consume tokens; summaries can lose the details needed
later. Skein asks whether an agent can work more precisely by keeping computation and
data outside the prompt, then deliberately selecting what to bring back into it.

The architecture distinguishes execution, document format, runtime-state recovery,
memory programs, context selection, and result presentation. The current code has one
PTC implementation, plus stable seams for selecting a ledger backend, reviewed memory
programs, and context policy. Keeping one implementation makes those boundaries serve
today's product instead of preserving experimental alternatives.

## Current modular shape

The code currently composes these layers:

| Layer | Current implementations | Selection boundary |
| --- | --- | --- |
| Model-facing tools | Four direct tools, or one `execute_code` | `notebook_ptc.enabled` |
| PTC session | Skein notebook | `notebook_ptc.enabled` |
| PTC persistence | Notebook + safe replay | Explicit serialization and recovery policy |
| Execution environment | Local or Docker command adapter | Typed host/runtime seams and explicit trust boundaries |
| Canonical trace | JSONL or optional DuckDB | `memory.enabled` and `memory.ledger` |
| Memory programs | Finite `(name, version)` registry with a shared request/result contract | `memory.context_programs`; active, shadow, or off |
| Context | Exact ADK history, Pi compaction, or trace-backed bounded windows | Memory implementation plus `context.window_management` |
| Result presentation | Compact text plus hashes, structured metadata, and artifact references | One normalizer for all PTC implementations; not independently configurable |

The default remains the four direct tools with canonical trace memory disabled. A
disabled module selects the ordinary behavior; it never disables authorization,
redaction, output bounds, or independent verification. Invalid combinations fail while
loading configuration instead of silently falling back.

## One code-mode tool gives the model a language for acting

The target model-facing surface is one `execute_code` tool. Within it, Python can
compose file operations, shell commands, searches, and registered capabilities through
the host's broker. A persistent runtime can retain intermediate values between calls.
The model decides which results deserve to be printed or displayed.

As models become stronger at programming, code becomes an increasingly useful language
for specifying work. A program can express a filter, a join, a loop, or an exact
condition once. The computer performs the repetitive part, and the model returns to
reasoning when an observation requires a new decision. That can reduce both tool-call
round trips and the amount of raw data sent back into context.

For example, an agent investigating failing tests can collect several known files,
extract the relevant definitions, and return a compact comparison in one program.
The complete file contents can remain available in runtime variables or artifacts.
The next model turn receives the comparison it needs to choose a fix. Precision comes
from executable conditions and explicit data selection; efficiency comes from avoiding
repeated conversational coordination of mechanical operations.

The advantage is conditional. A large program that guesses several dependent decisions
can waste more work than a short tool call. Good composition groups operations whose
inputs and purpose are already known, then returns to the model when the result calls
for judgment. The host continues to own authorization, deadlines, effects, and verification.

## An append-only trace lets the agent revisit its evidence

When canonical memory is enabled, Skein retains attempts and observations in one
JSONL or DuckDB ledger: what was requested, what was
authorized, which code ran, which capability calls completed, what failed, and what
remains uncertain. Large outputs live in referenced artifacts. A later correction
adds evidence to the history instead of silently replacing the earlier account.

With canonical memory disabled, the default compatibility profile continues to use its
operational JSONL and SQLite stores. Those stores are not described as the canonical
cross-module trace, and trace-backed programs and bounded reconstruction remain off.

This gives the agent more than a transcript to summarize. It can ask which operations
were still open at a particular point, what changed after a test passed, or which
result supports a claim. A watermark identifies the exact boundary of the history
used to answer the question. The same retained evidence can support debugging,
recovery, a notebook, and several different prompt views.

Prompts become bounded computations over that history. Stable instructions and tool
declarations form a reusable prefix. The changing portion includes the current task,
selected evidence, recent results, and steering. Keeping full evidence available
outside the prompt makes it possible to reduce active context without making the
reduction the only surviving account of the work.

## A notebook makes code and its results a durable document

The Skein notebook implementation uses a notebook as its workbench document: exact
submitted code alongside selected output, explanatory Markdown, and provenance. It persists the record of code
state and its evolution. The live Python heap has a separate lifetime; an `.ipynb`
alone does not preserve every object, background task, or external resource.

The ledger remains authoritative, and the notebook can be reconstructed at a specified
watermark. Cell and attempt identities connect the readable document to the execution
evidence. Recovery separately chooses whether to restore safe computations, load an
explicitly supported snapshot, or start with an empty runtime. This separation lets
the agent retain a useful document even when some live values cannot be recovered.

`nb-cli` provides the document inspection boundary. The agent can search for a relevant
cell and read a small slice rather than pulling an entire notebook into context:

```bash
nb search session.ipynb PATTERN
nb read session.ipynb --no-output --cell-index 7
```

Here, querying means locating and selecting document content. Structured historical
queries belong to the ledger and its memory programs. Notebook inspection does not
execute cells; execution continues through `execute_code` and its normal host controls.

## Multimodal results can make the document more informative

A notebook can place a program, a table, an image, and an explanation next to one
another. Code can process multimodal data and emit selected MIME outputs. A visual
result can communicate a spatial relationship that would require a long list of
coordinates or a fragile textual description.

Consider anomaly detection. A program could compute candidate clusters, plot the
points, and label a small suspicious group. The agent could inspect that plot to
choose a follow-up calculation, while the notebook preserves the code, plot, and
source-data reference together. The numerical data still supports checking the
conclusion; the picture helps direct attention.

For localization within a plot, a tool could return an annotated overview and a crop
of the relevant region, with axis bounds and source coordinates. That gives a
multimodal model both an orientation and a focused view. The next call can investigate
the selected region without receiving every point in the original dataset.

The potential efficiency comes from selecting an informative representation. Notebook
files themselves are JSON, and embedding an image can increase storage substantially.
A plot or crop may nevertheless communicate a pattern more economically than sending
a flat JSON array of all observations. Exact values, counting, and arithmetic often
remain better served by a small table or structured result. Image-token costs,
rendering, and visual interpretation quality must be measured for the task and model.

This requires an explicit path from a cell's rich output to a model-visible image:
select the MIME representation, bound its size, preserve the source reference, and
deliver it through the provider's multimodal interface. The notebook supplies document
structure and provenance; output selection and delivery determine what the model sees.

## Memory is a view produced by a versioned computation

Databases provide useful concepts for agent memory: an event log, queries, views,
materialized results, provenance, and invalidation. A progress report, a failure
summary, and a list of relevant prior observations can all be different views over
the same underlying evidence.

Conceptually, a memory result is:

```text
view = program@version(authorized evidence at watermark, parameters, budgets)
```

The program is reusable; the result belongs to a particular input and historical
boundary. Skein records the program identity, parameters, evidence addresses, source
watermark, and result hash. A retained materialization can be discarded and rebuilt
when its program is deterministic and its inputs remain available.

Versioning makes improvements inspectable. Suppose `task.progress@1` counts completed
operations, while a candidate `task.progress@2` groups evidence by acceptance criterion.
Both can run on the same frozen trace. Their usefulness can be compared before version
2 supplies active context. The agent does not have to reinvent that computation for
every task, and an investigation can identify exactly which version shaped a prompt.

Reuse also makes costs easier to control. A repeated, expensive query may justify a
materialized view or a cache keyed by its full inputs. A simple query can recompute
cheaply. JSONL can supply the local event stream; optional database backends can supply
indexed or analytical access when the workload warrants them. These are implementation
choices beneath the evidence and program contracts.

Model-generated summaries need a different reproducibility promise: preserve their
inputs, generation settings, output, and provenance so the recorded result can be
reused. Running the model again need not generate identical bytes. Summaries and
semantic rankings remain advisory when the host decides whether an effect completed
or a task has been verified.

## From premise to execution contracts

The pieces work together: code mode performs computations and selects observations;
the trace preserves what happened; the notebook profile organizes code and multimodal
results; versioned programs construct bounded memory views. The design keeps execution,
document serialization, runtime-state recovery, memory selection, context policy, and
presentation as separate responsibilities. Today, some are independent selectors and
some remain implementation-native bundles, so evaluations compare only combinations
that configuration validation and deterministic contracts actually support.

The companion ADRs turn that premise into execution contracts:

| ADR | Questions it answers |
| --- | --- |
| [Trace-native harness and composable PTC](trace-native-harness.md) | How do `execute_code`, runtime selection, notebook/JSONL serialization, and state recovery fit together? |
| [Context, versioned memory programs, and long sessions](context-and-memory.md) | How are programs versioned, evidence scoped, views computed, and prompts kept bounded? |
| [Execution, recovery, and verified completion](execution-and-recovery.md) | Who authorizes effects, handles interruption, reconciles outcomes, and decides when work is complete? |
| [Skein compared with Codex, OpenCode, and Pi](harness-comparison.md) | Which ideas are inherited, which tradeoffs differ, and what is genuinely distinctive in Skein? |
