from __future__ import annotations

from google.adk.events import Event, EventActions
from google.genai import types

from harness.adapters.adk.runtime import AgUiEventType
from harness.adapters.adk.runtime.adk_mapper import AdkAgUiNormalizer, map_adk_event


def test_selected_skill_names_are_public_but_skill_bodies_remain_private() -> None:
    event = Event(author="workflow", actions=EventActions(state_delta={
        "selected_skill_names": ["python-checks"], "skill_context_text": "PRIVATE_BODY",
        "selected_skill_hashes": ["private-internal-hash"],
    }))
    mapped = map_adk_event(event, run_id="run", thread_id="thread")
    assert mapped[0].delta == [{"op": "add", "path": "/selected_skill_names", "value": ["python-checks"]}]
    assert "PRIVATE_BODY" not in mapped[0].model_dump_json()


def test_adk_text_event_maps_to_correlated_ag_ui_message_triplet() -> None:
    event = Event(
        id="event-text-1",
        invocation_id="invocation-1",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[types.Part(text="Use the public interface.")],
        ),
    )

    mapped = map_adk_event(event, run_id="run-1", thread_id="thread-1")

    assert [item.type for item in mapped] == [
        AgUiEventType.TEXT_MESSAGE_START,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_END,
    ]
    assert len({item.message_id for item in mapped}) == 1
    assert mapped[0].message_id is not None
    assert mapped[1].delta == "Use the public interface."
    assert all(item.run_id == "run-1" for item in mapped)
    assert all(item.thread_id == "thread-1" for item in mapped)


def test_adk_function_call_maps_to_canonical_ag_ui_tool_sequence() -> None:
    event = Event(
        id="event-tool-1",
        invocation_id="invocation-1",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call-1",
                        name="read",
                        args={"path": "README.md", "limit": 20},
                    )
                )
            ],
        ),
    )

    mapped = map_adk_event(event, run_id="run-1", thread_id="thread-1")

    assert [item.type for item in mapped] == [
        AgUiEventType.TOOL_CALL_START,
        AgUiEventType.TOOL_CALL_ARGS,
        AgUiEventType.TOOL_CALL_END,
    ]
    tool_call_ids = {item.tool_call_id for item in mapped}
    assert len(tool_call_ids) == 1
    assert next(iter(tool_call_ids)).startswith("tool-")
    assert mapped[0].tool_call_name == "read"
    assert mapped[1].delta == '{"limit":20,"path":"README.md"}'
    assert all(item.run_id == "run-1" for item in mapped)


def test_adk_state_delta_and_error_map_without_exposing_raw_adk_objects() -> None:
    state_event = Event(
        id="event-state-1",
        invocation_id="invocation-1",
        author="coding_workflow",
        actions=EventActions(state_delta={"task_route": "verify", "checkpoint_id": "checkpoint-1"}),
    )
    error_event = Event(
        id="event-error-1",
        invocation_id="invocation-1",
        author="coding_worker",
        error_code="MODEL_RATE_LIMIT",
        error_message="retry later",
    )

    state = map_adk_event(state_event, run_id="run-1", thread_id="thread-1")
    error = map_adk_event(error_event, run_id="run-1", thread_id="thread-1")

    assert len(state) == 1
    assert state[0].type == AgUiEventType.STATE_DELTA
    assert state[0].delta == [
        {"op": "add", "path": "/checkpoint_id", "value": "checkpoint-1"},
        {"op": "add", "path": "/task_route", "value": "verify"},
    ]
    assert len(error) == 1
    assert error[0].type == AgUiEventType.RUN_ERROR
    assert error[0].code == "MODEL_RATE_LIMIT"
    assert error[0].message == "retry later"
    assert "google.adk" not in state[0].model_dump_json()
    assert "google.adk" not in error[0].model_dump_json()


def test_adk_mapper_is_deterministic_and_ignores_empty_observational_events() -> None:
    text_event = Event(
        id="event-text-1",
        invocation_id="invocation-1",
        author="coding_worker",
        content=types.Content(role="model", parts=[types.Part(text="hello")]),
    )
    empty_event = Event(
        id="event-empty-1",
        invocation_id="invocation-1",
        author="coding_worker",
    )

    first = map_adk_event(text_event, run_id="run-1", thread_id="thread-1")
    second = map_adk_event(text_event, run_id="run-1", thread_id="thread-1")

    assert first == second
    assert map_adk_event(empty_event, run_id="run-1", thread_id="thread-1") == ()


def test_streaming_normalizer_opens_once_and_deduplicates_final_aggregate() -> None:
    normalizer = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1")
    first = Event(
        id="chunk-1",
        invocation_id="invocation-1",
        author="coding_worker",
        partial=True,
        content=types.Content(role="model", parts=[types.Part(text="hel")]),
    )
    second = Event(
        id="chunk-2",
        invocation_id="invocation-1",
        author="coding_worker",
        partial=True,
        content=types.Content(role="model", parts=[types.Part(text="lo")]),
    )
    final = Event(
        id="chunk-final",
        invocation_id="invocation-1",
        author="coding_worker",
        partial=False,
        content=types.Content(role="model", parts=[types.Part(text="hello")]),
    )

    mapped = normalizer.push(first) + normalizer.push(second) + normalizer.push(final)

    assert [item.type for item in mapped] == [
        AgUiEventType.TEXT_MESSAGE_START,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_END,
    ]
    assert [item.delta for item in mapped if item.delta is not None] == ["hel", "lo"]
    assert len({item.message_id for item in mapped}) == 1
    assert normalizer.finish() == ()


def test_normalizer_closes_text_before_tools_and_correlates_results() -> None:
    normalizer = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1")
    text = Event(
        id="text",
        author="coding_worker",
        partial=True,
        content=types.Content(role="model", parts=[types.Part(text="Checking")]),
    )
    tool = Event(
        id="tool",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call-1", name="read", args={"path": "README.md"}
                    )
                )
            ],
        ),
    )
    result = Event(
        id="result",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call-1", name="read", response={"status": "ok"}
                    )
                )
            ],
        ),
    )

    mapped = normalizer.push(text) + normalizer.push(tool) + normalizer.push(result)

    assert [item.type for item in mapped] == [
        AgUiEventType.TEXT_MESSAGE_START,
        AgUiEventType.TEXT_MESSAGE_CONTENT,
        AgUiEventType.TEXT_MESSAGE_END,
        AgUiEventType.TOOL_CALL_START,
        AgUiEventType.TOOL_CALL_ARGS,
        AgUiEventType.TOOL_CALL_END,
        AgUiEventType.TOOL_CALL_RESULT,
    ]
    assert mapped[-1].tool_call_id == mapped[-4].tool_call_id
    assert mapped[-1].content == '{"status":"ok"}'


def test_normalizer_scopes_sequentially_reused_provider_tool_call_ids() -> None:
    normalizer = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1")
    read_call = Event(
        id="read-call",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call_icn_0", name="read", args={"path": "README.md"}
                    )
                )
            ],
        ),
    )
    read_result = Event(
        id="read-result",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call_icn_0", name="read", response={"status": "ok"}
                    )
                )
            ],
        ),
    )
    bash_call = Event(
        id="bash-call",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call_icn_0",
                        name="bash",
                        args={"command": "git status --short"},
                    )
                )
            ],
        ),
    )
    bash_result = Event(
        id="bash-result",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call_icn_0", name="bash", response={"status": "blocked"}
                    )
                )
            ],
        ),
    )

    first_call = normalizer.push(read_call)
    first_result = normalizer.push(read_result)
    second_call = normalizer.push(bash_call)
    second_result = normalizer.push(bash_result)

    first_id = first_call[0].tool_call_id
    second_id = second_call[0].tool_call_id
    assert first_id is not None
    assert second_id is not None
    assert first_id != second_id
    assert first_result[0].tool_call_id == first_id
    assert second_result[0].tool_call_id == second_id
    assert all(item.tool_call_id == first_id for item in first_call)
    assert all(item.tool_call_id == second_id for item in second_call)
    assert len(first_id) <= 256
    assert len(second_id) <= 256
    assert normalizer.push(read_call) == ()
    assert normalizer.push(read_result) == ()


def test_normalizer_bounds_long_provider_tool_call_ids_deterministically() -> None:
    event = Event(
        id="long-call",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="provider-" + "x" * 1_000,
                        name="read",
                        args={"path": "README.md"},
                    )
                )
            ],
        ),
    )

    first = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1").push(event)
    second = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1").push(event)

    assert first[0].tool_call_id == second[0].tool_call_id
    assert first[0].tool_call_id is not None
    assert len(first[0].tool_call_id) <= 256


def test_normalizer_filters_private_state_user_content_and_reasoning() -> None:
    normalizer = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1")
    user = Event(
        id="user",
        author="user",
        content=types.Content(role="user", parts=[types.Part(text="private prompt")]),
    )
    state = Event(
        id="state",
        author="coding_workflow",
        actions=EventActions(
            state_delta={
                "task_route": "verify",
                "task_ledger": {"goal": "do not publish"},
                "steering_packet_message_ids": ["private-message"],
            }
        ),
        content=types.Content(
            role="model",
            parts=[types.Part(text="hidden reasoning", thought=True)],
        ),
    )

    assert normalizer.push(user) == ()
    mapped = normalizer.push(state)

    assert len(mapped) == 1
    assert mapped[0].type == AgUiEventType.STATE_DELTA
    assert mapped[0].delta == [{"op": "add", "path": "/task_route", "value": "verify"}]
    serialized = mapped[0].model_dump_json()
    assert "task_ledger" not in serialized
    assert "private-message" not in serialized
    assert "hidden reasoning" not in serialized


def test_normalizer_redacts_public_text_and_rejects_conflicting_replay() -> None:
    normalizer = AdkAgUiNormalizer(run_id="run-1", thread_id="thread-1")
    secret_text = Event(
        id="secret",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[types.Part(text="api_key=abcdefghijklmnopqrstuvwxyz123456")],
        ),
    )
    first_call = Event(
        id="tool-1",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call-1", name="read", args={"path": "one.py"}
                    )
                )
            ],
        ),
    )
    changed_call = Event(
        id="tool-2",
        author="coding_worker",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call-1", name="read", args={"path": "two.py"}
                    )
                )
            ],
        ),
    )

    text_events = normalizer.push(secret_text)
    assert text_events[1].delta == "api_key=<redacted>"
    normalizer.push(first_call)
    try:
        normalizer.push(changed_call)
    except ValueError as error:
        assert "reused with different content" in str(error)
    else:
        raise AssertionError("conflicting tool-call replay must fail closed")
