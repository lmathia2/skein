"""Execution boundaries shared by local and benchmark-hosted runs."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness.execution.repo import RepositoryManifest, build_repository_manifest
from harness.execution.sandbox import CommandSandbox

from .local import WorkspaceEnvironment


class RepositoryRuntime(Protocol):
    def manifest(self) -> RepositoryManifest: ...

    def changed_paths(self, base_revision: str | None) -> list[str]: ...

    def fingerprint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    files: WorkspaceEnvironment
    commands: CommandSandbox
    repository: RepositoryRuntime


class LocalRepositoryRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            args,
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.stdout if completed.returncode == 0 else ""

    def manifest(self) -> RepositoryManifest:
        return build_repository_manifest(self.root)

    def changed_paths(self, base_revision: str | None) -> list[str]:
        from harness.core.orchestration.runtime import changed_paths

        return changed_paths(self.root, base_revision)

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        revision = self._git("git", "rev-parse", "HEAD")
        if not revision:
            # ponytail: non-Git workspaces need an O(files) fallback; use Git if this becomes hot.
            for target in sorted(self.root.rglob("*")):
                relative = target.relative_to(self.root).as_posix()
                if target.is_symlink():
                    digest.update(relative.encode())
                    digest.update(os.readlink(target).encode())
                elif target.is_file():
                    digest.update(relative.encode())
                    with target.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
            return digest.hexdigest()
        digest.update(revision.encode())
        digest.update(self._git("git", "diff", "--binary", "HEAD").encode())
        untracked = self._git(
            "git", "ls-files", "--others", "--exclude-standard", "-z"
        )
        for relative in sorted(path for path in untracked.split("\0") if path):
            target = self.root / relative
            digest.update(relative.encode())
            if target.is_file():
                digest.update(target.read_bytes())
        return digest.hexdigest()


__all__ = [
    "ExecutionRuntime",
    "LocalRepositoryRuntime",
    "RepositoryRuntime",
]
