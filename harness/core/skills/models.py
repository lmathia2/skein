"""Typed contracts for deterministic, progressively disclosed skills."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

SkillLifecycle = Literal["enabled", "candidate", "disabled"]
SkillMetadataValue = str | tuple[str, ...]


class SkillRegistryError(ValueError):
    """Base class for skill discovery and selection failures."""


class SkillValidationError(SkillRegistryError):
    """A skill root or SKILL.md document is invalid."""


class UntrustedSkillRootError(SkillRegistryError):
    """A configured skill root is not trusted for model-facing content."""


class DuplicateSkillError(SkillRegistryError):
    """Two configured roots define the same canonical skill name."""


class SkillPathError(SkillRegistryError):
    """A skill or reference path escapes its configured directory."""


@dataclass(frozen=True, slots=True)
class SkillRoot:
    """A configured skill source and its control-plane metadata.

    Lower ``precedence`` values sort before higher values. Trust is explicit:
    an untrusted root is rejected rather than silently omitted.
    """

    path: Path
    origin: str
    lifecycle: SkillLifecycle = "enabled"
    trusted: bool = True
    precedence: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if not self.origin.strip():
            raise SkillValidationError("skill root origin must not be empty")
        object.__setattr__(self, "origin", self.origin.strip())
        if self.lifecycle not in {"enabled", "candidate", "disabled"}:
            raise SkillValidationError(f"invalid skill lifecycle: {self.lifecycle!r}")
        if self.precedence < 0:
            raise SkillValidationError("skill root precedence must be non-negative")


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """A validated SKILL.md plus immutable discovery metadata."""

    name: str
    description: str
    directory: Path
    manifest_path: Path
    origin: str
    lifecycle: SkillLifecycle
    precedence: int
    content_hash: str
    body: str
    metadata: Mapping[str, SkillMetadataValue] = field(
        default_factory=lambda: MappingProxyType({})
    )
    reference_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    """A bounded catalog suitable for a model-facing context section."""

    text: str
    included_names: tuple[str, ...]
    truncated: bool
    byte_count: int
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class SelectedSkill:
    """One selected skill body, with references disclosed when explicit."""

    name: str
    content: str
    explicit: bool
    matched_terms: tuple[str, ...]
    included_references: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class SkillSelection:
    """A bounded deterministic selection result."""

    skills: tuple[SelectedSkill, ...]
    text: str
    unmatched_explicit_names: tuple[str, ...]
    truncated: bool
    byte_count: int
    estimated_tokens: int
