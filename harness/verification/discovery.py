"""Discover a cheap-to-broad validation ladder from repository metadata."""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import Path

from harness.execution.repo.discovery import RepositoryManifest

from .models import ValidationCommand, ValidationPlan


def _existing_candidates(
    root: Path,
    candidates: Iterable[Path],
    known_paths: frozenset[str] | None = None,
) -> list[str]:
    found: list[str] = []
    for candidate in candidates:
        relative = candidate.relative_to(root).as_posix()
        exists = candidate.is_file() if known_paths is None else relative in known_paths
        if exists:
            found.append(relative)
    return sorted(set(found))


def find_adjacent_tests(
    root: Path,
    changed_paths: Iterable[str],
    *,
    known_paths: frozenset[str] | None = None,
) -> list[str]:
    """Infer likely tests using conservative naming conventions."""

    tests: list[str] = []
    for relative in changed_paths:
        path = root / relative
        suffix = path.suffix.lower()
        stem = path.stem
        if suffix in {".py", ".pyi"}:
            if stem.startswith("test_"):
                tests.extend(_existing_candidates(root, (path,), known_paths))
            else:
                test_stem = stem.lstrip("_") or stem
                tests.extend(
                    _existing_candidates(
                        root,
                        (
                            path.with_name(f"test_{test_stem}.py"),
                            root / "tests" / path.parent.relative_to(root) / f"test_{test_stem}.py",
                            root / "tests" / f"test_{test_stem}.py",
                        ),
                        known_paths,
                    )
                )
        elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
            if any(marker in stem for marker in (".test", ".spec")):
                tests.extend(_existing_candidates(root, (path,), known_paths))
            else:
                tests.extend(
                    _existing_candidates(
                        root,
                        (
                            path.with_name(f"{stem}.test{suffix}"),
                            path.with_name(f"{stem}.spec{suffix}"),
                            path.parent / "__tests__" / f"{stem}.test{suffix}",
                        ),
                        known_paths,
                    )
                )
    return sorted(set(tests))


def discover_validation_plan(
    manifest: RepositoryManifest,
    changed_paths: Iterable[str],
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> ValidationPlan:
    changed = sorted(set(changed_paths))
    commands: list[ValidationCommand] = []

    python_files = [path for path in changed if Path(path).suffix.lower() in {".py", ".pyi"}]
    if python_files:
        quoted = " ".join(shlex.quote(path) for path in python_files)
        commands.append(
            ValidationCommand(
                category="syntax",
                command=f"python -m py_compile {quoted}",
                source="changed Python files",
                targeted=True,
            )
        )

    adjacent_tests = find_adjacent_tests(
        manifest.root, changed, known_paths=manifest.file_paths
    )
    command_by_kind = {item.kind: item for item in manifest.commands}
    for kind in ("lint", "typecheck"):
        discovered = command_by_kind.get(kind)
        if discovered:
            commands.append(
                ValidationCommand(
                    category=kind,
                    command=discovered.command,
                    source=discovered.source,
                    required=False,
                )
            )

    test_command = command_by_kind.get("test")
    if test_command:
        command = test_command.command
        targeted = False
        if adjacent_tests and "pytest" in command:
            command = f"{command} " + " ".join(shlex.quote(path) for path in adjacent_tests)
            targeted = True
        commands.append(
            ValidationCommand(
                category="test",
                command=command,
                source=test_command.source,
                targeted=targeted,
            )
        )
    elif adjacent_tests:
        for test_path in adjacent_tests:
            path = Path(test_path)
            if path.suffix.lower() != ".py":
                continue
            start = path.parent.as_posix()
            commands.append(
                ValidationCommand(
                    category="test",
                    command=(
                        "python -m unittest discover "
                        f"-s {shlex.quote(start)} -p {shlex.quote(path.name)} -v"
                    ),
                    source=f"adjacent unittest module {test_path}",
                    targeted=True,
                    minimum_test_count=1,
                )
            )

    commands.append(
        ValidationCommand(
            category="diff",
            command="git diff --check",
            source="git",
        )
    )
    return ValidationPlan(
        commands=commands,
        changed_paths=changed,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths or [],
    )
