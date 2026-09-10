"""Explicit harness registry used by declarative composition."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from harness.core.config import HarnessComposition, RuntimeBindings

from .contracts import (
    AdkHarnessAssembly,
    HarnessDescriptor,
    HarnessFactory,
    ModelConfigurableHarness,
    RuntimeCapability,
)
from .resources import HarnessResources, ResourceConfigurableHarness


class HarnessRegistry:
    """Resolve code-owned factories without accepting YAML import paths."""

    def __init__(self) -> None:
        self._factories: dict[str, HarnessFactory] = {}

    def register(self, factory: HarnessFactory) -> None:
        implementation = factory.descriptor.implementation
        if implementation in self._factories:
            raise ValueError(f"harness implementation already registered: {implementation}")
        self._factories[implementation] = factory

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly:
        implementation = composition.harness.implementation
        try:
            factory = self._factories[implementation]
        except KeyError as error:
            raise LookupError(f"harness implementation is not registered: {implementation}") from error
        if factory.descriptor.api_version != composition.harness.api_version:
            raise ValueError(
                "harness API version mismatch: "
                f"configured {composition.harness.api_version}, "
                f"registered {factory.descriptor.api_version}"
            )
        if not isinstance(composition.harness.config, factory.config_model):
            raise TypeError(
                "harness configuration was not validated with the registered model: "
                f"{factory.config_model.__name__}"
            )
        required = {RuntimeCapability(value) for value in composition.harness.required_capabilities}
        missing = required - factory.descriptor.capabilities
        if missing:
            values = ", ".join(sorted(capability.value for capability in missing))
            raise ValueError(f"harness implementation lacks required capabilities: {values}")
        return factory.build(composition, bindings)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def model_configuration(self, implementation: str) -> ModelConfigurableHarness | None:
        factory = self._factories[implementation]
        return factory if isinstance(factory, ModelConfigurableHarness) else None

    def resources(self, composition: HarnessComposition, bindings: RuntimeBindings) -> HarnessResources | None:
        factory = self._factories[composition.harness.implementation]
        return factory.resources(composition, bindings) if isinstance(factory, ResourceConfigurableHarness) else None

    def descriptor(self, implementation: str) -> HarnessDescriptor:
        try:
            return self._factories[implementation].descriptor
        except KeyError as error:
            raise LookupError(
                f"harness implementation is not registered: {implementation}"
            ) from error

    def config_models(self) -> Mapping[str, type[BaseModel]]:
        return {
            implementation: factory.config_model
            for implementation, factory in sorted(self._factories.items())
        }


__all__ = ["HarnessRegistry"]
