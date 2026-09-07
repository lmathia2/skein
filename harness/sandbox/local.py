"""Resource-bounded local command runner for development and CI.

This runner is not an OS security boundary. Production deployments should use the
Docker sandbox adapter; approval policy remains active for every backend.
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .base import SandboxRequest, SandboxResult
from .output import bounded_result, environment_secret_values


def _limit_resources(
    *,
    max_memory_bytes: int,
    max_processes: int,
    max_file_bytes: int,
) -> None:
    def set_limit(kind: int, requested: int) -> None:
        try:
            _, hard = resource.getrlimit(kind)
            limit = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(kind, (limit, limit))
        except (OSError, ValueError):
            # Some development hosts disallow selected limits in a pre-exec child.
            # The local adapter is not a security boundary; managed deployments use
            # Docker for enforceable resource isolation.
            return

    set_limit(resource.RLIMIT_CORE, 0)
    set_limit(resource.RLIMIT_FSIZE, max_file_bytes)
    if hasattr(resource, "RLIMIT_AS"):
        set_limit(resource.RLIMIT_AS, max_memory_bytes)
    if hasattr(resource, "RLIMIT_NPROC") and sys.platform != "darwin":
        # RLIMIT_NPROC is per-user on macOS, so lowering it in a child can block
        # ordinary shell pipelines when the desktop user already has many processes.
        set_limit(resource.RLIMIT_NPROC, max_processes)


class LocalSandbox:
    """Execute with a minimal environment and Unix resource limits."""

    def __init__(
        self,
        workspace: Path,
        artifact_root: Path,
        *,
        environment: Mapping[str, str] | None = None,
        known_secrets: Sequence[str] = (),
        max_memory_bytes: int = 4 * 1024 * 1024 * 1024,
        max_processes: int = 256,
        max_file_bytes: int = 1024 * 1024 * 1024,
        max_output_bytes: int = 16_000,
    ) -> None:
        self.workspace = workspace.resolve()
        self.artifact_root = artifact_root.resolve()
        self.environment = dict(environment or {})
        self.known_secrets = tuple(known_secrets)
        self.max_memory_bytes = max_memory_bytes
        self.max_processes = max_processes
        self.max_file_bytes = max_file_bytes
        self.max_output_bytes = max_output_bytes

    def _environment(self, request: SandboxRequest) -> dict[str, str]:
        safe_defaults = {
            "HOME": str(self.artifact_root / "home"),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
            "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPYCACHEPREFIX": str(self.artifact_root / "pycache"),
        }
        home = Path(safe_defaults["HOME"])
        home.mkdir(parents=True, exist_ok=True)
        return {**safe_defaults, **self.environment, **dict(request.environment)}

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        timeout = max(1, min(request.timeout_seconds, 3_600))
        environment = self._environment(request)
        known_secrets = tuple(
            sorted(
                {
                    *self.known_secrets,
                    *environment_secret_values(environment),
                }
            )
        )
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                request.command,
                cwd=self.workspace,
                shell=True,
                executable="/bin/bash",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
                preexec_fn=lambda: _limit_resources(
                    max_memory_bytes=self.max_memory_bytes,
                    max_processes=self.max_processes,
                    max_file_bytes=self.max_file_bytes,
                ),
            )
            stdout, stderr = process.communicate(timeout=timeout)
            return bounded_result(
                status="ok" if process.returncode == 0 else "error",
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except subprocess.TimeoutExpired:
            assert process is not None
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            return bounded_result(
                status="timeout",
                exit_code=124,
                stdout=stdout,
                stderr=(stderr + "\ncommand timed out").strip(),
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )
        except (OSError, ValueError) as exc:
            return bounded_result(
                status="blocked",
                exit_code=None,
                stdout="",
                stderr=f"local sandbox unavailable: {exc}",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=known_secrets,
            )


__all__ = ["LocalSandbox"]
