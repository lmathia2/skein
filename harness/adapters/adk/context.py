"""Opt-in bounded ADK context windows over retained, public evidence."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from harness.core.config.models import ContextConfig
from harness.core.context import estimate_tokens
from harness.core.context.compiler import estimate_model_tokens
from harness.core.models import TaskLedger
from harness.core.orchestration import build_work_packet
from harness.evidence.ledger import LedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.state import EventKind, EventStore, rebuild_ledger
from harness.execution.safety import SecretRedactor

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


def _evidence_manifest(events: list[Any], modified_paths: list[str], focus: tuple[str, ...] = ()) -> dict[str, Any]:
    """Return a small deterministic index of evidence hidden by a future cut."""
    reads: list[dict[str, Any]] = []
    seen_reads: set[str] = set()
    validations: list[dict[str, Any]] = []
    touched: set[str] = set()
    for event in reversed(events):
        evidence = event.payload.get("read_evidence")
        touched.update(event.payload.get("changed_paths", ()))
        if event.kind in {EventKind.CAPABILITY_COMPLETED, EventKind.READ_OBSERVED} and isinstance(evidence, dict):
            identity = canonical_json(evidence)
            if identity not in seen_reads:
                seen_reads.add(identity)
                item = dict(evidence)
                if isinstance(event.payload.get("source_coverage"), dict):
                    item["source_coverage"] = event.payload["source_coverage"]
                same = [read for read in reads if (read.get("path"), read.get("sha256")) ==
                        (item.get("path"), item.get("sha256"))]
                start, end = item["offset"], item["offset"] + item["returned_lines"]
                if any(read["offset"] <= start and read["offset"] + read["returned_lines"] >= end for read in same):
                    continue
                # A single addressed superset can replace contained ranges. Keep
                # partial overlaps separate: never invent one merged artifact.
                reads = [read for read in reads if read not in same or not (
                    start <= read["offset"] and read["offset"] + read["returned_lines"] <= end)]
                if event.payload.get("result_artifact_uri"):
                    item["artifact_uri"] = event.payload["result_artifact_uri"]
                reads.append(item)
        if event.kind in {"execution.validation_completed", "execution.validation_observed"}:
            result = event.payload.get("result", {})
            if isinstance(result, dict) and len(validations) < 4:
                validations.append({
                    "command_sha256": event.payload.get("command_sha256"),
                    "exit_code": result.get("exit_code"),
                    "status": result.get("status"),
                })
    reads.sort(key=lambda item: item.get("path") not in focus)
    return {
        "modified_paths": sorted(modified_paths)[:16],
        "touched_paths": sorted(touched)[:128],
        "reads_newest_first": reads[:32],
        "omitted_reads": max(0, len(reads) - 32),
        "validations_newest_first": validations,
    }


def render_handoff(details: dict[str, Any], *, max_tokens: int) -> str:
    """Preserve control metadata and whole advisory entries, never JSON fragments."""
    critical = {key: details[key] for key in (
        "history_boundary", "kernel", "unresolved_effects", "retrieval", "note_stale"
    ) if key in details}
    notebook = details.get("notebook", {})
    if notebook:
        critical["notebook"] = {key: value for key, value in notebook.items() if key != "state"}
    working = details.get("working_set", {})
    if working:
        critical["working_set"] = {key: working[key] for key in (
            "program", "version", "program_hash", "execution_hash", "watermark", "content_hash", "status"
        ) if key in working}
    required = "Required continuation metadata:\n" + json.dumps(critical, sort_keys=True, ensure_ascii=False)
    candidates: list[tuple[str, Any]] = []
    for item in working.get("data", {}).get("findings", []):
        candidates.append(("findings", item))
    if details.get("note"):
        candidates.append(("note", details["note"]))
    if details.get("note_excerpt"):
        candidates.append(("note_excerpt", details["note_excerpt"]))
    if notebook.get("availability") == "live":
        candidates.extend(("live_bindings", item) for item in notebook.get("state", {}).get("manifest", [])
                          if item.get("description") or item.get("read_reference"))
    manifest = details.get("evidence_manifest", {})
    for key in ("touched_paths", "modified_paths", "validations_newest_first", "reads_newest_first"):
        candidates.extend((key, item) for item in manifest.get(key, []))
    advisory: dict[str, Any] = {"entries": [], "omitted_count": len(candidates),
                               "upstream_omitted_count": working.get("data", {}).get("omitted_count", 0)
                               + manifest.get("omitted_reads", 0)}

    def serialized(value: dict[str, Any]) -> str:
        return required + "\nAdvisory memory (not execution authority):\n" + json.dumps(value, sort_keys=True, ensure_ascii=False)

    if estimate_tokens(serialized(advisory)) > max_tokens:
        raise ValueError("required handoff exceeds compaction budget; context retained")
    for kind, value in candidates:
        proposed = {**advisory, "entries": [*advisory["entries"], {"kind": kind, "value": value}],
                    "omitted_count": advisory["omitted_count"] - 1}
        if estimate_tokens(serialized(proposed)) <= max_tokens:
            advisory = proposed
    return serialized(advisory)


def select_context_cut(
    contents: list[types.Content],
    *,
    prior_cut: int,
    header: types.Content,
    transient: list[types.Content],
    config: ContextConfig,
    available_tokens: int | None = None,
    previous_header: types.Content | None = None,
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
            if estimate_tokens(_serialized(tail)) <= config.recent_event_tokens:
                selected = candidate
                break
    effective = [previous_header if selected == prior_cut and previous_header is not None else header,
                 *contents[selected:], *transient]
    # The latest call/result is indivisible and may exceed the soft packet target.
    # Never drop an unconsumed result; the remaining hard-window budget still wins.
    hard_limit = config.max_context_tokens if available_tokens is None else available_tokens
    if estimate_tokens(_serialized(effective)) >= hard_limit:
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
        handoff: Callable[[TaskLedger], dict[str, Any]],
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
        self._captured_objects: dict[tuple[str, str], tuple[types.Content, ...]] = {}

    def _capture(self, task_id: str, invocation: str, raw: list[types.Content]) -> None:
        key = (task_id, invocation)
        captured = self._captured_history.get(key)
        captured_length = len(captured) if captured is not None else 0
        objects = tuple(raw)
        captured_objects = self._captured_objects.get(key, ())
        incremental = captured is not None and len(raw) >= len(captured_objects) and all(
            current is previous
            for current, previous in zip(raw, captured_objects, strict=False)
        )
        captured_count = (
            captured_length if incremental else 0
        )
        if captured is not None and len(raw) < captured_count:
            raise ValueError("retained context history changed before its captured boundary")
        retrieval_calls: set[str] = set()
        records: list[tuple[str, dict[str, Any]]] = []
        for index, content in enumerate(raw[captured_count:], start=captured_count):
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

        new_identities = tuple(identity for identity, _ in records)
        identities = (*captured[:captured_count], *new_identities) if captured else new_identities
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
        elif not captured_count:
            if identities[:len(captured)] != captured:
                raise ValueError("retained context history changed before its captured boundary")
            captured_count = len(captured)

        for identity, payload in records if incremental else records[captured_count:]:
            self.ledger.append(
                task_id=task_id, source="context", source_id=identity,
                kind="context.history", payload=payload,
            )
        self._captured_history[key] = identities
        self._captured_objects[key] = objects

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
        details = dict(self.handoff(task))
        if self.config.continuity_representation != "findings":
            details.pop("working_set", None)
        note_available = not self.require_notes or (
            details.get("note", {}).get("status") == "ok"
            and int(details.get("note", {}).get("version", 0)) > 0
            and bool(str(details.get("note_excerpt", "")).strip() or details.get("note", {}).get("entry_count"))
        )
        if self.require_notes and previous.get("note") and int(
            details.get("note", {}).get("version", 0)
        ) <= int(previous["note"].get("version", 0)):
            # A checkpoint used for an earlier cut does not summarize the work
            # about to be removed by the next cut, even if it is nonempty.
            note_available = False
        if self.require_notes:
            details["note_stale"] = not note_available
        if not note_available and previous.get("note"):
            details["note"] = previous["note"]
            details["note_excerpt"] = previous.get("note_excerpt", "")
            details["note_stale"] = True
        details["history_boundary"] = events[-1].sequence
        finding_paths = tuple(path for item in details.get("working_set", {}).get("data", {}).get("findings", [])
                              for path in item.get("finding", {}).get("related_paths", []))
        details["evidence_manifest"] = _evidence_manifest(events, task.files_modified, finding_paths)
        if self.config.continuity_representation == "metadata":
            manifest = details["evidence_manifest"]
            manifest["omitted_reads"] += max(0, len(manifest["reads_newest_first"]) - 8)
            manifest["reads_newest_first"] = manifest["reads_newest_first"][:8]
        committed = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED]
        failures = [event for event in events if event.kind in {
            EventKind.REPL_CELL_FAILED, EventKind.REPL_CELL_TIMEOUT,
        }]
        if committed:
            observations = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED or
                            (event.kind == EventKind.REPL_CELL_FAILED and event.payload.get("exception", {}).get("state_preserved")
                             and event.payload.get("exception", {}).get("stage") not in {"parse", "source_validation"})]
            last = observations[-1]
            kernel = details.get("kernel", {})
            unknown_failure = next((event for event in reversed(failures)
                                    if event.payload.get("effect") == "unknown"), None)
            details["notebook"] = {
                "last_committed_kernel_epoch": committed[-1].payload.get("kernel_epoch"),
                "observation_kernel_epoch": last.payload.get("kernel_epoch"),
                "observation_cell_id": last.payload.get("cell_id"),
                "state": last.payload.get("state", {}),
                "availability": "effect_reconciliation_required"
                if unknown_failure and unknown_failure.sequence > last.sequence
                else "live" if kernel.get("live") and kernel.get("kernel_epoch") ==
                last.payload.get("kernel_epoch") else "restart_pending_safe_restore",
            }
            if self.config.continuity_representation == "metadata":
                details["notebook"]["state"] = {}
        handoff = render_handoff(self.redactor.redact(details), max_tokens=self.config.compaction_tokens)
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
        if previous.get("header"):
            header = types.Content.model_validate(previous["header"])
        remaining = raw[cut:]
        root_key = f"context_root:{task_id}:{invocation}:{anchor}"
        initial_hint = callback_context.state.get(root_key)
        if initial_hint is None:
            initial_hint = handoff
            if self.require_notes:
                initial_hint += (
                    "\nKeep working intent in memory notes before changing phase. Use "
                    "the bash capability (agent.shell.run in PTC) with memory note read, then memory note write "
                    "--text TEXT --expected-version N --operation-id ID through the same "
                    "capability. Record findings, the selected approach, remaining work, "
                    "and evidence paths. --entries JSON adds typed public findings: id, kind, text, "
                    "evidence_refs, task_links, related_paths. Observations require available event IDs "
                    "or read artifact URIs; unsupported claims are hypotheses. Use supersedes or "
                    "conflicts_with for corrections. Read PTC results from the data field."
                )
            callback_context.state[root_key] = initial_hint
        effective = [header, *remaining, *transient] if cut else [raw[0], types.Content(
            role="user", parts=[types.Part.from_text(text=str(initial_hint))]
        ), *raw[1:], *transient]
        effective_tokens = estimate_tokens(_serialized(effective))
        estimate_key = f"context_request_estimate:{task_id}:{invocation}:{anchor}"
        previous_estimate = int(callback_context.state.get(estimate_key, 0) or 0)
        previous_provider_tokens = int(
            callback_context.state.get("context_provider_input_tokens", 0) or 0
        )
        phase = str(callback_context.state.get("task_phase", ""))
        if phase in {"understand", "plan"} and any(
            event.payload.get("effect") == "changed" for event in committed
        ):
            phase = "implement"
        phase_key = "context_window_phase"
        previous_phase = str(callback_context.state.get(phase_key, ""))
        phase_boundary = bool(phase and previous_phase and phase != previous_phase)
        request_overhead = estimate_model_tokens(llm_request.config) if llm_request.config else 0
        reserved_output = (llm_request.config.max_output_tokens or 0) if llm_request.config else 0
        estimated_request_tokens = effective_tokens + request_overhead
        projected_provider_tokens = (
            previous_provider_tokens + max(estimated_request_tokens - previous_estimate, 0)
            if previous_provider_tokens and previous_estimate else estimated_request_tokens
        )
        compaction_threshold = int(
            max(self.config.max_context_tokens - reserved_output, 0)
            * self.config.compaction_threshold_ratio
        )
        over_soft_limit = effective_tokens > self.config.work_packet_tokens
        over_hard_limit = projected_provider_tokens >= compaction_threshold
        phase_boundary = phase_boundary and projected_provider_tokens >= compaction_threshold // 2
        pending_key = f"context_compaction_pending:{task_id}:{invocation}:{anchor}"
        should_compact = over_hard_limit or (
            over_soft_limit
            and (
                self.config.compaction_timing == "immediate"
                or not phase
                or phase_boundary or callback_context.state.get(pending_key, False)
            )
        )
        checkpoint_key = f"context_checkpoint_requested:{task_id}:{invocation}:{anchor}:{cut}"
        if not note_available and (over_soft_limit or over_hard_limit) and (
            not previous or (self.config.window_management and should_compact)
        ):
            possible_cut = _complete_cuts(raw)[-2] if any(
                part.function_response for part in raw[-1].parts or ()
            ) else len(raw)
            if possible_cut > cut and not (
                self.config.window_management and over_hard_limit
            ) and not callback_context.state.get(checkpoint_key):
                callback_context.state[checkpoint_key] = True
                callback_context.state[pending_key] = (
                    self.config.window_management and should_compact
                )
                effective.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="A working-note checkpoint is pending before this context cut. "
                    f"Current note version: {details.get('note', {}).get('version', 0)}. "
                    "Refresh it with memory note write through bash (agent.shell.run in PTC), "
                    "using --expected-version N --operation-id ID --text TEXT and optional --entries JSON. "
                    "Preserve the exact task-relevant findings learned since the previous checkpoint, "
                    "their evidence references, completed changes, actual verification outcomes, and "
                    "remaining unknowns/next actions. Do not replace requested symbols with alternatives "
                    "or claim blocked checks ran. This is one checkpoint opportunity, not a new task."
                )]))
                if phase:
                    callback_context.state[phase_key] = phase
                callback_context.state[estimate_key] = estimate_tokens(_serialized(effective)) + request_overhead
                llm_request.contents = effective
                return
        if self.config.window_management and should_compact:
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
                available_tokens=self.config.max_context_tokens - request_overhead - reserved_output,
                previous_header=types.Content.model_validate(previous["header"]) if previous.get("header") else None,
            )
            if new_cut == cut:
                # The newest tool result is indivisible and not consumed yet.
                # Keep the published epoch; refreshing its content under the same
                # identity would violate both idempotency and the cache contract.
                if phase:
                    callback_context.state[phase_key] = phase
                callback_context.state[estimate_key] = estimate_tokens(_serialized(effective)) + request_overhead
                llm_request.contents = effective
                return
            effective = [header, *raw[new_cut:], *transient]
            prefix_hash = hashlib.sha256(_serialized(raw[:new_cut]).encode()).hexdigest()
            epoch = hashlib.sha256(f"{invocation}:{anchor}:{new_cut}:{prefix_hash}".encode()).hexdigest()
            # Publication failure leaves llm_request untouched; caller fails closed.
            self.events.append(
                task_id, EventKind.COMPACTION_CREATED,
                {"context_epoch": epoch, "invocation_id": invocation,
                 "anchor": anchor, "cut": new_cut, "input_hash": prefix_hash,
                 "reconstruction": self.config.reconstruction, "summary": handoff,
                 "header": header.model_dump(mode="json", exclude_none=True),
                 "trigger": "hard_limit" if over_hard_limit else "phase_boundary"
                 if self.config.compaction_timing == "phase_boundary" else "soft_limit",
                 "phase": phase,
                 "note": details.get("note"), "note_excerpt": details.get("note_excerpt", ""),
                 "note_stale": bool(details.get("note_stale")),
                 "checkpoint_requested": bool(callback_context.state.get(checkpoint_key)),
                 "history_watermark": events[-1].sequence,
                 "handoff_program": "continuation@2",
                 "handoff_program_hash": hashlib.sha256((inspect.getsource(render_handoff) +
                                                          inspect.getsource(_evidence_manifest) +
                                                          inspect.getsource(select_context_cut) +
                                                          inspect.getsource(type(self))).encode()).hexdigest(),
                 "working_set": {key: value for key, value in details.get("working_set", {}).items() if key != "data"},
                 "tokens_before": projected_provider_tokens,
                 "tokens_after": estimate_tokens(_serialized(effective)) + request_overhead,
                 "token_estimate_source": "provider_previous_plus_delta"
                 if previous_provider_tokens and previous_estimate else "serialized_fallback",
                 "threshold_tokens": compaction_threshold},
                idempotency_key=f"context-epoch:{epoch}",
            )
            callback_context.state["context_epoch"] = epoch
            callback_context.state[pending_key] = False
        if phase:
            callback_context.state[phase_key] = phase
        callback_context.state[estimate_key] = estimate_tokens(_serialized(effective)) + request_overhead
        llm_request.contents = effective
