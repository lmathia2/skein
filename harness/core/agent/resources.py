"""Optional, provider-free discovery contract for client resource presentation."""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from harness.core.config import HarnessComposition, RuntimeBindings


class ResourceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["instruction", "prompt", "skill", "skill_root", "tool"]
    name: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, max_length=4096)
    description: str = Field(default="", max_length=256)
    status: Literal["available", "disabled", "missing"] = "available"


class HarnessResources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ResourceItem, ...] = Field(default=(), max_length=128)
    warnings: tuple[str, ...] = Field(default=(), max_length=8)
    truncated: bool = False


@runtime_checkable
class ResourceConfigurableHarness(Protocol):
    def resources(self, composition: HarnessComposition, bindings: RuntimeBindings) -> HarnessResources: ...
