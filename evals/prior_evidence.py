"""Scope-aware prior availability for controlled evaluations, not model authority.

Inputs are host-owned run bindings and frozen canonical producer evidence. Foreign
sequences never become current-task reads. Only completed managed retrievals count;
file identity must be observed in the consumer before each answer submission.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from harness.core.config import RuntimeBindings
from harness.evidence.ledger import LedgerEvent
from harness.evidence.ledger.models import canonical_json, extend_event_hash
from harness.evidence.memory.findings import source_freshness, source_observations
from harness.evidence.memory.models import MemoryFinding, ReadEvidence, ViewResult
from harness.evidence.state.receipts import ToolReceipt
from harness.evidence.state.recovery import unresolved_execution
from harness.execution.tools.adk_adapter import _ArtifactResolver


@dataclass(frozen=True)
class PriorRun:
    bindings: RuntimeBindings
    events: tuple[LedgerEvent, ...]
    receipts: tuple[ToolReceipt, ...]


def _manifest(events: list[LedgerEvent], watermark: int) -> dict[str, Any]:
    digest = ""
    for event in events:
        if event.sequence <= watermark and event.kind not in {"memory.retrieval", "memory.summary"}:
            digest = extend_event_hash(digest, event.event_id, event.payload_hash)
    return {"watermark": watermark, "hash": digest}


@dataclass(frozen=True)
class PriorEvidence:
    current: RuntimeBindings
    sources: tuple[PriorRun, ...]

    def __post_init__(self) -> None:
        current = self.current
        if not current.task_id or not current.user_id or not current.conversation_id:
            raise ValueError("prior audit requires an owned consumer binding")
        selected = dict(zip(current.prior_task_ids, current.prior_state_roots, strict=True))
        if len(self.sources) > 8 or len({s.bindings.task_id for s in self.sources}) != len(self.sources):
            raise ValueError("prior audit requires bounded unique sources")
        for source in self.sources:
            binding = source.bindings
            if (not binding.task_id or binding.task_id == current.task_id or binding.task_id not in selected
                    or binding.user_id != current.user_id or binding.conversation_id != current.conversation_id
                    or binding.workspace.resolve() != current.workspace.resolve()
                    or binding.state_root.resolve() != selected[binding.task_id].resolve()
                    or binding.state_root.resolve() == current.state_root.resolve()):
                raise ValueError("prior source is outside the owned task/workspace/state scope")
            if (not source.events or len(source.events) > 100_000 or len(source.receipts) > 10_000
                    or {e.task_id for e in source.events} != {binding.task_id}
                    or len({e.event_id for e in source.events}) != len(source.events)):
                raise ValueError("prior source trace has invalid identity or exceeds budget")
            for event in source.events:
                if hashlib.sha256(canonical_json(event.payload).encode()).hexdigest() != event.payload_hash:
                    raise ValueError("prior source payload hash mismatch")
            finished = [e for e in source.events if e.kind == "task.finished"]
            if (len(finished) != 1 or finished[0].payload.get("verification", {}).get("passed") is not True
                    or unresolved_execution(source.events, source.receipts)):
                raise ValueError("qualification producer must finish with verified, resolved evidence")

    def reads_before(self, events: list[LedgerEvent], boundary: int) -> list[dict[str, Any]]:
        """Return applicable finding/recovery ranges, with both task identities.

        Metadata-only reads.lookup, opaque artifact/shell output and byte fragments
        remain unmapped. A selected finding is advisory derived availability, not
        proof that the model consumed every cited line or reasoned correctly.
        """
        # Revalidate host snapshots, which contain mutable Pydantic payload dicts.
        self.__post_init__()
        if (len(events) > 100_000 or {e.task_id for e in events} != {self.current.task_id}
                or len({e.event_id for e in events}) != len(events)
                or any(a.sequence >= b.sequence for a, b in pairwise(events))):
            raise ValueError("prior audit requires ordered consumer evidence")
        if any(e.recorded_at >= events[0].recorded_at for source in self.sources for e in source.events):
            raise ValueError("frozen producer evidence must precede consumer creation")
        retained = [e for e in events if e.sequence < boundary]
        sources = {s.bindings.task_id: s for s in self.sources}
        requests = {e.payload.get("operation_id"): e for e in retained if e.kind == "capability.requested"}
        if len(requests) != sum(e.kind == "capability.requested" for e in retained):
            raise ValueError("prior retrieval audit has duplicate request identities")
        terminals = Counter(e.payload.get("operation_id") for e in retained
                            if e.kind in {"capability.completed", "capability.failed", "capability.blocked"})
        observations = source_observations([
            {"task_id": e.task_id, "sequence": e.sequence, "kind": e.kind, "payload": e.payload}
            for e in retained if e.kind in {"capability.completed", "capability.failed", "read.observed",
                                          "workspace.effect_observed", "execution.validation_observed"}])
        resolver = _ArtifactResolver(workspace=self.current.workspace, state_root=self.current.state_root)
        result = []
        consumed = 0
        for terminal in retained:
            p = terminal.payload
            if (terminal.kind != "capability.completed" or p.get("operation") != "shell.run"
                    or p.get("status") != "ok" or p.get("effect") not in {"none", "observed"}
                    or not p.get("result_artifact_uri")):
                continue
            request = requests.get(p.get("operation_id"))
            if (request is None or request.sequence >= terminal.sequence
                    or terminals[p.get("operation_id")] != 1
                    or any(request.payload.get(k) != p.get(k) for k in ("operation", "arguments_sha256", "attempt_id"))):
                raise ValueError("prior retrieval has unmatched operation identity")
            raw = resolver._read_content(p["result_artifact_uri"], max_source_bytes=1_000_000)
            consumed += len(raw)
            if consumed > 16_000_000:
                raise ValueError("prior retrieval audit exceeds artifact byte budget")
            body = json.loads(raw)
            if p.get("result_hash") != hashlib.sha256(json.dumps(
                    body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest():
                raise ValueError("retrieval artifact does not match the broker result hash")
            if (not isinstance(body, dict) or body.get("result_kind") != "managed"
                    or body.get("ui_details", {}).get("memory") is not True):
                continue
            data = body.get("data", {})
            if not isinstance(data, dict) or "program" not in data:
                continue  # Note writes and bounded unavailable envelopes are not views.
            view = ViewResult.model_validate(data)
            if view.task_id != self.current.task_id:
                raise ValueError("retrieval view belongs to another consumer")
            if body.get("status") != "ok" or view.status not in {"ok", "partial"}:
                continue
            receipt_hash = hashlib.sha256(canonical_json(data).encode()).hexdigest()
            # Public-view receipts deduplicate by content, not operation identity.
            # A repeated prior-only query can reuse an earlier receipt; this
            # operation's completed artifact, not that receipt, sets availability.
            if not any(e.kind == "memory.retrieval" and e.sequence < terminal.sequence
                       and e.payload.get("result_hash") == receipt_hash
                       and e.payload.get("view_id") == view.view_id
                       and e.payload.get("execution_hash") == view.execution_hash for e in retained):
                raise ValueError("completed retrieval lacks its matching public view receipt")
            for task, manifest in view.source_manifest.items():
                if task == self.current.task_id:
                    selected_events = retained
                elif task in sources:
                    selected_events = list(sources[task].events)
                else:
                    raise ValueError("retrieval selected an unauthorized source")
                watermark = manifest.get("watermark")
                if (type(watermark) is not int or watermark < 0
                        or watermark > max((e.sequence for e in selected_events), default=0)
                        or (task == self.current.task_id and watermark >= terminal.sequence)
                        or manifest != _manifest(selected_events, watermark)):
                    raise ValueError("retrieval source manifest does not match canonical evidence")
            candidates: list[tuple[str, LedgerEvent, ReadEvidence, str]] = []
            for task, source in sources.items():
                if task not in view.source_manifest:
                    continue
                assert isinstance(task, str)
                watermark = view.source_manifest[task]["watermark"]
                selected_events = [e for e in source.events if e.sequence <= watermark]
                if not any(e.kind == "task.finished" for e in selected_events):
                    raise ValueError("retrieved producer snapshot predates completion")
                indexed = {ref: e for e in selected_events for ref in
                           (e.event_id, e.payload.get("result_artifact_uri")) if isinstance(ref, str)}
                selected_findings = []
                if view.program == "working_set":
                    for item in view.data.get("findings", []):
                        if item.get("source_task_id") != task:
                            continue
                        note = indexed.get(item.get("note_event_id"))
                        finding = MemoryFinding.model_validate(item["finding"])
                        if (note is None or note.kind != "memory.note" or note.event_id not in view.evidence_event_ids
                                or not any(entry.get("finding") == finding.model_dump(mode="json")
                                           and entry.get("revision") == item.get("revision")
                                           for entry in note.payload.get("entries", []))):
                            raise ValueError("selected finding does not match its canonical note")
                        selected_findings.append(finding)
                elif view.program in {"history.page", "event.read"}:
                    for row in view.data.get("events", []):
                        if row.get("task_id") != task or row.get("kind") != "memory.note":
                            continue
                        note = indexed.get(row.get("event_id"))
                        if (note is None or note.kind != "memory.note" or note.event_id not in view.evidence_event_ids
                                or row.get("payload", {}).get("entries") != note.payload.get("entries")):
                            raise ValueError("selected note history does not match canonical findings")
                        selected_findings.extend(MemoryFinding.model_validate(entry["finding"])
                                                 for entry in note.payload.get("entries", []))
                elif view.program == "read.recover" and view.status == "ok" and view.data.get("complete") is True:
                    if view.data.get("byte_offset") != 0 or len(view.evidence_event_ids) != 1:
                        continue  # A suffix byte page is not the complete selected lines.
                    read_event = indexed.get(view.evidence_event_ids[0])
                    if read_event is None:
                        continue
                    captured = ReadEvidence.model_validate(read_event.payload["read_evidence"])
                    if (view.data.get("read_evidence") != captured.model_dump()
                            or view.data.get("artifact_uri") != read_event.payload.get("result_artifact_uri")):
                        raise ValueError("recovered source identity does not match the producer")
                    page = ReadEvidence(path=captured.path, sha256=captured.sha256,
                                        offset=view.data["source_offset"], returned_lines=view.data["selected_lines"])
                    if page.offset < captured.offset or page.offset + page.returned_lines > captured.offset + captured.returned_lines:
                        raise ValueError("recovery expanded the captured range")
                    candidates.append((task, read_event, page, "read.recover"))
                for finding in selected_findings:
                    for ref in finding.evidence_refs:
                        read_event = indexed.get(ref)
                        if read_event is not None and read_event.payload.get("operation") == "fs.read":
                            candidates.append((task, read_event, ReadEvidence.model_validate(
                                read_event.payload["read_evidence"]), "finding"))
            for task, event, read, route in candidates:
                if (event.kind not in {"capability.completed", "read.observed"}
                        or event.payload.get("status") != "ok" or not read.returned_lines):
                    continue
                version = source_freshness(read.model_dump(), observations, str(self.current.task_id))
                if version["observation_sequence"] > 0 and version["status"] == "historical_snapshot":
                    result.append({"source_task_id": task, "source_event_id": event.event_id,
                                   "retrieval_event_id": terminal.event_id, "retrieval_sequence": terminal.sequence,
                                   "version_observation_sequence": version["observation_sequence"],
                                   "route": route, "read_evidence": read.model_dump()})
        return result
