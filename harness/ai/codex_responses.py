"""ADK ``BaseLlm`` adapter for the ChatGPT subscription Codex Responses API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx
from google.adk.models import BaseLlm
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from .codex_auth import CodexCredential, CodexCredentialManager

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
DEFAULT_CODEX_USER_AGENT = "skein/0.1"


class _AsyncClientFactory(Protocol):
    def __call__(self) -> httpx.AsyncClient: ...


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def provider_request_profile(body: dict[str, Any]) -> dict[str, Any]:
    """Hash the exact JSON bytes httpx sends, with per-field byte attribution."""

    encoded = httpx.Request("POST", "https://local.invalid", json=body).content
    regions: dict[str, dict[str, int | str]] = {}
    for name, value in sorted(body.items()):
        part = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        regions[name] = {
            "bytes": len(part),
            "sha256": hashlib.sha256(part).hexdigest(),
        }
    return {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "regions": regions,
    }


def _system_instruction(request: LlmRequest) -> str:
    instruction = request.config.system_instruction
    if isinstance(instruction, str):
        return instruction
    if isinstance(instruction, types.Content):
        return "".join(part.text or "" for part in instruction.parts or [])
    return ""


def _part_text_block(part: types.Part, *, role: str) -> dict[str, str] | None:
    if not part.text or part.thought:
        return None
    return {
        "type": "output_text" if role in {"assistant", "model"} else "input_text",
        "text": part.text,
    }


def _content_to_response_input(content: types.Content) -> list[dict[str, Any]]:
    role = content.role or "user"
    items: list[dict[str, Any]] = []
    message_blocks: list[dict[str, str]] = []
    for part in content.parts or []:
        if part.function_response is not None:
            response = part.function_response
            output = response.response
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": response.id or response.name or "missing_call_id",
                    "output": output if isinstance(output, str) else _json(output or {}),
                }
            )
            continue
        if part.function_call is not None:
            call = part.function_call
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id or "missing_call_id",
                    "name": call.name or "unknown_tool",
                    "arguments": _json(call.args or {}),
                }
            )
            continue
        if part.thought and part.thought_signature:
            items.append(
                {
                    "type": "reasoning",
                    "encrypted_content": base64_bytes(part.thought_signature),
                    "summary": [],
                }
            )
            continue
        block = _part_text_block(part, role=role)
        if block is not None:
            message_blocks.append(block)
    if message_blocks:
        items.insert(
            0,
            {
                "role": "assistant" if role in {"assistant", "model"} else "user",
                "content": message_blocks,
            },
        )
    return items


def base64_bytes(value: bytes) -> str:
    """Round-trip opaque reasoning content without placing it in text context."""

    return value.decode("utf-8", errors="strict")


def _schema_dict(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, types.Schema):
        return cast(
            dict[str, Any],
            value.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    model_json_schema = getattr(value, "model_json_schema", None)
    if callable(model_json_schema):
        schema = model_json_schema()
        return cast(dict[str, Any], schema) if isinstance(schema, dict) else None
    return None


def _function_tools(request: LlmRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for configured in request.config.tools or []:
        if not isinstance(configured, types.Tool):
            continue
        for declaration in configured.function_declarations or []:
            parameters = declaration.parameters_json_schema
            if parameters is None:
                parameters = _schema_dict(declaration.parameters)
            tools.append(
                {
                    "type": "function",
                    "name": declaration.name,
                    "description": declaration.description or "",
                    "parameters": parameters or {"type": "object", "properties": {}},
                    "strict": False,
                }
            )
    return tools


def build_codex_request_body(
    request: LlmRequest,
    *,
    model: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Compile one deterministic Codex request from ADK's provider-neutral shape."""

    instruction = _system_instruction(request) or "You are a helpful coding assistant."
    tools = _function_tools(request)
    schema = _schema_dict(request.config.response_json_schema or request.config.response_schema)
    text: dict[str, Any] = {"verbosity": "low"}
    if schema is not None:
        text["format"] = {
            "type": "json_schema",
            "name": "adk_response",
            "schema": schema,
            "strict": True,
        }
    cache_prefix = {
        "model": model,
        "instructions": instruction,
        "tools": tools,
        "text_format": text.get("format"),
    }
    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": instruction,
        "input": [
            item for content in request.contents for item in _content_to_response_input(content)
        ],
        "include": ["reasoning.encrypted_content"],
        "parallel_tool_calls": True,
        "prompt_cache_key": hashlib.sha256(_json(cache_prefix).encode()).hexdigest(),
        "text": text,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    if request.config.temperature is not None:
        body["temperature"] = request.config.temperature
    if request.config.max_output_tokens is not None:
        body["max_output_tokens"] = request.config.max_output_tokens
    return body


def _function_call_part(item: Mapping[str, Any]) -> types.Part | None:
    name = item.get("name")
    call_id = item.get("call_id") or item.get("id")
    arguments = item.get("arguments", "{}")
    if not isinstance(name, str) or not isinstance(call_id, str):
        return None
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        args = {"_raw_arguments": arguments}
    if not isinstance(args, dict):
        args = {"value": args}
    part = types.Part.from_function_call(name=name, args=args)
    if part.function_call is not None:
        part.function_call.id = call_id
    return part


def _usage(response: Mapping[str, Any]) -> types.GenerateContentResponseUsageMetadata | None:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return None
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    cached = input_details.get("cached_tokens", 0) if isinstance(input_details, Mapping) else 0
    reasoning = (
        output_details.get("reasoning_tokens", 0) if isinstance(output_details, Mapping) else 0
    )
    input_tokens = int(raw.get("input_tokens", 0) or 0)
    output_tokens = int(raw.get("output_tokens", 0) or 0)
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
        total_token_count=int(raw.get("total_tokens", input_tokens + output_tokens) or 0),
        cached_content_token_count=int(cached or 0),
        thoughts_token_count=int(reasoning or 0) or None,
    )


@dataclass(slots=True)
class _ResponseAccumulator:
    text: list[str] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    calls: dict[str, types.Part] = field(default_factory=dict)
    reasoning_items: dict[str, bytes] = field(default_factory=dict)
    usage: types.GenerateContentResponseUsageMetadata | None = None
    response_id: str | None = None
    model_version: str | None = None
    custom_metadata: dict[str, Any] = field(default_factory=dict)
    completed: bool = False

    def consume(self, event: Mapping[str, Any]) -> types.Part | None:
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self.text.append(delta)
                return types.Part.from_text(text=delta)
        if event_type == "response.reasoning_summary_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self.thoughts.append(delta)
                return types.Part(text=delta, thought=True)
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, Mapping):
                self._capture_item(item)
                if item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id")
                    return self.calls.get(str(call_id))
        if event_type == "response.completed":
            response = event.get("response")
            if isinstance(response, Mapping):
                completed = dict(response)
                if "openrouter_metadata" not in completed:
                    completed["openrouter_metadata"] = event.get("openrouter_metadata")
                self._capture_response(completed)
            self.completed = True
        if event_type in {"response.failed", "response.incomplete", "error"}:
            error = event.get("error") or event.get("response") or event
            raise RuntimeError(f"Codex response failed: {_json(error)}")
        return None

    def _capture_item(self, item: Mapping[str, Any]) -> None:
        item_type = item.get("type")
        if item_type == "function_call":
            part = _function_call_part(item)
            if part is not None and part.function_call is not None:
                self.calls[part.function_call.id or part.function_call.name or "unknown"] = part
        elif item_type == "reasoning":
            encrypted = item.get("encrypted_content")
            item_id = item.get("id")
            if isinstance(encrypted, str) and isinstance(item_id, str):
                self.reasoning_items[item_id] = encrypted.encode("utf-8")

    def _capture_response(self, response: Mapping[str, Any]) -> None:
        response_id = response.get("id")
        if isinstance(response_id, str):
            self.response_id = response_id
        model = response.get("model")
        if isinstance(model, str):
            self.model_version = model
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            cost = usage.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
                self.custom_metadata["provider_cost_usd"] = float(cost)
        router = response.get("openrouter_metadata")
        if isinstance(router, Mapping):
            for source, target in (
                ("requested", "requested_model"),
                ("strategy", "routing_strategy"),
                ("region", "routing_region"),
                ("attempt", "routing_attempt"),
            ):
                value = router.get(source)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    self.custom_metadata[target] = value
            endpoints = router.get("endpoints")
            available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
            if isinstance(available, list):
                selected = next(
                    (
                        endpoint
                        for endpoint in available
                        if isinstance(endpoint, Mapping) and endpoint.get("selected") is True
                    ),
                    None,
                )
                if isinstance(selected, Mapping):
                    provider = selected.get("provider")
                    if isinstance(provider, str):
                        self.custom_metadata["provider_name"] = provider
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                self._capture_item(item)
                if item.get("type") == "message" and not self.text:
                    content = item.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, Mapping) and block.get("type") == "output_text":
                                text = block.get("text")
                                if isinstance(text, str):
                                    self.text.append(text)
        self.usage = _usage(response)

    def final_response(self, model: str) -> LlmResponse:
        parts: list[types.Part] = []
        if self.thoughts:
            parts.append(types.Part(text="".join(self.thoughts), thought=True))
        for encrypted in self.reasoning_items.values():
            parts.append(types.Part(text="", thought=True, thought_signature=encrypted))
        if self.text:
            parts.append(types.Part.from_text(text="".join(self.text)))
        parts.extend(self.calls.values())
        return LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
            turn_complete=True,
            finish_reason=types.FinishReason.STOP,
            usage_metadata=self.usage,
            model_version=self.model_version or model,
            interaction_id=self.response_id,
            custom_metadata=self.custom_metadata or None,
        )


async def _iter_sse(response: httpx.Response) -> AsyncGenerator[dict[str, Any], None]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                payload = "\n".join(data)
                data.clear()
                if payload != "[DONE]":
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        yield parsed
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        parsed = json.loads("\n".join(data))
        if isinstance(parsed, dict):
            yield parsed


class CodexResponsesLlm(BaseLlm):
    """Stream ChatGPT subscription Codex responses through ADK's native model seam."""

    base_url: str = DEFAULT_CODEX_BASE_URL
    reasoning_effort: str | None = None
    retry_attempts: int = 3
    retry_initial_delay_seconds: float = 1
    retry_exponential_base: float = 2
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    originator: str = "skein"
    client_version: str | None = None

    _credentials: CodexCredentialManager = PrivateAttr()
    _client_factory: _AsyncClientFactory = PrivateAttr()

    def __init__(
        self,
        *,
        credential_manager: CodexCredentialManager,
        client_factory: _AsyncClientFactory | None = None,
        **data: Any,
    ) -> None:
        super().__init__(**data)
        self._credentials = credential_manager
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=30))
        )

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    def _headers(self, credential: CodexCredential) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {credential.access_token}",
            "chatgpt-account-id": credential.account_id,
            "originator": self.originator,
            "User-Agent": DEFAULT_CODEX_USER_AGENT,
            "OpenAI-Beta": "responses=experimental",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        if self.client_version:
            headers["version"] = self.client_version
        return headers

    @property
    def responses_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/codex/responses"):
            return base
        if base.endswith("/codex"):
            return f"{base}/responses"
        return f"{base}/codex/responses"

    async def _stream_events(self, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        forced_refresh = False
        for attempt in range(self.retry_attempts):
            credential = await asyncio.to_thread(
                self._credentials.resolve,
                force_refresh=forced_refresh,
            )
            forced_refresh = False
            async with self._client_factory() as client:
                try:
                    async with client.stream(
                        "POST",
                        self.responses_url,
                        headers=self._headers(credential),
                        json=body,
                    ) as response:
                        if response.status_code == 401 and attempt + 1 < self.retry_attempts:
                            forced_refresh = True
                            continue
                        if not response.is_success:
                            raw = (await response.aread()).decode("utf-8", errors="replace")
                            if (
                                response.status_code in self.retry_statuses
                                and attempt + 1 < self.retry_attempts
                            ):
                                await asyncio.sleep(
                                    self.retry_initial_delay_seconds
                                    * self.retry_exponential_base**attempt
                                )
                                continue
                            raise RuntimeError(
                                f"Codex request failed ({response.status_code}): "
                                + " ".join(raw.split())[:1000]
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
        raise RuntimeError("Codex request exhausted retries")

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        body = build_codex_request_body(
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
            raise RuntimeError("Codex stream ended before response.completed")
        final = accumulator.final_response(self.model)
        final.custom_metadata = {
            **(final.custom_metadata or {}),
            "provider_request_profile": request_profile,
        }
        yield final


__all__ = [
    "DEFAULT_CODEX_BASE_URL",
    "CodexResponsesLlm",
    "build_codex_request_body",
    "provider_request_profile",
]
