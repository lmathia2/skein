from __future__ import annotations

import subprocess
from pathlib import Path

from harness.execution.repo.discovery import (
    build_repository_manifest,
    collect_project_instructions,
    discover_instruction_files,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def test_build_manifest_discovers_git_language_and_commands(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[dependency-groups]\ndev=['pytest','ruff','pyright']\n",
        encoding="utf-8",
    )
    (tmp_path / "demo.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    manifest = build_repository_manifest(tmp_path)

    assert manifest.base_revision
    assert manifest.tracked_file_count == 2
    assert "python" in manifest.languages
    commands = {item.kind: item.command for item in manifest.commands}
    assert commands["test"] == "uv run pytest"
    assert commands["lint"] == "uv run ruff check ."
    assert commands["typecheck"] == "uv run pyright"
    rendered = manifest.to_compact_text()
    assert "Repository manifest" in rendered
    assert "tracked files: 2" in rendered


def test_instruction_override_replaces_same_directory_default(tmp_path: Path) -> None:
    nested = tmp_path / "services" / "payments"
    nested.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root guidance", encoding="utf-8")
    (tmp_path / "services" / "AGENTS.md").write_text("service guidance", encoding="utf-8")
    (nested / "AGENTS.md").write_text("ignored", encoding="utf-8")
    (nested / "AGENTS.override.md").write_text("payment override", encoding="utf-8")

    discovered = discover_instruction_files(tmp_path, nested)
    assert [path.name for path in discovered] == [
        "AGENTS.md",
        "AGENTS.md",
        "AGENTS.override.md",
    ]
    combined = collect_project_instructions(tmp_path, nested)
    assert "root guidance" in combined
    assert "service guidance" in combined
    assert "payment override" in combined
    assert "ignored" not in combined
