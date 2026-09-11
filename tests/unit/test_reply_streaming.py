from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agent.streaming import PublicReplies, PublicReplyStream
from harness.core.models.agent_step import AgentStep
from harness.core.orchestration.reply import parse_reply, reply_header
from harness.core.orchestration.runtime import parse_agent_step
from harness.execution.safety import SecretRedactor


def response(text: str, *, partial: bool = True) -> LlmResponse:
    return LlmResponse(partial=partial, content=types.Content(parts=[types.Part(text=text)]))


async def allow(step: AgentStep) -> bool:
    return step.status == "answer" and not step.completion_claims


def test_header_is_complete_typed_control_and_body_is_only_markdown() -> None:
    body = '## Reply\n```json\n{"status":"done"}\n```\n'
    value = '{"status":"answer"}\n' + body
    assert parse_agent_step(value) == AgentStep(status="answer", message=body)
    assert parse_reply('{"status":"answer","message":"legacy"}\n') is None
    for line in ('{"status":"answer","status":"done"}', '{"status":"answer","unknown":1}',
                 '{"status":"answer","message":"hidden"}', '[]'):
        with pytest.raises(ValueError):
            reply_header(line)
    with pytest.raises(ValueError):
        parse_reply('{"status":"answer"}\n' + "x" * 16_001)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "Hello! Here is a **streamed reply**.\nSecond line: 中文 🐍.\n",
    "Before ghp_" + "a" * 30 + " after.\n",
    "Before AKIAABCDEFGHIJKLMNOP after.\n",
    "Before eyJabcdefghijk.abcdefghijk.abcdefghijk after.\n",
    "Before password\n=\nabcdefghijk after.\n",
    "Before Authorization: Bearer abcdefghijk after.\n",
    "Before -----BEGIN PRIVATE KEY-----\nsensitive\n-----END PRIVATE KEY----- after.\n",
    "Before known\nmultiline\ncredential after.\n",
])
async def test_character_chunks_are_redacted_before_publication_and_aggregate_deduplicates(body) -> None:
    redactor = SecretRedactor(known_secrets=("known\nmultiline\ncredential",))
    stream = PublicReplyStream(redactor)
    stream.prepare(allow)
    raw = '{"status":"answer"}\n' + body
    deltas = []
    for char in raw:
        chunk = response(char)
        await stream.after_model(None, chunk)
        deltas.append((chunk.custom_metadata or {}).get("coding.public_delta", ""))
        assert redactor.redact_text(body).startswith("".join(deltas))
    assert any(deltas), "must publish before the final aggregate"
    final = response(raw, partial=False)
    await stream.after_model(None, final)
    deltas.append((final.custom_metadata or {}).get("coding.public_delta", ""))
    assert "".join(deltas) == redactor.redact_text(body)
    assert stream.finish(parse_agent_step(raw)).message == redactor.redact_text(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [
    {"status": "verify"}, {"status": "done"}, {"status": "blocked"},
    {"status": "answer", "completion_claims": [{"criterion_id": "criterion-work"}]},
])
async def test_ineligible_control_does_not_stream(header) -> None:
    stream = PublicReplyStream(SecretRedactor())
    stream.prepare(allow)
    raw = json.dumps(header) + "\nA completion claim must remain hidden.\n"
    for chunk in (response(raw), response(raw, partial=False)):
        await stream.after_model(None, chunk)
        assert not chunk.custom_metadata
    assert not stream.started


@pytest.mark.asyncio
async def test_stream_cannot_retract_prefix_or_run_tools_or_change_result() -> None:
    stream = PublicReplyStream(SecretRedactor())
    stream.prepare(allow)
    await stream.after_model(None, response('{"status":"answer"}\nHello there '))
    assert stream.started
    with pytest.raises(ValueError, match="tool call"):
        stream.guard_tool()
    with pytest.raises(ValueError, match="tool call"):
        await stream.before_model(None, None)
    with pytest.raises(ValueError, match="disagrees"):
        await stream.after_model(None, response('{"status":"done"}\nNew text', partial=False))
    with pytest.raises(ValueError, match="disagrees"):
        stream.finish(AgentStep(status="done"))
    with pytest.raises(ValueError, match="tool call"):
        await stream.after_model(None, LlmResponse(content=types.Content(parts=[
            types.Part(function_call=types.FunctionCall(name="write", args={}))])))
    stream.prepare(allow)
    stream.guard_tool()
    await stream.before_model(None, None)


@pytest.mark.asyncio
async def test_legacy_json_thoughts_and_mixed_tool_response_stay_private() -> None:
    stream = PublicReplyStream(SecretRedactor())
    stream.prepare(allow)
    for chunk in (
        response('{"status":"answer","message":"Buffered legacy reply"}'),
        LlmResponse(partial=True, content=types.Content(parts=[types.Part(text="private thought", thought=True)])),
        LlmResponse(content=types.Content(parts=[
            types.Part(text='{"status":"answer"}\nDo not publish this mixed response'),
            types.Part(function_call=types.FunctionCall(name="read", args={"path": "README.md"})),
        ])),
    ):
        await stream.after_model(None, chunk)
        assert not chunk.custom_metadata
    assert not stream.started


@pytest.mark.asyncio
async def test_concurrent_invocations_do_not_share_reply_buffers_or_authorization() -> None:
    replies = PublicReplies(SecretRedactor())
    first, second = SimpleNamespace(invocation_id="first"), SimpleNamespace(invocation_id="second")
    replies.for_invocation("first").prepare(allow)
    async def deny(_step):
        return False
    replies.for_invocation("second").prepare(deny)
    await replies.before_model(first, None)
    await replies.before_model(second, None)
    one = response('{"status":"answer"}\nFirst reply ')
    two = response('{"status":"answer"}\nPrivate second reply ')
    await replies.after_model(first, one)
    await replies.after_model(second, two)
    assert one.custom_metadata == {"coding.public_delta": "First reply "}
    assert two.custom_metadata is None
    with pytest.raises(ValueError, match="tool call"):
        replies.guard_tool(first)
    replies.guard_tool(second)
    replies.release("first")
    replies.guard_tool(first)
    assert not replies.for_invocation("first").started
    assert replies.for_invocation("second").authorize is deny
