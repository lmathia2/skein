"""Environment-derived configuration for the Agents CLI application."""

from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from harness.core.config import (
    DEFAULT_COMPOSITION_PATH,
    HarnessComposition,
    RuntimeBindings,
    SkeinConfig,
)
from harness.core.context import build_static_prefix
from harness.execution.repo import collect_project_instructions
from harness.ptc.repl.worker import WORKSPACE_EXECUTION_GUIDANCE

NOTEBOOK_PTC_INSTRUCTION = """
Notebook-native programmatic tool calling is enabled. Your only model-visible tool is
`execute_code(code)`. Each call appends and executes one durable notebook cell in a persistent
CPython worker. Compose managed capabilities through `agent.fs.read`, `agent.fs.write`,
`agent.fs.edit`, and `agent.shell.run`; filter intermediate results in Python and expose
only what is useful. `agent` is prebound; do not import or introspect it. Core signatures:
`agent.fs.read(path, offset=1, limit=400)` (limit must be 1-400),
`agent.fs.write(path, content, expected_sha256=None, expected_absent=False)`,
`agent.fs.edit(path, old_text, new_text, expected_sha256=None)`, and
`agent.shell.run(command, timeout_seconds=120)`. `agent.help()` lists all exact signatures;
`agent.help(name, details=True)` returns one targeted result contract. Capability calls
return mappings. Process the machine-readable `data` field in Python and expose only facts
or short excerpts needed for the next decision; `model_text` is a bounded human rendering.
For shell processes, stdout/stderr are in `data`. Managed commands such as memory/search
return their native payload instead: a memory query's view is `result['data']`, with the
program body in `result['data']['data']` (including `text` for read.recover). Check both
the capability and view statuses; absent stdout does not mean empty evidence.
`result_kind` identifies process versus managed routes, not success. Never require
exit_code or parse stdout for a managed memory/search result. Process data in Python;
when rendering a result, select data fields or model_text, not both copies.
After a successful fs.read, the citation is `result['read_reference']['artifact_uri']`
at the top level, not inside data. Keep it with the retained text; data.sha256 is the
source-file hash, not a receipt URI. Reuse the saved result or its state descriptor to
retrieve this citation; do not reread the file merely to locate metadata.
`agent.state.describe` returns the descriptor directly, not a status/data envelope.
Retain reusable intermediate values instead of spending a model turn on each trivial call.
The entire cell is validated before any line runs, including unreachable branches.
If failure_stage is parse or source_validation, no assignments or calls ran: old
bindings remain unchanged, not newly produced results. Fix and resubmit before using
the intended result. For errors use str(exc), not type(exc).__name__; dunder access is blocked.
Use Python for exact calculation, parsing, aggregation, comparison, and deterministic
transformation when it reduces copying or reasoning error; return prose directly when
execution adds no evidence.
Retain useful reads under descriptive path-or-purpose names, not repeatedly overwritten
scratch names such as `r` or `result`; keep them separate from answer/check outputs.
Successful reads are also retained automatically for the live worker epoch:
`agent.state.reads(path)` lists exact source/version/range metadata and a runnable
`content_expression`; `agent.state.reuse(handle)` returns the original result (the exact
artifact URI is also accepted).
`agent.fs.read` consults that catalog automatically: for a confirmed unchanged source it
returns covered content from the live worker and acquires only missing lines. Its concise
response identifies reuse at the decision point; use the returned data without printing
already captured source. A review boundary is not
a source change: reuse the retained source mapping for new calculations instead of reacquiring it. Reuse covered ranges of the same source
version; a partial read is not a whole-file snapshot. After edits, external changes,
unknown shell effects, or a missing range, obtain fresh evidence where needed and use
expected_sha256 for guarded edits. A missing match in captured lines is not evidence
of absence in the file. Check data.complete/total_lines/next_offset and read the
uncovered range or search the current file; never substitute a different symbol or
weaken the task requirement to avoid a necessary read. Recovery returns captured
coverage only, not unseen lines: recovery `complete` applies to its selected capture
page, whereas `source_coverage.whole_file` describes the original source capture.
Use `source_coverage.next_unread_offset` for a needed fresh source read, never as a
recovery-page offset. Correctness takes priority over reducing re-reads.
Finish source-dependent calculations and assertions before a later cell performs
workspace mutations. An exception after a write cannot roll back that effect and
discards the live epoch, so do not combine speculative interpretation with writes.
Lost-binding updates may retain historical_read and a recover_expression for the
completed result envelope, not the current variable. Check loader status and complete
byte paging before parsing its data.text; recovered reads never establish that a failed
calculation or unknown effect completed.
Completed reads inside a failed cell can also have historical_read recovery entries,
without any variable name: their read receipts completed, not the enclosing cell.
Use `search grep --pattern TEXT
--path PATH --limit 20` through `agent.shell.run` before recursive grep or repeated
exploratory reads.

Compose work until new semantic judgment is required. Examples:
```
result = agent.fs.read(path)
src = result["data"]["text"] if result["status"] == "ok" else ""
source_ref = result["read_reference"]["artifact_uri"] if result["status"] == "ok" else None
print(src[start:start + 4000])
agent.state.annotate("src", "Source range used for the next edit")
# Later: agent.state.describe("src", preview=True), then slice the retained range.
# If memory programs are active, recover a historical range after worker loss:
# agent.shell.run("memory query --program read.recover --artifact-uri URI --offset 1 --limit 40")
# Without memory programs, use agent.artifacts.load(URI); agent.help('artifacts.load', details=True)
# describes exact byte paging and the saved result envelope. Do not retry disabled memory commands.

source_pages = agent.parallel([
    {"operation": "fs.read", "arguments": {"path": path}} for path in known_paths
])
source_reads = {p["data"]["path"]: p for p in source_pages if p["status"] == "ok"}
errors = [p["model_text"] for p in source_pages if p["status"] != "ok"]
del source_pages
[(path, "needle" in p["data"]["text"]) for path, p in source_reads.items()], errors
# Later: source_reads[path]["data"]["text"]; retain its read_reference and range metadata.
# Keep answer_reads/check_results separate; never mutate captured source envelopes.
# Retain multiple ranges/versions of one path separately, keyed by (path, sha256, offset).

changed = agent.fs.edit(path, old, new, expected_sha256=digest)
check = agent.shell.run(targeted_check) if changed["status"] == "ok" else changed
{"change": changed["status"], "check": check.get("exit_code"), "error": check.get("data", {}).get("stderr", "")[-2000:]}
```
These illustrate orchestration, not permission to invent repairs or completion. Return to
the model when results require interpretation; independent verification owns completion.
`agent.parallel(...)` accepts only independent `fs.read` operations and returns results in
input order; other operations remain serial.
Use
`agent.state.list()` or
`agent.state.describe(name, selector=(), preview=False)` to inspect
live values selectively. Retained read handles such as `read:4` are accepted as `name`;
describe the handle directly instead of guessing a Python binding or issuing a memory query.
A descriptor with a selector describes the selected leaf,
not the parent binding: use its `access_expression` or `inspect_expression` exactly;
`binding_type` identifies the parent and `type` identifies the selected value.
`agent.state.annotate(name, description, selector=())` attaches
a brief advisory purpose to supported values; it does not durably retain conclusions.
When working notes are enabled, record concise public findings and next actions with
`memory note write --text TEXT --entries JSON --expected-version N --operation-id ID`
through `agent.shell.run`; quote text and JSON with `shlex.quote`, including multiline text.
Memory commands are virtual: send the complete `memory ...` command alone, without
`cd`, `&&`, pipes, or other shell commands. The broker routes it without entering
the workspace shell; use a separate call for Git or project commands.
Use `memory note schema` for the validated input format, live byte budget, and merge rules
when needed; its input_schema describes the write arguments, while --entries takes only
the entries array. Its schema_version identifies the API contract, never --expected-version.
Use the newest observed note version from supplied metadata or a
successful receipt; read notes only to recover needed content/version or resolve a conflict.
Do not start with an empty-note read or plan-only write. Finding IDs
use 1-96 letters, digits, underscores, or hyphens (not file paths). Each finding's text is at most
1000 characters and 2000 UTF-8 bytes; the complete note also has a bounded serialized
budget, including automatically attached source dependencies. Keep --text a short
checkpoint heading; put conclusions in entries, without repeating receipt hashes/ranges
in prose. Do not add an aggregate finding that repeats evidence refs already attached to
specific findings; that wastes the bounded note and can force a retry. Make independently
reusable facts separate entries, each with only its supporting
evidence and paths. Do not bundle unrelated file facts into one catalog entry: all of an
entry's source dependencies must be validated together. Keep genuinely cross-source
conclusions together with every required dependency; never split away evidence needed
to support the conclusion. Batch independent entries in one note write.
Reuse already retrieved applicable prior findings in their source scope. Do not reread
sources or copy prior findings merely to manufacture current-task note citations;
checkpoint new learning, changed conclusions, and unresolved questions instead.
Writes merge by ID: revise the same finding under its existing ID, rather than
adding a parallel *_current entry. New IDs are for distinct findings; omitted entries remain.
A budget rejection reports required/budget bytes and retains the last checkpoint.
Successful writes return a compact commit receipt, not full text/entries: check status,
then retain event_id and version. version identifies the committed note;
receipt_version identifies the response schema. An identical retry may refer to an
older commit. Do not reread just to confirm a
successful write. Use memory note read for current merged content when needed, or
the receipt's recovery command for the exact committed event if available. A committed
note is not proof that its findings are true, sources are current, or the task is done.
Each entry has id, kind (observation, hypothesis, decision, rejected_approach,
open_question, or next_action), text, and optional evidence_refs,
task_links, related_paths, supersedes, conflicts_with. An observation requires a public
event ID or read_reference artifact URI; use hypothesis for an unsupported claim.
Use short live handles while composing entries: `agent.state.cite('read:N')` resolves one
to its exact attested artifact URI. Source-dependent observations without completed
evidence remain unsupported; do not promote them to completed findings.
Checkpoint learned evidence and unresolved questions at meaningful boundaries, not
ceremonial plans or a second log of tool receipts. Use `memory query --program working_set` to recover them. Findings survive
binding loss but remain advisory and historical, never proof of current freshness or
verification. Missing/disabled memory is not an execution failure; continue using
available evidence. For `.ipynb` files, use `nb read` or
`nb search` through `agent.shell.run`; never parse notebook JSON in Python. The notebook
records code and selected outputs, while the append-only ledger records execution and
nested capability outcomes. `open()` and direct filesystem, process, or network APIs are
blocked; use the corresponding `agent.*` capability. A notebook is not proof that a side effect completed, and cells
that write or have unknown effects must never be replayed automatically.
""".strip() + "\n\n" + WORKSPACE_EXECUTION_GUIDANCE

@dataclass(frozen=True, slots=True)
class HarnessSettings:
    app_name: str
    model: str
    workspace: Path
    source_repository: Path | None
    state_root: Path
    task_id_override: str | None
    base_revision_override: str | None
    workspace_id_override: str | None
    worker_id: str
    task_lease_seconds: int
    max_iterations: int
    recent_event_limit: int
    static_instruction: str
    static_prefix: str
    trace_mode: str
    trace_max_content_bytes: int
    skill_roots: tuple[Path, ...]
    skill_max_selected: int
    skill_context_bytes: int
    project_trusted: bool


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("SKEIN_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "skein" / digest
    return root


def runtime_bindings_from_env(configuration_root: Path) -> RuntimeBindings:
    """Read invocation identity only; YAML is the single source of behavior."""
    obsolete = {
        "MODEL",
        "CONTROL_DATABASE_URL",
        "TASK_LEASE_SECONDS",
        "MAX_ITERATIONS",
        "COMPACT_AT_TOKENS",
        "RECENT_EVENTS",
        "TRACE_MODE",
        "TRACE_MAX_CONTENT_BYTES",
        "SKILL_DIRS",
        "SKILL_MAX_SELECTED",
        "SKILL_CONTEXT_BYTES",
        "FINAL_REVIEWER",
        "REVIEW_MODEL",
        "REVIEW_MAX_CHARS",
        "LEARNING_ENABLED",
        "LEARNING_MIN_SUPPORT",
        "LEARNING_TRIAL_PERCENT",
        "COMPACTION_INTERVAL",
        "COMPACTION_OVERLAP",
        "SEARCH_BACKEND",
    }
    configured = sorted(
        f"SKEIN_{name}" for name in obsolete if f"SKEIN_{name}" in os.environ
    )
    if configured:
        raise ValueError(
            "Move removed behavior environment settings to SKEIN_CONFIG YAML: "
            + ", ".join(configured)
        )
    workspace = Path(os.getenv("SKEIN_WORKSPACE", os.getcwd())).expanduser().resolve()
    source = os.getenv("SKEIN_SOURCE_REPOSITORY")
    return RuntimeBindings(
        workspace=workspace,
        state_root=_state_root(workspace),
        configuration_root=configuration_root,
        source_repository=Path(source).expanduser().resolve() if source else None,
        task_id=os.getenv("SKEIN_TASK_ID"),
        base_revision=os.getenv("SKEIN_BASE_REVISION"),
        workspace_id=os.getenv("SKEIN_WORKSPACE_ID"),
        worker_id=os.getenv("SKEIN_WORKER_ID"),
        project_trusted=os.getenv("SKEIN_TRUST_PROJECT", "0").lower()
        in {"1", "true", "yes", "on"},
    )


def load_settings() -> HarnessSettings:
    from harness.core.config import DEFAULT_COMPOSITION_PATH, load_harness_composition

    path = (
        Path(os.getenv("SKEIN_CONFIG", str(DEFAULT_COMPOSITION_PATH))).expanduser().resolve()
    )
    return settings_from_composition(
        load_harness_composition(path), runtime_bindings_from_env(path.parent)
    )


def settings_from_composition(
    composition: HarnessComposition,
    bindings: RuntimeBindings,
) -> HarnessSettings:
    """Resolve validated declarative behavior against volatile runtime bindings."""

    config = composition.harness.config
    if not isinstance(config, SkeinConfig):
        raise TypeError("skein_v1 requires SkeinConfig")
    workspace = bindings.workspace.expanduser().resolve()
    state_root = bindings.state_root.expanduser().resolve()
    configuration_root = (
        (bindings.configuration_root or DEFAULT_COMPOSITION_PATH.parent).expanduser().resolve()
    )
    worker_config = config.agents["coding_worker"]
    instruction = worker_config.instruction.strip()
    project_instructions = (
        collect_project_instructions(workspace) if bindings.project_trusted else ""
    )
    project_instruction_bytes = config.context.project_instruction_bytes
    encoded_project_instructions = project_instructions.encode("utf-8")
    if len(encoded_project_instructions) > project_instruction_bytes:
        project_instructions = encoded_project_instructions[:project_instruction_bytes].decode(
            "utf-8", errors="ignore"
        )
        if project_instruction_bytes:
            project_instructions += (
                "\n[project instructions truncated; read the source files when needed]"
            )
    if project_instructions:
        instruction += "\n\nStable project instructions:\n" + project_instructions

    tool_names = ("read", "bash", "edit", "write")
    if config.notebook_ptc.enabled:
        instruction += (
            "\n\n"
            + NOTEBOOK_PTC_INSTRUCTION
            + "\n\nPhase-aware cell composition:\n"
            + config.notebook_ptc.batching_instruction.strip()
            + f"\nagent.parallel accepts at most {config.notebook_ptc.max_parallel_reads} reads "
            "per call. Split larger lists into batches and inspect each result's status."
        )
        tool_names = ("execute_code",)
    coding_model = config.models[worker_config.model].name
    skill_roots: list[Path] = []
    if config.skills.project_root_enabled and bindings.project_trusted:
        skill_roots.append(workspace / ".agents" / "skills")
    for configured in config.skills.additional_roots:
        path = configured.expanduser()
        skill_roots.append(
            path.absolute() if path.is_absolute() else (configuration_root / path).absolute()
        )
    worker_id = bindings.worker_id
    if not worker_id:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
    return HarnessSettings(
        app_name=composition.app.name,
        model=coding_model,
        workspace=workspace,
        source_repository=(
            bindings.source_repository.expanduser().resolve()
            if bindings.source_repository is not None
            else None
        ),
        state_root=state_root,
        task_id_override=bindings.task_id,
        base_revision_override=bindings.base_revision,
        workspace_id_override=bindings.workspace_id,
        worker_id=worker_id,
        task_lease_seconds=config.steering.lease_seconds,
        max_iterations=config.workflow.max_iterations,
        recent_event_limit=config.context.recent_event_limit,
        static_instruction=instruction,
        static_prefix=build_static_prefix(
            model_name=coding_model,
            tool_names=tool_names,
            instruction=instruction,
        ),
        trace_mode=config.tracing.mode,
        trace_max_content_bytes=config.tracing.max_content_bytes,
        skill_roots=tuple(dict.fromkeys(skill_roots)),
        skill_max_selected=config.context.max_selected_skills,
        skill_context_bytes=config.context.skill_context_bytes,
        project_trusted=bindings.project_trusted,
    )


__all__ = [
    "HarnessSettings",
    "load_settings",
    "runtime_bindings_from_env",
    "settings_from_composition",
]
