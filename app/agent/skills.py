"""Bounded runtime discovery and disclosure of trusted directory skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness.core.skills import SkillRegistry, SkillRoot

if TYPE_CHECKING:
    from .config import HarnessSettings


@dataclass(frozen=True, slots=True)
class SkillRuntimeContext:
    text: str = ""
    selected_names: tuple[str, ...] = ()
    selected_hashes: tuple[str, ...] = ()


def build_skill_registry(settings: HarnessSettings) -> SkillRegistry:
    roots = [
        SkillRoot(
            path=path,
            origin=("project" if index == 0 else f"configured:{index}"),
            precedence=index * 10,
        )
        for index, path in enumerate(settings.skill_roots)
    ]
    return SkillRegistry(roots, allow_missing_roots=True)


def _bound_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _join_sections(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _remaining_section_bytes(parts: list[str], heading: str, budget: int) -> int:
    separator = "\n\n" if parts else ""
    return max(
        budget
        - len(_join_sections(parts).encode())
        - len(separator.encode())
        - len(heading.encode()),
        0,
    )


def build_skill_context(
    *,
    goal: str,
    next_action: str,
    settings: HarnessSettings,
) -> SkillRuntimeContext:
    """Build bounded dynamic skill context; never alter the stable prefix."""

    if settings.skill_context_bytes <= 0:
        return SkillRuntimeContext()
    registry = build_skill_registry(settings)
    budget = settings.skill_context_bytes
    catalog_heading = "Available skill catalog:\n"
    selected_heading = "Selected skill instructions:\n"
    parts: list[str] = []
    catalog_payload_budget = min(
        4_096,
        _remaining_section_bytes(parts, catalog_heading, budget),
    )
    catalog = None
    if catalog_payload_budget > 0:
        catalog = registry.build_catalog(
            max_bytes=catalog_payload_budget,
            max_tokens=max(1, catalog_payload_budget // 4),
        )
        if catalog.text:
            parts.append(catalog_heading + catalog.text.rstrip())

    selection_text = ""
    selected_names: list[str] = []
    selected_hashes: list[str] = []
    unmatched: tuple[str, ...] = ()
    selection_budget = _remaining_section_bytes(parts, selected_heading, budget)
    if selection_budget > 0 and settings.skill_max_selected > 0:
        selection = registry.select(
            goal=goal,
            next_action=next_action,
            top_n=settings.skill_max_selected,
            max_bytes=selection_budget,
            max_tokens=max(1, selection_budget // 4),
        )
        selection_text = selection.text
        selected_names.extend(skill.name for skill in selection.skills)
        selected_hashes.extend(skill.content_hash for skill in selection.skills)
        unmatched = selection.unmatched_explicit_names

    if selection_text:
        parts.append(selected_heading + selection_text.rstrip())
    if unmatched:
        unmatched_text = "Unmatched explicit skill requests: " + ", ".join(
            f"${name}" for name in unmatched
        )
        separator = "\n\n" if parts else ""
        remaining = budget - len(_join_sections(parts).encode()) - len(
            separator.encode()
        )
        bounded_unmatched = _bound_utf8(unmatched_text, max(remaining, 0))
        if bounded_unmatched:
            parts.append(bounded_unmatched)
    text = _join_sections(parts)
    if len(text.encode()) > budget:
        raise AssertionError("skill context exceeded its exact byte budget")
    return SkillRuntimeContext(
        text=text,
        selected_names=tuple(selected_names),
        selected_hashes=tuple(selected_hashes),
    )


__all__ = [
    "SkillRuntimeContext",
    "build_skill_context",
    "build_skill_registry",
]
