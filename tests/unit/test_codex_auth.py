from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import httpx
import pytest

from harness.adapters.providers.codex_auth import (
    CODEX_DEVICE_TOKEN_URL,
    CODEX_DEVICE_USER_CODE_URL,
    CODEX_TOKEN_URL,
    CodexAuthenticationError,
    CodexCredential,
    CodexCredentialManager,
    CodexCredentialStore,
    CodexOAuthClient,
)


def _token(account_id: str) -> str:
    payload = json.dumps(
        {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"header.{encoded}.signature"


def _credential(*, expires_at_ms: int = 2_000_000) -> CodexCredential:
    return CodexCredential(
        access_token=_token("acct-test-123456"),
        refresh_token="refresh-secret",
        expires_at_ms=expires_at_ms,
        account_id="acct-test-123456",
    )


def test_credential_store_is_private_atomic_and_redacts_repr(tmp_path: Path) -> None:
    store = CodexCredentialStore(tmp_path)
    credential = _credential()

    with store.locked():
        store.save(credential)
        loaded = store.load()

    assert loaded == credential
    assert "refresh-secret" not in repr(credential)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert not tuple(store.root.glob("*.tmp"))


def test_credential_store_rejects_permissions_that_expose_tokens(tmp_path: Path) -> None:
    store = CodexCredentialStore(tmp_path)
    with store.locked():
        store.save(_credential())
    store.path.chmod(0o644)

    with pytest.raises(CodexAuthenticationError, match="group or others"):
        store.load()


def test_device_flow_matches_pi_contract_and_exchanges_tokens() -> None:
    calls: list[tuple[str, object]] = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if str(request.url) == CODEX_DEVICE_USER_CODE_URL:
            calls.append(("usercode", json.loads(request.content)))
            return httpx.Response(
                200,
                json={"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 0},
            )
        if str(request.url) == CODEX_DEVICE_TOKEN_URL:
            polls += 1
            calls.append(("poll", json.loads(request.content)))
            if polls == 1:
                return httpx.Response(403, json={"error": "deviceauth_authorization_pending"})
            return httpx.Response(
                200,
                json={"authorization_code": "auth-code", "code_verifier": "verifier"},
            )
        assert str(request.url) == CODEX_TOKEN_URL
        calls.append(("exchange", request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": _token("acct-test-123456"),
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
            },
        )

    oauth = CodexOAuthClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: 1000,
        sleep=lambda _seconds: None,
    )
    authorization = oauth.start_device_authorization()
    credential = oauth.complete_device_authorization(authorization)

    assert authorization.user_code == "ABCD-EFGH"
    assert credential.account_id == "acct-test-123456"
    assert credential.expires_at_ms == 4_600_000
    assert calls[0][0] == "usercode"
    assert calls[1] == (
        "poll",
        {"device_auth_id": "device-1", "user_code": "ABCD-EFGH"},
    )
    assert "grant_type=authorization_code" in str(calls[-1][1])


def test_manager_refreshes_expired_credential_under_store_lock(tmp_path: Path) -> None:
    refreshes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        refreshes += 1
        assert str(request.url) == CODEX_TOKEN_URL
        return httpx.Response(
            200,
            json={
                "access_token": _token("acct-test-123456"),
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )

    store = CodexCredentialStore(tmp_path)
    with store.locked():
        store.save(_credential(expires_at_ms=900_000))
    oauth = CodexOAuthClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: 1000,
    )

    credential = CodexCredentialManager(store, oauth=oauth, now=lambda: 1000).resolve()

    assert refreshes == 1
    assert credential.refresh_token == "rotated-refresh"
    assert store.load() == credential


def test_manager_never_falls_back_to_an_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    manager = CodexCredentialManager(CodexCredentialStore(tmp_path))

    with pytest.raises(CodexAuthenticationError, match="codex login"):
        manager.resolve()
