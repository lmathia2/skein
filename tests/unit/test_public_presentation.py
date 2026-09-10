from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from app.agent.presentation import conversation_history, message_event, result_events
from harness.adapters.adk.runtime.adk_mapper import AdkAgUiNormalizer
from harness.adapters.adk.runtime.protocol import AgUiEventType
from harness.core.context import estimate_tokens


def test_structured_worker_partial_and_final_control_never_become_public_text() -> None:
    mapper = AdkAgUiNormalizer(
        run_id="run", thread_id="thread", explicit_public_messages=True
    )
    for partial in (True, False):
        assert mapper.push(Event(
            author="coding_worker", partial=partial,
            content=types.Content(parts=[types.Part(text='{"status":"done"}')]),
            output={"private": "ledger"} if not partial else None,
        )) == ()
    public = mapper.push(message_event("Verified reply"))
    assert [event.type for event in public] == [
        AgUiEventType.TEXT_MESSAGE_START,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_END,
    ]
    assert public[1].delta == "Verified reply"


def test_public_result_does_not_duplicate_reply_or_publish_diagnostics() -> None:
    mapper = AdkAgUiNormalizer(
        run_id="run", thread_id="thread", explicit_public_messages=True
    )
    events = result_events({
        "status": "complete", "message": "Updated parser.",
        "verification": {"passed": True, "internal": "hidden"},
        "metrics": {"hidden": True}, "changed_paths": ["parser.py"],
    })
    public = [item for event in events for item in mapper.push(event)]
    assert sum(event.type == AgUiEventType.TEXT_MESSAGE_CONTENT for event in public) == 1
    assert public[-1].value == {
        "status": "complete", "verified": True, "changed_paths": ["parser.py"],
    }


def test_blocked_result_publishes_question_not_model_completion_claim() -> None:
    message, _ = result_events({
        "status": "blocked", "message": "All done", "questions": ["Which file?"],
    })
    assert message.content.parts[0].text == "Which file?"


def test_public_messages_keep_streaming_deduplication() -> None:
    mapper = AdkAgUiNormalizer(
        run_id="run", thread_id="thread", explicit_public_messages=True
    )
    first = message_event("Hello")
    first.partial = True
    second = message_event("Hello there")
    assert [event.delta for event in (*mapper.push(first), *mapper.push(second))
            if event.type == AgUiEventType.TEXT_MESSAGE_CONTENT] == ["Hello", " there"]


def test_private_worker_still_exposes_correlated_tools() -> None:
    mapper = AdkAgUiNormalizer(
        run_id="run", thread_id="thread", explicit_public_messages=True
    )
    public = mapper.push(Event(author="coding_worker", content=types.Content(parts=[
        types.Part(text='{"status":"continue"}'),
        types.Part(function_call=types.FunctionCall(id="call1", name="read", args={"path": "README.md"})),
    ])))
    assert [event.type for event in public] == [
        AgUiEventType.TOOL_CALL_START, AgUiEventType.TOOL_CALL_ARGS,
        AgUiEventType.TOOL_CALL_END,
    ]


def test_conversation_history_is_bounded_and_excludes_internal_and_current_messages() -> None:
    events = [
        Event(author="user", invocation_id="old", content=types.Content(parts=[types.Part(text="Remember 731")])),
        Event(author="user", invocation_id="old", isolation_scope="private", content=types.Content(parts=[types.Part(text="PRIVATE_PACKET")])),
        message_event("Public response").model_copy(update={"invocation_id": "old"}),
        Event(author="user", invocation_id="current", content=types.Content(parts=[types.Part(text="CURRENT_INPUT")])),
    ]
    history = conversation_history(events, invocation_id="current", max_tokens=100)
    assert "Remember 731" in history and "Public response" in history
    assert "PRIVATE_PACKET" not in history and "CURRENT_INPUT" not in history
    for limit in (0, 1, 2, 4, 5, 20):
        assert estimate_tokens(conversation_history(events, invocation_id="current", max_tokens=limit)) <= limit
