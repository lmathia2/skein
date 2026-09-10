"""Deterministic optimizer-facing view of safe harness behavior knobs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import FrozenModel, HarnessComposition, SkeinConfig

TunableValue = str | int | float | bool | None


class TuningObjective(FrozenModel):
    metric: str
    direction: Literal["minimize", "maximize"]


class TuningParameter(FrozenModel):
    path: str
    value: TunableValue
    kind: Literal["boolean", "categorical", "integer", "number", "text"]
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    experimental: bool = False


class TuningSpec(FrozenModel):
    schema_version: Literal[1] = 1
    base_behavior_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_objective: TuningObjective
    secondary_objectives: tuple[TuningObjective, ...]
    guardrail_metrics: tuple[str, ...]
    diagnostic_metrics: tuple[str, ...]
    trace_export_command: str
    parameters: tuple[TuningParameter, ...]
    cross_parameter_constraints: tuple[str, ...]
    immutable_surfaces: tuple[str, ...]


def tuning_spec(composition: HarnessComposition) -> TuningSpec:
    """Expose current values and domains without granting safety or topology changes."""

    config = composition.harness.config
    if not isinstance(config, SkeinConfig):
        raise TypeError("tuning export currently supports skein_v1")

    parameters: list[TuningParameter] = []

    def add(path: str, value: TunableValue, kind: str, description: str, **domain: object) -> None:
        parameters.append(
            TuningParameter.model_validate(
                {
                    "path": f"harness.config.{path}",
                    "value": value,
                    "kind": kind,
                    "description": description,
                    **domain,
                }
            )
        )

    for name, model in sorted(config.models.items()):
        add(f"models.{name}.name", model.name, "categorical", "Coding model identity")
        add(
            f"models.{name}.reasoning",
            model.reasoning,
            "categorical",
            "Provider reasoning effort; supported values are provider-specific",
        )
    for name, agent in sorted(config.agents.items()):
        prefix = f"agents.{name}"
        add(f"{prefix}.instruction", agent.instruction, "text", "Cache-stable worker prompt")
        add(
            f"{prefix}.generation.temperature",
            agent.generation.temperature,
            "number",
            "Provider sampling temperature",
            minimum=0,
            maximum=2,
        )
        add(
            f"{prefix}.generation.top_p",
            agent.generation.top_p,
            "number",
            "Provider nucleus-sampling probability",
            minimum=0,
            maximum=1,
        )
        add(
            f"{prefix}.generation.max_output_tokens",
            agent.generation.max_output_tokens,
            "integer",
            "Maximum tokens in one model response",
            minimum=256,
            maximum=131_072,
        )

    progress = config.workflow.progress
    add(
        "workflow.max_iterations",
        config.workflow.max_iterations,
        "integer",
        "Maximum model work batches per task",
        minimum=1,
        maximum=1_000,
    )
    add(
        "workflow.progress.replan_after_no_progress",
        progress.replan_after_no_progress,
        "integer",
        "Repeated no-progress batches before deterministic replanning",
        minimum=1,
        maximum=100,
    )
    add(
        "workflow.progress.block_after_no_progress",
        progress.block_after_no_progress,
        "integer",
        "Repeated no-progress batches before requesting input",
        minimum=2,
        maximum=100,
    )
    add(
        "workflow.progress.action_history_limit",
        progress.action_history_limit,
        "integer",
        "Recent action fingerprints used for stagnation detection",
        minimum=1,
        maximum=1_000,
    )

    add(
        "tools.bash_default_timeout_seconds",
        config.tools.bash_default_timeout_seconds,
        "integer",
        "Default shell timeout",
        minimum=1,
        maximum=3_600,
    )
    add(
        "tools.read_default_lines",
        config.tools.read_default_lines,
        "integer",
        "Default file-read line window",
        minimum=1,
        maximum=400,
    )
    add(
        "tools.output.max_bytes",
        config.tools.output.max_bytes,
        "integer",
        "Maximum model-visible shell output bytes",
        minimum=1_024,
        maximum=1_000_000,
    )
    add(
        "tools.search.backend",
        config.tools.search.backend,
        "categorical",
        "Repository-search implementation",
        choices=("auto", "fff", "disabled"),
    )
    add(
        "tools.search.default_page_size",
        config.tools.search.default_page_size,
        "integer",
        "Default indexed-search matches per page",
        minimum=1,
        maximum=50,
    )
    add(
        "notebook_ptc.enabled",
        config.notebook_ptc.enabled,
        "boolean",
        "Use one-tool PTC instead of the four-tool worker",
        experimental=True,
    )
    add(
        "notebook_ptc.batching_instruction",
        config.notebook_ptc.batching_instruction,
        "text",
        "Cache-stable phase-aware PTC cell-composition policy",
        experimental=True,
    )
    add(
        "notebook_ptc.no_progress_cells_per_batch",
        config.notebook_ptc.no_progress_cells_per_batch,
        "integer",
        "Cells without a workspace change before a host review boundary",
        minimum=1,
        maximum=256,
        experimental=True,
    )
    add(
        "notebook_ptc.max_cells_per_batch",
        config.notebook_ptc.max_cells_per_batch,
        "integer",
        "Absolute cells before a host review boundary",
        minimum=1,
        maximum=256,
        experimental=True,
    )
    add(
        "memory.implementation",
        config.memory.implementation,
        "categorical",
        "Optional trace-native programs or simple Pi-style ADK history",
        choices=("trace_native", "pi"),
        experimental=True,
    )
    add(
        "memory.pi_context_window_tokens",
        config.memory.pi_context_window_tokens,
        "integer",
        "Context window used by Pi's reserve-based compaction trigger",
        minimum=8_000,
        maximum=1_000_000_000,
        experimental=True,
    )
    add(
        "memory.pi_reserve_tokens",
        config.memory.pi_reserve_tokens,
        "integer",
        "Pi tokens reserved for the summary prompt and output",
        minimum=1_024,
        maximum=256_000,
        experimental=True,
    )

    context = config.context
    context_parameters = (
        ("work_packet_tokens", "Total dynamic work-packet budget", 2_000, 256_000),
        ("max_task_input_tokens", "Cumulative task input-token budget", 8_000, 1_000_000_000),
        ("recent_event_limit", "Maximum recent events considered", 1, 100),
        (
            "project_instruction_bytes",
            "Cache-stable project-instruction byte budget",
            0,
            1_000_000,
        ),
        ("skill_context_bytes", "Selected-skill byte budget", 0, 1_000_000),
        ("max_selected_skills", "Maximum selected skills", 0, 20),
        ("ledger_tokens", "Task-ledger work-packet budget", 200, 16_000),
        ("manifest_tokens", "Repository-manifest work-packet budget", 100, 8_000),
        ("compaction_tokens", "Compacted-history work-packet budget", 0, 64_000),
        ("recent_event_tokens", "Recent-event work-packet budget", 0, 64_000),
        ("conversation_tokens", "Conversation-history work-packet budget", 0, 16_000),
        ("steering_tokens", "User-steering work-packet budget", 0, 16_000),
    )
    for field, description, minimum, maximum in context_parameters:
        add(
            f"context.{field}",
            getattr(context, field),
            "integer",
            description,
            minimum=minimum,
            maximum=maximum,
        )

    if config.memory.context_programs.mode == "active":
        programs = config.memory.context_programs
        for field, kind, minimum, maximum in (
            ("max_result_bytes", "integer", 1_024, 1_000_000),
            ("max_scan_events", "integer", 1, 100_000),
            ("timeout_seconds", "number", 0.001, 60),
        ):
            add(f"memory.context_programs.{field}", getattr(programs, field), kind,
                "Experimental bounded context-program budget", minimum=minimum,
                maximum=maximum, experimental=True)
        if config.memory.working_notes and config.context.window_management:
            add("context.reconstruction", config.context.reconstruction, "categorical",
                "Experimental context reconstruction; evidence and permissions are unchanged",
                choices=("handoff_tail", "fresh"), experimental=True)

    cache = config.adk.context_cache
    for field, description in (
        ("min_tokens", "Minimum stable-prefix size eligible for provider caching"),
        ("ttl_seconds", "Provider context-cache lifetime"),
        ("cache_intervals", "Provider context-cache refresh interval"),
    ):
        add(f"adk.context_cache.{field}", getattr(cache, field), "integer", description)
    return TuningSpec(
        base_behavior_sha256=composition.behavior_sha256,
        primary_objective=TuningObjective(metric="outcome_passed", direction="maximize"),
        secondary_objectives=(
            TuningObjective(metric="cost_per_passed_task", direction="minimize"),
            TuningObjective(metric="uncached_input_tokens", direction="minimize"),
            TuningObjective(metric="outcome_wall_time_ms", direction="minimize"),
        ),
        guardrail_metrics=(
            "outcome_tests_failed",
            "outcome_status",
            "prefix_versions",
        ),
        diagnostic_metrics=(
            "cache_read_ratio",
            "model_calls",
            "tool_calls",
            "model_visible_bytes",
            "omitted_bytes",
            "outcome_iterations",
            "outcome_compactions",
            "outcome_replans",
            "outcome_tests_passed",
        ),
        trace_export_command=(
            "skein trace-export --state-root STATE_ROOT --task-id TASK_ID"
        ),
        parameters=tuple(sorted(parameters, key=lambda parameter: parameter.path)),
        cross_parameter_constraints=(
            "workflow.progress.block_after_no_progress > workflow.progress.replan_after_no_progress",
            "context.max_task_input_tokens >= context.work_packet_tokens",
            "tools.search.default_page_size <= tools.search.max_page_size",
            "tools.bash_default_timeout_seconds <= tools.bash_max_timeout_seconds",
        ),
        immutable_surfaces=(
            "model-visible tool names and harness topology",
            "safety, approvals, sandbox, and secret handling",
            "verification and completion gates",
            "persistence, server, and volatile runtime identity",
            "trace redaction",
        ),
    )


__all__ = ["TuningObjective", "TuningParameter", "TuningSpec", "tuning_spec"]
