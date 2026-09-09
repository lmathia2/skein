from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from harness.ai.codex_auth import CodexCredential
from harness.ai.codex_responses import CodexResponsesLlm, build_codex_request_body


class _Credentials:
    def __init__(self) -> None:
        self.force_refresh: list[bool] = []

    def resolve(self, *, force_refresh: bool = False) -> CodexCredential:
        self.force_refresh.append(force_refresh)
        return CodexCredential(
            access_token="header.payload.signature",
            refresh_token="refresh",
            expires_at_ms=2_000_000,
            account_id="account-123",
        )


async def _collect(stream: AsyncGenerator[LlmResponse, None]) -> list[LlmResponse]:
    return [response async for response in stream]


def _request() -> LlmRequest:
    declaration = types.FunctionDeclaration(
        name="read",
        description="Read a file",
        parameters_json_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    return LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="Inspect README.md")]),
            types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="read", args={"path": "README.md"})],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name="read",
                        response={"status": "ok", "model_text": "# project"},
                    )
                ],
            ),
        ],
        config=types.GenerateContentConfig(
            system_instruction="You are a coding agent.",
            tools=[types.Tool(function_declarations=[declaration])],
        ),
    )


def test_request_compiler_preserves_tools_history_and_stable_cache_prefix() -> None:
    request = _request()
    assert request.contents is not None
    assert request.contents[1].parts is not None
    assert request.contents[2].parts is not None
    call = request.contents[1].parts[0].function_call
    response = request.contents[2].parts[0].function_response
    assert call is not None and response is not None
    call.id = "call-1"
    response.id = "call-1"

    first = build_codex_request_body(request, model="gpt-test", reasoning_effort="low")
    second = build_codex_request_body(request, model="gpt-test", reasoning_effort="low")

    request.contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text="Continue")])
    )
    extended = build_codex_request_body(request, model="gpt-test", reasoning_effort="low")

    assert request.config.tools is not None
    assert isinstance(request.config.tools[0], types.Tool)
    assert request.config.tools[0].function_declarations is not None
    request.config.tools[0].function_declarations[0].description = "Read one file"
    changed_prefix = build_codex_request_body(request, model="gpt-test", reasoning_effort="low")

    assert first == second
    assert first["store"] is False
    assert first["parallel_tool_calls"] is True
    assert first["reasoning"] == {"effort": "low", "summary": "auto"}
    assert first["tools"][0]["name"] == "read"
    assert first["input"][1] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "read",
        "arguments": '{"path":"README.md"}',
    }
    assert first["input"][2]["type"] == "function_call_output"
    assert first["input"][2]["call_id"] == "call-1"
    assert len(first["prompt_cache_key"]) == 64
    assert extended["input"][: len(first["input"])] == first["input"]
    assert extended["prompt_cache_key"] == first["prompt_cache_key"]
    assert changed_prefix["prompt_cache_key"] != first["prompt_cache_key"]


@pytest.mark.asyncio
async def test_codex_llm_streams_text_tool_call_final_response_and_usage() -> None:
    captured: dict[str, Any] = {}
    events = [
        {"type": "response.output_text.delta", "delta": "I will "},
        {"type": "response.output_text.delta", "delta": "inspect."},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-1",
                "name": "read",
                "arguments": '{"path":"README.md"}',
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "response-1",
                "output": [],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
            },
        },
    ]
    sse = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    credentials = _Credentials()
    model = CodexResponsesLlm(
        model="gpt-test",
        reasoning_effort="low",
        credential_manager=credentials,  # type: ignore[arg-type]
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    responses = await _collect(model.generate_content_async(_request(), stream=True))

    assert len(responses) == 4
    assert [item.partial for item in responses] == [True, True, True, False]
    final = responses[-1]
    assert final.content is not None
    assert "".join(part.text or "" for part in final.content.parts or []) == "I will inspect."
    assert final.get_function_calls()[0].id == "call-1"
    assert final.get_function_calls()[0].args == {"path": "README.md"}
    assert final.usage_metadata is not None
    assert final.usage_metadata.cached_content_token_count == 4
    assert final.interaction_id == "response-1"
    assert final.custom_metadata is not None
    profile = final.custom_metadata["provider_request_profile"]
    assert profile["bytes"] == len(json.dumps(captured["body"], separators=(",", ":")).encode())
    assert set(profile["regions"]) >= {"input", "instructions", "model", "tools"}
    assert captured["headers"]["authorization"] == "Bearer header.payload.signature"
    assert captured["headers"]["chatgpt-account-id"] == "account-123"
    assert "OPENAI_API_KEY" not in captured["headers"]
    assert credentials.force_refresh == [False]


@pytest.mark.asyncio
async def test_codex_llm_forces_oauth_refresh_once_after_unauthorized() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(401, text="expired")
        completed = {
            "type": "response.completed",
            "response": {"id": "response-2", "output": [], "usage": {}},
        }
        return httpx.Response(200, text=f"data: {json.dumps(completed)}\n\n")

    credentials = _Credentials()
    model = CodexResponsesLlm(
        model="gpt-test",
        retry_attempts=2,
        credential_manager=credentials,  # type: ignore[arg-type]
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    responses = await _collect(model.generate_content_async(LlmRequest(), stream=False))

    assert len(responses) == 1
    assert attempts == 2
    assert credentials.force_refresh == [False, True]
