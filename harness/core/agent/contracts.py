"""ADK-first harness assembly and shared runtime contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from google.adk.agents import BaseAgent
from google.adk.apps import App
from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.core.config import HarnessComposition, ModelConfig, RuntimeBindings
from harness.execution.approvals.waiting import ApprovalWaiter


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeCapability(StrEnum):
    STREAMING = "streaming"
    STEERING = "steering"
    PAUSE = "pause"
    CANCEL = "cancel"
    REPLAY = "replay"
    TOOL_EVENTS = "tool_events"
    STATE_SNAPSHOTS = "state_snapshots"
    APPROVALS = "approvals"
    ARTIFACTS = "artifacts"
    SESSIONS = "sessions"
    SESSION_HISTORY = "session_history"
    RESOURCES = "resources"
    PROVIDER_CONTROLS = "provider_controls"
    MODEL_SELECTION = "model_selection"


class HarnessDescriptor(FrozenModel):
    implementation: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    api_version: int = Field(default=1, ge=1)
    display_name: str = Field(min_length=1, max_length=128)
    capabilities: frozenset[RuntimeCapability]
    protocol_versions: tuple[int, ...] = (1,)


class ModelReadiness(StrEnum):
    CONFIGURED = "configured"
    AUTHENTICATION_REQUIRED = "authentication_required"
    ADAPTER_INITIALIZED = "adapter_initialized"
    RESPONDING = "responding"


class PublicModelStatus(FrozenModel):
    """Allowlisted model identity and evidence safe for public clients."""

    role: Literal["coding"] = "coding"
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    readiness: ModelReadiness


class HarnessBuildInfo(FrozenModel):
    """Safe effective behavior metadata for diagnostics and assembly caching."""

    behavior_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    models: dict[str, str] = Field(default_factory=dict)
    model_providers: dict[str, str] = Field(default_factory=dict)
    tool_names: tuple[str, ...] = ()
    max_iterations: int | None = Field(default=None, ge=1)


class SteeringCommand(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-100, le=100)
    idempotency_key: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_content_bytes(self) -> SteeringCommand:
        if len(self.content.encode("utf-8")) > 4_096:
            raise ValueError("steering content exceeds 4096 UTF-8 bytes")
        return self


class ControlCommand(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=256)


class ControlReceipt(FrozenModel):
    accepted: bool
    command_id: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=2_048)


class AgentSnapshot(FrozenModel):
    run_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0)
    state: dict[str, object]


@runtime_checkable
class HarnessControlHooks(Protocol):
    async def steer(self, command: SteeringCommand) -> ControlReceipt: ...

    async def pause(self, command: ControlCommand) -> ControlReceipt: ...

    async def cancel(self, command: ControlCommand) -> ControlReceipt: ...

    async def snapshot(self, run_id: str) -> AgentSnapshot: ...


@dataclass(frozen=True, slots=True)
class AdkHarnessAssembly:
    """A harness-specific ADK App plus optional controls; Runner stays shared."""

    descriptor: HarnessDescriptor
    app: App
    build_info: HarnessBuildInfo
    agents: Mapping[str, BaseAgent] = field(default_factory=dict)
    controls: HarnessControlHooks | None = None
    # Structured workers opt in: only explicitly tagged prose/results cross the
    # public boundary. Other registered ADK harnesses retain normal text streaming.
    explicit_public_messages: bool = False
    approvals: ApprovalWaiter | None = None
    close: Callable[[], Awaitable[None] | None] | None = None


@runtime_checkable
class ModelConfigurableHarness(Protocol):
    """Optional configuration seam; no model invocation or UI dependency."""

    def coding_model(self, config: BaseModel) -> ModelConfig: ...

    def with_coding_model(self, config: BaseModel, model: ModelConfig) -> BaseModel: ...


@runtime_checkable
class HarnessFactory(Protocol):
    """Build an ADK App assembly registered under a safe YAML key."""

    @property
    def descriptor(self) -> HarnessDescriptor: ...

    @property
    def config_model(self) -> type[BaseModel]: ...

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly: ...


__all__ = [
    "AdkHarnessAssembly",
    "AgentSnapshot",
    "ControlCommand",
    "ControlReceipt",
    "HarnessBuildInfo",
    "HarnessControlHooks",
    "HarnessDescriptor",
    "HarnessFactory",
    "ModelConfigurableHarness",
    "ModelReadiness",
    "PublicModelStatus",
    "RuntimeCapability",
    "SteeringCommand",
]
