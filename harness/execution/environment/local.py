"""Local development environment; replace with a managed sandbox in production."""

from __future__ import annotations

import difflib
import hashlib
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness.core.models import CommandResult


class WorkspaceViolationError(ValueError):
    pass


class FileConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FileMutationResult:
    path: str
    changed: bool
    before_sha256: str | None
    after_sha256: str
    diff: str
    already_applied: bool = False


class WorkspaceEnvironment(Protocol):
    """Synchronous file boundary used by the four coding tools."""

    root: Path

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path: ...

    def relative_path(self, path: Path) -> str: ...

    def read_bytes(self, path: str | Path) -> bytes: ...

    def atomic_write(
        self,
        path: str | Path,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> FileMutationResult: ...

    def replace_text(
        self,
        path: str | Path,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> FileMutationResult: ...


_SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "LANG",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "JAVA_HOME",
    "GOPATH",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "UV_CACHE_DIR",
    "NPM_CONFIG_CACHE",
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text_diff(path: str, before: bytes, after: bytes, *, max_lines: int = 200) -> str:
    try:
        before_text = before.decode("utf-8").splitlines(keepends=True)
        after_text = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"Binary file changed: {path} ({len(before)} -> {len(after)} bytes)"
    lines = list(
        difflib.unified_diff(
            before_text,
            after_text,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = [
            *lines[: max_lines // 2],
            f"... {omitted} diff lines omitted ...\n",
            *lines[-max_lines // 2 :],
        ]
    return "".join(lines) or "(no textual diff)"


class LocalWorkspaceEnvironment:
    """Filesystem and process adapter constrained to one workspace root."""

    def __init__(self, root: str | Path, *, artifact_root: str | Path | None = None) -> None:
        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.is_dir():
            raise ValueError(f"Workspace does not exist or is not a directory: {resolved_root}")
        self.root = resolved_root
        configured_artifacts = (
            Path(artifact_root) if artifact_root is not None else Path(".artifacts")
        )
        if configured_artifacts.is_absolute():
            self.artifact_root = configured_artifacts.expanduser().resolve()
        else:
            self.artifact_root = (self.root / configured_artifacts).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolationError(f"Path leaves workspace: {path}") from exc
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Workspace path does not exist: {path}")
        return resolved

    def relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def read_bytes(self, path: str | Path) -> bytes:
        return self.resolve(path, must_exist=True).read_bytes()

    def atomic_write(
        self,
        path: str | Path,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> FileMutationResult:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = target.read_bytes() if target.exists() else b""
        before_hash = sha256_bytes(before) if target.exists() else None
        after_hash = sha256_bytes(content)

        if expected_absent and target.exists():
            if before == content:
                return FileMutationResult(
                    path=self.relative_path(target),
                    changed=False,
                    before_sha256=before_hash,
                    after_sha256=after_hash,
                    diff="(already contained requested content)",
                    already_applied=True,
                )
            raise FileConflictError(f"Expected new file but path already exists: {path}")
        if expected_sha256 is not None and before_hash != expected_sha256:
            if before == content:
                return FileMutationResult(
                    path=self.relative_path(target),
                    changed=False,
                    before_sha256=before_hash,
                    after_sha256=after_hash,
                    diff="(already contained requested content)",
                    already_applied=True,
                )
            raise FileConflictError(
                f"File hash changed for {path}: expected {expected_sha256}, found {before_hash}"
            )
        if before == content:
            return FileMutationResult(
                path=self.relative_path(target),
                changed=False,
                before_sha256=before_hash,
                after_sha256=after_hash,
                diff="(no change)",
                already_applied=True,
            )

        handle, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        return FileMutationResult(
            path=self.relative_path(target),
            changed=True,
            before_sha256=before_hash,
            after_sha256=after_hash,
            diff=_text_diff(self.relative_path(target), before, content),
        )

    def replace_text(
        self,
        path: str | Path,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> FileMutationResult:
        target = self.resolve(path, must_exist=True)
        before = target.read_bytes()
        before_hash = sha256_bytes(before)
        if expected_sha256 is not None and before_hash != expected_sha256:
            raise FileConflictError(
                f"File hash changed for {path}: expected {expected_sha256}, found {before_hash}"
            )
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileConflictError(f"Cannot apply text edit to binary file: {path}") from exc

        occurrences = text.count(old_text)
        if occurrences == 0:
            if new_text and new_text in text:
                return FileMutationResult(
                    path=self.relative_path(target),
                    changed=False,
                    before_sha256=before_hash,
                    after_sha256=before_hash,
                    diff="(requested replacement already present)",
                    already_applied=True,
                )
            raise FileConflictError(f"Exact edit preimage not found in {path}")
        if occurrences != 1:
            raise FileConflictError(
                f"Exact edit preimage occurs {occurrences} times in {path}; provide more context"
            )
        after = text.replace(old_text, new_text, 1).encode("utf-8")
        return self.atomic_write(path, after, expected_sha256=before_hash)

    def _subprocess_environment(self, extra_env: dict[str, str] | None) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in _SAFE_ENV_KEYS or key.startswith("LC_")
        }
        environment.setdefault("PATH", os.defpath)
        environment.setdefault("HOME", str(self.root))
        environment["PWD"] = str(self.root)
        if extra_env:
            environment.update(extra_env)
        return environment

    def run(
        self,
        command: str,
        *,
        timeout_seconds: int,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        process = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            cwd=self.root,
            env=self._subprocess_environment(extra_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        duration_ms = int((time.monotonic() - started) * 1_000)
        return CommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=None if timed_out else process.returncode,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

    def store_artifact(self, category: str, content: bytes, *, suffix: str = ".txt") -> str:
        digest = sha256_bytes(content)
        directory = self.artifact_root / category
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}{suffix}"
        if not target.exists():
            target.write_bytes(content)
        return f"artifact://{category}/{target.name}"
