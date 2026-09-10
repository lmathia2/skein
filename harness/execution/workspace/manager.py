"""Idempotent Git worktree lifecycle for isolated coding tasks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    task_id: str
    workspace_id: str
    path: Path
    source_repository: Path
    base_revision: str
    branch: str | None
    head_revision: str
    tree_hash: str


def _run(
    cwd: Path,
    *args: str,
    check: bool = True,
    timeout: int = 120,
    strip: bool = True,
) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"command failed ({' '.join(args)}): {message}")
    return completed.stdout.strip() if strip else completed.stdout


def _safe_name(task_id: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-._")[:40]
    digest = hashlib.sha256(task_id.encode()).hexdigest()[:12]
    return f"{readable or 'task'}-{digest}"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class GitWorktreeManager:
    """Provision one detached or named worktree per durable task."""

    def __init__(self, source_repository: Path, state_root: Path) -> None:
        self.source_repository = source_repository.resolve()
        self.state_root = state_root.resolve()
        self.worktrees_root = self.state_root / "worktrees"
        self.metadata_root = self.state_root / "workspace-records"
        self.lock_path = self.state_root / "worktrees.lock"
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        top = Path(_run(self.source_repository, "git", "rev-parse", "--show-toplevel"))
        if top.resolve() != self.source_repository:
            raise ValueError(
                f"source_repository must be the Git root: expected {top}, got {self.source_repository}"
            )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _path(self, task_id: str) -> Path:
        return self.worktrees_root / _safe_name(task_id)

    def _metadata_path(self, task_id: str) -> Path:
        return self.metadata_root / f"{_safe_name(task_id)}.json"

    def _record(self, task_id: str, path: Path, base_revision: str) -> WorkspaceRecord:
        head = _run(path, "git", "rev-parse", "HEAD")
        tree = _run(path, "git", "rev-parse", "HEAD^{tree}")
        branch = _run(path, "git", "branch", "--show-current", check=False) or None
        record = WorkspaceRecord(
            task_id=task_id,
            workspace_id=hashlib.sha256(path.as_posix().encode()).hexdigest()[:24],
            path=path,
            source_repository=self.source_repository,
            base_revision=base_revision,
            branch=branch,
            head_revision=head,
            tree_hash=tree,
        )
        _atomic_json(
            self._metadata_path(task_id),
            {
                "task_id": record.task_id,
                "workspace_id": record.workspace_id,
                "path": record.path.as_posix(),
                "source_repository": record.source_repository.as_posix(),
                "base_revision": record.base_revision,
                "branch": record.branch,
                "head_revision": record.head_revision,
                "tree_hash": record.tree_hash,
            },
        )
        return record

    def create(
        self,
        task_id: str,
        *,
        base_ref: str = "HEAD",
        branch: str | None = None,
    ) -> WorkspaceRecord:
        """Create or reattach the task worktree without discarding existing work."""

        with self._lock():
            base_revision = _run(self.source_repository, "git", "rev-parse", base_ref)
            path = self._path(task_id)
            if path.exists():
                top = Path(_run(path, "git", "rev-parse", "--show-toplevel")).resolve()
                if top != path.resolve():
                    raise RuntimeError(f"existing task path is not its own worktree: {path}")
                metadata_path = self._metadata_path(task_id)
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if Path(metadata["source_repository"]).resolve() != self.source_repository:
                        raise RuntimeError("workspace belongs to a different source repository")
                    base_revision = str(metadata["base_revision"])
                return self._record(task_id, path, base_revision)

            path.parent.mkdir(parents=True, exist_ok=True)
            command = ["git", "worktree", "add"]
            if branch:
                command.extend(["-b", branch])
            else:
                command.append("--detach")
            command.extend([path.as_posix(), base_revision])
            _run(self.source_repository, *command, timeout=300)
            return self._record(task_id, path, base_revision)

    def load(self, task_id: str) -> WorkspaceRecord | None:
        metadata_path = self._metadata_path(task_id)
        if not metadata_path.exists():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        path = Path(payload["path"])
        if not path.exists():
            return None
        return self._record(task_id, path, str(payload["base_revision"]))

    def dirty_paths(self, task_id: str) -> list[str]:
        record = self.load(task_id)
        if record is None:
            raise KeyError(task_id)
        output = _run(
            record.path,
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            strip=False,
        )
        paths: list[str] = []
        entries = output.split("\0")
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4 or entry[2] != " ":
                raise RuntimeError("malformed git status --porcelain=v1 -z output")

            status = entry[:2]
            paths.append(entry[3:])
            if "R" in status or "C" in status:
                if index >= len(entries) or not entries[index]:
                    raise RuntimeError(
                        "rename or copy missing source path in git status output"
                    )
                paths.append(entries[index])
                index += 1
        return sorted(set(path for path in paths if path))

    def fingerprint(self, task_id: str) -> str:
        """Hash HEAD plus tracked/untracked workspace deltas for resume checks."""

        record = self.load(task_id)
        if record is None:
            raise KeyError(task_id)
        digest = hashlib.sha256()
        digest.update(_run(record.path, "git", "rev-parse", "HEAD").encode())
        digest.update(_run(record.path, "git", "diff", "--binary", "HEAD").encode())
        untracked = _run(
            record.path,
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        for relative in sorted(path for path in untracked.split("\0") if path):
            target = record.path / relative
            digest.update(relative.encode())
            if target.is_file():
                digest.update(target.read_bytes())
        return digest.hexdigest()

    def remove(self, task_id: str, *, force: bool = False) -> None:
        with self._lock():
            record = self.load(task_id)
            if record is None:
                return
            dirty = self.dirty_paths(task_id)
            if dirty and not force:
                raise RuntimeError(
                    "refusing to remove a dirty workspace; checkpoint, commit, or pass force=True"
                )
            command = ["git", "worktree", "remove"]
            if force:
                command.append("--force")
            command.append(record.path.as_posix())
            _run(self.source_repository, *command, timeout=300)
            self._metadata_path(task_id).unlink(missing_ok=True)
            _run(self.source_repository, "git", "worktree", "prune")
