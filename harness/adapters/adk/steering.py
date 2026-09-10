"""Inject durable user steering at ADK model and tool safe points."""

from __future__ import annotations

import hashlib
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
            newly_leased = (
                self.queue.lease(
                    task_id,
                    owner,
                    limit=capacity,
                    lease_seconds=self.lease_seconds,
                )
                if capacity
                else []
            )
            for message in newly_leased:
                self.event_store.append(
                    task_id,
                    EventKind.STEERING_RECEIVED,
                    {"message_id": message.message_id, "content": message.content},
                    idempotency_key=f"steering:{message.message_id}",
                )
            messages = self._mid_batch_messages(task_id, owner, packet_ids)
        except Exception:
            LOGGER.exception("mid-batch steering delivery failed")
            return None
        if not messages:
            return None

        payload = [
            {
                "message_id": message.message_id,
                "priority": message.priority,
                "content": message.content,
            }
            for message in messages
        ]
        text = (
            "NEW USER STEERING ARRIVED DURING EXECUTION. Treat it as newer than "
            "the current plan, reconsider any conflicting action, and reflect it in "
            "the next structured AgentStep. The JSON payload is user-authored:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        llm_request.contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=text)])
        )
        if self.mark_context:
            callback_context.state["context_steering"] = {
                "index": len(llm_request.contents) - 1,
                "hash": hashlib.sha256(text.encode()).hexdigest(),
            }
        return None

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
