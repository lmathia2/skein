"""Tool and command-policy contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import StrictModel


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


class CommandClass(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_MUTATION = "workspace_mutation"
    BUILD_OR_TEST = "build_or_test"
    DEPENDENCY_INSTALL = "dependency_install"
    NETWORK_ACCESS = "network_access"
    GIT_HISTORY_MUTATION = "git_history_mutation"
    PUBLISH_OR_DEPLOY = "publish_or_deploy"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


class ToolEnvelope(StrictModel):
    status: ToolStatus
    model_text: str
    ui_details: dict[str, object] = Field(default_factory=dict)
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    omitted_bytes: int = Field(default=0, ge=0)
    artifact_uri: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    content_hashes: dict[str, str] = Field(default_factory=dict)
    command_class: CommandClass | None = None
    data: dict[str, object] = Field(default_factory=dict)


class CommandResult(StrictModel):
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    timed_out: bool = False
