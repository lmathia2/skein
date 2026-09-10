"""Strict declarative contracts for composing an ADK coding harness."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

FOUR_CODING_TOOLS = ("read", "bash", "edit", "write")
HarnessCapability = Literal[
    "streaming",
    "steering",
    "pause",
    "cancel",
    "replay",
    "tool_events",
    "state_snapshots",
    "approvals",
    "artifacts",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecretRef(FrozenModel):
    """Reference a secret without storing its value in composition state."""

    env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")


class AppConfig(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")


class RetryConfig(FrozenModel):
    attempts: int = Field(default=3, ge=1, le=10)
    exponential_base: float = Field(default=2, ge=1, le=10)
    initial_delay_seconds: float = Field(default=1, ge=0, le=60)
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)


class ModelConfig(FrozenModel):
    """Provider key and ADK model configuration."""

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    reasoning: str | None = Field(default=None, max_length=32)
    retry: RetryConfig = RetryConfig()
    base_url: str | None = Field(default=None, min_length=8, max_length=2_048)
    api_key: SecretRef | None = None
    client_version: str | None = Field(
        default=None,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$",
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("model base_url cannot contain credentials, a query, or a fragment")
        return normalized

    @model_validator(mode="after")
    def validate_provider_options(self) -> ModelConfig:
        if self.provider == "google_adk" and (
            self.base_url is not None or self.api_key is not None or self.client_version is not None
        ):
            raise ValueError("google_adk models cannot define base_url, api_key, or client_version")
        elif self.provider == "openai_codex" and (
            self.base_url is not None or self.api_key is not None
        ):
            raise ValueError(
                "openai_codex uses the fixed ChatGPT subscription endpoint and cannot "
                "define base_url or api_key"
            )
        elif self.provider == "openrouter" and (
            self.base_url is not None or self.api_key is None
        ):
            raise ValueError(
                "openrouter uses its fixed API endpoint and requires an api_key reference"
            )
        elif self.provider != "openai_codex" and self.client_version is not None:
            raise ValueError("client_version is supported only by openai_codex")
        return self


class GenerationConfig(FrozenModel):
    """Provider-neutral sampling controls passed through Google ADK."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=256, le=131_072)


class AgentConfig(FrozenModel):
    model: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=128_000)
    generation: GenerationConfig = GenerationConfig()


class ProgressConfig(FrozenModel):
    replan_after_no_progress: int = Field(default=2, ge=1, le=100)
    block_after_no_progress: int = Field(default=4, ge=2, le=100)
    action_history_limit: int = Field(default=40, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_thresholds(self) -> ProgressConfig:
        if self.block_after_no_progress <= self.replan_after_no_progress:
            raise ValueError("block threshold must exceed replan threshold")
        return self


class WorkflowConfig(FrozenModel):
    """Only executable loop settings; topology belongs to the harness factory."""

    max_iterations: int = Field(default=40, ge=1, le=1_000)
    progress: ProgressConfig = ProgressConfig()


class ToolOutputConfig(FrozenModel):
    max_bytes: int = Field(default=16_000, ge=1_024, le=1_000_000)


class SearchConfig(FrozenModel):
    backend: Literal["auto", "fff", "disabled"] = "auto"
    default_page_size: int = Field(default=20, ge=1, le=50)
    max_page_size: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def validate_page_sizes(self) -> SearchConfig:
        if self.default_page_size > self.max_page_size:
            raise ValueError("default search page size cannot exceed its maximum")
        return self


class ToolSurfaceConfig(FrozenModel):
    read_default_lines: int = Field(default=400, ge=1, le=400)
    bash_default_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    bash_max_timeout_seconds: int = Field(default=600, ge=1, le=3_600)
    output: ToolOutputConfig = ToolOutputConfig()
    search: SearchConfig = SearchConfig()

    @model_validator(mode="after")
    def validate_surface(self) -> ToolSurfaceConfig:
        if self.bash_default_timeout_seconds > self.bash_max_timeout_seconds:
            raise ValueError("default bash timeout cannot exceed its maximum")
        return self


class NotebookPtcConfig(FrozenModel):
    enabled: bool = False
    serialization: Literal["native", "notebook", "jsonl"] = "native"
    state: Literal["native", "none", "replay_safe", "snapshot"] = "native"
    continuity: Literal["run", "conversation"] = "run"
    default_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    max_timeout_seconds: int = Field(default=600, ge=1, le=3_600)
    max_output_bytes: int = Field(default=16_000, ge=1_024, le=1_000_000)
    no_progress_cells_per_batch: int = Field(default=24, ge=1, le=256)
    max_cells_per_batch: int = Field(default=48, ge=1, le=256)
    max_parallel_reads: int = Field(default=4, ge=1, le=16)
    max_capability_calls_per_cell: int = Field(default=256, ge=1, le=1_024)
    batching_instruction: str = Field(
        default=(
            "Use the task phase in the current work packet to compose cells. During understand "
            "and plan, put independent bounded reads and searches with already-known inputs in "
            "one cell, then filter and print a compact summary. During implement, group independent "
            "reads that support one selected change; an already-decided edit and its already-selected "
            "targeted check may share a cell when code stops on failure. Return to the model before "
            "any result-dependent repair or semantic choice. During review, collect evidence for "
            "independent weak acceptance criteria together; test both sides of state transitions, "
            "including histories where a prerequisite never occurred, and stop exploring when every "
            "criterion has evidence or a concrete blocker. During verify, group already-selected "
            "formatter, type, and targeted-test commands in one program but run commands serially "
            "unless the broker explicitly certifies them parallel-safe; preserve every exit status."
        ),
        min_length=1,
        max_length=8_000,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_removed_implementation(cls, data: object) -> object:
        if isinstance(data, Mapping) and data.get("implementation") in {
            "adk_code_mode",
            "prime_repl",
        }:
            raise NotImplementedError(
                f"{data['implementation']} was removed; use Skein notebook PTC"
            )
        return data

    @model_validator(mode="after")
    def validate_timeouts(self) -> NotebookPtcConfig:
        if self.serialization not in {"native", "notebook"}:
            raise NotImplementedError(
                f"Skein notebook PTC does not implement serialization={self.serialization}; "
                "use serialization=native to preserve its existing persistence"
            )
        if self.state not in {"native", "replay_safe"}:
            raise NotImplementedError(
                f"Skein notebook PTC does not implement state={self.state}; "
                "its native state policy is replay_safe"
            )
        if self.continuity == "conversation" and not self.enabled:
            raise ValueError("conversation continuity requires notebook PTC")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError("default notebook PTC timeout cannot exceed its maximum")
        if self.no_progress_cells_per_batch > self.max_cells_per_batch:
            raise ValueError("no-progress cell limit cannot exceed the work-batch limit")
        return self


class ContextProgramConfig(FrozenModel):
    mode: Literal["off", "shadow", "active"] = "off"
    programs: dict[str, int] | None = None
    max_result_bytes: int = Field(default=16_000, ge=1_024, le=1_000_000)
    max_scan_events: int = Field(default=10_000, ge=1, le=100_000)
    timeout_seconds: float = Field(default=2, gt=0, le=60)
    reuse: bool = False

    @model_validator(mode="after")
    def validate_reuse(self) -> ContextProgramConfig:
        if self.programs is not None:
            from harness.evidence.memory.programs import available_programs

            available = available_programs(reuse=self.reuse, model_visible=True)
            for name, version in self.programs.items():
                if available.get(name) != version:
                    raise NotImplementedError(f"context program {name}@{version} is not implemented in this profile")
            if self.mode == "off":
                raise ValueError("program selection requires shadow or active context programs")
            if self.mode == "shadow" and self.programs.get("events.count") != 1:
                raise ValueError("shadow mode requires events.count@1")
        if self.reuse and self.mode != "active":
            raise ValueError("program reuse requires active context programs")
        return self


class MemoryConfig(FrozenModel):
    enabled: bool = False
    implementation: Literal["trace_native", "pi"] = "trace_native"
    pi_context_window_tokens: int = Field(default=200_000, ge=8_000, le=1_000_000_000)
    pi_reserve_tokens: int = Field(default=16_384, ge=1_024, le=256_000)
    prior_runs: bool = False
    ledger: Literal["jsonl", "duckdb"] = "jsonl"
    retrieval: Literal["lexical", "lance"] = "lexical"
    context_programs: ContextProgramConfig = ContextProgramConfig()
    working_notes: bool = False

    @model_validator(mode="after")
    def validate_backends(self) -> MemoryConfig:
        if self.pi_reserve_tokens >= self.pi_context_window_tokens:
            raise ValueError("Pi compaction reserve must be smaller than the context window")
        if self.implementation == "pi" and (
            self.context_programs.mode != "off"
            or self.prior_runs
            or self.working_notes
            or self.ledger != "jsonl"
            or self.retrieval != "lexical"
        ):
            raise ValueError("Pi memory is the simple ADK transcript/compaction strategy")
        if self.context_programs.mode != "off" and not self.enabled:
            raise ValueError("context programs require canonical memory")
        if self.prior_runs and self.context_programs.mode != "active":
            raise ValueError("prior-run recall requires active context programs")
        if self.working_notes and self.context_programs.mode != "active":
            raise ValueError("working notes require active context programs")
        if not self.enabled and (self.ledger != "jsonl" or self.retrieval != "lexical"):
            raise ValueError("disabled memory cannot select an active backend")
        if self.retrieval == "lance" and self.ledger != "duckdb":
            raise ValueError("Lance retrieval requires the DuckDB ledger")
        return self


class ContextConfig(FrozenModel):
    window_management: bool = False
    reconstruction: Literal["handoff_tail", "fresh"] = "handoff_tail"
    work_packet_tokens: int = Field(default=20_000, ge=2_000, le=256_000)
    max_task_input_tokens: int = Field(default=200_000, ge=8_000, le=1_000_000_000)
    recent_event_limit: int = Field(default=12, ge=1, le=100)
    project_instruction_bytes: int = Field(default=16_000, ge=0, le=1_000_000)
    skill_context_bytes: int = Field(default=24_000, ge=0, le=1_000_000)
    max_selected_skills: int = Field(default=3, ge=0, le=20)
    ledger_tokens: int = Field(default=2_000, ge=200, le=16_000)
    manifest_tokens: int = Field(default=800, ge=100, le=8_000)
    compaction_tokens: int = Field(default=3_000, ge=0, le=64_000)
    recent_event_tokens: int = Field(default=3_500, ge=0, le=64_000)
    conversation_tokens: int = Field(default=2_000, ge=0, le=16_000)
    steering_tokens: int = Field(default=1_000, ge=0, le=16_000)

    @model_validator(mode="after")
    def validate_work_packet_budget(self) -> ContextConfig:
        if self.max_task_input_tokens < self.work_packet_tokens:
            raise ValueError("max_task_input_tokens cannot be smaller than work_packet_tokens")
        return self


class SafetyConfig(FrozenModel):
    allow_dependency_install: bool = False
    allow_network: bool = False
    allow_git_history_mutation: bool = False
    allow_unknown_commands: bool = False
    redact_environment_names: tuple[str, ...] = ()


class LocalSandboxConfig(FrozenModel):
    kind: Literal["local"] = "local"
    memory_bytes: int = Field(default=4 * 1024**3, ge=64 * 1024**2)
    max_processes: int = Field(default=256, ge=1, le=32_768)
    max_file_bytes: int = Field(default=1024**3, ge=1024**2)


class DockerSandboxConfig(FrozenModel):
    kind: Literal["docker"]
    image: str = Field(min_length=1, max_length=256)
    cpus: float = Field(default=2, gt=0, le=128)
    memory: str = Field(default="4g", pattern=r"^[1-9][0-9]*[kKmMgG]$")
    pids_limit: int = Field(default=256, ge=1, le=32_768)


SandboxConfig = Annotated[
    LocalSandboxConfig | DockerSandboxConfig,
    Field(discriminator="kind"),
]


class SteeringConfig(FrozenModel):
    enabled: bool = True
    lease_seconds: int = Field(default=900, ge=30, le=86_400)
    batch_limit: int = Field(default=4, ge=1, le=20)
    max_message_bytes: int = Field(default=4_096, ge=256, le=4_096)
    safe_points: tuple[Literal["before_model", "before_tool", "work_batch_boundary"], ...] = (
        "before_model",
        "before_tool",
        "work_batch_boundary",
    )

    @model_validator(mode="after")
    def validate_safe_points(self) -> SteeringConfig:
        if len(set(self.safe_points)) != len(self.safe_points):
            raise ValueError("steering safe points must be unique")
        if self.enabled and not self.safe_points:
            raise ValueError("enabled steering requires at least one safe point")
        if "before_tool" in self.safe_points and "before_model" not in self.safe_points:
            raise ValueError("before_tool steering requires before_model delivery")
        return self


class ContextCacheConfig(FrozenModel):
    min_tokens: int = Field(default=4_096, ge=0)
    ttl_seconds: int = Field(default=1_800, ge=60)
    cache_intervals: int = Field(default=10, ge=1)


class AdkConfig(FrozenModel):
    context_cache: ContextCacheConfig = ContextCacheConfig()
    resumable: Literal[True] = True
    recovery: Literal["explicit", "safe_auto"] = "explicit"


class TraceConfig(FrozenModel):
    mode: Literal["off", "metadata", "redacted"] = "metadata"
    max_content_bytes: int = Field(default=8_192, ge=64, le=1_000_000)


class SkillConfig(FrozenModel):
    project_root_enabled: bool = True
    additional_roots: tuple[Path, ...] = ()


class SkeinConfig(FrozenModel):
    """Typed behavior payload owned by the skein_v1 implementation."""

    models: dict[str, ModelConfig]
    agents: dict[str, AgentConfig]
    workflow: WorkflowConfig
    tools: ToolSurfaceConfig = ToolSurfaceConfig()
    notebook_ptc: NotebookPtcConfig = NotebookPtcConfig()
    memory: MemoryConfig = MemoryConfig()
    context: ContextConfig = ContextConfig()
    safety: SafetyConfig = SafetyConfig()
    sandbox: SandboxConfig = LocalSandboxConfig()
    steering: SteeringConfig = SteeringConfig()
    adk: AdkConfig = AdkConfig()
    tracing: TraceConfig = TraceConfig()
    skills: SkillConfig = SkillConfig()

    @model_validator(mode="after")
    def validate_references(self) -> SkeinConfig:
        if self.context.window_management and not (
            self.memory.enabled and self.memory.implementation == "trace_native"
        ):
            raise ValueError("bounded context windows require trace-native memory")
        if self.context.reconstruction == "fresh" and not (
            self.context.window_management
            and self.memory.context_programs.mode == "active" and self.memory.working_notes
        ):
            raise ValueError("fresh reconstruction requires bounded windows, active retrieval and working notes")
        if (
            self.notebook_ptc.continuity == "conversation"
            and self.memory.implementation != "trace_native"
        ):
            raise ValueError("conversation notebook continuity requires trace-native memory")
        if self.adk.recovery == "safe_auto" and not (
            self.memory.enabled and self.memory.implementation == "trace_native"
        ):
            raise ValueError("safe-auto recovery requires trace-native memory")
        for name, agent in self.agents.items():
            if agent.model not in self.models:
                raise ValueError(f"agent {name!r} references unknown model {agent.model!r}")
        if set(self.agents) != {"coding_worker"}:
            raise ValueError("skein_v1 accepts only coding_worker")
        referenced_models = {agent.model for agent in self.agents.values()}
        if set(self.models) != referenced_models:
            raise ValueError("skein_v1 model entries must be referenced by a configured agent")
        return self


class HarnessSelectionConfig(FrozenModel):
    """Safe implementation key plus its registry-validated typed payload."""

    implementation: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    api_version: int = Field(default=1, ge=1, le=1_000)
    required_capabilities: tuple[HarnessCapability, ...] = (
        "streaming",
        "steering",
        "tool_events",
        "state_snapshots",
        "artifacts",
    )
    config: SerializeAsAny[BaseModel]

    @field_validator("config", mode="before")
    @classmethod
    def require_registry_validated_config(cls, value: object) -> object:
        if not isinstance(value, BaseModel):
            raise ValueError("harness config must be validated through parse_harness_composition")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> HarnessSelectionConfig:
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required capabilities must be unique")
        return self


class PersistenceConfig(FrozenModel):
    session_backend: Literal["in_memory", "sqlite"] = "in_memory"
    artifact_backend: Literal["in_memory", "file"] = "in_memory"


class ServerConfig(FrozenModel):
    use_saved_model_default: bool = False
    approval_wait_timeout_seconds: float = Field(default=120, gt=0, le=3_600)
    host: str = "127.0.0.1"
    port: int = Field(default=8_765, ge=1, le=65_535)
    websocket_path: str = Field(default="/v1/agent", pattern=r"^/")
    protocol: Literal["ag_ui_websocket_v1"] = "ag_ui_websocket_v1"
    max_connections: int = Field(default=32, ge=1, le=10_000)
    outbound_queue_size: int = Field(default=256, ge=1, le=100_000)
    first_event_timeout_seconds: float = Field(default=120, gt=0, le=3_600)
    idle_timeout_seconds: float = Field(default=180, gt=0, le=86_400)
    total_timeout_seconds: float = Field(default=1_800, gt=0, le=86_400)
    first_event_retries: int = Field(default=1, ge=0, le=3)
    close_timeout_seconds: float = Field(default=15, gt=0, le=300)

    @model_validator(mode="after")
    def validate_liveness_deadlines(self) -> ServerConfig:
        if self.total_timeout_seconds < self.first_event_timeout_seconds:
            raise ValueError(
                "server total_timeout_seconds cannot be shorter than first_event_timeout_seconds"
            )
        return self


class HarnessComposition(FrozenModel):
    schema_version: Literal[1] = 1
    app: AppConfig
    harness: HarnessSelectionConfig
    persistence: PersistenceConfig = PersistenceConfig()
    server: ServerConfig = ServerConfig()

    @model_validator(mode="after")
    def validate_recovery_storage(self) -> HarnessComposition:
        config = self.harness.config
        if isinstance(config, SkeinConfig) and config.adk.recovery == "safe_auto" and (
            self.persistence.session_backend != "sqlite"
            or self.persistence.artifact_backend != "file"
        ):
            raise ValueError("safe-auto recovery requires SQLite sessions and file artifacts")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def composition_sha256(self) -> str:
        """Hash the complete portable configuration, including deployment settings."""

        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @property
    def behavior_sha256(self) -> str:
        """Hash harness behavior without server or persistence deployment details."""

        harness = self.harness.model_dump(mode="json")
        harness["required_capabilities"] = sorted(harness["required_capabilities"])
        payload = json.dumps(
            {"app": self.app.model_dump(mode="json"), "harness": harness},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class RuntimeBindings(FrozenModel):
    """Volatile task/process identity intentionally excluded from YAML behavior."""

    workspace: Path
    state_root: Path
    auth_state_root: Path | None = None
    configuration_root: Path | None = None
    source_repository: Path | None = None
    task_id: str | None = Field(default=None, max_length=256)
    base_revision: str | None = Field(default=None, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    worker_id: str | None = Field(default=None, max_length=256)
    invocation_id: str | None = Field(default=None, max_length=256)
    conversation_id: str | None = Field(default=None, max_length=256)
    user_id: str | None = Field(default=None, max_length=256)
    prior_task_ids: tuple[str, ...] = ()
    prior_state_roots: tuple[Path, ...] = ()
    notebook_prior_task_ids: tuple[str, ...] = ()
    notebook_prior_state_roots: tuple[Path, ...] = ()
    notebook_state_root: Path | None = None
    project_trusted: bool = False
    interactive_approvals: bool = False

    @model_validator(mode="after")
    def validate_history_binding(self) -> RuntimeBindings:
        if len(self.prior_task_ids) != len(self.prior_state_roots):
            raise ValueError("prior task IDs and state roots must be paired")
        if len(set(self.prior_task_ids)) != len(self.prior_task_ids):
            raise ValueError("prior task IDs must be unique")
        if len(self.notebook_prior_task_ids) != len(self.notebook_prior_state_roots):
            raise ValueError("notebook prior task IDs and roots must be paired")
        if len(set(self.notebook_prior_task_ids)) != len(self.notebook_prior_task_ids):
            raise ValueError("notebook prior task IDs must be unique")
        if (self.prior_task_ids or self.notebook_state_root or self.notebook_prior_task_ids) and not (
            self.conversation_id and self.user_id
        ):
            raise ValueError("cross-run memory requires an owned conversation binding")
        return self


__all__ = [
    "FOUR_CODING_TOOLS",
    "AgentConfig",
    "GenerationConfig",
    "HarnessCapability",
    "HarnessComposition",
    "HarnessSelectionConfig",
    "ModelConfig",
    "NotebookPtcConfig",
    "ProgressConfig",
    "RuntimeBindings",
    "SandboxConfig",
    "SecretRef",
    "ServerConfig",
    "SkeinConfig",
    "ToolSurfaceConfig",
    "WorkflowConfig",
]
