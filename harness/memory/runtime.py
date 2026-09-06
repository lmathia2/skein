from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from harness.ledger import LedgerEvent, LedgerStore
from harness.ledger.models import canonical_json
from harness.safety.redaction import SecretRedactor

from .context import CONTEXT_PROGRAMS, REVIEWED_PROGRAMS, ArtifactReader, compute_context
from .lance import LanceMemorySearch
from .models import ViewRequest, ViewResult

Program = Callable[[list[LedgerEvent], ViewRequest], dict[str, Any]]
TERMINAL = {"completed", "failed", "blocked", "timeout"}


def _history(events: list[LedgerEvent], _: ViewRequest) -> dict[str, Any]:
    return {
        "events": [
            {
                "seq": event.sequence,
                "at": event.observed_at.isoformat(),
                "kind": event.kind,
                "status": event.status,
                "effect": event.effect,
                "payload": event.payload,
            }
            for event in events
        ]
    }


def _progress(events: list[LedgerEvent], _: ViewRequest) -> dict[str, Any]:
    completed = [event.kind for event in events if event.status == "completed"]
    failed = [event.kind for event in events if event.status in {"failed", "timeout", "blocked"}]
    open_by_correlation: dict[str, LedgerEvent] = {}
    for event in events:
        key = event.correlation_id or event.source_id
        if event.status in {"requested", "started", "open"}:
            open_by_correlation[key] = event
        elif event.status in TERMINAL:
            open_by_correlation.pop(key, None)
    return {
        "completed": completed,
        "in_progress": [event.kind for event in open_by_correlation.values()],
        "failed_or_blocked": failed,
        "last_event": events[-1].kind if events else None,
    }


def _execution_open(events: list[LedgerEvent], _: ViewRequest) -> dict[str, Any]:
    progress = _progress(events, _)
    return {
        "open": progress["in_progress"],
        "effect_unknown": [
            event.kind for event in events if event.effect == "unknown" and event.status != "completed"
        ],
    }


def _time(events: list[LedgerEvent], _: ViewRequest) -> dict[str, Any]:
    if not events:
        return {"first": None, "last": None, "elapsed_seconds": 0}
    first = min(event.observed_at for event in events)
    last = max(event.observed_at for event in events)
    return {
        "first": first.isoformat(),
        "last": last.isoformat(),
        "elapsed_seconds": (last - first).total_seconds(),
    }


def _task_memory(events: list[LedgerEvent], request: ViewRequest) -> dict[str, Any]:
    terms = {term.casefold() for term in (request.query or "").split() if len(term) > 2}
    ranked: list[tuple[int, LedgerEvent]] = []
    for event in events:
        text = canonical_json({"kind": event.kind, "payload": event.payload}).casefold()
        score = sum(term in text for term in terms)
        if score or not terms:
            ranked.append((score, event))
    ranked.sort(key=lambda item: (-item[0], -item[1].sequence))
    return _memory_payload([event for _, event in ranked[:32]], request.query)


def _memory_payload(
    events: list[LedgerEvent], query: str | None, retrieval_version: str | None = None
) -> dict[str, Any]:
    result = {
        "query": query,
        "relevant": [
            {"seq": event.sequence, "kind": event.kind, "status": event.status, "payload": event.payload}
            for event in events
        ],
    }
    if retrieval_version is not None:
        result["retrieval_version"] = retrieval_version
    return result


def _dream(events: list[LedgerEvent], _: ViewRequest) -> dict[str, Any]:
    failures = Counter(event.kind for event in events if event.status in {"failed", "timeout"})
    completions = Counter(event.kind for event in events if event.status == "completed")
    return {
        "failure_patterns": sorted(failures.items(), key=lambda item: (-item[1], item[0])),
        "completion_patterns": sorted(completions.items(), key=lambda item: (-item[1], item[0])),
        "limitations": [
            {"seq": event.sequence, "kind": event.kind, "status": event.status}
            for event in events
            if event.status in {"failed", "timeout", "blocked", "open"} or event.effect == "unknown"
        ],
    }


PROGRAMS: dict[tuple[str, int], Program] = {
    ("history.model", 1): _history,
    ("task.progress", 1): _progress,
    ("execution.open", 1): _execution_open,
    ("time.state", 1): _time,
    ("task.memory", 1): _task_memory,
    ("dream.analysis", 1): _dream,
}


class MemoryProgramRuntime:
    def __init__(
        self, ledger: LedgerStore, *, semantic_search: LanceMemorySearch | None = None,
        authorized_tasks: tuple[str, ...] = (), redactor: SecretRedactor | None = None,
        reuse: bool = False, artifact_reader: ArtifactReader | None = None,
        source_ledgers: Mapping[str, LedgerStore] | None = None,
    ) -> None:
        self.ledger = ledger
        self.semantic_search = semantic_search
        self.authorized_tasks = authorized_tasks
        self.redactor = redactor or SecretRedactor()
        self.reuse = reuse
        self.artifact_reader = artifact_reader
        self.source_ledgers = source_ledgers
        self._known_context_sources: set[str] = set()

    def compute(self, request: ViewRequest) -> ViewResult:
        if request.program in CONTEXT_PROGRAMS | REVIEWED_PROGRAMS:
            return compute_context(
                self.ledger, request, authorized_tasks=self.authorized_tasks,
                redactor=self.redactor, reuse=self.reuse, artifact_reader=self.artifact_reader,
                source_ledgers=self.source_ledgers,
                semantic_search=self.semantic_search,
                known_sources=self._known_context_sources,
            )
        program = PROGRAMS.get((request.program, request.version))
        if program is None:
            raise KeyError(f"unknown memory program: {request.program}@{request.version}")
        events = self.ledger.read(request.task_id, as_of=request.as_of)
        evidence_events = events
        retrieval_version: str | None = None
        if request.program == "task.memory" and request.query and self.semantic_search is not None:
            retrieval_version = f"lancedb:{self.semantic_search.embedding_version}"
            event_by_id = {event.event_id: event for event in events}
            evidence_events = [
                event_by_id[event_id]
                for event_id in self.semantic_search.search(events, request.query)
                if event_id in event_by_id
            ]
            data = _memory_payload(evidence_events, request.query, retrieval_version)
        else:
            data = program(events, request)
        encoded = canonical_json(data).encode()
        truncated = len(encoded) > request.max_bytes
        if truncated:
            data = {"omitted_bytes": len(encoded) - request.max_bytes, "summary": _progress(events, request)}
            if retrieval_version is not None:
                data["retrieval_version"] = retrieval_version
            evidence_events = events
        watermark = max((event.sequence for event in events), default=0)
        evidence = tuple(event.event_id for event in evidence_events)
        view_key = canonical_json(
            {
                "task": request.task_id,
                "program": request.program,
                "version": request.version,
                "watermark": watermark,
                "query": request.query,
                "retrieval_version": retrieval_version,
            }
        )
        return ViewResult(
            view_id=hashlib.sha256(view_key.encode()).hexdigest(),
            task_id=request.task_id,
            program=request.program,
            version=request.version,
            watermark=watermark,
            data=data,
            evidence_event_ids=evidence,
            truncated=truncated,
        )
