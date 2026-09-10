from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import yaml

from harness.adapters.providers.codex import (
    CODEX_CATALOG_CLIENT_VERSION,
    CODEX_MODELS_URL,
    CodexModel,
    CodexSelection,
    benchmark_codex_models,
    discover_codex_models,
    fastest_candidates,
    load_codex_selection,
    save_codex_selection,
    write_codex_config,
)
from harness.adapters.providers.codex_auth import (
    CodexCredential,
    CodexCredentialManager,
    CodexCredentialStore,
)
from harness.core.config import parse_harness_composition


def _token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        ).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def _manager(tmp_path: Path) -> CodexCredentialManager:
    store = CodexCredentialStore(tmp_path)
    with store.locked():
        store.save(
            CodexCredential(
                access_token=_token(),
                refresh_token="refresh",
                expires_at_ms=4_000_000_000_000,
                account_id="account-123",
            )
        )
    return CodexCredentialManager(store)


def test_account_catalog_filters_disabled_models_and_ranks_fast_candidates(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url.copy_with(query=None)) == CODEX_MODELS_URL
        assert request.url.params["client_version"] == CODEX_CATALOG_CLIENT_VERSION
        assert request.headers["version"] == CODEX_CATALOG_CLIENT_VERSION
        assert request.headers["chatgpt-account-id"] == "account-123"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-quality", "priority": 1, "supported_in_api": True},
                    {
                        "slug": "gpt-5.6-luna",
                        "display_name": "Luna",
                        "priority": 3,
                        "minimal_client_version": "0.147.0",
                        "supported_in_api": True,
                    },
                    {"slug": "disabled-fast", "supported_in_api": False},
                ]
            },
        )

    models = discover_codex_models(
        _manager(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [model.id for model in models] == ["gpt-quality", "gpt-5.6-luna"]
    assert fastest_candidates(models) == (
        CodexModel("gpt-5.6-luna", "Luna", "0.147.0", 3),
    )


def test_catalog_compatibility_version_can_be_overridden_without_changing_identity(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["client_version"] == "0.150.0"
        assert request.headers["version"] == "0.150.0"
        assert request.headers["originator"] == "skein"
        return httpx.Response(200, json={"models": [{"slug": "fixture", "supported_in_api": True}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert discover_codex_models(_manager(tmp_path), client=client, client_version="0.150.0")[0].id == "fixture"


def test_benchmark_sorts_by_first_token_and_reports_unavailable_model(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "unavailable":
            return httpx.Response(404, text="model unavailable")
        completed = {
            "type": "response.completed",
            "response": {
                "id": "response-1",
                "output": [],
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
        }
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"O(n)."}\n\n'
                f"data: {json.dumps(completed)}\n\n"
            ),
        )

    results = benchmark_codex_models(
        _manager(tmp_path),
        (CodexModel("fast", "Fast"), CodexModel("unavailable", "Unavailable")),
        runs=1,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert results[0].model == "fast"
    assert results[0].successful_runs == 1
    assert results[0].median_first_token_ms is not None
    assert results[1].model == "unavailable"
    assert "404" in (results[1].error or "")


def test_selection_and_generated_yaml_are_private_valid_and_deterministic(tmp_path: Path) -> None:
    selection = CodexSelection(
        model="gpt-5.3-codex-spark",
        reasoning="low",
        client_version="0.147.0",
    )
    first = save_codex_selection(tmp_path, selection)
    first_bytes = first.read_bytes()
    second = save_codex_selection(tmp_path, selection)
    config = write_codex_config(tmp_path / "server" / "codex.yaml", selection=selection)
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))

    assert first_bytes == second.read_bytes()
    assert load_codex_selection(tmp_path) == selection
    assert first.stat().st_mode & 0o077 == 0
    assert config.stat().st_mode & 0o077 == 0
    coding = payload["harness"]["config"]["models"]["coding"]
    assert coding == {
        "provider": "openai_codex",
        "name": "gpt-5.3-codex-spark",
        "reasoning": "low",
        "retry": {
            "attempts": 3,
            "exponential_base": 2,
            "initial_delay_seconds": 1,
            "retry_statuses": [429, 500, 502, 503, 504],
        },
        "client_version": "0.147.0",
    }
    parse_harness_composition(payload)


def test_generated_codex_config_can_enable_notebook_ptc(tmp_path: Path) -> None:
    config = write_codex_config(
        tmp_path / "server/codex.yaml",
        selection=CodexSelection(model="gpt-test", reasoning="low", client_version=None),
        notebook_ptc=True,
    )

    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["harness"]["config"]["notebook_ptc"]["enabled"] is True
    assert payload["harness"]["config"]["memory"]["enabled"] is True
