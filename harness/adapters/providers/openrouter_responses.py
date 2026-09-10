"""OpenRouter's OpenResponses endpoint through Google ADK's model seam."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
from google.adk.models import BaseLlm
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from .codex_responses import (
    _iter_sse,
    _ResponseAccumulator,
    build_codex_request_body,
    provider_request_profile,
)

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_AsyncClientFactory = Callable[[], httpx.AsyncClient]


def build_openrouter_request_body(
    request: LlmRequest,
    *,
    model: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Compile the shared Responses shape without Codex-only extensions."""

    body = build_codex_request_body(
        request,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    body.pop("include", None)
    body.pop("parallel_tool_calls", None)
    text = body.get("text")
    if isinstance(text, dict):
        text.pop("verbosity", None)
        if not text:
            body.pop("text")
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("summary", None)
    body["provider"] = {"require_parameters": True}
    return body


class OpenRouterResponsesLlm(BaseLlm):
    """Stream OpenRouter responses without adding a second agent runtime."""

    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    reasoning_effort: str | None = None
    retry_attempts: int = 3
    retry_initial_delay_seconds: float = 1
    retry_exponential_base: float = 2
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504, 529)

    _api_key: str = PrivateAttr()
    _client_factory: _AsyncClientFactory = PrivateAttr()

    def __init__(
        self,
        *,
        api_key: str,
        client_factory: _AsyncClientFactory | None = None,
        **data: Any,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter API key is empty")
        super().__init__(**data)
        self._api_key = api_key
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=30))
        )

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    @property
    def responses_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/responses") else f"{base}/responses"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-OpenRouter-Metadata": "enabled",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

    async def _stream_events(self, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        for attempt in range(self.retry_attempts):
            async with self._client_factory() as client:
                try:
                    async with client.stream(
                        "POST",
                        self.responses_url,
                        headers=self._headers(),
                        json=body,
                    ) as response:
                        if not response.is_success:
                            raw = (await response.aread()).decode("utf-8", errors="replace")
                            in_flight_budget_exhausted = (
                                response.status_code == 402 and "in_flight_budget_exhausted" in raw
                            )
                            if (
                                response.status_code in self.retry_statuses
                                or in_flight_budget_exhausted
                            ) and attempt + 1 < self.retry_attempts:
                                delay = response.headers.get("Retry-After")
                                await asyncio.sleep(
                                    float(delay)
                                    if delay is not None
                                    else self.retry_initial_delay_seconds
                                    * self.retry_exponential_base**attempt
                                )
                                continue
                            detail = " ".join(raw.replace(self._api_key, "<redacted>").split())
                            raise RuntimeError(
                                f"OpenRouter request failed ({response.status_code}): "
                                f"{detail[:1000]}"
                            )
                        async for event in _iter_sse(response):
                            yield event
                        return
                except httpx.TransportError:
                    if attempt + 1 >= self.retry_attempts:
                        raise
                    await asyncio.sleep(
                        self.retry_initial_delay_seconds * self.retry_exponential_base**attempt
                    )
        raise RuntimeError("OpenRouter request exhausted retries")

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        body = build_openrouter_request_body(
            llm_request,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
        )
        request_profile = provider_request_profile(body)
        accumulator = _ResponseAccumulator()
        async for event in self._stream_events(body):
            part = accumulator.consume(event)
            if stream and part is not None:
                yield LlmResponse(
                    content=types.Content(role="model", parts=[part]),
                    partial=True,
                    model_version=self.model,
                )
        if not accumulator.completed:
            raise RuntimeError("OpenRouter stream ended before response.completed")
        final = accumulator.final_response(self.model)
        final.custom_metadata = {
            **(final.custom_metadata or {}),
            "provider_request_profile": request_profile,
        }
        yield final


__all__ = [
    "DEFAULT_OPENROUTER_BASE_URL",
    "OpenRouterResponsesLlm",
    "build_openrouter_request_body",
]
