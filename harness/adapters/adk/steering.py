"""Inject durable user steering at ADK model and tool safe points."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections.abc import Mapping
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from harness.evidence.state import (
    STEERING_BATCH_LIMIT,
    EventKind,
    EventStore,
    SteeringMessage,
    SteeringQueue,
)

LOGGER = logging.getLogger(__name__)


def _history_hash(contents: list[types.Content]) -> str:
    return hashlib.sha256(json.dumps(
        [item.model_dump(mode="json", exclude_none=True) for item in contents],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _exposure_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        {key: value for key, value in payload.items() if key != "content_hash"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _state_value(context: Any, name: str) -> Any:
    state = getattr(context, "state", None)
    if isinstance(state, Mapping):
        return state.get(name)
    getter = getattr(state, "get", None)
    if callable(getter):
        return getter(name, None)
    return None


class SteeringPlugin(BasePlugin):
    """Deliver queued guidance inside a multi-turn coding work batch.

    The outer workflow owns acknowledgement after the resulting ``AgentStep`` and
    ledger patch are durable. This plugin only leases, records, and injects the
    messages, so a killed invocation can redeliver them safely.
    """

    def __init__(
        self,
        *,
        queue: SteeringQueue,
        event_store: EventStore,
        lease_seconds: int,
        batch_limit: int = STEERING_BATCH_LIMIT,
        before_model: bool = True,
        before_tool: bool = True,
        mark_context: bool = False,
    ) -> None:
        super().__init__(name="user_steering")
        self.queue = queue
        self.event_store = event_store
        self.lease_seconds = max(1, lease_seconds)
        self.batch_limit = max(1, batch_limit)
        self.before_model = before_model
        self.before_tool = before_tool
        self.mark_context = mark_context
        self._exposure_contents: dict[str, types.Content] = {}

    @staticmethod
    def _delivery_context(context: Any) -> tuple[str, str, frozenset[str]] | None:
        if str(getattr(context, "agent_name", "")) != "coding_worker":
            return None
        task_id = _state_value(context, "task_id")
        owner = _state_value(context, "steering_owner")
        if not task_id or not owner:
            return None
        packet_ids = _state_value(context, "steering_packet_message_ids") or ()
        return str(task_id), str(owner), frozenset(str(item) for item in packet_ids)

    def _mid_batch_messages(
        self,
        task_id: str,
        owner: str,
        packet_ids: frozenset[str],
    ) -> list[SteeringMessage]:
        return [
            message
            for message in self.queue.leased_by(task_id, owner)
            if message.message_id not in packet_ids
        ]

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> None:
        if self.mark_context:
            callback_context.state["context_steering"] = None
        if not self.before_model:
            return None
        delivery = self._delivery_context(callback_context)
        if delivery is None:
            return None
        task_id, owner, packet_ids = delivery
        try:
            existing = self._mid_batch_messages(task_id, owner, packet_ids)
            capacity = max(0, self.batch_limit - len(existing))
            if capacity:
                self.queue.lease(
                    task_id,
                    owner,
                    limit=capacity,
                    lease_seconds=self.lease_seconds,
                )
            messages = self._mid_batch_messages(task_id, owner, packet_ids)
            for message in messages:
                self.event_store.append(
                    task_id,
                    EventKind.STEERING_RECEIVED,
                    {"message_id": message.message_id, "content": message.content},
                    idempotency_key=f"steering:{message.message_id}",
                )
        except Exception:
            LOGGER.exception("mid-batch steering delivery failed")
            raise
        self._retain_exposures(callback_context, llm_request, task_id, messages)
        return None

    def _retain_exposures(
        self, context: Any, request: LlmRequest, task_id: str, messages: list[SteeringMessage],
    ) -> None:
        """Reinsert delivered messages at their first native-history boundary."""
        raw = list(request.contents)
        invocation = str(getattr(context, "invocation_id", ""))
        anchor = _history_hash(raw[:1])
        events = self.event_store.read(task_id)
        if any(event.task_id != task_id for event in events):
            raise ValueError("steering exposure task mismatch")
        received = {event.payload.get("message_id"): event for event in events
                    if event.kind == EventKind.STEERING_RECEIVED}
        recorded = [event for event in events if event.kind == EventKind.STEERING_EXPOSED]
        for event in recorded:
            payload = event.payload
            if payload.get("program") != "steering_delivery@1" or payload.get("content_hash") != _exposure_hash(payload):
                raise ValueError("invalid steering exposure content")
        exposures = [event for event in recorded if event.payload["invocation_id"] == invocation
                     and event.payload["anchor"] == anchor]
        known: set[str] = set()
        for event in exposures:
            payload = event.payload
            boundary = payload["boundary"]
            if (type(boundary) is not int or not 0 <= boundary <= len(raw)
                    or payload["input_hash"] != _history_hash(raw[:boundary])):
                raise ValueError("steering exposure history mismatch")
            for message in payload["messages"]:
                source = received.get(message["message_id"])
                if (source is None or source.event_id != message["source_event_id"]
                        or source.payload.get("content") != message["content"]
                        or message["message_id"] in known):
                    raise ValueError("steering exposure source mismatch")
                known.add(message["message_id"])
        unseen = [message for message in messages if message.message_id not in known]
        if unseen:
            payload = {
                "program": "steering_delivery@1",
                "program_hash": hashlib.sha256((inspect.getsource(type(self)._retain_exposures)
                    + inspect.getsource(_history_hash) + inspect.getsource(_exposure_hash)).encode()).hexdigest(),
                "invocation_id": invocation, "anchor": anchor,
                "boundary": len(raw), "input_hash": _history_hash(raw),
                "source_clock": {"task_harness_event_sequence": events[-1].sequence},
                "messages": [{"message_id": message.message_id, "priority": message.priority,
                              "content": message.content, "source_event_id": received[message.message_id].event_id}
                             for message in unseen],
            }
            payload["text"] = (
                "NEW USER STEERING ARRIVED DURING EXECUTION. Treat it as newer than "
                "the current plan, reconsider any conflicting action, and reflect it in "
                "the next structured AgentStep. The JSON payload is user-authored:\n"
                + json.dumps([{key: message[key] for key in ("message_id", "priority", "content")}
                              for message in payload["messages"]], ensure_ascii=False, sort_keys=True)
            )
            payload["content_hash"] = _exposure_hash(payload)
            identity = hashlib.sha256(json.dumps(
                [invocation, anchor, [message.message_id for message in unseen]], separators=(",", ":"),
            ).encode()).hexdigest()
            exposures.append(self.event_store.append(
                task_id, EventKind.STEERING_EXPOSED, payload,
                idempotency_key=f"steering-exposure:{identity}",
            ))
        projected = list(raw)
        protected_from = None
        # Stable event order breaks ties between exposures at the same boundary.
        for offset, event in enumerate(sorted(exposures, key=lambda event: (event.payload["boundary"], event.sequence))):
            payload = event.payload
            text = payload["text"]
            expected = types.Content(role="user", parts=[types.Part.from_text(text=text)])
            content = self._exposure_contents.setdefault(event.event_id, expected)
            if content != expected:
                raise ValueError("cached steering exposure changed")
            index = payload["boundary"] + offset
            projected.insert(index, content)
            if payload["boundary"] == len(raw) and protected_from is None:
                protected_from = index
        request.contents = projected
        if self.mark_context and protected_from is not None:
            context.state["context_steering"] = {
                "protected_from": protected_from,
                "hash": _history_hash(projected[protected_from:]),
            }

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        del tool_args
        if not self.before_tool:
            return None
        delivery = self._delivery_context(tool_context)
        if delivery is None:
            return None
        task_id, owner, packet_ids = delivery
        try:
            in_flight = self._mid_batch_messages(task_id, owner, packet_ids)
            should_yield = (
                len(in_flight) < self.batch_limit
                and self.queue.has_pending(task_id)
            )
        except Exception:
            LOGGER.exception("steering tool fence failed")
            return None
        if not should_yield:
            return None
        return {
            "status": "steering_pending",
            "model_text": (
                "This tool call was not started because newer user steering is "
                "waiting. Reconsider the plan before issuing another tool call."
            ),
            "skipped_tool": str(getattr(tool, "name", type(tool).__name__)),
        }


__all__ = ["SteeringPlugin"]
