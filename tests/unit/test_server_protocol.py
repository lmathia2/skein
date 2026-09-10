from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from harness.adapters.adk.runtime import (
    PROTOCOL_VERSION,
    AckMessage,
    AgUiEvent,
    AgUiEventType,
    AttachTaskMessage,
    HelloMessage,
    ServerEnvelope,
    ServerHello,
    StartTaskMessage,
    SteerTaskMessage,
    parse_client_message,
)
from harness.adapters.adk.runtime.protocol import (
    ControlResultMessage,
    PongMessage,
    ServerErrorMessage,
    SessionRequestMessage,
    SessionResultMessage,
    TaskAcceptedMessage,
    parse_server_message,
)
from harness.core.agent import (
    HarnessDescriptor,
    ModelReadiness,
    PublicModelStatus,
    RuntimeCapability,
)


def _descriptor() -> HarnessDescriptor:
    return HarnessDescriptor(
        implementation="skein_v1",
        display_name="Skein",
        capabilities=frozenset(
            {
                RuntimeCapability.STREAMING,
                RuntimeCapability.STEERING,
                RuntimeCapability.REPLAY,
            }
        ),
        protocol_versions=(PROTOCOL_VERSION,),
    )


def test_parser_discriminates_json_and_mapping_client_messages() -> None:
    hello = parse_client_message(
        {"type": "client.hello", "protocol_versions": [1], "client_name": "test-tui"}
    )
    start = parse_client_message(
        json.dumps(
            {
                "type": "task.start",
                "protocol_version": 1,
                "request_id": "request-1",
                "idempotency_key": "start-1",
                "input": "Fix the parser",
            }
        )
    )

    assert isinstance(hello, HelloMessage)
    assert hello.protocol_versions == (1,)
    assert isinstance(start, StartTaskMessage)
    assert start.input == "Fix the parser"


def test_session_controls_round_trip_and_validate_operation_parameters() -> None:
    request = SessionRequestMessage(type="session.request", operation="follow_up",
        request_id="queue-key", thread_id="thread", content="Next task")
    assert parse_client_message(request.model_dump_json()) == request
    response = SessionResultMessage(request_id="queue-key", operation="follow_up", data={"queue": []})
    assert parse_server_message(response.model_dump_json()) == response
    for invalid in [
        {"operation": "follow_up", "thread_id": "thread"},
        {"operation": "list", "thread_id": "thread"},
        {"operation": "remove_follow_up", "thread_id": "thread"},
        {"operation": "state", "thread_id": "thread", "content": "unexpected"},
        {"operation": "follow_up", "thread_id": "thread", "content": "🙂" * 12_501},
        {"operation": "events", "thread_id": "thread"},
        {"operation": "get", "thread_id": "thread", "run_id": "run"},
        {"operation": "events", "thread_id": "thread", "run_id": "run", "after_sequence": -1},
        {"operation": "events", "thread_id": "thread", "run_id": "run", "after_sequence": 3, "high_water_sequence": 2},
    ]:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "session.request", "request_id": "key", **invalid})

    events = SessionRequestMessage(type="session.request", operation="events", request_id="history",
        thread_id="thread", run_id="run", after_sequence=2, high_water_sequence=100)
    assert parse_client_message(events.model_dump_json()) == events


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "unknown", "protocol_version": 1},
        {
            "type": "task.start",
            "protocol_version": 2,
            "request_id": "request-1",
            "idempotency_key": "start-1",
            "input": "Fix it",
        },
        {
            "type": "task.attach",
            "protocol_version": 1,
            "run_id": "run-1",
            "after_sequence": 0,
            "unexpected": True,
        },
    ],
)
def test_parser_fails_closed_for_unknown_messages_versions_and_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(payload)


def test_replay_and_ack_cursors_are_nonnegative() -> None:
    attach = parse_client_message(
        {
            "type": "task.attach",
            "protocol_version": 1,
            "run_id": "run-1",
            "after_sequence": 17,
        }
    )
    ack = parse_client_message(
        {
            "type": "events.ack",
            "protocol_version": 1,
            "run_id": "run-1",
            "through_sequence": 17,
        }
    )

    assert isinstance(attach, AttachTaskMessage)
    assert isinstance(ack, AckMessage)
    assert attach.after_sequence == ack.through_sequence == 17

    with pytest.raises(ValidationError):
        parse_client_message(
            {
                "type": "task.attach",
                "protocol_version": 1,
                "run_id": "run-1",
                "after_sequence": -1,
            }
        )


def test_steering_limit_is_utf8_bytes_and_idempotency_is_required() -> None:
    message = parse_client_message(
        {
            "type": "task.steer",
            "protocol_version": 1,
            "run_id": "run-1",
            "content": "Keep the API stable",
            "idempotency_key": "steer-1",
        }
    )
    assert isinstance(message, SteerTaskMessage)

    with pytest.raises(ValidationError, match="4096 UTF-8 bytes"):
        parse_client_message(
            {
                "type": "task.steer",
                "protocol_version": 1,
                "run_id": "run-1",
                "content": "é" * 3_000,
                "idempotency_key": "steer-2",
            }
        )
    with pytest.raises(ValidationError, match="idempotency_key"):
        parse_client_message(
            {
                "type": "task.steer",
                "protocol_version": 1,
                "run_id": "run-1",
                "content": "Keep the API stable",
            }
        )


def test_ag_ui_text_and_tool_deltas_require_correlation_ids() -> None:
    text = AgUiEvent(
        type=AgUiEventType.TEXT_MESSAGE_CONTENT,
        run_id="run-1",
        message_id="message-1",
        delta="hello",
    )
    tool = AgUiEvent(
        type=AgUiEventType.TOOL_CALL_ARGS,
        run_id="run-1",
        tool_call_id="tool-1",
        delta='{"path":"README.md"}',
    )

    assert text.message_id == "message-1"
    assert tool.tool_call_id == "tool-1"

    with pytest.raises(ValidationError, match="message_id and delta"):
        AgUiEvent(type=AgUiEventType.TEXT_MESSAGE_CONTENT, delta="orphan")
    with pytest.raises(ValidationError, match="tool_call_id and delta"):
        AgUiEvent(type=AgUiEventType.TOOL_CALL_ARGS, delta="{}")


def test_ag_ui_standard_event_shapes_fail_closed() -> None:
    with pytest.raises(ValidationError, match="thread_id and run_id"):
        AgUiEvent(type=AgUiEventType.RUN_STARTED, run_id="run-1")
    with pytest.raises(ValidationError, match="message_id"):
        AgUiEvent(type=AgUiEventType.TEXT_MESSAGE_START, role="assistant")
    with pytest.raises(ValidationError, match="role cannot be tool"):
        AgUiEvent(
            type=AgUiEventType.TEXT_MESSAGE_START,
            message_id="message-1",
            role="tool",
        )
    with pytest.raises(ValidationError, match="message_id, tool_call_id, and content"):
        AgUiEvent(
            type=AgUiEventType.TOOL_CALL_RESULT,
            message_id="message-1",
            tool_call_id="call-1",
        )
    with pytest.raises(ValidationError, match="RFC 6902"):
        AgUiEvent(type=AgUiEventType.STATE_DELTA, delta="not-a-json-patch")

    result = AgUiEvent(
        type=AgUiEventType.TOOL_CALL_RESULT,
        message_id="message-1",
        tool_call_id="call-1",
        content='{"ok":true}',
        role="tool",
    )
    assert result.role == "tool"
    with pytest.raises(ValidationError, match="role must be tool"):
        AgUiEvent(
            type=AgUiEventType.TOOL_CALL_RESULT,
            message_id="message-1",
            tool_call_id="call-1",
            content="ok",
            role="assistant",
        )


def test_ag_ui_text_start_allows_canonical_default_role() -> None:
    event = AgUiEvent(
        type=AgUiEventType.TEXT_MESSAGE_START,
        message_id="message-1",
    )

    assert event.role is None
    assert json.loads(event.model_dump_json()) == {
        "type": "TEXT_MESSAGE_START",
        "messageId": "message-1",
    }


def test_coding_specific_events_use_a_namespaced_custom_event() -> None:
    checkpoint = AgUiEvent(
        type=AgUiEventType.CUSTOM,
        run_id="run-1",
        name="coding.checkpoint.created",
        value={"checkpoint_id": "checkpoint-1"},
    )
    assert checkpoint.name == "coding.checkpoint.created"

    with pytest.raises(ValidationError, match=r"coding\."):
        AgUiEvent(
            type=AgUiEventType.CUSTOM,
            name="checkpoint.created",
            value={"checkpoint_id": "checkpoint-1"},
        )
    with pytest.raises(ValidationError, match="require a value"):
        AgUiEvent(type=AgUiEventType.CUSTOM, name="coding.checkpoint.created")


def test_server_hello_negotiates_capabilities_without_tui_knowledge() -> None:
    hello = ServerHello(
        harness=_descriptor(),
        coding_model=PublicModelStatus(
            provider="openai_codex",
            name="gpt-5.3-codex-spark",
            readiness=ModelReadiness.AUTHENTICATION_REQUIRED,
        ),
    )

    assert hello.protocol_version == PROTOCOL_VERSION
    assert RuntimeCapability.STEERING in hello.harness.capabilities
    assert hello.coding_model is not None
    assert hello.coding_model.readiness == ModelReadiness.AUTHENTICATION_REQUIRED
    assert "tui" not in hello.model_dump_json()


def test_server_envelope_round_trips_normalized_ag_ui_event() -> None:
    envelope = ServerEnvelope(
        sequence=23,
        run_id="run-1",
        session_id="session-1",
        invocation_id="invocation-1",
        durable=True,
        event=AgUiEvent(
            type=AgUiEventType.STATE_SNAPSHOT,
            run_id="run-1",
            snapshot={"phase": "verify"},
        ),
    )

    restored = ServerEnvelope.model_validate_json(envelope.model_dump_json())

    assert restored == envelope
    assert restored.sequence == 23
    assert restored.event.type == AgUiEventType.STATE_SNAPSHOT


def test_run_started_model_status_metadata_round_trips_without_secrets() -> None:
    event = AgUiEvent(
        type=AgUiEventType.RUN_STARTED,
        thread_id="thread-1",
        run_id="run-1",
        metadata={
            "coding.model": {
                "role": "coding",
                "provider": "openai_compatible",
                "name": "qwen-local",
                "readiness": "adapter_initialized",
            }
        },
    )

    restored = AgUiEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    assert "api_key" not in restored.model_dump_json()
    assert "base_url" not in restored.model_dump_json()


def test_ag_ui_wire_payload_uses_canonical_camel_case_fields() -> None:
    envelope = ServerEnvelope(
        sequence=1,
        run_id="run-1",
        durable=True,
        event=AgUiEvent(
            type=AgUiEventType.TOOL_CALL_START,
            tool_call_id="call-1",
            tool_call_name="read",
        ),
    )

    payload = json.loads(envelope.model_dump_json())

    assert payload["event"]["toolCallId"] == "call-1"
    assert payload["event"]["toolCallName"] == "read"
    assert "tool_call_id" not in payload["event"]


def test_ag_ui_envelope_has_deterministic_sparse_golden_wire_json() -> None:
    envelope = ServerEnvelope(
        sequence=1,
        run_id="run-1",
        durable=True,
        event=AgUiEvent(
            type=AgUiEventType.TOOL_CALL_START,
            tool_call_id="call-1",
            tool_call_name="read",
        ),
    )

    assert envelope.model_dump_json() == (
        '{"type":"event","protocol_version":1,"sequence":1,'
        '"run_id":"run-1","durable":true,"event":'
        '{"type":"TOOL_CALL_START","toolCallId":"call-1",'
        '"toolCallName":"read"}}'
    )
    assert ServerEnvelope.model_validate_json(envelope.model_dump_json()) == envelope


def test_ag_ui_metadata_must_be_omitted_instead_of_null() -> None:
    with pytest.raises(ValidationError, match="must be omitted rather than null"):
        AgUiEvent.model_validate(
            {"type": "TEXT_MESSAGE_END", "messageId": "message-1", "metadata": None}
        )


@pytest.mark.parametrize(
    ("message", "expected_type"),
    [
        (
            TaskAcceptedMessage(
                request_id="request-1",
                run_id="run-1",
                thread_id="thread-1",
                created=True,
            ),
            TaskAcceptedMessage,
        ),
        (
            ControlResultMessage(
                operation="steer",
                run_id="run-1",
                accepted=True,
                command_id="steer-1",
            ),
            ControlResultMessage,
        ),
        (PongMessage(nonce="ping-1"), PongMessage),
        (
            ServerErrorMessage(code="bad_request", message="Invalid control message"),
            ServerErrorMessage,
        ),
    ],
)
def test_server_response_messages_round_trip_through_discriminator(
    message: TaskAcceptedMessage | ControlResultMessage | PongMessage | ServerErrorMessage,
    expected_type: type[object],
) -> None:
    payload = message.model_dump_json()

    assert isinstance(parse_server_message(payload), expected_type)
    assert None not in json.loads(payload).values()


def test_server_error_omits_absent_correlations_from_golden_wire_json() -> None:
    error = ServerErrorMessage(
        code="run_not_found",
        message="No such run",
        retryable=False,
    )

    assert error.model_dump_json() == (
        '{"type":"error","protocol_version":1,"code":"run_not_found",'
        '"message":"No such run","retryable":false}'
    )
