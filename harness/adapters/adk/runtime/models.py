"""Server-owned model preferences; no model execution or terminal dependencies."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from harness.adapters.providers.codex_auth import CodexCredentialStore
from harness.adapters.providers.selection import (
    ModelChoice,
    load_model_default,
    model_default_path,
    save_model_default,
)
from harness.core.agent import ModelReadiness, PublicModelStatus
from harness.core.config import HarnessComposition

from .protocol import ModelRequestMessage

if TYPE_CHECKING:
    from .runtime import AdkRunExecutionFactory
    from .sessions import ConversationStore

MODEL_METADATA = "coding.model_choice"


class ModelControlError(ValueError):
    """Safe user-facing error, never a raw provider exception."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    choice: ModelChoice
    display_name: str = Field(min_length=1, max_length=256)


class ModelControls:
    def __init__(self, factory: AdkRunExecutionFactory, store: ConversationStore,
                 *, catalog: Callable[[], Sequence[CatalogModel]] | None = None,
                 owner_id: str = "local-user") -> None:
        self.factory = factory
        self.store = store
        self.owner_id = owner_id
        self.adapter = factory.registry.model_configuration(factory.descriptor.implementation)
        if self.adapter is None:
            raise ValueError("harness does not expose model configuration")
        self.base = self.adapter.coding_model(factory.composition.harness.config)
        self.state_root = factory.bindings.auth_state_root or factory.bindings.state_root
        self.binding = json.dumps({key: factory.run_metadata.get(key) for key in (
            "coding.workspace_identity", "coding.harness_implementation")}, sort_keys=True)
        self._use_saved_default = factory.composition.server.use_saved_model_default
        self._default_warning: str | None = None
        self._catalog = catalog or self._discover
        self._models: tuple[CatalogModel, ...] = ()
        self._catalog_error: str | None = None
        self._catalog_at = 0.0
        self._refresh: asyncio.Task[None] | None = None
        self._auth_key: tuple[int, int] | None = None
        with self.store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversation_models (user_id TEXT, thread_id TEXT, binding TEXT, choice TEXT, PRIMARY KEY(user_id, thread_id, binding))")
            db.execute("CREATE TABLE IF NOT EXISTS model_control_receipts (user_id TEXT, request_id TEXT, request TEXT, result TEXT, PRIMARY KEY(user_id, request_id))")

    def composition(self, choice: ModelChoice) -> HarnessComposition:
        assert self.adapter is not None
        config = self.adapter.with_coding_model(self.factory.composition.harness.config, choice.apply(self.base))
        return self.factory.composition.model_copy(update={"harness": self.factory.composition.harness.model_copy(update={"config": config})})

    def _owned(self, user_id: str, thread_id: str) -> None:
        try:
            record = self.store.latest(user_id, thread_id)
        except KeyError:
            return  # A new conversation can select a model before its first turn.
        identity = json.dumps({key: record.metadata.get(key) for key in (
            "coding.workspace_identity", "coding.harness_implementation")}, sort_keys=True)
        if identity != self.binding:
            raise ModelControlError("conversation belongs to a different workspace or harness")

    def default(self) -> ModelChoice:
        saved = load_model_default(self.state_root) if self._use_saved_default else None
        choice = saved or ModelChoice.from_config(self.base)
        self._default_warning = None
        try:
            self.composition(choice)
        except (ValueError, TypeError):
            self._default_warning = "Saved default is unsupported by this harness; using its configured model. Select a supported model with /model."
            choice = ModelChoice.from_config(self.base)
        return choice

    def current(self, user_id: str, thread_id: str) -> ModelChoice:
        self._owned(user_id, thread_id)
        with self.store._connect() as db:
            row = db.execute("SELECT choice FROM conversation_models WHERE user_id=? AND thread_id=? AND binding=?", (user_id, thread_id, self.binding)).fetchone()
        if row is not None:
            return ModelChoice.model_validate_json(row["choice"])
        try:
            previous = self.store.latest(user_id, thread_id).metadata.get(MODEL_METADATA)
        except KeyError:
            previous = None
        return ModelChoice.model_validate_json(previous) if previous else self.default()

    def status(self, user_id: str, thread_id: str) -> dict[str, object]:
        current = self.current(user_id, thread_id)
        return {"thread_id": thread_id, "selected": current.model_dump(),
                "default": self.default().model_dump(), "saved_default": (saved.model_dump() if (saved := load_model_default(self.state_root)) else None),
                "default_path": str(model_default_path(self.state_root)), "effective": "next_turn",
                "coding_model": self.public_model(current).model_dump(mode="json"), "warning": self._default_warning}

    def public_model(self, choice: ModelChoice) -> PublicModelStatus:
        readiness = ModelReadiness.CONFIGURED
        if choice.provider == "openai_codex":
            try:
                credential = CodexCredentialStore(self.state_root).load()
            except Exception:
                credential = None
            if credential is None:
                readiness = ModelReadiness.AUTHENTICATION_REQUIRED
        return PublicModelStatus(provider=choice.provider, name=choice.name, readiness=readiness)

    def run_metadata(self, user_id: str, thread_id: str) -> dict[str, str]:
        choice = self.current(user_id, thread_id)
        composition = self.composition(choice)
        return {MODEL_METADATA: choice.model_dump_json(), "coding.behavior_sha256": composition.behavior_sha256,
                "coding.composition_sha256": composition.composition_sha256}

    def _discover(self) -> Sequence[CatalogModel]:
        from harness.adapters.providers.codex import credential_manager, discover_codex_models
        if CodexCredentialStore(self.state_root).load() is None:
            return ()
        manager = credential_manager(self.state_root)
        try:
            return tuple(CatalogModel(choice=ModelChoice(provider="openai_codex", name=model.id,
                reasoning=self.base.reasoning if self.base.provider == "openai_codex" else "low",
                client_version=model.client_version), display_name=model.display_name)
                for model in discover_codex_models(manager))
        finally:
            manager.oauth.close()

    async def _refresh_catalog(self) -> None:
        try:
            models = await asyncio.to_thread(self._catalog)
            accepted = []
            for model in models[:256]:
                try:
                    self.composition(model.choice)
                except (ValueError, TypeError):
                    continue
                accepted.append(model)
            self._models = tuple(accepted)
            self._catalog_error = None
        except Exception:
            self._models = ()
            self._catalog_error = "Model catalog unavailable. Check /auth and connectivity, then reopen /model."
        finally:
            self._catalog_at = time.monotonic()

    def catalog(self, user_id: str, thread_id: str) -> dict[str, object]:
        result = self.status(user_id, thread_id)
        try:
            stat = CodexCredentialStore(self.state_root).path.stat()
            auth_key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            auth_key = None
        if auth_key != self._auth_key:
            self._models = ()
            self._catalog_at = 0
        if self._refresh is None or (self._refresh.done() and time.monotonic() - self._catalog_at > 30):
            self._auth_key = auth_key
            self._refresh = asyncio.create_task(self._refresh_catalog(), name="model-catalog")
        models = {(model.choice.provider, model.choice.name): model for model in self._models}
        current = self.current(user_id, thread_id)
        for choice in (ModelChoice.from_config(self.base), self.default(), current):
            models.setdefault((choice.provider, choice.name), CatalogModel(choice=choice, display_name=choice.name))
        result.update(models=[model.model_dump() for model in models.values()],
                      refreshing=self._refresh is not None and not self._refresh.done(), error=self._catalog_error)
        return result

    async def request(self, message: ModelRequestMessage, *, user_id: str) -> dict[str, object]:
        if message.operation == "status":
            return self.status(user_id, message.thread_id)
        if message.operation == "catalog":
            return self.catalog(user_id, message.thread_id)
        if message.persist and user_id != self.owner_id:
            raise ModelControlError("only the local server owner can change the shared model default")
        self._owned(user_id, message.thread_id)
        request = json.dumps({"binding": self.binding, "message": message.model_dump()}, sort_keys=True)
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT request, result FROM model_control_receipts WHERE user_id=? AND request_id=?", (user_id, message.request_id)).fetchone()
            if prior is not None:
                if prior["request"] != request:
                    raise ModelControlError("model request_id was reused with different content")
                return json.loads(prior["result"])
            choices = [item.choice for item in self._models] + [ModelChoice.from_config(self.base), self.default(), self.current(user_id, message.thread_id)]
            choice = next((item for item in choices if item.provider == message.provider and item.name == message.name), None)
            if choice is None:
                raise ModelControlError("model is not in the current catalog; reopen /model")
            self.composition(choice)
            if message.persist:
                save_model_default(self.state_root, choice)
            db.execute("INSERT OR REPLACE INTO conversation_models VALUES (?, ?, ?, ?)", (user_id, message.thread_id, self.binding, choice.model_dump_json()))
            result = {"thread_id": message.thread_id, "selected": choice.model_dump(), "saved_default": message.persist,
                      "default_path": str(model_default_path(self.state_root)), "effective": "next_turn",
                      "coding_model": self.public_model(choice).model_dump(mode="json")}
            db.execute("INSERT INTO model_control_receipts VALUES (?, ?, ?, ?)", (user_id, message.request_id, request, json.dumps(result)))
        if message.persist:
            self._use_saved_default = True
        return result

    async def aclose(self) -> None:
        if self._refresh is not None:
            await self._refresh
