"""Secure ChatGPT subscription OAuth credentials for the Codex provider."""

from __future__ import annotations

import base64
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

try:  # pragma: no cover - Windows fallback is exercised by import, not CI
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTH_BASE_URL = "https://auth.openai.com"
CODEX_TOKEN_URL = f"{CODEX_AUTH_BASE_URL}/oauth/token"
CODEX_DEVICE_USER_CODE_URL = f"{CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/token"
CODEX_DEVICE_VERIFICATION_URL = f"{CODEX_AUTH_BASE_URL}/codex/device"
CODEX_DEVICE_REDIRECT_URI = f"{CODEX_AUTH_BASE_URL}/deviceauth/callback"
CODEX_JWT_CLAIM = "https://api.openai.com/auth"


class CodexAuthenticationError(RuntimeError):
    """A subscription credential is absent, invalid, or could not be refreshed."""


@dataclass(frozen=True, slots=True, repr=False)
class CodexCredential:
    access_token: str
    refresh_token: str
    expires_at_ms: int
    account_id: str

    def public_status(self, *, now_ms: int) -> dict[str, object]:
        return {
            "authenticated": True,
            "account_id_suffix": self.account_id[-6:],
            "expires_in_seconds": max(0, (self.expires_at_ms - now_ms) // 1000),
        }


@dataclass(frozen=True, slots=True)
class CodexDeviceAuthorization:
    device_auth_id: str
    user_code: str
    interval_seconds: float
    verification_url: str = CODEX_DEVICE_VERIFICATION_URL


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    pieces = token.split(".")
    if len(pieces) != 3:
        raise CodexAuthenticationError("OpenAI OAuth access token is not a JWT")
    encoded = pieces[1] + "=" * (-len(pieces[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodexAuthenticationError("OpenAI OAuth access token has an invalid JWT payload") from error
    if not isinstance(payload, dict):
        raise CodexAuthenticationError("OpenAI OAuth access token has an invalid JWT payload")
    return payload


def extract_codex_account_id(token: str) -> str:
    payload = _decode_jwt_payload(token)
    claim = payload.get(CODEX_JWT_CLAIM)
    account_id = claim.get("chatgpt_account_id") if isinstance(claim, dict) else None
    if not isinstance(account_id, str) or not account_id:
        raise CodexAuthenticationError("OpenAI OAuth token does not contain a ChatGPT account id")
    return account_id


class CodexCredentialStore:
    """Private, atomic credential persistence rooted in harness operational state."""

    def __init__(self, state_root: Path) -> None:
        self.root = state_root.expanduser().resolve() / "auth"
        self.path = self.root / "openai-codex.json"
        self.lock_path = self.root / "openai-codex.lock"

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    @contextmanager
    def locked(self) -> Iterator[None]:
        self._prepare_root()
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def load(self) -> CodexCredential | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CodexAuthenticationError("Codex credential path must be a regular file")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise CodexAuthenticationError(
                    "Codex credential file must not be accessible by group or others"
                )
            try:
                payload = json.load(stream)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise CodexAuthenticationError("Codex credential file is invalid") from error
        try:
            credential = CodexCredential(
                access_token=str(payload["access_token"]),
                refresh_token=str(payload["refresh_token"]),
                expires_at_ms=int(payload["expires_at_ms"]),
                account_id=str(payload["account_id"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CodexAuthenticationError("Codex credential file is missing required fields") from error
        if extract_codex_account_id(credential.access_token) != credential.account_id:
            raise CodexAuthenticationError("Codex credential account id does not match its token")
        return credential

    def save(self, credential: CodexCredential) -> None:
        self._prepare_root()
        payload = json.dumps(
            {
                "access_token": credential.access_token,
                "account_id": credential.account_id,
                "expires_at_ms": credential.expires_at_ms,
                "refresh_token": credential.refresh_token,
                "type": "oauth",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary = self.root / f".{self.path.name}.{os.getpid()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


class CodexOAuthClient:
    """Pi-compatible OpenAI Codex device flow and refresh operations."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None
        self._now = now
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def start_device_authorization(self) -> CodexDeviceAuthorization:
        response = self._client.post(
            CODEX_DEVICE_USER_CODE_URL,
            json={"client_id": CODEX_CLIENT_ID},
        )
        self._raise_for_status(response, "device-code request")
        payload = response.json()
        try:
            interval = float(payload["interval"])
            result = CodexDeviceAuthorization(
                device_auth_id=str(payload["device_auth_id"]),
                user_code=str(payload["user_code"]),
                interval_seconds=interval,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CodexAuthenticationError("OpenAI device-code response is invalid") from error
        if not result.device_auth_id or not result.user_code or interval < 0:
            raise CodexAuthenticationError("OpenAI device-code response is invalid")
        return result

    def complete_device_authorization(
        self,
        authorization: CodexDeviceAuthorization,
        *,
        timeout_seconds: float = 15 * 60,
        cancelled: threading.Event | None = None,
    ) -> CodexCredential:
        deadline = self._now() + timeout_seconds
        interval = max(1.0, authorization.interval_seconds)
        while self._now() < deadline:
            if cancelled is not None and cancelled.is_set():
                raise CodexAuthenticationError("OpenAI device authorization cancelled")
            response = self._client.post(
                CODEX_DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": authorization.device_auth_id,
                    "user_code": authorization.user_code,
                },
            )
            if cancelled is not None and cancelled.is_set():
                raise CodexAuthenticationError("OpenAI device authorization cancelled")
            if response.is_success:
                payload = response.json()
                try:
                    code = str(payload["authorization_code"])
                    verifier = str(payload["code_verifier"])
                except (KeyError, TypeError) as error:
                    raise CodexAuthenticationError("OpenAI device authorization is invalid") from error
                credential = self.exchange_authorization_code(code, verifier)
                if cancelled is not None and cancelled.is_set():
                    raise CodexAuthenticationError("OpenAI device authorization cancelled")
                return credential
            if response.status_code not in {403, 404}:
                error_code = self._error_code(response)
                if error_code == "slow_down":
                    interval += 5
                elif error_code != "deviceauth_authorization_pending":
                    self._raise_for_status(response, "device authorization")
            if cancelled is not None:
                cancelled.wait(interval)
            else:
                self._sleep(interval)
        raise CodexAuthenticationError("OpenAI device authorization timed out")

    def exchange_authorization_code(self, code: str, verifier: str) -> CodexCredential:
        response = self._client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CODEX_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
            },
        )
        return self._credential_from_response(response, "token exchange")

    def refresh(self, refresh_token: str) -> CodexCredential:
        response = self._client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_CLIENT_ID,
            },
        )
        return self._credential_from_response(response, "token refresh")

    def _credential_from_response(self, response: httpx.Response, operation: str) -> CodexCredential:
        self._raise_for_status(response, operation)
        payload = response.json()
        try:
            access = str(payload["access_token"])
            refresh = str(payload["refresh_token"])
            expires_in = float(payload["expires_in"])
        except (KeyError, TypeError, ValueError) as error:
            raise CodexAuthenticationError(f"OpenAI {operation} response is invalid") from error
        return CodexCredential(
            access_token=access,
            refresh_token=refresh,
            expires_at_ms=int((self._now() + expires_in) * 1000),
            account_id=extract_codex_account_id(access),
        )

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        try:
            error = response.json().get("error")
        except (ValueError, AttributeError):
            return None
        if isinstance(error, str):
            return error
        return str(error.get("code")) if isinstance(error, dict) and error.get("code") else None

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        message = " ".join(response.text.split())[:500]
        raise CodexAuthenticationError(
            f"OpenAI Codex {operation} failed ({response.status_code})"
            + (f": {message}" if message else "")
        )


class CodexCredentialManager:
    """Resolve a valid access token with cross-process serialized refresh."""

    def __init__(
        self,
        store: CodexCredentialStore,
        *,
        oauth: CodexOAuthClient | None = None,
        now: Callable[[], float] = time.time,
        refresh_margin_seconds: int = 60,
    ) -> None:
        self.store = store
        self.oauth = oauth or CodexOAuthClient(now=now)
        self._now = now
        self._refresh_margin_ms = refresh_margin_seconds * 1000

    def resolve(self, *, force_refresh: bool = False) -> CodexCredential:
        with self.store.locked():
            credential = self.store.load()
            if credential is None:
                raise CodexAuthenticationError(
                    "OpenAI Codex is not authenticated; run `skein codex login`"
                )
            now_ms = int(self._now() * 1000)
            if force_refresh or credential.expires_at_ms <= now_ms + self._refresh_margin_ms:
                credential = self.oauth.refresh(credential.refresh_token)
                self.store.save(credential)
            return credential


__all__ = [
    "CODEX_DEVICE_VERIFICATION_URL",
    "CodexAuthenticationError",
    "CodexCredential",
    "CodexCredentialManager",
    "CodexCredentialStore",
    "CodexDeviceAuthorization",
    "CodexOAuthClient",
    "extract_codex_account_id",
]
