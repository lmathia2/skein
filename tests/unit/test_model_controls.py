"""Model selections drive actual ADK invocations, not only UI labels."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr, ValidationError

from app.agent.factory import default_harness_registry
from harness.adapters.adk.persistence import build_service_bundle, settings_from_composition
from harness.adapters.adk.runtime.models import MODEL_METADATA, CatalogModel, ModelControlError
from harness.adapters.adk.runtime.protocol import ModelRequestMessage, StartTaskMessage
from harness.adapters.adk.runtime.registry import RunEventBroker, SqliteRunEventStore
from harness.adapters.adk.runtime.runtime import AdkRunExecutionFactory, RunCoordinator
from harness.adapters.providers import ClosedAdkModelProviderRegistry
from harness.adapters.providers.codex import (
    CodexSelection,
    load_codex_selection,
    prepare_codex_config,
    save_codex_selection,
)
from harness.adapters.providers.selection import (
    ModelChoice,
    load_model_default,
    model_default_path,
    save_model_default,
)
from harness.core.config import RuntimeBindings, load_harness_composition


class ScriptedModel(BaseLlm):
    _provider: ScriptedProvider = PrivateAttr()

    async def generate_content_async(self, llm_request, stream=False):
        self._provider.invoked.append(self.model)
        self._provider.entered.set()
        await self._provider.release.wait()
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps({"status": "answer", "message": "Fixture reply"}))]))


class ScriptedProvider:
    provider_id = "scripted"

    def __init__(self) -> None:
        self.invoked: list[str] = []
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    def build_model(self, config, *, secrets, bindings=None):
        model = ScriptedModel(model=config.name)
        model._provider = self
        return model


def coordinator(tmp_path: Path, *, use_saved: bool = False) -> tuple[RunCoordinator, ScriptedProvider]:
    provider = ScriptedProvider()
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((provider,)))
    composition = load_harness_composition(config_models=registry.config_models())
    config = composition.harness.config
    config = config.model_copy(update={"models": {"coding": config.models["coding"].model_copy(update={"provider": "scripted", "name": "alpha"})}})
    composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config}),
        "server": composition.server.model_copy(update={"use_saved_model_default": use_saved}),
        "persistence": composition.persistence.model_copy(update={"session_backend": "in_memory", "artifact_backend": "in_memory"})})
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    services = build_service_bundle(settings_from_composition(composition.persistence, state_root=state))
    result = RunCoordinator(store=SqliteRunEventStore(state / "server/runs.db"), broker=RunEventBroker(),
        execution_factory=AdkRunExecutionFactory(composition=composition,
            bindings=RuntimeBindings(workspace=workspace, state_root=state), registry=registry, services=services))
    assert result.models is not None
    result.models.owner_id = "user"
    result.models._catalog = lambda: tuple(CatalogModel(choice=ModelChoice(provider="scripted", name=name), display_name=name.title()) for name in ("alpha", "beta", "gamma"))
    return result, provider


def request(operation: str = "select", *, request_id: str = "select-1", thread: str = "conversation", **fields) -> ModelRequestMessage:
    return ModelRequestMessage.model_validate({"type": "model.request", "operation": operation, "request_id": request_id, "thread_id": thread, **fields})


async def catalog(control: RunCoordinator) -> None:
    first = await control.model_request(request("catalog"), user_id="user")
    assert first["refreshing"]
    await control.models._refresh


def start(id: str, thread: str = "conversation", **fields) -> StartTaskMessage:
    return StartTaskMessage(type="task.start", request_id=id, idempotency_key=id, thread_id=thread, input="fixture", **fields)


async def finished(control: RunCoordinator, run_id: str) -> None:
    async with asyncio.timeout(10):
        while control.store.get_run(run_id).status in {"queued", "running"}:
            await asyncio.sleep(0.005)
    assert control.store.get_run(run_id).status == "completed"


@pytest.mark.asyncio
async def test_selection_changes_next_real_adk_invocation_not_active_run_or_retry(tmp_path: Path) -> None:
    control, provider = coordinator(tmp_path)
    try:
        await catalog(control)
        first, _ = await control.start(start("first"), user_id="user")
        await asyncio.wait_for(provider.entered.wait(), 5)
        selection = request(provider="scripted", name="beta")
        result = await control.model_request(selection, user_id="user")
        assert result["effective"] == "next_turn"
        assert provider.invoked == ["alpha"]
        retry, created = await control.start(start("first"), user_id="user")
        assert not created and retry.metadata == first.metadata
        assert ModelChoice.model_validate_json(first.metadata[MODEL_METADATA]).name == "alpha"
        provider.release.set()
        await finished(control, first.run_id)
        second, _ = await control.start(start("second"), user_id="user")
        await finished(control, second.run_id)
        assert provider.invoked == ["alpha", "beta"]
        assert ModelChoice.model_validate_json(second.metadata[MODEL_METADATA]).name == "beta"
        assert second.metadata["coding.behavior_sha256"] != first.metadata["coding.behavior_sha256"]
        assert control.execution_factory.composition.harness.config.models["coding"].name == "alpha"
        assert load_model_default(tmp_path / "state") is None
        other, _ = await control.start(start("other", "other"), user_id="user")
        await finished(control, other.run_id)
        assert provider.invoked[-1] == "alpha"
        with pytest.raises(ValueError, match="reserved"):
            await control.start(start("forged", metadata={MODEL_METADATA: '{"provider":"evil"}'}), user_id="user")
    finally:
        provider.release.set()
        await control.aclose()


@pytest.mark.asyncio
async def test_defaults_persist_and_retries_do_not_revert_newer_choices(tmp_path: Path) -> None:
    control, _ = coordinator(tmp_path)
    try:
        await catalog(control)
        selection = request(provider="scripted", name="beta", persist=True)
        first = await control.model_request(selection, user_id="user")
        await control.model_request(request(request_id="newer", provider="scripted", name="gamma", persist=True), user_id="user")
        assert first == await control.model_request(selection, user_id="user")
        assert control.models.current("user", "conversation").name == "gamma"
        assert control.models.current("user", "new-conversation").name == "gamma"
        assert load_model_default(tmp_path / "state").name == "gamma"
        with pytest.raises(ModelControlError, match="different content"):
            await control.model_request(request(provider="scripted", name="alpha"), user_id="user")
        with pytest.raises(ModelControlError, match="local server owner"):
            await control.model_request(request(provider="scripted", name="alpha", persist=True), user_id="other-user")
    finally:
        await control.aclose()
    configured, _ = coordinator(tmp_path)
    saved, _ = coordinator(tmp_path, use_saved=True)
    try:
        assert configured.models.current("user", "conversation").name == "gamma"
        assert configured.models.current("other-user", "conversation").name == "alpha"
        assert configured.models.current("user", "new").name == "alpha"
        assert saved.models.current("user", "new").name == "gamma"
        assert first == await saved.model_request(selection, user_id="user")
        assert saved.models.current("user", "new").name == "gamma"
    finally:
        await configured.aclose()
        await saved.aclose()


@pytest.mark.asyncio
async def test_slow_or_failed_catalog_never_blocks_status_or_leaks_errors(tmp_path: Path) -> None:
    control, _ = coordinator(tmp_path)
    release = threading.Event()
    def slow():
        assert release.wait(5)
        raise RuntimeError("Bearer private-catalog-token")
    control.models._catalog = slow
    try:
        listing = await control.model_request(request("catalog"), user_id="user")
        assert listing["refreshing"] is True
        status = await asyncio.wait_for(control.model_request(request("status"), user_id="user"), 1)
        assert status["selected"]["name"] == "alpha"
        release.set()
        await control.models._refresh
        listing = await control.model_request(request("catalog"), user_id="user")
        assert listing["refreshing"] is False
        assert "unavailable" in listing["error"]
        assert "private-catalog-token" not in json.dumps(listing)
        with pytest.raises(ModelControlError, match="not in the current catalog"):
            await control.model_request(request(provider="scripted", name="invented"), user_id="user")
    finally:
        release.set()
        await control.aclose()


def test_default_store_is_private_stable_and_migrates_legacy_selection(tmp_path: Path) -> None:
    legacy = tmp_path / "auth/openai-codex-selection.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"model": "fixture-old", "reasoning": "low"}))
    assert load_codex_selection(tmp_path).model == "fixture-old"
    choice = ModelChoice(provider="openai_codex", name="fixture-new", reasoning="low")
    path = save_model_default(tmp_path, choice)
    before = path.read_bytes()
    assert save_codex_selection(tmp_path, CodexSelection("fixture-new", "low", None)) == path
    assert path == model_default_path(tmp_path)
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o077 == 0
    assert load_codex_selection(tmp_path).model == "fixture-new"
    configured, _ = prepare_codex_config(tmp_path, model="explicit")
    assert load_harness_composition(configured).server.use_saved_model_default is False
    configured, _ = prepare_codex_config(tmp_path)
    assert load_harness_composition(configured).server.use_saved_model_default is True
    with pytest.raises(ValidationError):
        ModelChoice(provider="openai_codex", name="fixture", api_key="forbidden")
    with pytest.raises(ValidationError):
        ModelChoice(provider="openai_codex", name="fixture\x1b[31m")


@pytest.mark.asyncio
async def test_incompatible_saved_default_remains_recoverable_and_workspace_retry_is_rejected(tmp_path: Path) -> None:
    control, provider = coordinator(tmp_path, use_saved=True)
    try:
        save_model_default(tmp_path / "state", ModelChoice(provider="unsupported", name="old"))
        status = await control.model_request(request("status"), user_id="user")
        assert status["selected"]["name"] == "alpha"
        assert "unsupported" in status["warning"]
        provider.release.set()
        first, _ = await control.start(start("first"), user_id="user")
        await finished(control, first.run_id)
        changed_workspace = tmp_path / "different-workspace"
        changed_workspace.mkdir()
        control.execution_factory.bindings = control.execution_factory.bindings.model_copy(update={"workspace": changed_workspace})
        with pytest.raises(ValueError, match="different workspace"):
            await control.start(start("first"), user_id="user")
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_failed_default_write_does_not_change_conversation_choice(tmp_path: Path, monkeypatch) -> None:
    control, _ = coordinator(tmp_path)
    def fail(*_args):
        raise OSError("disk full")
    try:
        await catalog(control)
        monkeypatch.setattr("harness.adapters.adk.runtime.models.save_model_default", fail)
        with pytest.raises(OSError):
            await control.model_request(request(provider="scripted", name="beta", persist=True), user_id="user")
        assert control.models.current("user", "conversation").name == "alpha"
    finally:
        await control.aclose()
