from __future__ import annotations

from pathlib import Path

import pytest
from google.adk.errors import StaleSessionError
from google.adk.events import Event
from google.genai import types
from pydantic import ValidationError

from harness.adapters.adk.persistence.adk_services import (
    ArtifactBackend,
    PersistenceSettings,
    SessionBackend,
    build_artifact_service,
    build_service_bundle,
    build_session_service,
    local_durable_settings,
    settings_from_composition,
)
from harness.adapters.adk.persistence.observed_session import ObservedSessionService
from harness.core.config import load_harness_composition
from harness.core.config.models import PersistenceConfig
from harness.evidence.ledger import DuckDbLedgerStore
from harness.evidence.ledger.importers import import_session_record


def test_bundled_composition_selects_local_durable_backends() -> None:
    persistence = load_harness_composition().persistence

    assert persistence.session_backend == "sqlite"
    assert persistence.artifact_backend == "file"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"session_backend": SessionBackend.SQLITE}, "sqlite_path"),
        ({"artifact_backend": ArtifactBackend.FILE}, "artifact_root"),
    ],
)
def test_selected_backends_require_their_settings(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        PersistenceSettings.model_validate(kwargs)


def test_local_durable_settings_are_scoped_to_resolved_state_root(tmp_path: Path) -> None:
    settings = local_durable_settings(tmp_path / "state" / ".." / "state")

    assert settings.session_backend == SessionBackend.SQLITE
    assert settings.sqlite_path == (tmp_path / "state" / "adk" / "sessions.db").resolve()
    assert settings.artifact_backend == ArtifactBackend.FILE
    assert settings.artifact_root == (tmp_path / "state" / "adk" / "artifacts").resolve()


def test_composition_resolves_local_paths(tmp_path: Path) -> None:
    assert settings_from_composition(
        PersistenceConfig(session_backend="sqlite", artifact_backend="file"),
        state_root=tmp_path,
    ) == local_durable_settings(tmp_path)


@pytest.mark.parametrize("backend", ["database", "vertex"])
def test_removed_cloud_sessions_fail_closed(backend: str) -> None:
    with pytest.raises(ValidationError):
        PersistenceConfig(session_backend=backend)


@pytest.mark.asyncio
async def test_local_adk_services_survive_reconstruction(tmp_path: Path) -> None:
    settings = local_durable_settings(tmp_path)
    first_sessions = build_session_service(settings)
    first_artifacts = build_artifact_service(settings)

    await first_sessions.create_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        state={"phase": "coding"},
    )
    version = await first_artifacts.save_artifact(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        filename="result.txt",
        artifact=types.Part.from_text(text="durable"),
    )

    second_sessions = build_session_service(settings)
    second_artifacts = build_artifact_service(settings)
    session = await second_sessions.get_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
    )
    artifact = await second_artifacts.load_artifact(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
        filename="result.txt",
        version=version,
    )

    assert session is not None
    assert session.state["phase"] == "coding"
    assert artifact is not None
    assert artifact.text == "durable"


@pytest.mark.asyncio
async def test_shallow_session_copy_can_append_after_runner_write(tmp_path: Path) -> None:
    service = build_service_bundle(local_durable_settings(tmp_path)).session_service
    assert isinstance(service, ObservedSessionService)
    session = await service.create_session(
        app_name="coding_harness", user_id="user", session_id="session"
    )
    worker_session = session.model_copy(deep=False)
    independent_session = await service.get_session(
        app_name="coding_harness", user_id="user", session_id="session"
    )
    assert independent_session is not None

    await service.append_event(
        session, Event(author="worker", timestamp=session.last_update_time + 1)
    )
    await service.append_event(
        worker_session,
        Event(author="compactor", timestamp=session.last_update_time + 1),
    )

    with pytest.raises(StaleSessionError):
        await service.append_event(
            independent_session,
            Event(author="stale", timestamp=session.last_update_time + 1),
        )

    stored = await service.get_session(
        app_name="coding_harness", user_id="user", session_id="session"
    )
    assert stored is not None
    assert [event.author for event in stored.events] == ["worker", "compactor"]


@pytest.mark.asyncio
async def test_session_lifecycle_is_captured_in_canonical_ledger(tmp_path: Path) -> None:
    ledger = DuckDbLedgerStore(tmp_path / "ledger.duckdb")
    services = build_service_bundle(
        PersistenceSettings(),
        session_sink=lambda session_id, kind, payload: import_session_record(
            ledger, session_id, kind, payload
        ),
    )
    await services.session_service.create_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
    )
    await services.session_service.delete_session(
        app_name="coding_harness",
        user_id="user",
        session_id="session",
    )
    assert [event.kind for event in ledger.read("session")] == [
        "adk.session.created",
        "adk.session.deleted",
    ]
