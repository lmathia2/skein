"""Trusted batch-eval backend: warm container, fresh interpreter per invocation."""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import stat
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from harness.adk.code_mode.runtime.docker import (
    _accept_connection,
    _DockerSandboxSession,
    _materialise_tools,
)
from harness.adk.code_mode.runtime.protocol import ShutdownFrame
from harness.environment.async_call import run_managed_thread

_IDLE = """
import os, time
while True:
    try:
        while os.waitpid(-1, os.WNOHANG)[0]: pass
    except ChildProcessError: pass
    time.sleep(.05)
"""
_RESET = """
import os, pathlib, shutil, signal, time
me = os.getpid()
deadline = time.monotonic() + 5
while True:
    alive = []
    for p in pathlib.Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in (1, me): continue
        try:
            if (p / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z': continue
            alive.append(int(p.name)); os.kill(int(p.name), signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError): pass
    if not alive: break
    if time.monotonic() > deadline: raise RuntimeError('process cleanup timed out')
    time.sleep(.05)
for root in ('/tmp', '/workspace'):
    for child in pathlib.Path(root).iterdir():
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
"""


def _clear(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _replace_tree(source: Path, destination: Path, *, max_bytes: int = 100 * 1024 * 1024) -> None:
    """Copy regular files/directories; sandbox-created links never reach the host."""
    temporary = destination.parent / f".{destination.name}.ptc-{secrets.token_hex(8)}"
    temporary.mkdir(mode=0o700)
    size = 0
    try:
        for current, directories, files in os.walk(source, followlinks=False):
            current_path = Path(current)
            relative = current_path.relative_to(source)
            directories[:] = [
                name for name in directories if not (current_path / name).is_symlink()
            ]
            (temporary / relative).mkdir(parents=True, exist_ok=True)
            for name in files:
                candidate = current_path / name
                if not stat.S_ISREG(candidate.lstat().st_mode):
                    continue
                size += candidate.stat().st_size
                if size > max_bytes:
                    raise ValueError("eval PTC workspace output exceeds 100 MiB")
                shutil.copy2(candidate, temporary / relative / name)
        _clear(destination)
        for child in temporary.iterdir():
            shutil.move(str(child), destination / child.name)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


class ReusableDockerBackend:
    """One exclusive warm container with verified process/filesystem reset per lease."""

    def __init__(self, image: str, root: Path) -> None:
        from harness._vendor import docker

        self.configured_image = image
        self.root = root.resolve()
        self.tools = self.root / "tools"
        self.workspace = self.root / "workspace"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _clear(self.tools)
        _clear(self.workspace)
        client = docker.from_env(timeout=10)
        try:
            self.image_id = str(client.images.get(image).id)
        finally:
            client.close()
        self.container: Any = None
        self.client: Any = None
        self.session: _ReusableSession | None = None
        self.leased = False
        self.closed = False
        self.dirty = False
        self.metrics = {
            "ptc_container_start_ms": 0,
            "ptc_reset_ms": 0,
            "ptc_container_starts": 0,
            "ptc_resets": 0,
        }

    @property
    def identity(self) -> str:
        return self.image_id

    def _ensure_container(self) -> None:
        if self.dirty:
            self._discard()
        if self.container is not None:
            return
        from harness._vendor import docker

        self.client = docker.from_env(timeout=10)
        started = time.monotonic()
        options = (
            {"extra_hosts": {"host.docker.internal": "host-gateway"}}
            if sys.platform.startswith("linux")
            else {}
        )
        self.container = self.client.containers.run(
            self.image_id,
            command=["python", "-u", "-c", _IDLE],
            detach=True,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="1g",
            cpu_period=100000,
            cpu_quota=100000,
            pids_limit=256,
            volumes={
                str(self.tools): {"bind": "/tools", "mode": "ro"},
                str(self.workspace): {"bind": "/workspace", "mode": "rw"},
            },
            tmpfs={"/tmp": "rw,noexec,nosuid,size=128m"},
            **options,
        )
        self.metrics["ptc_container_start_ms"] += int((time.monotonic() - started) * 1000)
        self.metrics["ptc_container_starts"] += 1

    def _discard(self) -> None:
        if self.container is not None:
            self.container.remove(force=True)
            self.container = None
        if self.client is not None:
            self.client.close()
            self.client = None
        self.dirty = False

    def _reset(self) -> None:
        if self.container is None:
            return
        started = time.monotonic()
        self.dirty = True
        try:
            result = self.container.exec_run(["python", "-c", _RESET])
            if result.exit_code != 0:
                raise RuntimeError("eval container reset could not verify clean state")
            _clear(self.tools)
            _clear(self.workspace)
            self.dirty = False
            self.metrics["ptc_resets"] += 1
        except Exception:
            self._discard()
            raise
        finally:
            self.metrics["ptc_reset_ms"] += int((time.monotonic() - started) * 1000)

    async def start(
        self,
        *,
        tools_files: Mapping[str, str],
        workdir_path: str,
        timeout_seconds: int | None,
    ) -> _ReusableSession:
        if self.closed or self.leased:
            raise RuntimeError("eval PTC worker is closed or already leased")
        host_workspace = Path(workdir_path).resolve()
        if not host_workspace.is_dir() or host_workspace.is_symlink():
            raise ValueError("eval PTC turn workspace must be an existing owned directory")
        for name in tools_files:
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("tool source escapes the read-only tool directory")
        self.leased = True
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            await run_managed_thread(self._ensure_container)
            await run_managed_thread(self._reset)
            _materialise_tools(tools_files, str(self.tools))
            listener.bind(("0.0.0.0", 0))
            listener.listen(1)
            token = secrets.token_urlsafe(32)
            environment = {
                "ADK_CODE_MODE_CONTROL_TCP": f"host.docker.internal:{listener.getsockname()[1]}",
                "ADK_CODE_MODE_CONTROL_TOKEN": token,
                "ADK_CODE_MODE_WORKDIR": "/workspace",
                "ADK_CODE_MODE_TOOLS_DIR": "/tools",
                "TMPDIR": "/tmp",
                "HOME": "/workspace",
            }
            await run_managed_thread(
                self.container.exec_run,
                ["python", "-m", "harness.adk.code_mode_sandbox"],
                environment=environment,
                workdir="/workspace",
                detach=True,
            )
            connection = await run_managed_thread(_accept_connection, listener, 15.0, token)
            self.session = _ReusableSession(
                owner=self,
                host_workspace=host_workspace,
                container=self.container,
                control_sock=connection,
                listener=listener,
                tools_dir=str(self.tools),
                timeout_seconds=timeout_seconds,
            )
            return self.session
        except BaseException:
            listener.close()
            await run_managed_thread(self._discard)
            self.leased = False
            raise

    async def aclose(self) -> None:
        self.closed = True
        try:
            if self.session is not None:
                await self.session.close()
        finally:
            await run_managed_thread(self._discard)
            _clear(self.tools)
            _clear(self.workspace)


class _ReusableSession(_DockerSandboxSession):
    def __init__(self, *, owner: ReusableDockerBackend, host_workspace: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.owner = owner
        self.host_workspace = host_workspace

    async def wait(self):
        result = await super().wait()
        await run_managed_thread(_replace_tree, self.owner.workspace, self.host_workspace)
        return result

    async def close(self) -> None:
        if self._closed:
            return
        with suppress(OSError, RuntimeError):
            await super().send(ShutdownFrame())
        self._closed = True
        self._sock.close()
        self._listener.close()
        try:
            await run_managed_thread(self.owner._reset)
        finally:
            self.owner.session = None
            self.owner.leased = False


__all__ = ["ReusableDockerBackend"]
