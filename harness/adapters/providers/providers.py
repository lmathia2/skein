"""Closed ADK model-provider registry and built-in provider adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from google.adk.models import BaseLlm, Gemini
from google.genai import types

from harness.core.config import ModelConfig, RuntimeBindings, SecretRef

from .codex_auth import CodexCredentialManager, CodexCredentialStore
from .codex_responses import CodexResponsesLlm
from .contracts import AdkModelProvider
from .openrouter_responses import OpenRouterResponsesLlm


class GoogleAdkModelProvider:
    """Build the native ADK Gemini adapter used by existing configurations."""

    @property
    def provider_id(self) -> str:
        return "google_adk"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
        bindings: RuntimeBindings | None = None,
    ) -> BaseLlm:
        del secrets, bindings
        retry = config.retry
        return Gemini(
            model=config.name,
            retry_options=types.HttpRetryOptions(
                attempts=retry.attempts,
                exp_base=retry.exponential_base,
                initial_delay=retry.initial_delay_seconds,
                http_status_codes=list(retry.retry_statuses),
            ),
        )


class OpenAiCodexModelProvider:
    """Use a ChatGPT Plus/Pro OAuth subscription through ADK's model seam."""

    @property
    def provider_id(self) -> str:
        return "openai_codex"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
        bindings: RuntimeBindings | None = None,
    ) -> BaseLlm:
        if secrets or config.api_key is not None:
            raise ValueError("openai_codex is subscription-only and cannot use API keys")
        if bindings is None:
            raise ValueError("openai_codex requires runtime bindings for private OAuth state")
        retry = config.retry
        return CodexResponsesLlm(
            model=config.name,
            reasoning_effort=config.reasoning,
            retry_attempts=retry.attempts,
            retry_initial_delay_seconds=retry.initial_delay_seconds,
            retry_exponential_base=retry.exponential_base,
            retry_statuses=retry.retry_statuses,
            client_version=config.client_version,
            credential_manager=CodexCredentialManager(
                CodexCredentialStore(bindings.auth_state_root or bindings.state_root)
            ),
        )


class OpenRouterModelProvider:
    """Use OpenRouter through its fixed OpenResponses API endpoint."""

    @property
    def provider_id(self) -> str:
        return "openrouter"

    def build_model(
        self,
        config: ModelConfig,
        *,
        secrets: Mapping[str, SecretRef],
        bindings: RuntimeBindings | None = None,
    ) -> BaseLlm:
        del bindings
        secret = secrets.get("api_key") or config.api_key
        if secret is None:
            raise ValueError("openrouter requires an API key reference")
        api_key = os.getenv(secret.env)
        if not api_key:
            raise ValueError(f"openrouter API key environment variable {secret.env!r} is unset")
        retry = config.retry
        return OpenRouterResponsesLlm(
            model=config.name,
            reasoning_effort=config.reasoning,
            retry_attempts=retry.attempts,
            retry_initial_delay_seconds=retry.initial_delay_seconds,
            retry_exponential_base=retry.exponential_base,
            retry_statuses=retry.retry_statuses,
            api_key=api_key,
        )


class ClosedAdkModelProviderRegistry:
    """Deterministic provider registry; configuration cannot import arbitrary code."""

    def __init__(self, providers: Iterable[AdkModelProvider] = ()) -> None:
        self._providers: dict[str, AdkModelProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: AdkModelProvider) -> None:
        provider_id = provider.provider_id
        if provider_id in self._providers:
            raise ValueError(f"model provider {provider_id!r} is already registered")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> AdkModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            available = ", ".join(self.available()) or "<none>"
            raise LookupError(
                f"unknown model provider {provider_id!r}; available: {available}"
            ) from exc

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def default_adk_model_provider_registry() -> ClosedAdkModelProviderRegistry:
    return ClosedAdkModelProviderRegistry(
        (
            GoogleAdkModelProvider(),
            OpenAiCodexModelProvider(),
            OpenRouterModelProvider(),
        )
    )


__all__ = [
    "ClosedAdkModelProviderRegistry",
    "GoogleAdkModelProvider",
    "OpenAiCodexModelProvider",
    "OpenRouterModelProvider",
    "default_adk_model_provider_registry",
]
