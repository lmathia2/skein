"""Local ADK service construction for the pinned ADK release.

No signature guessing, cloud fallbacks, or parallel environment configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from harness.core.config.models import PersistenceConfig

from .observed_session import ObservedSessionService, SessionSink


class SessionBackend(StrEnum):
    IN_MEMORY = "in_memory"
    SQLITE = "sqlite"


class ArtifactBackend(StrEnum):
    IN_MEMORY = "in_memory"
    FILE = "file"


class PersistenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_backend: SessionBackend = SessionBackend.IN_MEMORY
    sqlite_path: Path | None = None
    artifact_backend: ArtifactBackend = ArtifactBackend.IN_MEMORY
    artifact_root: Path | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> PersistenceSettings:
        if self.session_backend == SessionBackend.SQLITE and self.sqlite_path is None:
            raise ValueError("sqlite_path is required for SQLite sessions")
        if self.artifact_backend == ArtifactBackend.FILE and self.artifact_root is None:
            raise ValueError("artifact_root is required for file artifacts")
        return self


@dataclass(frozen=True, slots=True)
class AdkServiceBundle:
    session_service: Any
    artifact_service: Any
    memory_service: Any


def build_session_service(settings: PersistenceSettings) -> Any:
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.sqlite_session_service import SqliteSessionService

    if settings.session_backend == SessionBackend.IN_MEMORY:
        return InMemorySessionService()
    assert settings.sqlite_path is not None
    path = settings.sqlite_path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSessionService(db_path=str(path))


def build_artifact_service(settings: PersistenceSettings) -> Any:
    from google.adk.artifacts import FileArtifactService, InMemoryArtifactService

    if settings.artifact_backend == ArtifactBackend.IN_MEMORY:
        return InMemoryArtifactService()
    assert settings.artifact_root is not None
    return FileArtifactService(root_dir=settings.artifact_root.expanduser().resolve())


def build_memory_service(settings: PersistenceSettings) -> Any:
    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()


def build_service_bundle(
    settings: PersistenceSettings,
    *,
    session_sink: SessionSink | None = None,
) -> AdkServiceBundle:
    session_service = ObservedSessionService(
        build_session_service(settings), session_sink
    )
    return AdkServiceBundle(
        session_service=session_service,
        artifact_service=build_artifact_service(settings),
        memory_service=build_memory_service(settings),
    )


def settings_from_composition(
    config: PersistenceConfig,
    *,
    state_root: Path,
) -> PersistenceSettings:
    root = state_root.expanduser().resolve() / "adk"
    return PersistenceSettings(
        session_backend=SessionBackend(config.session_backend),
        sqlite_path=root / "sessions.db" if config.session_backend == "sqlite" else None,
        artifact_backend=ArtifactBackend(config.artifact_backend),
        artifact_root=root / "artifacts" if config.artifact_backend == "file" else None,
    )


def local_durable_settings(state_root: Path) -> PersistenceSettings:
    return settings_from_composition(
        PersistenceConfig(session_backend="sqlite", artifact_backend="file"),
        state_root=state_root,
    )
