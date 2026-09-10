"""Deterministic repository discovery used by the context compiler.

The manifest deliberately contains orientation data rather than source bodies. It is
small enough to include in a normal coding turn and can be rebuilt without an LLM.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}

_LANGUAGE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py", ".pyi"},
    "typescript": {".ts", ".tsx"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "java": {".java"},
    "kotlin": {".kt", ".kts"},
    "go": {".go"},
    "rust": {".rs"},
    "c": {".c", ".h"},
    "cpp": {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"},
    "csharp": {".cs"},
    "ruby": {".rb"},
    "php": {".php"},
    "swift": {".swift"},
    "scala": {".scala"},
    "shell": {".sh", ".bash", ".zsh"},
}


@dataclass(frozen=True, slots=True)
class BuildCommand:
    """One repository command discovered from standard project manifests."""

    kind: str
    command: str
    source: str


@dataclass(slots=True)
class RepositoryManifest:
    """Compact, deterministic description of a checked-out repository."""

    root: Path
    base_revision: str | None = None
    branch: str | None = None
    dirty: bool = False
    languages: list[str] = field(default_factory=list)
    build_systems: list[str] = field(default_factory=list)
    commands: list[BuildCommand] = field(default_factory=list)
    instruction_files: list[Path] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    tracked_file_count: int = 0
    file_paths: frozenset[str] | None = None

    def to_compact_text(self) -> str:
        """Render stable model-facing text without timestamps or random values."""

        lines = ["Repository manifest:", f"- root: {self.root.as_posix()}"]
        if self.base_revision:
            lines.append(f"- base revision: {self.base_revision}")
        if self.branch:
            lines.append(f"- branch: {self.branch}")
        lines.extend(
            [
                f"- dirty: {'yes' if self.dirty else 'no'}",
                f"- tracked files: {self.tracked_file_count}",
                "- languages: " + (", ".join(self.languages) or "unknown"),
                "- build systems: " + (", ".join(self.build_systems) or "unknown"),
            ]
        )
        if self.commands:
            lines.append("- commands:")
            for item in sorted(self.commands, key=lambda value: (value.kind, value.command)):
                lines.append(f"  - {item.kind}: {item.command} ({item.source})")
        if self.instruction_files:
            lines.append("- project instructions:")
            for path in self.instruction_files:
                lines.append(f"  - {path.relative_to(self.root).as_posix()}")
        if self.top_level:
            lines.append("- top-level entries: " + ", ".join(self.top_level))
        return "\n".join(lines)


def _run(cwd: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _walk_files(root: Path) -> Iterable[Path]:
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(name for name in names if name not in _EXCLUDED_DIRS)
        base = Path(directory)
        for filename in sorted(filenames):
            yield base / filename


def _git_tracked_files(root: Path) -> list[Path]:
    output = _run(root, "git", "ls-files", "-z")
    if output is not None:
        return [root / item for item in output.split("\0") if item]
    return list(_walk_files(root))


def _detect_languages(files: Iterable[Path]) -> list[str]:
    counts: dict[str, int] = {}
    for path in files:
        suffix = path.suffix.lower()
        for language, extensions in _LANGUAGE_EXTENSIONS.items():
            if suffix in extensions:
                counts[language] = counts.get(language, 0) + 1
                break
    return [name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _add_command(
    commands: list[BuildCommand], kind: str, command: str, source: str
) -> None:
    candidate = BuildCommand(kind=kind, command=command, source=source)
    if candidate not in commands:
        commands.append(candidate)


def _discover_build_commands(root: Path) -> tuple[list[str], list[BuildCommand]]:
    names = {path.relative_to(root).as_posix() for path in _walk_files(root)}

    def read_text(name: str) -> str:
        return (root / name).read_text(encoding="utf-8", errors="replace")

    return discover_build_commands(names, read_text)


def discover_build_commands(
    files: set[str], read_text: Callable[[str], str]
) -> tuple[list[str], list[BuildCommand]]:
    """Discover standard commands from a portable file-name/content snapshot."""

    systems: list[str] = []
    commands: list[BuildCommand] = []

    if "pyproject.toml" in files:
        systems.append("pyproject.toml")
        text = read_text("pyproject.toml")
        if "pytest" in text or any(name.startswith("tests/") for name in files):
            _add_command(commands, "test", "uv run pytest", "pyproject.toml")
        if "ruff" in text:
            _add_command(commands, "lint", "uv run ruff check .", "pyproject.toml")
        if "pyright" in text:
            _add_command(commands, "typecheck", "uv run pyright", "pyproject.toml")
        elif "mypy" in text:
            _add_command(commands, "typecheck", "uv run mypy .", "pyproject.toml")

    if "package.json" in files:
        systems.append("package.json")
        try:
            package = json.loads(read_text("package.json"))
        except (json.JSONDecodeError, OSError):
            package = {}
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        if isinstance(scripts, dict):
            for kind, script in (
                ("test", "test"),
                ("lint", "lint"),
                ("typecheck", "typecheck"),
                ("build", "build"),
            ):
                if script in scripts:
                    _add_command(commands, kind, f"npm run {script}", "package.json")

    if "Cargo.toml" in files:
        systems.append("Cargo.toml")
        _add_command(commands, "test", "cargo test", "Cargo.toml")
        _add_command(commands, "lint", "cargo clippy --all-targets --all-features", "Cargo.toml")
    if "go.mod" in files:
        systems.append("go.mod")
        _add_command(commands, "test", "go test ./...", "go.mod")
        _add_command(commands, "build", "go build ./...", "go.mod")
    if "pom.xml" in files:
        systems.append("pom.xml")
        _add_command(commands, "test", "mvn test", "pom.xml")
    if "gradlew" in files:
        systems.append("gradle")
        _add_command(commands, "test", "./gradlew test", "gradlew")
    if "Makefile" in files:
        systems.append("Makefile")

    return sorted(set(systems)), sorted(commands, key=lambda value: (value.kind, value.command))


def repository_manifest_from_snapshot(
    *,
    root: Path,
    files: Iterable[str],
    read_text: Callable[[str], str],
    base_revision: str | None = None,
    branch: str | None = None,
    dirty: bool = False,
) -> RepositoryManifest:
    """Build the compact manifest from an isolated environment snapshot."""

    names = sorted(set(files))
    systems, commands = discover_build_commands(set(names), read_text)
    top_level = sorted(
        {
            name.split("/", 1)[0] + ("/" if "/" in name else "")
            for name in names
            if name.split("/", 1)[0] not in _EXCLUDED_DIRS
        }
    )[:80]
    return RepositoryManifest(
        root=root,
        base_revision=base_revision,
        branch=branch,
        dirty=dirty,
        languages=_detect_languages(root / name for name in names),
        build_systems=systems,
        commands=commands,
        top_level=top_level,
        tracked_file_count=len(names),
        file_paths=frozenset(names),
    )


def discover_instruction_files(root: Path, cwd: Path | None = None) -> list[Path]:
    """Find layered AGENTS/CLAUDE files from repository root through ``cwd``.

    ``AGENTS.override.md`` replaces other instruction files in the same directory,
    matching the override behavior used by Pi and other coding harnesses.
    """

    root = root.resolve()
    current = (cwd or root).resolve()
    try:
        relative = current.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"cwd {current} is outside repository root {root}") from exc

    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)

    discovered: list[Path] = []
    for directory in directories:
        override = directory / "AGENTS.override.md"
        if override.is_file():
            discovered.append(override)
            continue
        agents = directory / "AGENTS.md"
        claude = directory / "CLAUDE.md"
        if agents.is_file():
            discovered.append(agents)
        elif claude.is_file():
            discovered.append(claude)
    return discovered


def collect_project_instructions(root: Path, cwd: Path | None = None) -> str:
    """Return deterministic project instructions with source-path boundaries."""

    root = root.resolve()
    blocks: list[str] = []
    for path in discover_instruction_files(root, cwd):
        relative = path.relative_to(root).as_posix()
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            blocks.append(f"## {relative}\n{body}")
    return "\n\n".join(blocks)


def build_repository_manifest(root: Path, cwd: Path | None = None) -> RepositoryManifest:
    """Inspect Git and standard build files without calling an LLM."""

    root = root.resolve()
    files = _git_tracked_files(root)
    revision = _run(root, "git", "rev-parse", "HEAD")
    branch = _run(root, "git", "branch", "--show-current")
    status = _run(root, "git", "status", "--porcelain")
    systems, commands = _discover_build_commands(root)
    top_level = sorted(
        path.name + ("/" if path.is_dir() else "")
        for path in root.iterdir()
        if path.name not in _EXCLUDED_DIRS
    )[:80]
    return RepositoryManifest(
        root=root,
        base_revision=revision,
        branch=branch,
        dirty=bool(status),
        languages=_detect_languages(files),
        build_systems=systems,
        commands=commands,
        instruction_files=discover_instruction_files(root, cwd),
        top_level=top_level,
        tracked_file_count=len(files),
        file_paths=frozenset(path.relative_to(root).as_posix() for path in files),
    )
