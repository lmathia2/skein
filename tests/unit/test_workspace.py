from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.execution.workspace import GitWorktreeManager


def _run(root: Path, *args: str) -> None:
    subprocess.run(args, cwd=root, check=True, capture_output=True)


def _repository(root: Path) -> Path:
    root.mkdir()
    _run(root, "git", "init", "-q")
    _run(root, "git", "config", "user.email", "test@example.com")
    _run(root, "git", "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run(root, "git", "add", ".")
    _run(root, "git", "commit", "-qm", "initial")
    return root


def test_worktree_creation_is_idempotent_and_isolated(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    manager = GitWorktreeManager(source, tmp_path / "state")

    first = manager.create("task/with spaces")
    second = manager.create("task/with spaces")

    assert first.path == second.path
    assert first.workspace_id == second.workspace_id
    assert first.path != source
    assert (first.path / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert first.base_revision == second.base_revision


def test_fingerprint_changes_with_workspace_content(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    manager = GitWorktreeManager(source, tmp_path / "state")
    workspace = manager.create("task")
    before = manager.fingerprint("task")

    (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")
    (workspace.path / "new.txt").write_text("new\n", encoding="utf-8")

    assert manager.fingerprint("task") != before
    assert manager.dirty_paths("task") == ["README.md", "new.txt"]


def test_dirty_paths_parse_rename_source_and_destination(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    manager = GitWorktreeManager(source, tmp_path / "state")
    workspace = manager.create("task")

    _run(workspace.path, "git", "mv", "README.md", "renamed.md")

    assert manager.dirty_paths("task") == ["README.md", "renamed.md"]


def test_dirty_workspace_requires_force_to_remove(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    manager = GitWorktreeManager(source, tmp_path / "state")
    workspace = manager.create("task")
    (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="dirty workspace"):
        manager.remove("task")

    manager.remove("task", force=True)
    assert not workspace.path.exists()
    assert manager.load("task") is None
