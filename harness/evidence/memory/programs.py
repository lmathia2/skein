"""Closed registry of reviewed memory program identities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryProgramSpec:
    name: str
    version: int
    requires_reuse: bool = False
    model_visible: bool = True


PROGRAM_REGISTRY = {
    (spec.name, spec.version): spec
    for spec in (
        MemoryProgramSpec("history.page", 1),
        MemoryProgramSpec("event.read", 1),
        MemoryProgramSpec("events.count", 1),
        MemoryProgramSpec("artifact.read", 1),
        MemoryProgramSpec("tools.usage", 1),
        MemoryProgramSpec("failures.by_kind", 1, requires_reuse=True),
    )
}


def resolve_program(
    name: str, version: int, *, reuse: bool, model_visible: bool = False
) -> MemoryProgramSpec | None:
    spec = PROGRAM_REGISTRY.get((name, version))
    if spec is None or (spec.requires_reuse and not reuse):
        return None
    if model_visible and not spec.model_visible:
        return None
    return spec


def available_programs(*, reuse: bool, model_visible: bool = False) -> dict[str, int]:
    return {
        spec.name: spec.version
        for spec in PROGRAM_REGISTRY.values()
        if resolve_program(
            spec.name, spec.version, reuse=reuse, model_visible=model_visible
        ) is not None
    }


__all__ = [
    "PROGRAM_REGISTRY",
    "MemoryProgramSpec",
    "available_programs",
    "resolve_program",
]
