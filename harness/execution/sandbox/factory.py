"""Environment-driven sandbox backend selection."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from harness.core.config import SandboxConfig

from .base import CommandSandbox
from .docker import DockerSandbox
from .local import LocalSandbox


def create_configured_command_sandbox(
    workspace: Path,
    state_root: Path,
    config: SandboxConfig,
    *,
    max_output_bytes: int,
    known_secrets: Sequence[str] = (),
) -> CommandSandbox:
    """Build a sandbox entirely from validated composition and explicit bindings."""

    artifact_root = state_root / "artifacts" / "commands"
    if config.kind == "local":
        return LocalSandbox(
            workspace,
            artifact_root,
            known_secrets=known_secrets,
            max_memory_bytes=config.memory_bytes,
            max_processes=config.max_processes,
            max_file_bytes=config.max_file_bytes,
            max_output_bytes=max_output_bytes,
        )
    if config.kind == "docker":
        return DockerSandbox(
            workspace,
            artifact_root,
            image=config.image,
            known_secrets=known_secrets,
            allow_network=False,
            cpus=config.cpus,
            memory=config.memory,
            pids_limit=config.pids_limit,
            max_output_bytes=max_output_bytes,
        )
    raise ValueError(f"unsupported sandbox: {config.kind}")


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_command_sandbox(
    workspace: Path,
    state_root: Path,
    *,
    known_secrets: Sequence[str] = (),
) -> CommandSandbox:
    backend = os.getenv("SKEIN_SANDBOX", "local").strip().lower()
    artifact_root = state_root / "artifacts" / "commands"
    if backend == "local":
        return LocalSandbox(
            workspace,
            artifact_root,
            known_secrets=known_secrets,
            max_memory_bytes=int(
                os.getenv("SKEIN_LOCAL_MEMORY_BYTES", str(4 * 1024**3))
            ),
            max_processes=int(os.getenv("SKEIN_LOCAL_PIDS", "256")),
            max_file_bytes=int(
                os.getenv("SKEIN_LOCAL_FILE_BYTES", str(1024**3))
            ),
            max_output_bytes=int(os.getenv("SKEIN_TOOL_OUTPUT_BYTES", "16000")),
        )
    if backend == "docker":
        image = os.getenv("SKEIN_SANDBOX_IMAGE", "").strip()
        if not image:
            raise ValueError(
                "SKEIN_SANDBOX_IMAGE is required for the Docker sandbox"
            )
        return DockerSandbox(
            workspace,
            artifact_root,
            image=image,
            known_secrets=known_secrets,
            docker_binary=os.getenv("SKEIN_DOCKER_BINARY", "docker"),
            allow_network=_truthy("SKEIN_ALLOW_NETWORK"),
            cpus=float(os.getenv("SKEIN_SANDBOX_CPUS", "2.0")),
            memory=os.getenv("SKEIN_SANDBOX_MEMORY", "4g"),
            pids_limit=int(os.getenv("SKEIN_SANDBOX_PIDS", "256")),
            tmpfs_size=os.getenv("SKEIN_SANDBOX_TMPFS", "512m"),
            max_output_bytes=int(os.getenv("SKEIN_TOOL_OUTPUT_BYTES", "16000")),
        )
    raise ValueError(f"unsupported SKEIN_SANDBOX={backend!r}; use local or docker")


__all__ = ["create_command_sandbox", "create_configured_command_sandbox"]
