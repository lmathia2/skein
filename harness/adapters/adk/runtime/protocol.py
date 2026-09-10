"""Versioned WebSocket controls and AG-UI-compatible server events."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

from harness.adapters.providers.controls import ProviderControlRequest
from harness.core.agent import HarnessDescriptor, PublicModelStatus

PROTOCOL_VERSION = 1


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SparseWireModel(FrozenModel):
    """Serialize absent optional fields as absent rather than explicit JSON nulls."""

    @model_serializer(mode="wrap")
    def serialize_without_none(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        serialized = handler(self)
        if not isinstance(serialized, dict):  # pragma: no cover - Pydantic contract
            raise TypeError("protocol models must serialize to JSON objects")
        return {key: value for key, value in serialized.items() if value is not None}


class HelloMessage(FrozenModel):
    type: Literal["client.hello"]
    protocol_versions: tuple[int, ...] = Field(min_length=1)
    client_name: str = Field(min_length=1, max_length=128)


class StartTaskMessage(FrozenModel):
    type: Literal["task.start"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    input: str = Field(min_length=1, max_length=50_000)
    thread_id: str | None = Field(default=None, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict)
    source_run_ids: tuple[str, ...] = Field(default=(), max_length=100)


class AttachTaskMessage(FrozenModel):
    type: Literal["task.attach"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    after_sequence: int = Field(default=0, ge=0)


class SteerTaskMessage(FrozenModel):
    type: Literal["task.steer"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-100, le=100)
    idempotency_key: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_content_bytes(self) -> SteerTaskMessage:
        if len(self.content.encode("utf-8")) > 4_096:
            raise ValueError("steering content exceeds 4096 UTF-8 bytes")
        return self


class PauseTaskMessage(FrozenModel):
    type: Literal["task.pause"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)


class CancelTaskMessage(FrozenModel):
    type: Literal["task.cancel"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)


class AckMessage(FrozenModel):
    type: Literal["events.ack"]
    protocol_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=256)
    through_sequence: int = Field(ge=0)


class PingMessage(FrozenModel):
    type: Literal["ping"]
    protocol_version: Literal[1] = 1
    nonce: str = Field(min_length=1, max_length=256)


class SessionRequestMessage(FrozenModel):
    """Authenticated session controls; request_id is also the mutation retry key."""

    type: Literal["session.request"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    operation: Literal["list", "get", "state", "events", "follow_up", "remove_follow_up", "continue"]
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    item_id: str | None = Field(default=None, min_length=1, max_length=256)
    before_run_id: str | None = Field(default=None, min_length=1, max_length=256)
    after_run_id: str | None = Field(default=None, min_length=1, max_length=256)
    run_id: str | None = Field(default=None, min_length=1, max_length=256)
    after_sequence: int | None = Field(default=None, ge=0)
    high_water_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_operation(self) -> SessionRequestMessage:
        required = {
            "list": set(), "get": {"thread_id"}, "state": {"thread_id"}, "continue": {"thread_id"},
            "events": {"thread_id", "run_id"},
            "follow_up": {"thread_id", "content"}, "remove_follow_up": {"thread_id", "item_id"},
        }[self.operation]
        supplied = {key for key in ("thread_id", "content", "item_id", "before_run_id", "after_run_id", "run_id", "after_sequence", "high_water_sequence") if getattr(self, key) is not None}
        optional = {"before_run_id"} if self.operation == "get" else {"after_run_id"} if self.operation == "state" else {"after_sequence", "high_water_sequence"} if self.operation == "events" else set()
        if not required <= supplied or supplied - required - optional:
            raise ValueError("invalid session operation parameters")
        if self.content is not None and len(self.content.encode("utf-8")) > 50_000:
            raise ValueError("follow-up exceeds 50000 UTF-8 bytes")
        if self.high_water_sequence is not None and (self.after_sequence or 0) > self.high_water_sequence:
            raise ValueError("event cursor exceeds snapshot boundary")
        return self


class ModelRequestMessage(FrozenModel):
    type: Literal["model.request"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=256)
    operation: Literal["status", "catalog", "select"]
    provider: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=128)
    persist: bool = False

    @model_validator(mode="after")
    def validate_operation(self) -> ModelRequestMessage:
        if self.operation == "select":
            if not self.provider or not self.name:
                raise ValueError("select requires provider and name")
        elif self.provider is not None or self.name is not None or self.persist:
            raise ValueError("model identity and persist are only valid for select")
        return self


class ProviderRequestMessage(ProviderControlRequest):
    type: Literal["provider.request"]
    protocol_version: Literal[1] = 1


class ResourceRequestMessage(FrozenModel):
    type: Literal["resource.request"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    operation: Literal["list"] = "list"


class ApprovalRequestMessage(FrozenModel):
    type: Literal["approval.request"]
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    operation: Literal["list", "decide"]
    approval_id: str | None = Field(default=None, min_length=1, max_length=256)
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "denied"] | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ApprovalRequestMessage:
        fields = (self.approval_id, self.fingerprint, self.decision)
        if (self.operation == "decide" and any(value is None for value in fields)) or (self.operation == "list" and any(value is not None for value in fields)):
            raise ValueError("decide requires approval_id, fingerprint and decision; list accepts none")
        return self


ClientMessage = Annotated[
    HelloMessage
    | StartTaskMessage
    | AttachTaskMessage
    | SteerTaskMessage
    | PauseTaskMessage
    | CancelTaskMessage
    | AckMessage
    | PingMessage
    | SessionRequestMessage
    | ProviderRequestMessage
    | ModelRequestMessage
    | ResourceRequestMessage
    | ApprovalRequestMessage,
    # Session operations extend v1 by capability; old clients never receive their replies.
    Field(discriminator="type"),
]
_CLIENT_MESSAGE_ADAPTER = TypeAdapter(ClientMessage)


def parse_client_message(value: str | bytes | dict[str, object]) -> ClientMessage:
    if isinstance(value, (str, bytes)):
        return _CLIENT_MESSAGE_ADAPTER.validate_json(value)
    return _CLIENT_MESSAGE_ADAPTER.validate_python(value)


class AgUiEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    CUSTOM = "CUSTOM"


class AgUiEvent(SparseWireModel):
    """Strict AG-UI wire event; raw provider and ADK objects never cross it."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    type: AgUiEventType
    timestamp: int | None = Field(default=None, ge=0)
    metadata: dict[str, object] | None = None
    thread_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    message_id: str | None = Field(default=None, max_length=256)
    role: Literal["developer", "system", "assistant", "user", "tool"] | None = None
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_call_name: str | None = Field(default=None, max_length=128)
    delta: str | list[dict[str, object]] | None = None
    content: str | None = None
    name: str | None = Field(default=None, max_length=256)
    value: object | None = None
    snapshot: dict[str, object] | None = None
    message: str | None = None
    code: str | None = Field(default=None, max_length=256)
    result: object | None = None
    step_name: str | None = Field(default=None, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_metadata(cls, value: object) -> object:
        if isinstance(value, dict) and "metadata" in value and value["metadata"] is None:
            raise ValueError("AG-UI metadata must be omitted rather than null")
        return value

    @model_validator(mode="after")
    def validate_event_shape(self) -> AgUiEvent:
        if self.type in {
            AgUiEventType.RUN_STARTED,
            AgUiEventType.RUN_FINISHED,
        } and (self.thread_id is None or self.run_id is None):
            raise ValueError("run lifecycle events require thread_id and run_id")
        if self.type == AgUiEventType.RUN_ERROR and self.message is None:
            raise ValueError("run errors require message")
        if self.type in {
            AgUiEventType.STEP_STARTED,
            AgUiEventType.STEP_FINISHED,
        } and self.step_name is None:
            raise ValueError("step events require step_name")
        if self.type == AgUiEventType.TEXT_MESSAGE_START:
            if self.message_id is None:
                raise ValueError("text message start requires message_id")
            if self.role == "tool":
                raise ValueError("text message start role cannot be tool")
        if self.type == AgUiEventType.TEXT_MESSAGE_CONTENT and (
            self.message_id is None or not isinstance(self.delta, str)
        ):
            raise ValueError("text message content requires message_id and delta")
        if self.type == AgUiEventType.TEXT_MESSAGE_END and self.message_id is None:
            raise ValueError("text message end requires message_id")
        if self.type == AgUiEventType.TOOL_CALL_START and (
            self.tool_call_id is None or self.tool_call_name is None
        ):
            raise ValueError("tool call start requires tool_call_id and tool_call_name")
        if self.type == AgUiEventType.TOOL_CALL_ARGS and (
            self.tool_call_id is None or not isinstance(self.delta, str)
        ):
            raise ValueError("tool call args require tool_call_id and delta")
        if self.type == AgUiEventType.TOOL_CALL_END and self.tool_call_id is None:
            raise ValueError("tool call end requires tool_call_id")
        if self.type == AgUiEventType.TOOL_CALL_RESULT and (
            self.message_id is None
            or self.tool_call_id is None
            or self.content is None
        ):
            raise ValueError(
                "tool call results require message_id, tool_call_id, and content"
            )
        if self.type == AgUiEventType.TOOL_CALL_RESULT and self.role not in {None, "tool"}:
            raise ValueError("tool call result role must be tool when provided")
        if self.type == AgUiEventType.STATE_SNAPSHOT and self.snapshot is None:
            raise ValueError("state snapshot events require snapshot")
        if self.type == AgUiEventType.STATE_DELTA and not isinstance(self.delta, list):
            raise ValueError("state delta events require an RFC 6902 delta list")
        if self.type == AgUiEventType.CUSTOM:
            if not self.name or not self.name.startswith("coding."):
                raise ValueError("custom event names must use the coding. namespace")
            if self.value is None:
                raise ValueError("custom events require a value")
        return self


class ServerHello(FrozenModel):
    type: Literal["server.hello"] = "server.hello"
    protocol_version: Literal[1] = 1
    harness: HarnessDescriptor
    coding_model: PublicModelStatus | None = None


class ServerEnvelope(SparseWireModel):
    type: Literal["event"] = "event"
    protocol_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, max_length=256)
    invocation_id: str | None = Field(default=None, max_length=256)
    durable: bool
    event: AgUiEvent


class TaskAcceptedMessage(SparseWireModel):
    type: Literal["task.accepted"] = "task.accepted"
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=256)
    created: bool


class ControlResultMessage(SparseWireModel):
    type: Literal["control.result"] = "control.result"
    protocol_version: Literal[1] = 1
    operation: Literal["steer", "pause", "cancel"]
    run_id: str = Field(min_length=1, max_length=256)
    accepted: bool
    command_id: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=2_048)


class PongMessage(SparseWireModel):
    type: Literal["pong"] = "pong"
    protocol_version: Literal[1] = 1
    nonce: str = Field(min_length=1, max_length=256)


class ServerErrorMessage(SparseWireModel):
    type: Literal["error"] = "error"
    protocol_version: Literal[1] = 1
    code: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=4_096)
    request_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    retryable: bool = False


class SessionResultMessage(FrozenModel):
    type: Literal["session.result"] = "session.result"
    protocol_version: Literal[1] = 1
    request_id: str
    operation: str
    data: dict[str, object]


class ProviderResultMessage(FrozenModel):
    type: Literal["provider.result"] = "provider.result"
    protocol_version: Literal[1] = 1
    request_id: str
    operation: str
    data: dict[str, object]


class ModelResultMessage(FrozenModel):
    type: Literal["model.result"] = "model.result"
    protocol_version: Literal[1] = 1
    request_id: str
    operation: str
    data: dict[str, object]


class ResourceResultMessage(FrozenModel):
    type: Literal["resource.result"] = "resource.result"
    protocol_version: Literal[1] = 1
    request_id: str
    operation: Literal["list"] = "list"
    data: dict[str, object]


class ApprovalResultMessage(FrozenModel):
    type: Literal["approval.result"] = "approval.result"
    protocol_version: Literal[1] = 1
    request_id: str
    operation: Literal["list", "decide"]
    data: dict[str, object]


ServerMessage = Annotated[
    ServerHello
    | TaskAcceptedMessage
    | ControlResultMessage
    | PongMessage
    | ServerErrorMessage
    | ServerEnvelope
    | SessionResultMessage
    | ProviderResultMessage
    | ModelResultMessage
    | ResourceResultMessage
    | ApprovalResultMessage,
    Field(discriminator="type"),
]
_SERVER_MESSAGE_ADAPTER = TypeAdapter(ServerMessage)


def parse_server_message(value: str | bytes | dict[str, object]) -> ServerMessage:
    if isinstance(value, (str, bytes)):
        return _SERVER_MESSAGE_ADAPTER.validate_json(value)
    return _SERVER_MESSAGE_ADAPTER.validate_python(value)


__all__ = [
    "PROTOCOL_VERSION",
    "AckMessage",
    "AgUiEvent",
    "AgUiEventType",
    "AttachTaskMessage",
    "CancelTaskMessage",
    "ClientMessage",
    "ControlResultMessage",
    "HelloMessage",
    "PauseTaskMessage",
    "PingMessage",
    "PongMessage",
    "ServerEnvelope",
    "ServerErrorMessage",
    "ServerHello",
    "ServerMessage",
    "StartTaskMessage",
    "SteerTaskMessage",
    "TaskAcceptedMessage",
    "parse_client_message",
    "parse_server_message",
]
