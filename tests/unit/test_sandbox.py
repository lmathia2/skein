from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.execution.sandbox import (
    DockerSandbox,
    LocalSandbox,
    SandboxRequest,
    create_command_sandbox,
)


def test_local_sandbox_executes_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = LocalSandbox(workspace, tmp_path / "artifacts")

    result = sandbox.execute(
        SandboxRequest(
            command="printf 'hello' > result.txt && cat result.txt",
            timeout_seconds=10,
        )
    )

    assert result.status == "ok"
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "hello"


def test_local_sandbox_keeps_home_and_python_caches_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    sandbox = LocalSandbox(workspace, artifacts)

    result = sandbox.execute(
        SandboxRequest(
            command=(
                "python -c \"import os, pathlib; "
                "pathlib.Path(os.environ['HOME'], 'marker').write_text('ok'); "
                "print(os.environ['HOME']); print(os.environ['PYTHONPYCACHEPREFIX'])\""
            )
        )
    )

    assert result.status == "ok"
    assert result.stdout.splitlines() == [
        str(artifacts / "home"), str(artifacts / "pycache")
    ]
    assert (artifacts / "home" / "marker").read_text() == "ok"
    assert not (workspace / ".sandbox-home").exists()


def test_local_sandbox_spills_full_output_and_honors_byte_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = LocalSandbox(
        workspace,
        tmp_path / "artifacts",
        max_output_bytes=512,
    )

    result = sandbox.execute(
        SandboxRequest(command="python -c \"print('x' * 5000)\"")
    )

    assert result.status == "ok"
    assert result.truncated
    assert result.omitted_bytes > 0
    assert len(result.stdout.encode("utf-8")) <= 512
    assert result.artifact_uri
    artifact = Path(result.artifact_uri.removeprefix("file://"))
    assert artifact.exists()
    assert len(artifact.read_text(encoding="utf-8")) > 5_000


def test_local_sandbox_redacts_environment_secrets_before_artifact_spill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "local-sensitive-value"
    sandbox = LocalSandbox(
        workspace,
        tmp_path / "artifacts",
        known_secrets=[secret],
        max_output_bytes=512,
    )

    result = sandbox.execute(
        SandboxRequest(
            command="python -c \"print('local-sensitive-value' + 'x' * 3000)\""
        )
    )

    assert result.artifact_uri
    artifact = Path(result.artifact_uri.removeprefix("file://"))
    assert secret not in artifact.read_text(encoding="utf-8")
    assert "<redacted>" in artifact.read_text(encoding="utf-8")


def test_local_sandbox_reports_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = LocalSandbox(workspace, tmp_path / "artifacts")

    result = sandbox.execute(
        SandboxRequest(command="python -c 'import time; time.sleep(5)'", timeout_seconds=1)
    )

    assert result.status == "timeout"
    assert result.exit_code == 124
    assert "timed out" in result.stderr


def test_docker_sandbox_builds_hardened_networkless_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = DockerSandbox(
        workspace,
        tmp_path / "artifacts",
        image="python:3.12-slim",
        allow_network=False,
        cpus=1.5,
        memory="2g",
        pids_limit=128,
    )

    command = sandbox.build_command(SandboxRequest(command="pytest -q"))

    assert command[:3] == ["docker", "run", "--rm"]
    assert command[
        command.index("--network") : command.index("--network") + 2
    ] == ["--network", "none"]
    assert command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ] == ["--cap-drop", "ALL"]
    assert "no-new-privileges:true" in command
    assert "type=bind" in next(value for value in command if value.startswith("type=bind"))
    assert "python:3.12-slim" in command
    assert command[-1] == "pytest -q"


def test_docker_sandbox_execution_can_be_tested_without_docker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: list[list[str]] = []

    def fake_runner(command, **kwargs):
        captured.append(command)
        assert kwargs["timeout"] == 12
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="passed",
            stderr="",
        )

    sandbox = DockerSandbox(
        workspace,
        tmp_path / "artifacts",
        image="example/image@sha256:abcdef",
        runner=fake_runner,
    )
    result = sandbox.execute(SandboxRequest(command="run-tests", timeout_seconds=12))

    assert result.status == "ok"
    assert result.stdout == "passed"
    assert captured and captured[0][-1] == "run-tests"


def test_docker_sandbox_redacts_timeout_output_before_artifact_spill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "docker-sensitive-value"

    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=secret + ("x" * 3_000),
            stderr="still running",
        )

    sandbox = DockerSandbox(
        workspace,
        tmp_path / "artifacts",
        image="example/image@sha256:abcdef",
        known_secrets=[secret],
        max_output_bytes=512,
        runner=timeout_runner,
    )

    result = sandbox.execute(SandboxRequest(command="slow", timeout_seconds=1))

    assert result.status == "timeout"
    assert result.artifact_uri
    artifact = Path(result.artifact_uri.removeprefix("file://"))
    assert secret not in artifact.read_text(encoding="utf-8")
    assert "<redacted>" in artifact.read_text(encoding="utf-8")


def test_factory_defaults_local_and_requires_a_docker_image(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"

    monkeypatch.delenv("SKEIN_SANDBOX", raising=False)
    assert isinstance(create_command_sandbox(workspace, state), LocalSandbox)

    monkeypatch.setenv("SKEIN_SANDBOX", "docker")
    monkeypatch.delenv("SKEIN_SANDBOX_IMAGE", raising=False)
    with pytest.raises(ValueError, match="SANDBOX_IMAGE"):
        create_command_sandbox(workspace, state)
