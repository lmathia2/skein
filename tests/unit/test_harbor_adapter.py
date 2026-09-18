from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.adapters.pier import (
    HarborCommandSandbox,
    HarborRepositoryRuntime,
    HarborWorkspaceEnvironment,
    _AsyncBridge,
    _package_workspace,
)
from harness.execution.environment import WorkspaceViolationError
from harness.execution.sandbox import SandboxRequest


class _Result:
    def __init__(self, stdout: str = "", stderr: str = "", return_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


class _Environment:
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _Result:
        del user
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError from error
        return _Result(completed.stdout, completed.stderr, completed.returncode)

    async def is_file(self, path: str, user: str | int | None = None) -> bool:
        del user
        return Path(path).is_file()

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        shutil.copyfile(source_path, target_path)

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        shutil.copyfile(source_path, target_path)


class _TimeoutEnvironment(_Environment):
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _Result:
        raise RuntimeError(f"Command timed out after {timeout_sec} seconds")


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
async def test_empty_file_creation_is_a_real_mutation_before_idempotent_reuse(tmp_path, remote):
    from harness.execution.environment import LocalWorkspaceEnvironment

    environment = (HarborWorkspaceEnvironment(_Environment(), _AsyncBridge(asyncio.get_running_loop()), tmp_path.as_posix())
                   if remote else LocalWorkspaceEnvironment(tmp_path))
    for expected_absent, name in ((True, 'guarded'), (False, 'unguarded')):
        path = f'{name}/empty.txt'
        first = await asyncio.to_thread(environment.atomic_write, path, b'', expected_absent=expected_absent)
        assert first.changed and not first.already_applied and first.before_sha256 is None
        assert (tmp_path / path).is_file() and (tmp_path / path).read_bytes() == b''
        repeated = await asyncio.to_thread(environment.atomic_write, path, b'', expected_absent=expected_absent)
        assert repeated.already_applied and not repeated.changed
        assert repeated.before_sha256 == repeated.after_sha256 == first.after_sha256


def test_harbor_sandbox_normalizes_pier_command_timeout(tmp_path: Path) -> None:
    async def exercise() -> None:
        environment = _TimeoutEnvironment()
        sandbox = HarborCommandSandbox(
            environment,  # type: ignore[arg-type]
            _AsyncBridge(asyncio.get_running_loop()),
            tmp_path.as_posix(),
            tmp_path / "artifacts",
            max_output_bytes=1_000,
            known_secrets=(),
        )

        result = await asyncio.to_thread(
            sandbox.execute,
            SandboxRequest(command="pytest", timeout_seconds=7),
        )

        assert result.status == "timeout"
        assert result.exit_code == 124

    asyncio.run(exercise())


def test_harbor_runtime_keeps_files_commands_and_repository_in_task_environment(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=workspace, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=workspace, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=workspace, check=True)
    (workspace / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=workspace, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=workspace, check=True)

    async def exercise() -> None:
        environment = _Environment()
        bridge = _AsyncBridge(asyncio.get_running_loop())
        files = HarborWorkspaceEnvironment(environment, bridge, workspace.as_posix())  # type: ignore[arg-type]
        repository = await asyncio.to_thread(
            HarborRepositoryRuntime,
            environment,  # type: ignore[arg-type]
            bridge,
            files,
        )
        base_revision = (await asyncio.to_thread(repository.manifest)).base_revision
        assert base_revision is not None
        sandbox = HarborCommandSandbox(
            environment,  # type: ignore[arg-type]
            bridge,
            workspace.as_posix(),
            tmp_path / "artifacts",
            max_output_bytes=64,
            known_secrets=("private-token",),
        )

        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__" / "app.pyc").write_bytes(b"cache")
        assert await asyncio.to_thread(repository.changed_paths, None) == []

        assert await asyncio.to_thread(files.read_bytes, "app.py") == b"value = 1\n"
        mutation = await asyncio.to_thread(
            files.replace_text,
            "app.py",
            "value = 1",
            "value = 2",
        )
        assert mutation.changed
        assert await asyncio.to_thread(repository.changed_paths, None) == ["app.py"]
        (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
        assert await asyncio.to_thread(repository.changed_paths, None) == []
        (workspace / "new.py").write_text("new = True\n", encoding="utf-8")
        assert await asyncio.to_thread(repository.changed_paths, None) == ["new.py"]
        (workspace / "new.py").unlink()
        manifest = await asyncio.to_thread(repository.manifest)
        assert manifest.languages == ["python"]

        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(("git", "add", "app.py"), cwd=workspace, check=True)
        subprocess.run(("git", "commit", "-qm", "model change"), cwd=workspace, check=True)
        assert await asyncio.to_thread(repository.changed_paths, base_revision) == ["app.py"]
        assert (await asyncio.to_thread(repository.manifest)).dirty

        result = await asyncio.to_thread(
            sandbox.execute,
            SandboxRequest(command="printf 'private-token'"),
        )
        assert "private-token" not in result.stdout
        assert "<redacted>" in result.stdout

        with pytest.raises(WorkspaceViolationError):
            await asyncio.to_thread(files.read_bytes, "../outside")

    asyncio.run(exercise())


def test_harbor_submission_packages_uncommitted_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=workspace, check=True)
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=workspace, check=True)
    subprocess.run(("git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "initial"), cwd=workspace, check=True)

    async def exercise() -> None:
        environment = _Environment()
        bridge = _AsyncBridge(asyncio.get_running_loop())
        files = HarborWorkspaceEnvironment(environment, bridge, workspace.as_posix())
        repository = await asyncio.to_thread(HarborRepositoryRuntime, environment, bridge, files)
        base_revision = (await asyncio.to_thread(repository.manifest)).base_revision
        fingerprint_before = await asyncio.to_thread(repository.fingerprint)
        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
        assert await asyncio.to_thread(repository.fingerprint) != fingerprint_before
        changed, committed = await _package_workspace(
            environment, repository, workspace.as_posix(), base_revision)
        assert committed and changed == ("app.py",)
        assert subprocess.check_output(("git", "status", "--porcelain"), cwd=workspace) == b""
        assert subprocess.check_output(("git", "show", "HEAD:app.py"), cwd=workspace) == b"value = 2\n"

    asyncio.run(exercise())
