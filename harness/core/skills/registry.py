"""Discovery and bounded progressive disclosure for Agent Skills directories."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from harness.core.skills.models import (
    DuplicateSkillError,
    SelectedSkill,
    SkillCatalog,
    SkillDefinition,
    SkillPathError,
    SkillRoot,
    SkillSelection,
    SkillValidationError,
    UntrustedSkillRootError,
)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXPLICIT_RE = re.compile(r"(?<![\w$])\$([a-z0-9]+(?:-[a-z0-9]+)*)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_ALLOWED_FRONTMATTER = {"name", "description", "license", "compatibility", "metadata"}
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "and",
        "are",
        "before",
        "build",
        "can",
        "code",
        "for",
        "from",
        "help",
        "into",
        "next",
        "not",
        "that",
        "the",
        "their",
        "then",
        "this",
        "tool",
        "use",
        "using",
        "when",
        "with",
        "you",
    }
)


class SkillRegistry:
    """Immutable registry of validated skills from explicitly trusted roots."""

    def __init__(
        self,
        roots: Sequence[SkillRoot],
        *,
        max_manifest_bytes: int = 128 * 1024,
        max_reference_bytes: int = 128 * 1024,
        allow_missing_roots: bool = False,
    ) -> None:
        if max_manifest_bytes <= 0 or max_reference_bytes <= 0:
            raise SkillValidationError("skill file byte limits must be positive")
        self._max_manifest_bytes = max_manifest_bytes
        self._max_reference_bytes = max_reference_bytes
        self._allow_missing_roots = allow_missing_roots
        self._roots = tuple(sorted(roots, key=self._root_sort_key))
        self._validate_roots()
        definitions = self._discover()
        self._definitions = tuple(sorted(definitions, key=self._definition_sort_key))
        self._by_name = {definition.name: definition for definition in self._definitions}

    @property
    def roots(self) -> tuple[SkillRoot, ...]:
        return self._roots

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return self._definitions

    def get(self, name: str) -> SkillDefinition | None:
        return self._by_name.get(name.lower())

    def build_catalog(
        self,
        *,
        max_bytes: int = 8_192,
        max_tokens: int = 2_048,
    ) -> SkillCatalog:
        """Build a compact, entry-aligned catalog within both budgets."""

        self._validate_budget(max_bytes=max_bytes, max_tokens=max_tokens)
        lines: list[str] = []
        names: list[str] = []
        truncated = False
        for skill in self._definitions:
            line = (
                f"- ${skill.name} [{skill.lifecycle}; origin={skill.origin}; "
                f"sha256={skill.content_hash[:12]}] {skill.description}\n"
            )
            candidate = "".join((*lines, line))
            if not _fits(candidate, max_bytes=max_bytes, max_tokens=max_tokens):
                truncated = True
                break
            lines.append(line)
            names.append(skill.name)
        text = "".join(lines)
        return SkillCatalog(
            text=text,
            included_names=tuple(names),
            truncated=truncated,
            byte_count=len(text.encode("utf-8")),
            estimated_tokens=_estimated_tokens(text),
        )

    def select(
        self,
        *,
        goal: str = "",
        next_action: str = "",
        top_n: int = 3,
        max_bytes: int = 16_384,
        max_tokens: int = 4_096,
    ) -> SkillSelection:
        """Select enabled skill bodies from explicit mentions and lexical terms.

        Explicit ``$skill-name`` mentions outrank lexical matches and are the
        only route that discloses linked local reference files. The registry
        returns content; it never mutates instructions or tool declarations.
        """

        if top_n < 0:
            raise SkillValidationError("top_n must be non-negative")
        self._validate_budget(max_bytes=max_bytes, max_tokens=max_tokens)
        combined = f"{goal}\n{next_action}"
        explicit_names = _ordered_unique(
            match.group(1).lower() for match in _EXPLICIT_RE.finditer(combined)
        )
        unmatched = tuple(name for name in explicit_names if name not in self._by_name)
        explicit_positions = {name: index for index, name in enumerate(explicit_names)}
        goal_terms = _terms(goal)
        action_terms = _terms(next_action)

        ranked: list[tuple[tuple[Any, ...], SkillDefinition, tuple[str, ...], bool]] = []
        for skill in self._definitions:
            if skill.lifecycle != "enabled":
                continue
            is_explicit = skill.name in explicit_positions
            skill_terms = _terms(f"{skill.name.replace('-', ' ')} {skill.description}")
            goal_matches = skill_terms & goal_terms
            action_matches = skill_terms & action_terms
            matches = tuple(sorted(goal_matches | action_matches))
            score = (2 * len(goal_matches)) + (3 * len(action_matches))
            if not is_explicit and score == 0:
                continue
            key: tuple[Any, ...]
            if is_explicit:
                key = (0, explicit_positions[skill.name], skill.precedence, skill.name)
            else:
                key = (1, -score, skill.precedence, skill.name, skill.origin)
            ranked.append((key, skill, matches, is_explicit))
        ranked.sort(key=lambda item: item[0])

        selected: list[SelectedSkill] = []
        sections: list[str] = []
        truncated = len(ranked) > top_n
        for _, skill, matches, is_explicit in ranked[:top_n]:
            content, references = self._render_skill(skill, include_references=is_explicit)
            prefix = "" if not sections else "\n"
            remaining_bytes = max_bytes - len("".join(sections).encode("utf-8")) - len(
                prefix.encode("utf-8")
            )
            remaining_tokens = max_tokens - _estimated_tokens("".join(sections) + prefix)
            bounded, was_truncated = _truncate_to_budgets(
                content,
                max_bytes=max(0, remaining_bytes),
                max_tokens=max(0, remaining_tokens),
            )
            if not bounded:
                truncated = True
                break
            sections.extend((prefix, bounded))
            selected.append(
                SelectedSkill(
                    name=skill.name,
                    content=bounded,
                    explicit=is_explicit,
                    matched_terms=matches,
                    included_references=references if not was_truncated else (),
                    content_hash=skill.content_hash,
                )
            )
            if was_truncated:
                truncated = True
                break
        text = "".join(sections)
        return SkillSelection(
            skills=tuple(selected),
            text=text,
            unmatched_explicit_names=unmatched,
            truncated=truncated,
            byte_count=len(text.encode("utf-8")),
            estimated_tokens=_estimated_tokens(text),
        )

    def _validate_roots(self) -> None:
        seen_paths: set[Path] = set()
        for root in self._roots:
            if not root.trusted:
                raise UntrustedSkillRootError(
                    f"skill root {root.path} ({root.origin}) is not trusted"
                )
            if root.path.is_symlink():
                raise SkillPathError(f"skill root may not be a symlink: {root.path}")
            resolved = root.path.resolve()
            if resolved in seen_paths:
                raise SkillValidationError(f"skill root configured more than once: {root.path}")
            seen_paths.add(resolved)
            if self._allow_missing_roots and not root.path.exists():
                continue
            if not root.path.is_dir():
                raise SkillValidationError(f"skill root is not a directory: {root.path}")

    def _discover(self) -> list[SkillDefinition]:
        discovered: list[SkillDefinition] = []
        names: dict[str, SkillDefinition] = {}
        for root in self._roots:
            if self._allow_missing_roots and not root.path.exists():
                continue
            root_path = root.path.resolve()
            for entry in sorted(root.path.iterdir(), key=lambda item: item.name):
                if entry.name.startswith("."):
                    continue
                if entry.is_symlink():
                    raise SkillPathError(f"skill directory may not be a symlink: {entry}")
                if not entry.is_dir():
                    continue
                directory = entry.resolve()
                _require_within(directory, root_path, label="skill directory")
                manifest = entry / "SKILL.md"
                if not manifest.exists():
                    continue
                if manifest.is_symlink() or not manifest.is_file():
                    raise SkillPathError(f"SKILL.md must be a regular non-symlink file: {manifest}")
                definition = self._load_skill(root, directory, manifest)
                previous = names.get(definition.name)
                if previous is not None:
                    raise DuplicateSkillError(
                        f"duplicate skill {definition.name!r}: "
                        f"{previous.manifest_path} and {definition.manifest_path}"
                    )
                names[definition.name] = definition
                discovered.append(definition)
        return discovered

    def _load_skill(
        self,
        root: SkillRoot,
        directory: Path,
        manifest: Path,
    ) -> SkillDefinition:
        raw = manifest.read_bytes()
        if len(raw) > self._max_manifest_bytes:
            raise SkillValidationError(f"SKILL.md exceeds byte limit: {manifest}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError(f"SKILL.md must be UTF-8: {manifest}") from exc
        frontmatter, body = _parse_frontmatter(text, manifest)
        name = _required_string(frontmatter, "name", manifest)
        description = _required_string(frontmatter, "description", manifest)
        if len(name) > 64 or not _NAME_RE.fullmatch(name):
            raise SkillValidationError(
                f"skill name must be lowercase kebab-case and at most 64 characters: {manifest}"
            )
        if (entry_name := directory.name) and entry_name != name:
            raise SkillValidationError(
                f"skill directory {entry_name!r} must match frontmatter name {name!r}"
            )
        if len(description) > 1_024:
            raise SkillValidationError(f"skill description exceeds 1024 characters: {manifest}")
        if not body.strip():
            raise SkillValidationError(f"SKILL.md body must not be empty: {manifest}")
        unknown = sorted(set(frontmatter) - _ALLOWED_FRONTMATTER)
        if unknown:
            raise SkillValidationError(
                f"unsupported SKILL.md frontmatter keys {unknown!r}: {manifest}"
            )
        for optional in ("license", "compatibility"):
            if optional in frontmatter and not isinstance(frontmatter[optional], str):
                raise SkillValidationError(f"frontmatter {optional!r} must be a string: {manifest}")
        metadata = _metadata(frontmatter.get("metadata"), manifest)
        references = self._reference_paths(body, directory, manifest)
        return SkillDefinition(
            name=name,
            description=description,
            directory=directory,
            manifest_path=manifest.resolve(),
            origin=root.origin,
            lifecycle=root.lifecycle,
            precedence=root.precedence,
            content_hash=hashlib.sha256(raw).hexdigest(),
            body=body.strip(),
            metadata=metadata,
            reference_paths=references,
        )

    def _reference_paths(
        self,
        body: str,
        directory: Path,
        manifest: Path,
    ) -> tuple[Path, ...]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for match in _MARKDOWN_LINK_RE.finditer(body):
            target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = target.split("#", 1)[0]
            candidate = Path(path_text)
            if candidate.is_absolute():
                raise SkillPathError(f"absolute skill reference is not allowed in {manifest}: {target}")
            unresolved = directory / candidate
            if _path_contains_symlink(unresolved, directory):
                raise SkillPathError(f"skill reference may not be a symlink: {unresolved}")
            resolved = unresolved.resolve()
            _require_within(resolved, directory, label="skill reference")
            if not resolved.is_file():
                raise SkillValidationError(f"skill reference does not exist: {unresolved}")
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
        return tuple(sorted(paths, key=lambda path: path.relative_to(directory).as_posix()))

    def _render_skill(
        self,
        skill: SkillDefinition,
        *,
        include_references: bool,
    ) -> tuple[str, tuple[str, ...]]:
        chunks = [f"<skill name=\"{skill.name}\">\n{skill.body}\n</skill>\n"]
        included: list[str] = []
        if include_references:
            for reference in skill.reference_paths:
                relative_path = reference.relative_to(skill.directory)
                unresolved = skill.directory / relative_path
                if _path_contains_symlink(unresolved, skill.directory):
                    raise SkillPathError(f"skill reference may not be a symlink: {unresolved}")
                current = unresolved.resolve()
                _require_within(current, skill.directory, label="skill reference")
                if current != reference or not current.is_file():
                    raise SkillPathError(f"skill reference changed after discovery: {unresolved}")
                raw = current.read_bytes()
                if len(raw) > self._max_reference_bytes:
                    raise SkillValidationError(f"skill reference exceeds byte limit: {reference}")
                try:
                    reference_text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SkillValidationError(
                        f"skill reference must be UTF-8: {reference}"
                    ) from exc
                if "\x00" in reference_text:
                    raise SkillValidationError(f"skill reference must be text: {reference}")
                relative = reference.relative_to(skill.directory).as_posix()
                chunks.append(
                    f"<skill-reference skill=\"{skill.name}\" path=\"{relative}\">\n"
                    f"{reference_text.rstrip()}\n</skill-reference>\n"
                )
                included.append(relative)
        return "".join(chunks), tuple(included)

    @staticmethod
    def _root_sort_key(root: SkillRoot) -> tuple[int, str, str]:
        return (root.precedence, root.origin, root.path.as_posix())

    @staticmethod
    def _definition_sort_key(skill: SkillDefinition) -> tuple[int, int, str]:
        lifecycle_order = {"enabled": 0, "candidate": 1, "disabled": 2}
        return (lifecycle_order[skill.lifecycle], skill.precedence, skill.name)

    @staticmethod
    def _validate_budget(*, max_bytes: int, max_tokens: int) -> None:
        if max_bytes <= 0 or max_tokens <= 0:
            raise SkillValidationError("skill disclosure budgets must be positive")


def _parse_frontmatter(text: str, manifest: Path) -> tuple[Mapping[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError(f"SKILL.md must begin with YAML frontmatter: {manifest}")
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise SkillValidationError(f"SKILL.md frontmatter is not closed: {manifest}")
    yaml_text = "".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"invalid YAML frontmatter in {manifest}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SkillValidationError(f"SKILL.md frontmatter must be a mapping: {manifest}")
    if any(not isinstance(key, str) for key in loaded):
        raise SkillValidationError(f"SKILL.md frontmatter keys must be strings: {manifest}")
    body = "".join(lines[closing_index + 1 :])
    return loaded, body


def _required_string(frontmatter: Mapping[str, Any], key: str, manifest: Path) -> str:
    value = frontmatter.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillValidationError(f"frontmatter {key!r} must be a non-empty string: {manifest}")
    return value.strip()


def _metadata(value: Any, manifest: Path) -> Mapping[str, str | tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SkillValidationError(
            f"frontmatter 'metadata' must be a string-keyed mapping: {manifest}"
        )
    normalized: dict[str, str | tuple[str, ...]] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, (str, int, float, bool)):
            normalized[key] = str(item)
        elif isinstance(item, list) and all(
            isinstance(element, (str, int, float, bool)) for element in item
        ):
            normalized[key] = tuple(str(element) for element in item)
        else:
            raise SkillValidationError(
                "frontmatter 'metadata' values must be scalars or scalar lists: "
                f"{manifest}"
            )
    return MappingProxyType(normalized)


def _require_within(path: Path, directory: Path, *, label: str) -> None:
    try:
        path.relative_to(directory.resolve())
    except ValueError as exc:
        raise SkillPathError(f"{label} escapes configured directory: {path}") from exc


def _path_contains_symlink(path: Path, directory: Path) -> bool:
    """Check every existing path component below a trusted directory."""

    try:
        relative = path.relative_to(directory)
    except ValueError:
        return True
    current = directory
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _terms(text: str) -> set[str]:
    return {
        term
        for term in _WORD_RE.findall(text.lower())
        if len(term) >= 3 and term not in _STOP_WORDS
    }


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _estimated_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)


def _fits(text: str, *, max_bytes: int, max_tokens: int) -> bool:
    return len(text.encode("utf-8")) <= max_bytes and _estimated_tokens(text) <= max_tokens


def _truncate_to_budgets(
    text: str,
    *,
    max_bytes: int,
    max_tokens: int,
) -> tuple[str, bool]:
    byte_limit = min(max_bytes, max_tokens * 4)
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text, False
    if byte_limit <= 0:
        return "", True
    marker = "\n[skill content truncated]\n"
    marker_bytes = marker.encode("utf-8")
    if byte_limit <= len(marker_bytes):
        clipped = encoded[:byte_limit]
        return clipped.decode("utf-8", errors="ignore"), True
    clipped = encoded[: byte_limit - len(marker_bytes)]
    return clipped.decode("utf-8", errors="ignore").rstrip() + marker, True
