"""Provider registry stubs that deliberately reuse Google ADK model primitives."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from google.adk.models import BaseLlm

from harness.core.config import ModelConfig, RuntimeBindings, SecretRef


@runtime_checkable
class AdkModelProvider(Protocol):
    """Build an ADK BaseLlm; ADK remains responsible for model streaming."""

    @property
    def provider_id(self) -> str: ...

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
        bindings: RuntimeBindings | None = None,
    ) -> BaseLlm: ...


@runtime_checkable
class AdkModelProviderRegistry(Protocol):
    def get(self, provider_id: str) -> AdkModelProvider: ...

    def available(self) -> tuple[str, ...]: ...


__all__ = ["AdkModelProvider", "AdkModelProviderRegistry"]
