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

NOTEBOOK_PTC_INSTRUCTION = """
Use persistent Python as the control plane: retain useful values, orchestrate brokered
capabilities, and transform results before returning only the facts needed for the next
decision. Chain operations when their next inputs are already known; return to the model
when results require semantic judgment. Run projects through their own commands and
environment via `agent.shell.run`, not by importing them into the kernel.

Inspect capability signatures and result contracts with `agent.help()`. Direct filesystem,
process, and network access is blocked; use `agent.*`. Only `agent.parallel` operations are
parallel-safe. The append-only ledger records effects, and independent verification—not a
notebook cell or model claim—decides completion.
""".strip()

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
        instruction += "\n\n" + NOTEBOOK_PTC_INSTRUCTION
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
