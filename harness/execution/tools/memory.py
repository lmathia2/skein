"""Strict reserved ``memory`` commands, intercepted before shell dispatch."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from harness.evidence.ledger import LedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory import MemoryProgramRuntime, ViewRequest
from harness.evidence.memory.context import (
    ArtifactReader,
    ReadResultReader,
    _artifact_references,
    bounded_events,
    project,
)
from harness.evidence.memory.lance import LanceMemorySearch
from harness.evidence.memory.models import MemoryFinding, ReadEvidence
from harness.execution.safety.redaction import SecretRedactor

_RESERVED = re.compile(r"^\s*memory(?:\s|$)")
# ponytail: one process owns a state root; do not claim cross-process CAS.
_NOTE_LOCK = threading.RLock()


class _NoteRejected(ValueError):
    """Invalid note input rejected before any canonical mutation."""


def _public_note(event: Any) -> dict[str, Any]:
    return {"status": "ok", "event_id": event.event_id,
            **{key: value for key, value in event.payload.items() if key != "request_hash"}}


def _merge_findings(
    previous: list[dict[str, Any]], updates: list[MemoryFinding], rows: list[dict[str, Any]], revision: int,
) -> list[dict[str, Any]]:
    # Retire superseded entries from the next checkpoint, never from canonical history.
    entries = {item["finding"]["id"]: json.loads(canonical_json(item)) for item in previous
               if MemoryFinding.model_validate(item["finding"]).status != "superseded"}
    available = {ref: row for row in rows for ref in (row["event_id"], *_artifact_references(row["payload"]))}
    if len({item.id for item in updates}) != len(updates):
        raise _NoteRejected("finding update IDs must be unique")
    known = set(entries) | {item.id for item in updates}
    for finding in updates:
        if not set(finding.evidence_refs) <= available.keys():
            raise _NoteRejected("finding cites unavailable public task evidence")
        if not set((*finding.supersedes, *finding.conflicts_with)) <= known:
            raise _NoteRejected("finding links an unknown finding ID")
        dependencies = {}
        for ref in finding.evidence_refs:
            read = available[ref]["payload"].get("read_evidence")
            if read is not None:
                evidence = ReadEvidence.model_validate(read).model_dump(mode="json")
                dependencies[canonical_json(evidence)] = evidence
        entries[finding.id] = {"finding": finding.model_dump(mode="json"), "revision": revision,
                               "source_dependencies": [dependencies[key] for key in sorted(dependencies)]}
    # Explicit links only. Semantic contradictions are never resolved by string similarity.
    for finding in updates:
        for superseded in finding.supersedes:
            if superseded in {item.id for item in updates if item.supersedes}:
                raise _NoteRejected("supersession chains require separate revisions")
            entries[superseded]["finding"]["status"] = "superseded"
            entries[superseded]["revision"] = revision
        for conflict in finding.conflicts_with:
            for identity in (finding.id, conflict):
                if entries[identity]["finding"]["status"] == "superseded":
                    raise _NoteRejected("cannot dispute a superseded finding")
                entries[identity]["finding"]["status"] = "disputed"
                entries[identity]["revision"] = revision
    if len(entries) > 64:
        raise _NoteRejected("working findings exceed 64 entries; retain earlier versions in history")
    return [entries[key] for key in sorted(entries)]


class ContextProgramService:
    """Task-bound router, receipts and optimistic notes without a new agent tool."""

    def __init__(
        self, ledger: LedgerStore, task_id: str, *, mode: str = "active",
        max_result_bytes: int = 16000, max_scan_events: int = 10000,
        timeout_seconds: float = 2, working_notes: bool = False, reuse: bool = False,
        artifact_reader: ArtifactReader | None = None, authorized_tasks: tuple[str, ...] = (),
        read_result_reader: ReadResultReader | None = None,
        source_ledgers: Mapping[str, LedgerStore] | None = None,
        redactor: SecretRedactor | None = None,
        on_note: Callable[[dict[str, Any]], object] | None = None,
        semantic_search: LanceMemorySearch | None = None,
        programs: Mapping[str, int] | None = None,
    ) -> None:
        if mode not in {"off", "shadow", "active"}:
            raise ValueError("unknown context program mode")
        self.ledger, self.task_id, self.mode = ledger, task_id, mode
        self.max_result_bytes, self.max_scan_events = max_result_bytes, max_scan_events
        self.timeout_seconds, self.working_notes = timeout_seconds, working_notes
        self.redactor = redactor or SecretRedactor()
        self.on_note = on_note
        self.programs = dict(programs) if programs is not None else None
        self.runtime = MemoryProgramRuntime(
            ledger, authorized_tasks=tuple(sorted(set((task_id, *authorized_tasks)))),
            redactor=self.redactor, reuse=reuse, artifact_reader=artifact_reader,
            read_result_reader=read_result_reader,
            source_ledgers=source_ledgers,
            semantic_search=semantic_search,
        )

    def _events(self) -> list[Any]:
        return bounded_events(self.ledger, (self.task_id,), maximum=self.max_scan_events,
                              deadline=time.monotonic() + self.timeout_seconds)

    def note_read(self) -> dict[str, Any]:
        if not self.working_notes:
            return {"status": "unavailable", "reason": "working notes disabled"}
        notes = [event for event in self._events() if event.kind == "memory.note"]
        if not notes:
            return {"status": "ok", "version": 0, "text": "", "evidence_event_ids": []}
        event = notes[-1]
        return _public_note(event)

    def _publish_note(self, event: Any) -> dict[str, Any]:
        response = _public_note(event)
        if self.on_note:
            try:
                self.on_note(response)
            except _NoteRejected as exc:
                # A sink can invoke another note operation. Its rejection does
                # not undo this already committed note or resolve publication.
                raise ValueError(str(exc)) from exc
        return response

    def note_write(self, *, text: str, expected_version: int, operation_id: str,
                   evidence_event_ids: tuple[str, ...] = (), entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if self.mode != "active" or not self.working_notes:
            return {"status": "denied", "reason": "working note writes disabled", "effect": "none"}
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id) or expected_version < 0:
            raise _NoteRejected("note requires a safe operation ID and nonnegative expected version")
        if entries is not None and (not isinstance(entries, list) or len(entries) > 64):
            raise _NoteRejected("entries require a bounded array of typed findings")
        try:
            updates = [MemoryFinding.model_validate(self.redactor.redact(item)) for item in entries or []]
        except ValueError as exc:
            raise _NoteRejected(str(exc)) from exc
        payload = {"version": expected_version + 1, "expected_version": expected_version,
                   "text": self.redactor.redact_text(text), "evidence_event_ids": list(evidence_event_ids)}
        request_hash = hashlib.sha256(canonical_json({**payload, "updates": [item.model_dump(mode="json") for item in updates]}).encode()).hexdigest()
        with _NOTE_LOCK:
            events = self._events()
            key = f"memory-note:{operation_id}"
            prior = next((event for event in events if event.idempotency_key == key), None)
            if prior is not None:
                if (prior.payload.get("request_hash") != request_hash if "request_hash" in prior.payload
                        else prior.payload != payload or bool(updates)):
                    raise ValueError("operation ID reused with different note content")
                return self._publish_note(prior)
            notes = [event for event in events if event.kind == "memory.note"]
            current: dict[str, Any] = _public_note(notes[-1]) if notes else {"version": 0}
            if current.get("version") != expected_version:
                return {"status": "conflict", "current_version": current.get("version"), "effect": "none"}
            rows = [row for event in events if (row := project(event, self.redactor)) is not None]
            allowed = {row["event_id"] for row in rows}
            if not set(evidence_event_ids) <= allowed:
                return {"status": "denied", "reason": "note cites unavailable task evidence", "effect": "none"}
            merged = _merge_findings(current.get("entries", []), updates, rows, expected_version + 1)
            if merged:
                payload["entries"] = merged
            payload["request_hash"] = request_hash
            required_bytes = len(canonical_json(payload).encode())
            budget_bytes = min(self.max_result_bytes // 2, 8000)
            if required_bytes > budget_bytes:
                return {"status": "unavailable", "reason": "note exceeds budget; last checkpoint retained",
                        "current_version": expected_version, "effect": "none",
                        "required_bytes": required_bytes, "budget_bytes": budget_bytes,
                        "recovery": "Shorten note text/findings; cite receipts instead of duplicating source hashes and ranges. Retry at the same version with a new operation ID."}
            event = self.ledger.append(
                task_id=self.task_id, source="context_note", source_id=f"{self.task_id}:{operation_id}",
                kind="memory.note", payload=payload, status="completed", idempotency_key=key,
            )
            return self._publish_note(event)

    def handoff(self, *, focus: tuple[str, ...] = ()) -> dict[str, Any]:
        """Small advisory metadata; caller allocates its existing dynamic budget."""
        try:
            note = self.note_read()
            hint = (
                "memory history; memory query --program events.count; "
                "memory query --program tools.usage; memory note read"
            )
            if self.programs is None or self.programs.get("reads.lookup") == 1:
                hint += "; memory query --program reads.lookup --path PATH"
            if self.programs is None or self.programs.get("read.recover") == 1:
                hint += (
                    "; memory query --program read.recover --event-id ID --offset 1 --limit 40 "
                    "(offset is relative to captured lines; --byte-offset pages long selected text)"
                )
            if self.runtime.semantic_search is not None:
                hint += "; memory history --query TEXT --retrieval semantic|hybrid (top-k, not exhaustive)"
            sources = tuple(dict.fromkeys((self.task_id, *self.runtime.authorized_tasks)))[:16]
            if len(sources) > 1:
                hint += "; authorized source tasks: " + ",".join(sources) + "; use --tasks TASK with retrieval; prior findings are not current workspace evidence"
            details = {"memory": self.mode, "note": {key: value for key, value in note.items()
                                                    if key in {"status", "version", "event_id"}},
                       "note_excerpt": str(note.get("text", "")), "retrieval": hint}
            details["note"]["entry_count"] = len(note.get("entries", []))
            if self.mode == "active" and (self.programs is None or self.programs.get("working_set") == 1):
                details["working_set"] = self._query({"--tasks": ",".join(sources)}, "working_set", focus=focus)
            return details
        except (ValueError, OSError, TimeoutError, OverflowError):
            return {"memory": self.mode, "note": {"status": "unavailable"}}

    def _query(self, options: dict[str, str], program: str, *, focus: tuple[str, ...] = ()) -> dict[str, Any]:
        parameters: dict[str, Any] = {"task_id": self.task_id, "program": program,
                                     "max_bytes": max(128, self.max_result_bytes // 2),
                                     "max_scan_events": self.max_scan_events,
                                     "timeout_seconds": self.timeout_seconds, "focus": focus}
        names = {"--program": "program", "--query": "query", "--retrieval": "retrieval", "--event-id": "event_id",
                 "--cursor": "cursor", "--as-of": "as_of", "--recorded-before": "recorded_before",
                 "--observed-after": "observed_after", "--limit": "limit", "--version": "version",
                 "--watermark": "watermark", "--artifact-uri": "artifact_uri", "--offset": "offset"}
        names.update({"--byte-offset": "byte_offset", "--byte-limit": "byte_limit"})
        names.update({"--path": "path", "--source-sha256": "source_sha256"})
        for flag, value in options.items():
            if flag in {"--tasks", "--kinds", "--statuses", "--focus"}:
                parameters[{"--tasks": "source_tasks", "--kinds": "kinds", "--statuses": "statuses", "--focus": "focus"}[flag]] = tuple(value.split(","))
            elif flag in names:
                parameters[names[flag]] = value
            else:
                raise ValueError(f"unknown memory query option: {flag}")
        request = ViewRequest.model_validate(parameters)
        if self.programs is not None and self.programs.get(request.program) != request.version:
            raise ValueError("program/version is not enabled in this profile")
        # The virtual command cannot expose older unbounded internal prompt programs.
        from harness.evidence.memory.programs import resolve_program
        if resolve_program(
            request.program, request.version, reuse=self.runtime.reuse, model_visible=True
        ) is None:
            raise ValueError("program is not in the model-visible allowlist")
        view = self.runtime.compute(request)
        response = view.model_dump(mode="json")
        if len(canonical_json(response).encode()) > self.max_result_bytes:
            response = {"status": "partial", "reason": "result envelope exceeds egress budget",
                        "view_id": view.view_id, "content_hash": view.content_hash}
        receipt_hash = hashlib.sha256(canonical_json(response).encode()).hexdigest()
        self.ledger.append(
            task_id=self.task_id, source="context_retrieval", source_id=f"{self.task_id}:{receipt_hash}",
            kind="memory.retrieval", payload={"view_id": view.view_id, "execution_hash": view.execution_hash,
                                               "result_hash": receipt_hash, "status": response["status"],
                                               "exposed_bytes": len(canonical_json(response).encode()),
                                               "evidence_event_ids": response.get("evidence_event_ids", [])},
            idempotency_key=f"memory-retrieval:{receipt_hash}",
        )
        return response

    def execute(self, command: str) -> dict[str, Any] | None:
        if not _RESERVED.match(command):
            return None
        if self.mode != "active":
            return {"status": "denied", "reason": "context programs are not active", "effect": "none"}
        dispatched = False
        try:
            if len(command) > 16000 or "\x00" in command:
                raise ValueError("invalid memory command")
            # Quoted multiline note text is data. This grammar is interpreted
            # in-process, never sent to a shell; extra commands still fail parsing.
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
            lexer.whitespace_split, lexer.commenters = True, ""
            tokens = list(lexer)
            if any(token and set(token) <= set(";&|<>") for token in tokens):
                raise ValueError("shell composition is forbidden for memory commands")
            if len(tokens) < 2:
                raise ValueError("usage: memory history|event|query|artifact|note ...")
            operation = tokens[1]
            start = 3 if operation == "note" else 2
            if (len(tokens) - start) % 2:
                raise ValueError("memory options require flag/value pairs")
            options: dict[str, str] = {}
            for index in range(start, len(tokens), 2):
                if tokens[index] in options or not tokens[index].startswith("--"):
                    raise ValueError("invalid or duplicate memory option")
                options[tokens[index]] = tokens[index + 1]
            if operation == "note":
                if tokens[2] == "read" and not options:
                    return self.note_read()
                if (tokens[2] != "write" or set(options) - {"--text", "--expected-version", "--operation-id", "--evidence", "--entries"}
                        or not {"--text", "--expected-version", "--operation-id"} <= options.keys()):
                    raise ValueError("usage: memory note read|write --text TEXT --expected-version N --operation-id ID")
                arguments: dict[str, Any] = dict(text=options["--text"], expected_version=int(options["--expected-version"]),
                                 operation_id=options["--operation-id"],
                                 evidence_event_ids=tuple(filter(None, options.get("--evidence", "").split(","))),
                                 entries=json.loads(options["--entries"]) if "--entries" in options else None)
                dispatched = True
                return self.note_write(**arguments)
            program = {"history": "history.page", "search": "history.page", "read": "event.read",
                       "event": "event.read", "query": "history.page",
                       "artifact": "artifact.read"}.get(operation)
            if program is None:
                raise ValueError("unknown memory operation")
            dispatched = True
            return self._query(options, program)
        except (ValueError, KeyError, IndexError, OSError, TimeoutError, OverflowError) as exc:
            return {"status": "unavailable", "reason": self.redactor.redact_text(str(exc))[:512],
                    "effect": "none" if not dispatched or isinstance(exc, _NoteRejected) else "unknown"}

    def shadow(self) -> dict[str, Any]:
        """Fixed read-only probe; caller must not insert its output in model input."""
        if self.mode != "shadow":
            return {"status": "unavailable"}
        return self._query({}, "events.count")
