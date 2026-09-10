from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from harness.adapters.providers.openrouter_responses import (
    OpenRouterResponsesLlm,
    build_openrouter_request_body,
)


async def _collect(stream: AsyncGenerator[LlmResponse, None]) -> list[LlmResponse]:
    return [response async for response in stream]


def _request() -> LlmRequest:
    return LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="Inspect README.md")])
        ],
        config=types.GenerateContentConfig(
            system_instruction="You are a coding agent.",
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="read",
                            description="Read a file",
                            parameters_json_schema={
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        )
                    ]
                )
            ],
        ),
    )


def test_openrouter_request_uses_portable_responses_fields() -> None:
    body = build_openrouter_request_body(
        _request(),
        model="meta/muse-spark-1.2-contributor",
        reasoning_effort="xhigh",
    )

    assert body["model"] == "meta/muse-spark-1.2-contributor"
    assert body["reasoning"] == {"effort": "xhigh"}
    assert body["provider"] == {"require_parameters": True}
    assert body["store"] is False
    assert body["stream"] is True
    assert body["tools"][0]["name"] == "read"
    assert "include" not in body
    assert "parallel_tool_calls" not in body
    assert "text" not in body


@pytest.mark.asyncio
async def test_openrouter_streams_tools_usage_cost_and_routing_metadata() -> None:
    captured: dict[str, Any] = {}
    events = [
        {"type": "response.output_text.delta", "delta": "Inspecting."},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call-1",
                "name": "read",
                "arguments": '{"path":"README.md"}',
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "response-1",
                "model": "meta/muse-spark-1.2-contributor",
                "output": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0.000014,
                    "input_tokens_details": {"cached_tokens": 80},
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
            "openrouter_metadata": {
                "requested": "meta/muse-spark-1.2-contributor",
                "strategy": "direct",
                "region": "iad",
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {
                            "provider": "Meta",
                            "model": "meta/muse-spark-1.2-contributor",
                            "selected": True,
                        }
                    ]
                },
            },
        },
    ]
    sse = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    model = OpenRouterResponsesLlm(
        model="meta/muse-spark-1.2-contributor",
        reasoning_effort="xhigh",
        api_key="openrouter-test-key",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    responses = await _collect(model.generate_content_async(_request(), stream=True))

    assert [response.partial for response in responses] == [True, True, False]
    final = responses[-1]
    assert final.get_function_calls()[0].args == {"path": "README.md"}
    assert final.model_version == "meta/muse-spark-1.2-contributor"
    assert final.usage_metadata is not None
    assert final.usage_metadata.cached_content_token_count == 80
    assert final.custom_metadata == {
        "provider_cost_usd": 0.000014,
        "requested_model": "meta/muse-spark-1.2-contributor",
        "routing_strategy": "direct",
        "routing_region": "iad",
        "routing_attempt": 1,
        "provider_name": "Meta",
        "provider_request_profile": final.custom_metadata["provider_request_profile"],
    }
    profile = final.custom_metadata["provider_request_profile"]
    assert profile["bytes"] == len(json.dumps(captured["body"], separators=(",", ":")).encode())
    assert len(profile["sha256"]) == 64
    assert set(profile["regions"]) >= {"input", "model", "tools"}
    assert captured["url"] == "https://openrouter.ai/api/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer openrouter-test-key"
    assert captured["headers"]["x-openrouter-metadata"] == "enabled"
    assert captured["body"]["reasoning"] == {"effort": "xhigh"}


@pytest.mark.asyncio
async def test_openrouter_retries_transient_failures_and_redacts_api_key() -> None:
    attempts = 0

    def retry_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="retry")
        completed = {
            "type": "response.completed",
            "response": {"id": "response-2", "output": [], "usage": {}},
        }
        return httpx.Response(200, text=f"data: {json.dumps(completed)}\n\n")

    model = OpenRouterResponsesLlm(
        model="test-model",
        api_key="openrouter-test-key",
        retry_attempts=2,
        retry_initial_delay_seconds=0,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(retry_handler)
        ),
    )
    assert len(await _collect(model.generate_content_async(LlmRequest()))) == 1
    assert attempts == 2

    def error_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad openrouter-test-key")

    failing = OpenRouterResponsesLlm(
        model="test-model",
        api_key="openrouter-test-key",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(error_handler)
        ),
    )
    with pytest.raises(RuntimeError, match="<redacted>") as raised:
        await _collect(failing.generate_content_async(LlmRequest()))
    assert "openrouter-test-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_openrouter_retries_temporary_in_flight_budget_exhaustion() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                402,
                json={"error": {"code": "in_flight_budget_exhausted"}},
                headers={"Retry-After": "0"},
            )
        completed = {
            "type": "response.completed",
            "response": {"id": "response-3", "output": [], "usage": {}},
        }
        return httpx.Response(200, text=f"data: {json.dumps(completed)}\n\n")

    model = OpenRouterResponsesLlm(
        model="test-model",
        api_key="openrouter-test-key",
        retry_attempts=2,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert len(await _collect(model.generate_content_async(LlmRequest()))) == 1
    assert attempts == 2
