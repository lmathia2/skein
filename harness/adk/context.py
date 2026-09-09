"""Opt-in bounded ADK context windows over retained, public evidence."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from harness.config.models import ContextConfig
from harness.context import estimate_tokens, truncate_to_tokens
from harness.ledger import LedgerStore
from harness.ledger.models import canonical_json
from harness.orchestration import build_work_packet
from harness.safety import SecretRedactor
from harness.state import EventKind, EventStore, rebuild_ledger

LOGGER = logging.getLogger(__name__)


class MemoryShadowPlugin(BasePlugin):
    """Compute a fixed probe without modifying model requests."""

    def __init__(self, *, probe: Callable[[str], dict[str, Any]]) -> None:
        super().__init__(name="memory_shadow")
        self.probe = probe

    async def before_model_callback(
        self, *, callback_context: Any, llm_request: LlmRequest,
    ) -> None:
        del llm_request
        if getattr(callback_context, "agent_name", "") == "coding_worker":
            task = callback_context.state.get("task_id")
            if task:
                try:
                    self.probe(str(task))
                except (ValueError, OSError, TimeoutError, OverflowError):
                    LOGGER.warning("shadow context probe unavailable")


def _serialized(contents: list[types.Content]) -> str:
    return canonical_json([item.model_dump(mode="json", exclude_none=True) for item in contents])


def _complete_cuts(contents: list[types.Content]) -> list[int]:
    """Only cut after all calls in an interaction have matching results."""
    pending: list[str] = []
    cuts = [0]
    for index, content in enumerate(contents):
        for part in content.parts or ():
            if part.function_call is not None:
                pending.append(part.function_call.id or part.function_call.name or "")
            if part.function_response is not None:
                key = part.function_response.id or part.function_response.name or ""
                if key not in pending:
                    raise ValueError("context contains an unmatched tool response")
                pending.remove(key)
        if not pending:
            cuts.append(index + 1)
    if pending:
        raise ValueError("cannot reconstruct context with pending tool calls")
    return cuts


def select_context_cut(
    contents: list[types.Content],
    *,
    prior_cut: int,
    header: types.Content,
    transient: list[types.Content],
    config: ContextConfig,
) -> int:
    """Purely select a bounded complete-interaction suffix boundary."""
    cuts = _complete_cuts(contents)
    selected = len(contents)
    # A just-returned result has not been consumed by the model. Keep its call too.
    if any(part.function_response for part in contents[-1].parts or ()):
        selected = cuts[-2]
    if config.reconstruction == "handoff_tail":
        for candidate in cuts:
            if candidate <= prior_cut or candidate > selected:
                continue
            tail = contents[candidate:]
            if (
                estimate_tokens(_serialized(tail)) <= config.recent_event_tokens
                and estimate_tokens(_serialized([header, *tail, *transient]))
                <= config.work_packet_tokens
            ):
                selected = candidate
                break
    effective = [header, *contents[selected:], *transient]
    if estimate_tokens(_serialized(effective)) > config.work_packet_tokens:
        raise ValueError("required control context exceeds the configured window")
    return selected


class ContextWindowPlugin(BasePlugin):
    """Keep ADK history durable; publish a cut before changing a model request.

    No ADK session events are deleted, and no model-generated summary is invoked.
    The default/off and shadow profiles never install this plugin.
    """

    def __init__(
        self,
        *,
        events: EventStore,
        ledger: LedgerStore,
        config: ContextConfig,
        handoff: Callable[[str], dict[str, Any]],
        known_secrets: tuple[str, ...] = (),
        require_notes: bool = False,
    ) -> None:
        super().__init__(name="context_windows")
        self.events = events
        self.ledger = ledger
        self.config = config
        self.handoff = handoff
        self.redactor = SecretRedactor(known_secrets=known_secrets)
        self.require_notes = require_notes
        self._captured_history: dict[tuple[str, str], tuple[str, ...]] = {}

    def _capture(self, task_id: str, invocation: str, raw: list[types.Content]) -> None:
        retrieval_calls: set[str] = set()
        records: list[tuple[str, dict[str, Any]]] = []
        for index, content in enumerate(raw):
            public = []
            for part in content.parts or ():
                if part.thought or part.thought_signature:
                    continue
                call = part.function_call
                if call and call.name == "bash" and str((call.args or {}).get(
                    "command", ""
                )).lstrip().startswith("memory "):
                    retrieval_calls.add(call.id or call.name)
                    continue
                response = part.function_response
                if response and (response.id or response.name or "") in retrieval_calls:
                    continue
                value = part.model_dump(
                    mode="json", exclude_none=True,
                    include={"text", "function_call", "function_response"},
                )
                if value:
                    public.append(value)
            if not public:
                continue
            payload = self.redactor.redact({"role": content.role, "parts": public})
            digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
            identity = hashlib.sha256(f"{task_id}:{invocation}:{index}:{digest}".encode()).hexdigest()
            records.append((identity, payload))

        key = (task_id, invocation)
        identities = tuple(identity for identity, _ in records)
        captured = self._captured_history.get(key)
        if captured is None:
            existing = {
                event.source_id
                for event in self.ledger.read(task_id, kinds=("context.history",))
                if event.source == "context"
            }
            captured_count = 0
            while captured_count < len(identities) and identities[captured_count] in existing:
                captured_count += 1
            if any(identity in existing for identity in identities[captured_count + 1:]):
                raise ValueError("captured context history is not a contiguous prefix")
        else:
            if identities[:len(captured)] != captured:
                raise ValueError("retained context history changed before its captured boundary")
            captured_count = len(captured)

        for identity, payload in records[captured_count:]:
            self.ledger.append(
                task_id=task_id, source="context", source_id=identity,
                kind="context.history", payload=payload,
            )
        self._captured_history[key] = identities

    async def before_model_callback(
        self, *, callback_context: Any, llm_request: LlmRequest,
    ) -> None:
        if getattr(callback_context, "agent_name", "") != "coding_worker":
            return
        task_id = str(callback_context.state.get("task_id", ""))
        if not task_id or not llm_request.contents:
            return
        invocation = str(getattr(callback_context, "invocation_id", ""))
        raw = list(llm_request.contents)
        transient: list[types.Content] = []
        marker = callback_context.state.get("context_steering")
        if marker:
            index = marker["index"]
            marked = raw[index]
            marked_text = "".join(part.text or "" for part in marked.parts or ())
            if hashlib.sha256(marked_text.encode()).hexdigest() != marker["hash"]:
                raise ValueError("transient steering identity does not match request")
            transient.append(raw.pop(index))
        if not raw:
            return
        self._capture(task_id, invocation, raw)
        events = self.events.read(task_id)
        task = rebuild_ledger(events)
        # A new workflow work packet is a new selection root, even in one invocation.
        anchor = hashlib.sha256(_serialized(raw[:1]).encode()).hexdigest()
        epochs = [event for event in events if event.kind == EventKind.COMPACTION_CREATED
                  and event.payload.get("invocation_id") == invocation
                  and event.payload.get("anchor") == anchor]
        previous = epochs[-1].payload if epochs else {}
        cut = int(previous.get("cut", 0))
        if cut > len(raw) or (cut and previous.get("input_hash") != hashlib.sha256(
            _serialized(raw[:cut]).encode()
        ).hexdigest()):
            raise ValueError("context epoch does not match retained ADK history")
        details = self.handoff(task_id)
        note_available = not self.require_notes or details.get("note", {}).get("status") == "ok"
        if not note_available and previous.get("note"):
            details["note"] = previous["note"]
            details["note_excerpt"] = previous.get("note_excerpt", "")
            details["note_stale"] = True
        details["history_boundary"] = events[-1].sequence
        committed = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED]
        failures = [event for event in events if event.kind in {
            EventKind.REPL_CELL_FAILED, EventKind.REPL_CELL_TIMEOUT,
        }]
        if committed:
            last = committed[-1]
            details["notebook"] = {
                "last_committed_kernel_epoch": last.payload.get("kernel_epoch"),
                "state": last.payload.get("state", {}),
                "availability": "reconciliation_required" if failures and
                failures[-1].sequence > last.sequence else "metadata_only_not_proof_of_live_heap",
            }
        critical = {key: details[key] for key in (
            "history_boundary", "unresolved_effects", "retrieval"
        ) if key in details}
        if "notebook" in details:
            critical["notebook"] = {key: value for key, value in details["notebook"].items()
                                    if key != "state"}
        required = "Required continuation metadata:\n" + canonical_json(self.redactor.redact(critical))
        allowance = self.config.compaction_tokens - estimate_tokens(required) - 16
        if allowance < 0:
            raise ValueError("required handoff exceeds compaction budget; context retained")
        advisory = json.dumps(self.redactor.redact(details), sort_keys=True, ensure_ascii=False)
        advisory, _ = truncate_to_tokens(advisory, allowance)
        handoff = required + "\nAdvisory memory (not execution authority):\n" + advisory
        active_handoff = str(previous.get("summary") or handoff)
        # Newest intent first; full text remains in canonical history. Never
        # head/tail-splice old instructions around the newest correction.
        steering = [str(event.payload.get("content", "")) for event in reversed(events)
                    if event.kind == EventKind.STEERING_RECEIVED]
        if steering and estimate_tokens(steering[0]) > self.config.steering_tokens:
            raise ValueError("newest steering exceeds the control budget; context retained")
        control = build_work_packet(
            task,
            selected_skills=str(callback_context.state.get("skill_context_text", "")),
            compaction_summary=active_handoff,
            steering_messages=steering,
            max_tokens=self.config.work_packet_tokens,
            section_token_limits={
                "TASK": self.config.ledger_tokens,
                "SELECTED SKILLS": self.config.skill_context_bytes // 4,
                "COMPACTED HISTORY": self.config.compaction_tokens,
                "USER STEERING": self.config.steering_tokens,
            },
        )
        control = self.redactor.redact_text(control)
        header = types.Content(role="user", parts=[types.Part.from_text(text=control)])
        remaining = raw[cut:]
        effective = [header, *remaining, *transient] if cut else [*raw, types.Content(
            role="user", parts=[types.Part.from_text(text=handoff)]
        ), *transient]
        if self.config.window_management and estimate_tokens(_serialized(effective)) > self.config.work_packet_tokens:
            if not note_available:
                raise ValueError("working note unavailable; context transition refused")
            if active_handoff != handoff:
                control = build_work_packet(
                    task,
                    selected_skills=str(callback_context.state.get("skill_context_text", "")),
                    compaction_summary=handoff,
                    steering_messages=steering,
                    max_tokens=self.config.work_packet_tokens,
                    section_token_limits={
                        "TASK": self.config.ledger_tokens,
                        "SELECTED SKILLS": self.config.skill_context_bytes // 4,
                        "COMPACTED HISTORY": self.config.compaction_tokens,
                        "USER STEERING": self.config.steering_tokens,
                    },
                )
                control = self.redactor.redact_text(control)
                header = types.Content(role="user", parts=[types.Part.from_text(text=control)])
            new_cut = select_context_cut(
                raw,
                prior_cut=cut,
                header=header,
                transient=transient,
                config=self.config,
            )
            effective = [header, *raw[new_cut:], *transient]
            prefix_hash = hashlib.sha256(_serialized(raw[:new_cut]).encode()).hexdigest()
            epoch = hashlib.sha256(f"{invocation}:{anchor}:{new_cut}:{prefix_hash}".encode()).hexdigest()
            # Publication failure leaves llm_request untouched; caller fails closed.
            self.events.append(
                task_id, EventKind.COMPACTION_CREATED,
                {"context_epoch": epoch, "invocation_id": invocation,
                 "anchor": anchor, "cut": new_cut, "input_hash": prefix_hash,
                 "reconstruction": self.config.reconstruction, "summary": handoff,
                 "note": details.get("note"), "note_excerpt": details.get("note_excerpt", ""),
                 "history_watermark": events[-1].sequence,
                 "tokens_before": estimate_tokens(_serialized(raw)),
                 "tokens_after": estimate_tokens(_serialized(effective))},
                idempotency_key=f"context-epoch:{epoch}",
            )
            callback_context.state["context_epoch"] = epoch
        llm_request.contents = effective
