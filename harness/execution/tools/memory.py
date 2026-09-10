"""Strict reserved ``memory`` commands, intercepted before shell dispatch."""

from __future__ import annotations

import hashlib
import re
import shlex
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from harness.evidence.ledger import LedgerStore
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory import MemoryProgramRuntime, ViewRequest
from harness.evidence.memory.context import ArtifactReader, bounded_events
from harness.evidence.memory.lance import LanceMemorySearch
from harness.execution.safety.redaction import SecretRedactor

_RESERVED = re.compile(r"^\s*memory(?:\s|$)")
# ponytail: one process owns a state root; do not claim cross-process CAS.
_NOTE_LOCK = threading.RLock()


class ContextProgramService:
    """Task-bound router, receipts and optimistic notes without a new agent tool."""

    def __init__(
        self, ledger: LedgerStore, task_id: str, *, mode: str = "active",
        max_result_bytes: int = 16000, max_scan_events: int = 10000,
        timeout_seconds: float = 2, working_notes: bool = False, reuse: bool = False,
        artifact_reader: ArtifactReader | None = None, authorized_tasks: tuple[str, ...] = (),
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
        return {"status": "ok", "event_id": event.event_id, **event.payload}

    def note_write(self, *, text: str, expected_version: int, operation_id: str,
                   evidence_event_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        if self.mode != "active" or not self.working_notes:
            return {"status": "denied", "reason": "working note writes disabled"}
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id) or expected_version < 0:
            raise ValueError("note requires a safe operation ID and nonnegative expected version")
        payload = {"version": expected_version + 1, "expected_version": expected_version,
                   "text": self.redactor.redact_text(text), "evidence_event_ids": list(evidence_event_ids)}
        if len(canonical_json(payload).encode()) > min(self.max_result_bytes // 2, 8000):
            raise ValueError("note exceeds the bounded working-note budget")
        with _NOTE_LOCK:
            events = self._events()
            key = f"memory-note:{operation_id}"
            prior = next((event for event in events if event.idempotency_key == key), None)
            if prior is not None:
                if prior.payload != payload:
                    raise ValueError("operation ID reused with different note content")
                response = {"status": "ok", "event_id": prior.event_id, **prior.payload}
                if self.on_note:
                    self.on_note(response)
                return response
            current = self.note_read()
            if current.get("version") != expected_version:
                return {"status": "conflict", "current_version": current.get("version")}
            allowed = {event.event_id for event in events}
            if not set(evidence_event_ids) <= allowed:
                return {"status": "denied", "reason": "note cites unavailable task evidence"}
            event = self.ledger.append(
                task_id=self.task_id, source="context_note", source_id=f"{self.task_id}:{operation_id}",
                kind="memory.note", payload=payload, status="completed", idempotency_key=key,
            )
            response = {"status": "ok", "event_id": event.event_id, **payload}
            if self.on_note:
                self.on_note(response)
            return response

    def handoff(self) -> dict[str, Any]:
        """Small advisory metadata; caller allocates its existing dynamic budget."""
        try:
            note = self.note_read()
            hint = (
                "memory history; memory query --program events.count; "
                "memory query --program tools.usage; memory note read"
            )
            if self.runtime.semantic_search is not None:
                hint += "; memory history --query TEXT --retrieval semantic|hybrid (top-k, not exhaustive)"
            return {"memory": self.mode, "note": {key: value for key, value in note.items() if key != "text"},
                    "note_excerpt": str(note.get("text", ""))[:512],
                    "retrieval": hint}
        except (ValueError, OSError, TimeoutError, OverflowError):
            return {"memory": self.mode, "note": {"status": "unavailable"}}

    def _query(self, options: dict[str, str], program: str) -> dict[str, Any]:
        parameters: dict[str, Any] = {"task_id": self.task_id, "program": program,
                                     "max_bytes": max(128, self.max_result_bytes // 2),
                                     "max_scan_events": self.max_scan_events,
                                     "timeout_seconds": self.timeout_seconds}
        names = {"--program": "program", "--query": "query", "--retrieval": "retrieval", "--event-id": "event_id",
                 "--cursor": "cursor", "--as-of": "as_of", "--recorded-before": "recorded_before",
                 "--observed-after": "observed_after", "--limit": "limit", "--version": "version",
                 "--watermark": "watermark", "--artifact-uri": "artifact_uri", "--offset": "offset"}
        names.update({"--byte-offset": "byte_offset", "--byte-limit": "byte_limit"})
        for flag, value in options.items():
            if flag in {"--tasks", "--kinds", "--statuses"}:
                parameters[{"--tasks": "source_tasks", "--kinds": "kinds", "--statuses": "statuses"}[flag]] = tuple(value.split(","))
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
            return {"status": "denied", "reason": "context programs are not active"}
        try:
            if len(command) > 16000 or any(char in command for char in "\x00\r\n"):
                raise ValueError("invalid memory command")
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
                if tokens[2] != "write" or set(options) - {"--text", "--expected-version", "--operation-id", "--evidence"}:
                    raise ValueError("usage: memory note read|write --text TEXT --expected-version N --operation-id ID")
                return self.note_write(text=options["--text"], expected_version=int(options["--expected-version"]),
                                       operation_id=options["--operation-id"],
                                       evidence_event_ids=tuple(filter(None, options.get("--evidence", "").split(","))))
            program = {"history": "history.page", "search": "history.page", "read": "event.read",
                       "event": "event.read", "query": "history.page",
                       "artifact": "artifact.read"}.get(operation)
            if program is None:
                raise ValueError("unknown memory operation")
            return self._query(options, program)
        except (ValueError, KeyError, IndexError, OSError, TimeoutError, OverflowError) as exc:
            return {"status": "unavailable", "reason": self.redactor.redact_text(str(exc))[:512]}

    def shadow(self) -> dict[str, Any]:
        """Fixed read-only probe; caller must not insert its output in model input."""
        if self.mode != "shadow":
            return {"status": "unavailable"}
        return self._query({}, "events.count")
