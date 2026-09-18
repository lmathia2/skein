"""Opt-in bounded ADK context windows over retained, public evidence."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from harness.core.config.models import ContextConfig
from harness.core.context import estimate_tokens
from harness.core.context.compiler import ContextBudgetExceeded, estimate_model_tokens
from harness.core.models import TaskLedger
from harness.core.orchestration import build_work_packet
from harness.evidence.ledger import LedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory.models import ReadEvidence, ViewResult
from harness.evidence.state import EventKind, EventStore, rebuild_ledger
from harness.evidence.state.recovery import unresolved_execution
from harness.execution.safety import SecretRedactor
from harness.ptc.repl.worker import READ_RESULT_RECIPE, project_live_binding

from .steering import _exposure_hash, _history_hash

LOGGER = logging.getLogger(__name__)

_CUT_READ_RECIPE = "source_citation = None\n" + READ_RESULT_RECIPE + """
if saved_result is not None:
    if any(saved_result['data'].get(key) != value for key, value in expected_read.items()):
        raise ValueError('Recovered source identity differs from the selected historical read')
    source_citation = page['data']['uri']"""


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
                    if (event.payload.get("operation") == "fs.read" and event.payload.get("status") == "ok"
                            and event.payload.get("effect", "observed") in {"none", "observed"}
                            and event.payload.get("result_media_type") == "application/json"
                            and re.fullmatch(r"artifact://sha256/[0-9a-f]{64}", item["artifact_uri"])):
                        captured = ReadEvidence.model_validate(evidence)
                        coverage = event.payload.get("source_coverage")
                        if coverage is not None and (
                                not isinstance(coverage, dict)
                                or coverage != captured.source_coverage(coverage.get("total_lines"))):
                            raise ValueError("completed read has inconsistent source coverage")
                        item["recovery_kind"] = "historical_result_envelope"
                reads.append(item)
        if event.kind in {"execution.validation_completed", "execution.validation_observed"}:
            result = event.payload.get("result", {})
            if isinstance(result, dict) and len(validations) < 4:
                validations.append({
                    "harness_event_id": event.event_id,
                    "command_sha256": event.payload.get("command_sha256"),
                    "exit_code": result.get("exit_code"),
                    "status": result.get("status"),
                    **{key: event.payload[key] for key in ("operation_id", "receipt_id", "workspace_after")
                       if event.payload.get(key) is not None},
                    **({"command": event.payload.get("command") or result["command"]}
                       if event.payload.get("command") or result.get("command") else {}),
                    **({"artifact_uri": result["artifact_uri"]} if result.get("artifact_uri") else {}),
                })
    reads.sort(key=lambda item: item.get("path") not in focus)
    return {
        "modified_paths": sorted(modified_paths)[:16],
        "touched_paths": sorted(touched)[:128],
        "reads_newest_first": reads[:32],
        "omitted_reads": max(0, len(reads) - 32),
        "validations_newest_first": validations,
    }


def _project_advisory(advisory: dict[str, Any]) -> dict[str, Any]:
    """Factor exact repeated finding context/dependencies in this prompt only."""
    entries = advisory["entries"]
    findings = [entry["value"] for entry in entries if entry["kind"] in {"findings", "invalidated_findings"}]
    if any(entry["kind"] == "invalidated_findings" for entry in entries):
        advisory = {**advisory, "invalidated_guidance": (
            "Invalidated conclusions are not current facts. Historical text remains in memory note read; "
            "recover applicable evidence and recompute. Recorded captures do not prove current freshness; "
            "unknown effects still require reconciliation."
        )}
    if any(item.get("finding", {}).get("kind") == "next_action" for item in findings):
        advisory = {**advisory, "recorded_actions": (
            "Note next_actions are historical proposals, not pending user requests or proof of unfinished work. "
            "Check applicability against current task and latest steering; do not repeat a completed phase."
        )}
    context_keys = ("source_task_id", "note_event_id", "authority", "applicability", "provenance")
    contexts = [{key: item[key] for key in context_keys if key in item} for item in findings]
    dependencies = [dependency for item in findings for dependency in item.get("source_dependencies", [])]

    def table(values: list[dict[str, Any]], prefix: str) -> tuple[dict[str, str], dict[str, Any]]:
        counts = Counter(canonical_json(value) for value in values if value)
        identities = {value: f"{prefix}{index}" for index, value in enumerate(
            sorted(value for value, count in counts.items() if count > 1), start=1)}
        return identities, {identity: json.loads(value) for value, identity in identities.items()}

    context_ids, context_table = table(contexts, "c")
    dependency_ids, dependency_table = table(dependencies, "d")
    if not context_table and not dependency_table:
        return advisory
    projected = []
    for entry in entries:
        if entry["kind"] not in {"findings", "invalidated_findings"}:
            projected.append(entry)
            continue
        value = dict(entry["value"])
        context = {key: value[key] for key in context_keys if key in value}
        if identity := context_ids.get(canonical_json(context)):
            value = {key: item for key, item in value.items() if key not in context_keys}
            value["finding_context_ref"] = identity
        if "source_dependencies" in value:
            value["source_dependencies"] = [
                {"source_dependency_ref": dependency_ids[canonical_json(item)]}
                if canonical_json(item) in dependency_ids else item for item in value["source_dependencies"]
            ]
        projected.append({**entry, "value": value})
    result = {**advisory, "entries": projected}
    if context_table:
        result["finding_contexts"] = context_table
    if dependency_table:
        result["source_dependencies"] = dependency_table
    result["reference_scope"] = (
        "finding_context_ref/source_dependency_ref expand from the matching tables here. "
        "Labels are local, not tool arguments; recovery uses full evidence IDs."
    )
    # Small shared values may cost more as references. Keep the inline form then.
    return result if len(canonical_json(result).encode()) < len(canonical_json(advisory).encode()) else advisory


def continuation_details(
    task: TaskLedger, events: list[Any], details: dict[str, Any], *, representation: str,
) -> dict[str, Any]:
    """Join historical evidence with the separately observed current kernel state."""
    details = dict(details)
    if representation != "findings":
        details.pop("working_set", None)
    details["history_boundary"] = events[-1].sequence
    finding_paths = tuple(dict.fromkeys([
        *(path for item in details.get("working_set", {}).get("data", {}).get("findings", [])
          for path in item.get("finding", {}).get("related_paths", [])),
        *task.files_read[-12:], *task.files_modified[-12:],
    ]))
    details["evidence_manifest"] = _evidence_manifest(events, task.files_modified, finding_paths)
    if representation == "metadata":
        manifest = details["evidence_manifest"]
        manifest["omitted_reads"] += max(0, len(manifest["reads_newest_first"]) - 8)
        manifest["reads_newest_first"] = manifest["reads_newest_first"][:8]
    committed = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED]
    if committed:
        observations = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED or
                        (event.kind == EventKind.REPL_CELL_FAILED and event.payload.get("exception", {}).get("state_preserved")
                         and event.payload.get("exception", {}).get("stage") not in {"parse", "source_validation"})]
        last = observations[-1]
        kernel = details.get("kernel", {})
        unknown_failure = next((event for event in reversed(events)
                                if event.kind in {EventKind.REPL_CELL_FAILED, EventKind.REPL_CELL_TIMEOUT}
                                and event.payload.get("effect") == "unknown"), None)
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
        if representation == "metadata":
            details["notebook"]["state"] = {}
    return details


def render_handoff(details: dict[str, Any], *, max_tokens: int) -> str:
    """Preserve control metadata and whole advisory entries, never JSON fragments."""
    critical = {key: details[key] for key in (
        "history_boundary", "kernel", "unresolved_effects", "retrieval", "note_stale", "navigation"
    ) if key in details}
    notebook = details.get("notebook", {})
    if notebook:
        critical["notebook"] = {key: value for key, value in notebook.items() if key != "state"}
    working = details.get("working_set", {})
    if working:
        critical["working_set"] = {key: working[key] for key in (
            "program", "version", "program_hash", "execution_hash", "watermark", "content_hash", "status"
        ) if key in working}
    candidates: list[tuple[str, Any]] = []
    manifest = details.get("evidence_manifest", {})
    notebook = details.get("notebook", {})
    binding_candidates: list[dict[str, Any]] = []
    duplicate_aliases = 0
    if notebook.get("availability") == "live":
        bindings = [item for item in notebook.get("state", {}).get("manifest", [])
                    if item.get("description") or item.get("read_reference")]
        if details.get("navigation"):
            focus = details["navigation"]["parameters"]["focus_paths"]
            bindings.sort(key=lambda item: (item.get("read_reference", {}).get("path") not in focus,
                                           not bool(item.get("description")),
                                           len(item.get("access_expression", item.get("name", "")))))
        seen_references: set[str] = set()
        for item in bindings:
            reference = canonical_json(item["read_reference"]) if item.get("read_reference") else ""
            if details.get("navigation") and reference and reference in seen_references:
                duplicate_aliases += 1
                continue
            seen_references.add(reference)
            binding_candidates.append(project_live_binding(item))
    live_by_artifact = {
        item["read_reference"]["artifact_uri"]: item
        for item in binding_candidates
        if isinstance(item.get("read_reference"), dict)
        and isinstance(item["read_reference"].get("artifact_uri"), str)
    }
    attached_live_sources: set[str] = set()
    for item in working.get("data", {}).get("findings", []):
        consumer_status = item.get("consumer_versions", {}).get("status")
        invalidated = consumer_status in {"changed_since_capture", "revalidation_required"} or (
            item.get("freshness") in {"changed_since_capture", "revalidation_required"}
            and not (consumer_status == "matching_observations" and item.get("provenance") == "available"))
        if invalidated:
            # A historical conclusion must not look usable merely because its
            # invalidation was factored into a distant provenance table.
            dependencies = item.get("source_dependencies", [])
            captures = [read for read in manifest.get("reads_newest_first", [])
                        if item.get("applicability") == "current_task_advisory"
                        and any(dep.get("status") == "changed_since_capture"
                                and (read.get("path"), read.get("sha256")) ==
                                (dep.get("path"), dep.get("observed_sha256")) for dep in dependencies)]
            finding = item.get("finding", {})
            candidates.append(("invalidated_findings", {
                **item,
                "finding": {key: finding[key] for key in ("id", "kind", "evidence_refs", "related_paths") if key in finding},
                "usable_as_current_fact": False,
                "newer_recorded_captures": captures,
            }))
        else:
            references = item.get("finding", {}).get("evidence_refs", [])
            live_sources = [live_by_artifact[reference] for reference in references if reference in live_by_artifact]
            attached_live_sources.update(reference for reference in references if reference in live_by_artifact)
            candidates.append(("findings", {**item, **({"live_sources": live_sources} if live_sources else {})}))
    required = "Required continuation metadata:\n" + json.dumps(critical, sort_keys=True, ensure_ascii=False)
    if details.get("note"):
        candidates.append(("note", details["note"]))
    if details.get("note_excerpt"):
        if any(kind == "invalidated_findings" for kind, _ in candidates):
            candidates.append(("historical_note", {"text_withheld": True,
                "reason": "Unstructured note text may repeat invalidated conclusions; recover historical context with memory note read."}))
        else:
            candidates.append(("note_excerpt", details["note_excerpt"]))
    for item in binding_candidates:
        uri = item.get("read_reference", {}).get("artifact_uri")
        if uri not in attached_live_sources:
            candidates.append(("live_bindings", item))
    for key in ("touched_paths", "modified_paths", "validations_newest_first", "reads_newest_first"):
        candidates.extend((key, item) for item in manifest.get(key, []))
    advisory: dict[str, Any] = {"entries": [], "omitted_count": len(candidates),
                               "upstream_omitted_count": working.get("data", {}).get("omitted_count", 0)
                               + manifest.get("omitted_reads", 0) + duplicate_aliases}

    def serialized(value: dict[str, Any]) -> str:
        if any(entry["value"].get("read_recovery") for entry in value["entries"]
               if entry["kind"] == "reads_newest_first"):
            value = {**value, "completed_read_recovery": {
                "run_first": "Copy the selected read_recovery.load_code, then then_python into the same cell.",
                "then_python": _CUT_READ_RECIPE,
                "outputs": "saved_result is the original result envelope; source_text is its captured text; "
                           "source_citation is the exact URI to reuse in note evidence_refs, not a hash to retype. "
                           "Keep separate bindings for distinct paths/versions/ranges; print only needed excerpts.",
                "scope": "Historical captured range only: complete artifact bytes do not imply whole-file coverage "
                         "or current freshness. None means unavailable/incomplete: handle status or finish exact byte paging "
                         "with agent.help('artifacts.load', details=True). Never execute recovered source. "
                         "Required current-version checks and unknown-effect reconciliation still apply.",
            }}
        return required + "\nAdvisory memory (not execution authority):\n" + canonical_json(_project_advisory(value))

    required_tokens = estimate_tokens(serialized(advisory))
    if required_tokens > max_tokens:
        raise ContextBudgetExceeded(required_tokens, max_tokens)
    for kind, value in candidates:
        original = value
        if kind == "reads_newest_first" and "kernel" in details and value.get("recovery_kind") == "historical_result_envelope":
            expected = ReadEvidence.model_validate({key: value[key] for key in ReadEvidence.model_fields}).model_dump()
            value = {**value, "read_recovery": {
                "load_code": f"expected_read = {expected!r}\npage = agent.artifacts.load({value['artifact_uri']!r})",
            }}
        proposed = {**advisory, "entries": [*advisory["entries"], {"kind": kind, "value": value}],
                    "omitted_count": advisory["omitted_count"] - 1}
        if estimate_tokens(serialized(proposed)) <= max_tokens:
            advisory = proposed
        elif value is not original:
            # Retain the original evidence pointer when the optional executable
            # recipe cannot fit; required controls and the total budget never grow.
            proposed = {**proposed, "entries": [*advisory["entries"], {"kind": kind, "value": original}]}
            if estimate_tokens(serialized(proposed)) <= max_tokens:
                advisory = proposed
    return serialized(advisory)


def _unconsumed_cut_limit(contents: list[types.Content], protected_from: int | None) -> int:
    end = len(contents) if protected_from is None else protected_from
    if type(end) is not int or not 0 <= end <= len(contents):
        raise ValueError("invalid protected context boundary")
    prefix = contents[:end]
    cuts = _complete_cuts(prefix)
    # Fresh steering can follow a tool result that has not reached the model yet.
    if prefix and any(part.function_response for part in prefix[-1].parts or ()):
        return cuts[-2]
    return end


def select_context_cut(
    contents: list[types.Content],
    *,
    prior_cut: int,
    header: types.Content,
    transient: list[types.Content],
    config: ContextConfig,
    available_tokens: int | None = None,
    previous_header: types.Content | None = None,
    protected_from: int | None = None,
) -> int:
    """Purely select a bounded complete-interaction suffix boundary."""
    cuts = _complete_cuts(contents)
    selected = _unconsumed_cut_limit(contents, protected_from)
    if selected < prior_cut:
        raise ValueError("protected context precedes the published cut")
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


def prior_applicability_update(
    details: dict[str, Any], *, task_id: str, paths: tuple[str, ...],
    known: dict[str, str], max_bytes: int,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Bounded identity updates, never another exposure of prior learned content."""
    view = details.get("working_set", {})
    if view.get("status") not in {"ok", "partial"}:
        return None, {}
    if view.get("data", {}).get("findings"):
        validated = ViewResult.model_validate(view)
        if validated.task_id != task_id or validated.program != "working_set":
            raise ValueError("prior applicability source view identity mismatch")
    candidates = []
    for item in view.get("data", {}).get("findings", []):
        if item.get("source_task_id") == task_id or not any(
            dependency.get("path") in paths for dependency in item.get("source_dependencies", [])
        ):
            continue
        finding = item.get("finding", {})
        if item.get("consumer_versions", {}).get("task_id") != task_id:
            raise ValueError("prior applicability consumer identity mismatch")
        value = {"source_task_id": item["source_task_id"], "note_event_id": item["note_event_id"],
                 "finding_id": finding["id"], "revision": item["revision"],
                 "finding_status": finding.get("status"), "provenance": item.get("provenance"),
                 "consumer_versions": {key: item["consumer_versions"][key] for key in ("task_id", "status", "sources")}}
        identity = canonical_json([value["source_task_id"], value["note_event_id"], value["finding_id"]])
        versions = value["consumer_versions"]
        state = {**value, "consumer_versions": {**versions, "sources": [
            {key: val for key, val in source.items() if key != "observation_sequence"}
            for source in versions.get("sources", [])]},
            "unresolved_effect_count": details.get("unresolved_effects", {}).get("count", 0)}
        signature = hashlib.sha256(canonical_json(state).encode()).hexdigest()
        if known.get(identity) != signature:
            candidates.append((identity, signature, value))
    if not candidates:
        return None, {}
    content: dict[str, Any] = {
        "program": "prior_applicability@1", "content_hash": "0" * 64,
        "source_view": {key: view[key] for key in (
            "program", "version", "program_hash", "execution_hash", "content_hash", "source_manifest"
        ) if key in view},
        "scope": "Identity metadata only, not content or verification. Reuse already retrieved applicable findings "
                 "in place; recover missing content. Foreign citations stay source-scoped; unresolved effects and "
                 "independent checks still govern completion.",
        "unresolved_effect_count": details.get("unresolved_effects", {}).get("count", 0),
        "entries": [], "omitted_count": len(candidates),
    }
    states: dict[str, str] = {}
    for identity, signature, value in candidates:
        proposed = {**content, "entries": [*content["entries"], value],
                    "omitted_count": content["omitted_count"] - 1}
        if len(canonical_json(proposed).encode()) <= max_bytes:
            content = proposed
            states[identity] = signature
    if not states:
        return None, {}
    content["content_hash"] = hashlib.sha256(canonical_json({key: value for key, value in content.items()
                                                           if key != "content_hash"}).encode()).hexdigest()
    return content, states


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
        refresh_prior: bool = False,
        max_tool_result_bytes: int = 16000,
    ) -> None:
        super().__init__(name="context_windows")
        self.events = events
        self.ledger = ledger
        self.config = config
        self.handoff = handoff
        self.redactor = SecretRedactor(known_secrets=known_secrets)
        self.require_notes = require_notes
        self.refresh_prior = refresh_prior
        self.max_tool_result_bytes = max_tool_result_bytes
        self._captured_history: dict[tuple[str, str], tuple[str, ...]] = {}
        self._captured_objects: dict[tuple[str, str], tuple[types.Content, ...]] = {}
        self._checkpoint_contents: dict[str, types.Content] = {}

    def _checkpoint_history(
        self, task_id: str, invocation: str, raw: list[types.Content], events: list[Any],
    ) -> tuple[list[types.Content], list[Any]]:
        """Use steering's addressed-exposure pattern for historical host reminders."""
        anchor = _history_hash(raw[:1])
        exposures = []
        for event in events:
            if event.task_id != task_id:
                raise ValueError("checkpoint exposure task mismatch")
            if event.kind != EventKind.NOTE_CHECKPOINT_EXPOSED:
                continue
            payload = event.payload
            if (payload.get("program") != "note_checkpoint_delivery@1"
                    or payload.get("content_hash") != _exposure_hash(payload)
                    or not isinstance(payload.get("text"), str)
                    or not 1 <= len(payload["text"].encode()) <= 2048):
                raise ValueError("invalid checkpoint exposure content")
            if payload.get("invocation_id") != invocation or payload.get("anchor") != anchor:
                continue
            boundary = payload.get("boundary")
            if (type(boundary) is not int or not 1 <= boundary <= len(raw)
                    or payload.get("input_hash") != _history_hash(raw[:boundary])):
                raise ValueError("checkpoint exposure history mismatch")
            exposures.append(event)
        projected = list(raw)
        known_cuts = set()
        for offset, event in enumerate(sorted(exposures, key=lambda e: (e.payload["boundary"], e.sequence))):
            payload = event.payload
            cut = payload.get("cut")
            if type(cut) is not int or cut < 0 or cut in known_cuts:
                raise ValueError("checkpoint exposure cut identity mismatch")
            known_cuts.add(cut)
            expected = types.Content(role="user", parts=[types.Part.from_text(text=payload["text"])])
            content = self._checkpoint_contents.setdefault(event.event_id, expected)
            if content != expected:
                raise ValueError("cached checkpoint exposure changed")
            projected.insert(payload["boundary"] + offset, content)
        return projected, exposures

    async def after_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, result: dict[str, Any],
    ) -> None:
        """Append identity-only prior updates to a completed PTC response before capture.

        Mutate this new response and return None so ADK still runs metrics/artifact
        observers. Existing response history, execution hashes and effects stay intact.
        """
        del tool_args
        if (not self.refresh_prior or self.config.continuity_representation != "findings"
                or getattr(tool, "name", "") != "execute_code" or result.get("status") != "ok"):
            return
        task_id = str(tool_context.state.get("task_id", ""))
        attempt = str(result.get("attempt_id", ""))
        if not task_id or not attempt:
            return
        events = self.events.read(task_id)
        terminal = next((event for event in reversed(events) if event.kind == EventKind.REPL_CELL_COMPLETED
                         and event.payload.get("attempt_id") == attempt), None)
        if terminal is None:
            return
        key = f"prior-applicability:{attempt}"
        base = {key: value for key, value in result.items() if key != "prior_applicability"}
        input_hash = hashlib.sha256(canonical_json(base).encode()).hexdigest()
        previous = next((event for event in events if event.idempotency_key == key), None)
        if previous is not None:
            content = previous.payload.get("content", {})
            digest = hashlib.sha256(canonical_json({key: value for key, value in content.items()
                                                  if key != "content_hash"}).encode()).hexdigest()
            if (previous.kind != EventKind.PRIOR_APPLICABILITY_CREATED or
                    previous.payload.get("input_hash") != input_hash or content.get("content_hash") != digest):
                raise ValueError("prior applicability exposure identity mismatch")
            result["prior_applicability"] = content
            return
        paths = tuple(sorted({event.payload["read_evidence"]["path"] for event in events
                              if event.kind == EventKind.CAPABILITY_COMPLETED
                              and event.payload.get("attempt_id") == attempt
                              and event.payload.get("operation") == "fs.read" and event.payload.get("status") == "ok"
                              and event.sequence < terminal.sequence and isinstance(event.payload.get("read_evidence"), dict)}))
        available = min(2048, self.max_tool_result_bytes - len(canonical_json(base).encode())
                        - len(b',"prior_applicability":'))
        if not paths or available < 512:
            return
        known = {identity: signature for event in events if event.kind == EventKind.PRIOR_APPLICABILITY_CREATED
                 for identity, signature in event.payload.get("states", {}).items()}
        details = self.redactor.redact(self.handoff(rebuild_ledger(events)))
        content, states = prior_applicability_update(details, task_id=task_id, paths=paths, known=known, max_bytes=available)
        if content is None:
            return
        self.events.append(task_id, EventKind.PRIOR_APPLICABILITY_CREATED, {
            "attempt_id": attempt, "input_hash": input_hash, "paths": list(paths), "max_bytes": available,
            "inputs": details, "content": content, "states": states,
            "source_cell_event_id": terminal.event_id,
            "program_hash": hashlib.sha256((inspect.getsource(prior_applicability_update) +
                                            inspect.getsource(type(self).after_tool_callback)).encode()).hexdigest(),
        }, idempotency_key=key)
        result["prior_applicability"] = content

    def work_batch_handoff(self, task: TaskLedger, invocation: str) -> str:
        """Publish one historical snapshot in the host's next appended work packet.

        The first batch already has the initial hint. Inner tool calls never invoke
        this path. Re-entry returns the recorded bytes, not a refreshed old prefix.
        """
        if task.iteration == 0:
            return ""
        key = f"evidence-navigation:{invocation}:{task.iteration + 1}"
        parameters = {"task_id": task.task_id, "invocation_id": invocation,
                      "work_batch_id": str(task.iteration + 1), "phase": task.phase.value,
                      "representation": self.config.continuity_representation,
                      "focus_paths": list(dict.fromkeys([*task.files_read[-12:], *task.files_modified[-12:]])),
                      "max_tokens": self.config.compaction_tokens,
                      "task_hash": hashlib.sha256(canonical_json(task.model_dump(mode="json")).encode()).hexdigest()}
        events = self.events.read(task.task_id)
        previous = next((event for event in events if event.idempotency_key == key), None)
        if previous is not None:
            payload = previous.payload
            content = payload.get("content")
            if (previous.kind != EventKind.EVIDENCE_NAVIGATION_CREATED or payload.get("parameters") != parameters
                    or not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != payload.get("content_hash")):
                raise ValueError("work-batch navigation identity or captured content mismatch")
            return content
        # The memory query may publish its own receipt. Include that completed
        # observation in the snapshot watermark, without making it a new authority.
        supplied = self.handoff(task)
        events = self.events.read(task.task_id)
        details = continuation_details(task, events, supplied, representation=self.config.continuity_representation)
        program_hash = hashlib.sha256((inspect.getsource(type(self).work_batch_handoff) +
                                      inspect.getsource(continuation_details) + inspect.getsource(render_handoff) +
                                      inspect.getsource(_project_advisory) + inspect.getsource(_evidence_manifest) +
                                      inspect.getsource(unresolved_execution) + inspect.getsource(project_live_binding) + _CUT_READ_RECIPE).encode()).hexdigest()
        details["navigation"] = {
            "program": "work_batch_navigation@5", "program_hash": program_hash,
            "parameters": parameters, "source_watermark": events[-1].sequence,
            "source_clock": "task_harness_event_sequence",
            "scope": "Historical snapshot at this host work-batch boundary, not a live heap or freshness guarantee. "
                     "Use relevant completed bindings or addressed artifacts before fetching unchanged captured ranges again. "
                     "Acquire missing or changed ranges; reconcile unknown effects. Independent verification still governs completion.",
        }
        details = self.redactor.redact(details)
        content = render_handoff(details, max_tokens=self.config.compaction_tokens)
        self.events.append(task.task_id, EventKind.EVIDENCE_NAVIGATION_CREATED, {
            **details["navigation"], "inputs": details, "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }, idempotency_key=key)
        return content

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
        native = list(llm_request.contents)
        raw = native
        transient: list[types.Content] = []
        protected_from = None
        marker = callback_context.state.get("context_steering")
        if marker:
            protected_from = marker["protected_from"]
            if (type(protected_from) is not int or not 0 <= protected_from < len(raw)
                    or hashlib.sha256(_serialized(raw[protected_from:]).encode()).hexdigest() != marker["hash"]):
                raise ValueError("protected steering identity does not match request")
        if not raw:
            return
        raw, checkpoint_exposures = self._checkpoint_history(task_id, invocation, native, self.events.read(task_id))
        if protected_from is not None:
            protected_from += sum(e.payload["boundary"] <= protected_from for e in checkpoint_exposures)
        self._capture(task_id, invocation, raw)
        events = self.events.read(task_id)
        task = rebuild_ledger(events)
        # Later work packets append within the same root; only a cut replaces it.
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
        details = continuation_details(task, events, self.handoff(task),
                                       representation=self.config.continuity_representation)
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
        committed = [event for event in events if event.kind == EventKind.REPL_CELL_COMPLETED]
        handoff = render_handoff(self.redactor.redact(details), max_tokens=self.config.compaction_tokens)
        active_handoff = str(previous.get("summary") or handoff)
        # Preserve delivery order, with newer corrections after earlier instructions.
        steering = [str(event.payload.get("content", "")) for event in events
                    if event.kind == EventKind.STEERING_RECEIVED]
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
                    "\nCheckpoint learned evidence and unresolved questions at meaningful boundaries, "
                    "not a plan-only note before acquisition or a duplicate log of tool receipts. "
                    "Use the newest observed note version from supplied metadata/receipts; read only "
                    "for needed content/version or a conflict. Through bash (agent.shell.run in PTC), use "
                    "memory note write --text TEXT --expected-version N --operation-id ID. "
                    "Reuse existing finding IDs when revising the same conclusion; new IDs add entries. "
                    "--entries JSON contains typed public findings: id, kind, text, "
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
        checkpoint_requested = any(e.payload["cut"] == cut for e in checkpoint_exposures) or bool(
            callback_context.state.get(checkpoint_key))
        if not note_available and (over_soft_limit or over_hard_limit) and (
            not previous or (self.config.window_management and should_compact)
        ):
            possible_cut = _unconsumed_cut_limit(raw, protected_from)
            if possible_cut > cut and not (
                self.config.window_management and over_hard_limit
            ) and not checkpoint_requested:
                reminder = (
                    "A working-note checkpoint is pending before this context cut. "
                    f"Current note version: {details.get('note', {}).get('version', 0)}. "
                    "Refresh it with memory note write through bash (agent.shell.run in PTC), "
                    "using --expected-version N --operation-id ID --text TEXT and optional --entries JSON. "
                    "Preserve the exact task-relevant findings learned since the previous checkpoint, "
                    "their evidence references, completed changes, actual verification outcomes, and "
                    "remaining unknowns/next actions. Do not replace requested symbols with alternatives "
                    "or claim blocked checks ran. This is one checkpoint opportunity, not a new task."
                )
                payload = {
                    "program": "note_checkpoint_delivery@1",
                    "program_hash": hashlib.sha256((inspect.getsource(type(self)) +
                        inspect.getsource(_history_hash) + inspect.getsource(_exposure_hash)).encode()).hexdigest(),
                    "invocation_id": invocation, "anchor": _history_hash(native[:1]),
                    "boundary": len(native), "input_hash": _history_hash(native), "cut": cut,
                    "source_clock": {"task_harness_event_sequence": events[-1].sequence},
                    "text": reminder,
                }
                payload["content_hash"] = _exposure_hash(payload)
                self.events.append(task_id, EventKind.NOTE_CHECKPOINT_EXPOSED, payload,
                                   idempotency_key=checkpoint_key)
                # Capture the published reminder at its historical position before
                # dispatch. Later calls/restarts reconstruct it before new content.
                raw, _ = self._checkpoint_history(task_id, invocation, native, self.events.read(task_id))
                self._capture(task_id, invocation, raw)
                effective.append(raw[-1])
                callback_context.state[checkpoint_key] = True
                callback_context.state[pending_key] = self.config.window_management and should_compact
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
                protected_from=protected_from,
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
                 "checkpoint_requested": checkpoint_requested,
                 "history_watermark": events[-1].sequence,
                 "handoff_program": "continuation@12",
                 "handoff_program_hash": hashlib.sha256((inspect.getsource(render_handoff) +
                                                          inspect.getsource(continuation_details) +
                                                          inspect.getsource(_project_advisory) +
                                                          inspect.getsource(project_live_binding) +
                                                          inspect.getsource(_evidence_manifest) +
                                                          inspect.getsource(unresolved_execution) +
                                                          inspect.getsource(select_context_cut) +
                                                          inspect.getsource(_unconsumed_cut_limit) +
                                                          inspect.getsource(type(self)) + _CUT_READ_RECIPE).encode()).hexdigest(),
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
