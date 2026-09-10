"""ChatGPT subscription model discovery, benchmarking, and runtime configuration."""

from __future__ import annotations

import asyncio
import os
import statistics
import tempfile
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from harness.adapters.providers.codex_auth import (
    CodexCredential,
    CodexCredentialManager,
    CodexCredentialStore,
)
from harness.adapters.providers.codex_responses import CodexResponsesLlm
from harness.adapters.providers.selection import (
    ModelChoice,
    load_model_default,
    model_default_path,
    save_model_default,
)
from harness.core.config import DEFAULT_COMPOSITION_PATH, parse_harness_composition

CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_MODELS_URL = f"{CODEX_BASE_URL}/codex/models"
# Catalog protocol compatibility, not the harness's package/version identity.
CODEX_CATALOG_CLIENT_VERSION = "0.147.0"
DEFAULT_CODEX_MODEL = "gpt-5.3-codex-spark"
FAST_MODEL_MARKERS = ("luna", "spark", "mini-fast", "fast", "terra", "mini")


class CodexModelError(RuntimeError):
    """Codex model discovery or benchmarking failed."""


@dataclass(frozen=True, slots=True)
class CodexModel:
    id: str
    display_name: str
    client_version: str | None = None
    priority: int = 1_000


@dataclass(frozen=True, slots=True)
class CodexBenchmarkResult:
    model: str
    successful_runs: int
    median_first_token_ms: int | None
    median_total_ms: int | None
    client_version: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CodexSelection:
    model: str
    reasoning: str
    client_version: str | None


def _headers(credential: CodexCredential, *, client_version: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {credential.access_token}",
        "chatgpt-account-id": credential.account_id,
        "originator": "skein",
        "User-Agent": "skein/0.1",
        "accept": "application/json",
    }
    if client_version:
        headers["version"] = client_version
    return headers


def discover_codex_models(
    manager: CodexCredentialManager,
    *,
    client: httpx.Client | None = None,
    client_version: str = CODEX_CATALOG_CLIENT_VERSION,
) -> tuple[CodexModel, ...]:
    """Return the account-authorized API catalog, without exposing its credential."""

    credential = manager.resolve()
    active_client = client or httpx.Client(timeout=30)
    try:
        response = active_client.get(
            CODEX_MODELS_URL, params={"client_version": client_version},
            headers=_headers(credential, client_version=client_version),
        )
    finally:
        if client is None:
            active_client.close()
    if not response.is_success:
        detail = " ".join(response.text.split())[:500]
        raise CodexModelError(
            f"Codex model discovery failed ({response.status_code})"
            + (f": {detail}" if detail else "")
        )
    payload = response.json()
    raw_models = payload.get("models", payload.get("data")) if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        raise CodexModelError("Codex model discovery response has no model list")
    models: list[CodexModel] = []
    for raw in raw_models:
        if not isinstance(raw, dict) or raw.get("supported_in_api") is False:
            continue
        model_id = raw.get("slug") or raw.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        models.append(
            CodexModel(
                id=model_id,
                display_name=str(raw.get("display_name") or raw.get("name") or model_id),
                client_version=(
                    str(raw["minimal_client_version"])
                    if raw.get("minimal_client_version")
                    else None
                ),
                priority=int(raw.get("priority", 1_000)),
            )
        )
    if not models:
        raise CodexModelError("ChatGPT account has no Codex models enabled for API use")
    return tuple(sorted(models, key=lambda model: (model.priority, model.id)))


def fastest_candidates(models: Sequence[CodexModel], *, limit: int = 6) -> tuple[CodexModel, ...]:
    """Bound live probes to catalog models plausibly optimized for low latency."""

    marked = [
        model
        for model in models
        if any(marker in model.id.lower() for marker in FAST_MODEL_MARKERS)
    ]
    return tuple((marked or list(models))[:limit])


async def _benchmark_once(
    manager: CodexCredentialManager,
    model: CodexModel,
    *,
    reasoning: str,
    client_factory: Any = None,
) -> tuple[int, int]:
    llm = CodexResponsesLlm(
        model=model.id,
        reasoning_effort=reasoning,
        client_version=model.client_version,
        credential_manager=manager,
        **({"client_factory": client_factory} if client_factory is not None else {}),
    )
    request = LlmRequest(
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "A Python function receives an integer n. Reply with exactly one "
                            "short sentence stating the time complexity of summing range(n)."
                        )
                    )
                ],
            )
        ],
        config=types.GenerateContentConfig(
            system_instruction="Answer this small coding question accurately and concisely.",
            max_output_tokens=32,
        ),
    )
    started = time.perf_counter()
    first_token: float | None = None
    async for response in llm.generate_content_async(request, stream=True):
        if (
            response.partial
            and response.content
            and response.content.parts
            and any(part.text for part in response.content.parts)
        ):
            first_token = first_token or time.perf_counter()
    finished = time.perf_counter()
    if first_token is None:
        first_token = finished
    return round((first_token - started) * 1000), round((finished - started) * 1000)


def benchmark_codex_models(
    manager: CodexCredentialManager,
    models: Sequence[CodexModel],
    *,
    reasoning: str = "low",
    runs: int = 2,
    client_factory: Any = None,
) -> tuple[CodexBenchmarkResult, ...]:
    """Measure account-specific TTFT and total latency using identical requests."""

    results: list[CodexBenchmarkResult] = []
    for model in models:
        first_tokens: list[int] = []
        totals: list[int] = []
        error: str | None = None
        for _ in range(runs):
            try:
                first, total = asyncio.run(
                    _benchmark_once(
                        manager,
                        model,
                        reasoning=reasoning,
                        client_factory=client_factory,
                    )
                )
                first_tokens.append(first)
                totals.append(total)
            except Exception as caught:
                error = f"{type(caught).__name__}: {' '.join(str(caught).split())[:300]}"
                break
        results.append(
            CodexBenchmarkResult(
                model=model.id,
                successful_runs=len(first_tokens),
                median_first_token_ms=(
                    round(statistics.median(first_tokens)) if first_tokens else None
                ),
                median_total_ms=round(statistics.median(totals)) if totals else None,
                client_version=model.client_version,
                error=error,
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda result: (
                result.median_first_token_ms is None,
                result.median_first_token_ms or 2**31,
                result.median_total_ms or 2**31,
                result.model,
            ),
        )
    )


def selection_path(state_root: Path) -> Path:
    return model_default_path(state_root)


def save_codex_selection(state_root: Path, selection: CodexSelection) -> Path:
    return save_model_default(state_root, ModelChoice(provider="openai_codex", name=selection.model,
        reasoning=selection.reasoning, client_version=selection.client_version))


def load_codex_selection(state_root: Path) -> CodexSelection | None:
    choice = load_model_default(state_root)
    return CodexSelection(choice.name, choice.reasoning or "low", choice.client_version) if choice and choice.provider == "openai_codex" else None


def write_codex_config(
    destination: Path,
    *,
    selection: CodexSelection,
    template_path: Path = DEFAULT_COMPOSITION_PATH,
    use_saved_model_default: bool = True,
    notebook_ptc: bool = False,
) -> Path:
    payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    payload.setdefault("server", {})["use_saved_model_default"] = use_saved_model_default
    coding = payload["harness"]["config"]["models"]["coding"]
    coding.clear()
    coding.update(
        {
            "provider": "openai_codex",
            "name": selection.model,
            "reasoning": selection.reasoning,
            "retry": {
                "attempts": 3,
                "exponential_base": 2,
                "initial_delay_seconds": 1,
                "retry_statuses": [429, 500, 502, 503, 504],
            },
        }
    )
    if selection.client_version:
        coding["client_version"] = selection.client_version
    payload["harness"]["config"]["notebook_ptc"]["enabled"] = notebook_ptc
    if notebook_ptc:
        payload["harness"]["config"]["memory"]["enabled"] = True
    parse_harness_composition(payload)
    rendered = yaml.safe_dump(payload, sort_keys=False)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return destination


def prepare_codex_config(
    state_root: Path,
    *,
    model: str | None = None,
    reasoning: str | None = None,
    client_version: str | None = None,
    notebook_ptc: bool = False,
) -> tuple[Path, CodexSelection]:
    saved = load_codex_selection(state_root)
    selection = CodexSelection(
        model=model or (saved.model if saved else DEFAULT_CODEX_MODEL),
        reasoning=reasoning or (saved.reasoning if saved else "low"),
        client_version=(client_version if client_version is not None else saved.client_version if saved else None),
    )
    return (
        write_codex_config(state_root / "server" / "openai-codex.yaml", selection=selection,
                           use_saved_model_default=not any((model, reasoning, client_version)),
                           notebook_ptc=notebook_ptc),
        selection,
    )


def credential_manager(state_root: Path) -> CodexCredentialManager:
    return CodexCredentialManager(CodexCredentialStore(state_root))


__all__ = [
    "DEFAULT_CODEX_MODEL",
    "CodexBenchmarkResult",
    "CodexModel",
    "CodexModelError",
    "CodexSelection",
    "benchmark_codex_models",
    "credential_manager",
    "discover_codex_models",
    "fastest_candidates",
    "load_codex_selection",
    "prepare_codex_config",
    "save_codex_selection",
    "selection_path",
    "write_codex_config",
]
