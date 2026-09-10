from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from pathlib import Path

import pytest
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import ValidationError

from harness.adapters.providers import AdkModelProvider, AdkModelProviderRegistry
from harness.core.agent import (
    AdkHarnessAssembly,
    AgentSnapshot,
    ControlCommand,
    ControlReceipt,
    HarnessBuildInfo,
    HarnessDescriptor,
    HarnessFactory,
    HarnessRegistry,
    RuntimeCapability,
    SteeringCommand,
)
from harness.core.config import (
    HarnessComposition,
    ModelConfig,
    RuntimeBindings,
    SecretRef,
    SkeinConfig,
    load_harness_composition,
)


class _TestLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        if False:
            yield LlmResponse()


class _ModelProvider:
    @property
    def provider_id(self) -> str:
        return "test"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
        bindings: RuntimeBindings | None = None,
    ) -> BaseLlm:
        del bindings
        assert all(isinstance(secret, SecretRef) for secret in secrets.values())
        return _TestLlm(model=config.name)


class _ProviderRegistry:
    def __init__(self, provider: AdkModelProvider) -> None:
        self._provider = provider

    def get(self, provider_id: str) -> AdkModelProvider:
        if provider_id != self._provider.provider_id:
            raise LookupError(provider_id)
        return self._provider

    def available(self) -> tuple[str, ...]:
        return (self._provider.provider_id,)


class _ControlHooks:
    def __init__(self, descriptor: HarnessDescriptor) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self._descriptor

    async def steer(self, command: SteeringCommand) -> ControlReceipt:
        return ControlReceipt(accepted=True, command_id=command.idempotency_key or "steer-1")

    async def pause(self, command: ControlCommand) -> ControlReceipt:
        return ControlReceipt(accepted=True, command_id=command.idempotency_key or "pause-1")

    async def cancel(self, command: ControlCommand) -> ControlReceipt:
        return ControlReceipt(accepted=True, command_id=command.idempotency_key or "cancel-1")

    async def snapshot(self, run_id: str) -> AgentSnapshot:
        return AgentSnapshot(run_id=run_id, sequence=2, state={"phase": "active"})


class _HarnessFactory:
    def __init__(self, descriptor: HarnessDescriptor) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self._descriptor

    @property
    def config_model(self) -> type[SkeinConfig]:
        return SkeinConfig

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly:
        assert bindings.workspace.is_absolute()
        config = composition.harness.config
        assert isinstance(config, SkeinConfig)
        root_agent = LlmAgent(
            name="test_coding_agent",
            model=config.models["coding"].name,
        )
        return AdkHarnessAssembly(
            descriptor=self.descriptor,
            app=App(name=composition.app.name, root_agent=root_agent),
            build_info=HarnessBuildInfo(
                behavior_sha256=composition.behavior_sha256,
            ),
            controls=_ControlHooks(self.descriptor),
        )


def _descriptor(
    capabilities: frozenset[RuntimeCapability] | None = None,
    *,
    api_version: int = 1,
) -> HarnessDescriptor:
    return HarnessDescriptor(
        implementation="skein_v1",
        api_version=api_version,
        display_name="Test coding harness",
        capabilities=capabilities
        or frozenset(
            {
                RuntimeCapability.STREAMING,
                RuntimeCapability.STEERING,
                RuntimeCapability.CANCEL,
                RuntimeCapability.REPLAY,
                RuntimeCapability.TOOL_EVENTS,
                RuntimeCapability.STATE_SNAPSHOTS,
                RuntimeCapability.ARTIFACTS,
            }
        ),
    )


def test_model_provider_builds_an_adk_model_instead_of_a_second_runtime() -> None:
    provider = _ModelProvider()
    registry = _ProviderRegistry(provider)
    config = ModelConfig(provider="test", name="test-model")

    model = registry.get("test").build_model(
        config,
        secrets={"api_key": SecretRef(env="TEST_MODEL_API_KEY")},
    )

    assert isinstance(provider, AdkModelProvider)
    assert isinstance(registry, AdkModelProviderRegistry)
    assert isinstance(model, BaseLlm)
    assert model.model == "test-model"


def test_harness_registry_builds_an_adk_app_assembly(tmp_path: Path) -> None:
    descriptor = _descriptor()
    factory = _HarnessFactory(descriptor)
    registry = HarnessRegistry()
    registry.register(factory)
    bindings = RuntimeBindings(workspace=tmp_path.resolve(), state_root=tmp_path / "state")

    assembly = registry.build(load_harness_composition(), bindings)

    assert isinstance(factory, HarnessFactory)
    assert isinstance(assembly, AdkHarnessAssembly)
    assert isinstance(assembly.app, App)
    assert assembly.descriptor == descriptor
    assert registry.available() == ("skein_v1",)


def test_harness_registry_rejects_missing_required_capability(tmp_path: Path) -> None:
    registry = HarnessRegistry()
    registry.register(_HarnessFactory(_descriptor(frozenset({RuntimeCapability.STREAMING}))))

    with pytest.raises(
        ValueError,
        match="artifacts, state_snapshots, steering, tool_events",
    ):
        registry.build(
            load_harness_composition(),
            RuntimeBindings(workspace=tmp_path.resolve(), state_root=tmp_path / "state"),
        )


def test_harness_registry_rejects_api_version_mismatch(tmp_path: Path) -> None:
    registry = HarnessRegistry()
    registry.register(_HarnessFactory(_descriptor(api_version=2)))

    with pytest.raises(ValueError, match="API version mismatch"):
        registry.build(
            load_harness_composition(),
            RuntimeBindings(workspace=tmp_path.resolve(), state_root=tmp_path / "state"),
        )


def test_harness_registry_rejects_duplicate_implementation() -> None:
    registry = HarnessRegistry()
    factory = _HarnessFactory(_descriptor())
    registry.register(factory)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(factory)


def test_steering_command_limit_is_utf8_bytes_not_characters() -> None:
    with pytest.raises(ValidationError, match="4096 UTF-8 bytes"):
        SteeringCommand(run_id="run-1", content="é" * 3_000)
