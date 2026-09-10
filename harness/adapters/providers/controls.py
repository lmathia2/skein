"""Local provider management, independent of terminal and WebSocket framing."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codex_auth import CodexAuthenticationError, CodexCredentialStore, CodexOAuthClient


class ProviderControlError(ValueError):
    """An allowlisted operator-facing error, never a provider response body."""


class ProviderControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=256)
    operation: Literal["status", "login", "cancel_login", "logout"]
    provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    login_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_operation(self) -> ProviderControlRequest:
        if self.operation != "status" and self.provider is None:
            raise ValueError("provider is required")
        if self.operation == "cancel_login" and self.login_id is None:
            raise ValueError("login_id is required")
        if self.login_id is not None and (self.provider is None or self.operation not in {"status", "cancel_login"}):
            raise ValueError("login_id is not valid for this operation")
        return self


@dataclass(slots=True)
class _Login:
    request_id: str
    login_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "starting"
    verification_url: str | None = None
    user_code: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    commit_lock: threading.Lock = field(default_factory=threading.Lock)
    task: asyncio.Task[None] | None = None

    def cancel(self) -> None:
        with self.commit_lock:
            self.cancelled.set()
            if self.status in {"starting", "waiting"}:
                self.status = "cancelled"

    def public(self) -> dict[str, object]:
        result: dict[str, object] = {"login_id": self.login_id, "status": self.status}
        if self.status == "waiting":
            result.update(verification_url=self.verification_url, user_code=self.user_code)
        if self.status == "failed":
            result["error"] = "Login failed. Check connectivity and that device-code login is enabled for your account, then retry."
        return result


class LocalProviderControls:
    """Single local operator; credentials and OAuth workers stay on the server."""

    def __init__(self, state_root: Path, *, owner_id: str = "local-user",
                 oauth_factory: Callable[[], CodexOAuthClient] = CodexOAuthClient) -> None:
        self.store = CodexCredentialStore(state_root)
        self.owner_id = owner_id
        self.oauth_factory = oauth_factory
        self._attempts: dict[str, _Login] = {}
        self._closed = False
        self._mutation_lock = asyncio.Lock()
        self._receipts: dict[str, tuple[ProviderControlRequest, dict[str, object]]] = {}

    def _status(self) -> dict[str, object]:
        status: dict[str, object] = {"provider": "openai_codex", "display_name": "Codex subscription",
            "supports_login": True, "credential_path": str(self.store.path), "authenticated": False}
        try:
            credential = self.store.load()
        except (CodexAuthenticationError, OSError):
            status["error"] = "Saved credentials are unreadable or invalid. Sign in again, or use /logout to remove them."
        else:
            if credential is not None:
                status.update(credential.public_status(now_ms=int(time.time() * 1000)))
        return status

    async def request(self, command: ProviderControlRequest, *, user_id: str) -> dict[str, object]:
        if user_id != self.owner_id:
            raise PermissionError("provider controls belong to the local server operator")
        if self._closed:
            raise ProviderControlError("provider controls are shutting down")
        if command.provider not in {None, "openai_codex"}:
            raise ProviderControlError("provider does not support interactive login")
        if command.operation == "status":
            login = None
            if command.login_id is not None:
                attempt = self._attempts.get(command.login_id)
                if attempt is None:
                    raise ProviderControlError("login attempt expired; start a new login")
                login = attempt.public()
            else:
                active = next((attempt for attempt in self._attempts.values()
                               if attempt.status in {"starting", "waiting"}), None)
                if active is not None:
                    login = active.public()
            status = await asyncio.to_thread(self._status)
            if login is not None:
                status["login"] = login
            return {"providers": [status]} if command.provider is None else status
        async with self._mutation_lock:
            if self._closed:
                raise ProviderControlError("provider controls are shutting down")
            receipt = self._receipts.get(command.request_id)
            if receipt is not None:
                if receipt[0] != command:
                    raise ProviderControlError("provider request_id was reused with different content")
                return receipt[1]
            result = await self._mutate(command)
            self._receipts[command.request_id] = (command, result)
            # Receipts are process-local and bounded. Clients must reconcile
            # status, not automatically replay auth mutations after disconnect.
            if len(self._receipts) > 128:
                del self._receipts[next(iter(self._receipts))]
            return result

    async def _mutate(self, command: ProviderControlRequest) -> dict[str, object]:
        if command.operation == "login":
            for attempt in self._attempts.values():
                if attempt.task is not None and not attempt.task.done():
                    if attempt.cancelled.is_set():
                        raise ProviderControlError("previous login is stopping; try again shortly")
                    return {"provider": "openai_codex", "login": attempt.public()}
            attempt = _Login(command.request_id)
            self._attempts[attempt.login_id] = attempt
            while len(self._attempts) > 32:
                del self._attempts[next(iter(self._attempts))]
            attempt.task = asyncio.create_task(self._login(attempt), name="provider-login")
            return {"provider": "openai_codex", "login": attempt.public()}
        if command.operation == "cancel_login":
            attempt = self._attempts.get(command.login_id or "")
            if attempt is None:
                raise ProviderControlError("login attempt not found")
            attempt.cancel()
            return {"provider": "openai_codex", "login": attempt.public()}
        for attempt in self._attempts.values():
            attempt.cancel()
        def remove() -> bool:
            with self.store.locked():
                return self.store.delete()
        removed = await asyncio.to_thread(remove)
        return {"provider": "openai_codex", "authenticated": False, "removed": removed}

    async def _login(self, attempt: _Login) -> None:
        oauth = None
        try:
            oauth = await asyncio.to_thread(self.oauth_factory)
            if attempt.cancelled.is_set():
                return
            authorization = await asyncio.to_thread(oauth.start_device_authorization)
            if attempt.cancelled.is_set():
                return
            attempt.verification_url = authorization.verification_url
            attempt.user_code = authorization.user_code
            attempt.status = "waiting"
            credential = await asyncio.to_thread(oauth.complete_device_authorization,
                authorization, cancelled=attempt.cancelled)
            def save() -> None:
                with self.store.locked(), attempt.commit_lock:
                    if not attempt.cancelled.is_set():
                        self.store.save(credential)
                        attempt.status = "authenticated"
            await asyncio.to_thread(save)
        except asyncio.CancelledError:
            attempt.cancel()
            raise
        except Exception:
            # Provider response bodies can contain credentials. Do not copy them
            # into the public protocol, diagnostic transcript, or exception logs.
            if not attempt.cancelled.is_set():
                attempt.status = "failed"
        finally:
            if oauth is not None:
                # Cleanup must not leak provider exceptions or undo login.
                with suppress(Exception):
                    await asyncio.to_thread(oauth.close)

    async def aclose(self) -> None:
        self._closed = True
        tasks = []
        for attempt in self._attempts.values():
            attempt.cancel()
            if attempt.task is not None:
                tasks.append(attempt.task)
        await asyncio.gather(*tasks, return_exceptions=True)
