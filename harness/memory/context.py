"""Bounded, harness-owned context programs; retained evidence is not authority."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import inspect
import json
import platform
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from harness.ledger import JsonlLedgerStore, LedgerEvent, LedgerStore
from harness.ledger.models import canonical_json, extend_event_hash
from harness.safety.redaction import SecretRedactor

from .lance import LanceMemorySearch
from .models import ViewRequest, ViewResult

ArtifactReader = Callable[[str, int, int], dict[str, Any]]
CONTEXT_PROGRAMS = {"history.page", "event.read", "events.count", "artifact.read"}
REVIEWED_PROGRAMS = {"failures.by_kind"}
EXPOSURE_VERSION = "context-public-v1"
# Explicit field/kind allowlist: never expose raw ADK sessions, traces, heaps or reasoning.
_FIELDS = {
    "context.history": {"role", "parts"},
    "task.created": {"task_id", "objective", "request", "acceptance_criteria"},
    "message.recorded": {"role", "text", "content"},
    "action.recorded": {"action", "summary", "path", "command", "status"},
    "tool.artifact_recorded": {"artifact_uri", "tool_name", "content_hash"},
    "steering.received": {"message", "text", "instruction", "content", "message_id"},
    "verification.completed": {"verification", "passed", "commands", "summary"},
    "task.blocked": {"reason", "summary"},
    "task.finished": {"status", "summary", "outcome"},
    "memory.note": {"version", "text", "evidence_event_ids", "expected_version"},
}
_TOOL_FIELDS = {"tool_name", "status", "artifact_uri", "error", "result_hash"}


def _hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def project(event: LedgerEvent, redactor: SecretRedactor) -> dict[str, Any] | None:
    """The exact retained representation available to the model, not raw payloads."""
    fields = _FIELDS.get(event.kind)
    if event.source == "tool_receipt" and event.kind in {
        "tool.read", "tool.bash", "tool.edit", "tool.write", "tool.python", "tool.execute_code"
    }:
        fields = _TOOL_FIELDS
    if fields is None:
        return None
    if event.kind == "message.recorded" and event.payload.get("role") not in {"user", "assistant"}:
        return None
    payload = {key: value for key, value in event.payload.items() if key in fields}
    if event.kind == "task.created" and isinstance(event.payload.get("ledger"), dict):
        payload["ledger"] = {key: value for key, value in event.payload["ledger"].items()
                             if key in {"goal", "acceptance_criteria", "constraints", "non_goals",
                                        "verification_requirements", "plan", "next_action"}}
    return {
        "event_id": event.event_id, "task_id": event.task_id, "sequence": event.sequence,
        "observed_at": event.observed_at.isoformat(), "recorded_at": event.recorded_at.isoformat(),
        "kind": event.kind, "status": event.status, "effect": event.effect,
        "payload": redactor.redact(payload),
    }


def bounded_events(
    ledger: LedgerStore, tasks: tuple[str, ...], *, maximum: int, deadline: float,
    scan_budget: list[int] | None = None,
) -> list[LedgerEvent]:
    """Bound physical JSONL scan work, not just matched rows or returned output."""
    events: list[LedgerEvent] = []
    remaining = scan_budget if scan_budget is not None else [maximum]
    if isinstance(ledger, JsonlLedgerStore):
        if not ledger.path.exists():
            return []
        with ledger.path.open("rb") as stream:
            scanned = 0
            scanned_bytes = 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("context scan deadline exceeded")
                line = stream.readline(1_000_001)
                if not line:
                    break
                scanned += 1
                remaining[0] -= 1
                scanned_bytes += len(line)
                if remaining[0] < 0 or scanned > maximum or len(line) > 1_000_000 or scanned_bytes > 16_000_000:
                    raise OverflowError("context physical scan budget exceeded")
                if line.strip():
                    event = LedgerEvent.model_validate_json(line)
                    if event.task_id in tasks:
                        events.append(event)
    else:
        from harness.ledger.store import DuckDbLedgerStore

        if not isinstance(ledger, DuckDbLedgerStore):
            raise ValueError("bounded context reads require a supported canonical ledger")
        import duckdb

        with ledger._connect() as connection:
            timer = threading.Timer(max(0.001, deadline - time.monotonic()), connection.interrupt)
            timer.daemon = True
            timer.start()
            try:
                placeholders = ",".join("?" for _ in tasks)
                count, size = connection.execute(
                    f"SELECT count(*), coalesce(sum(length(payload_json)),0) FROM ledger_events WHERE task_id IN ({placeholders})",
                    list(tasks),
                ).fetchone() or (0, 0)
                remaining[0] -= count
                if remaining[0] < 0 or count > maximum or size > 16_000_000:
                    raise OverflowError("context scan budget exceeded")
                rows = connection.execute(
                    f"SELECT * FROM ledger_events WHERE task_id IN ({placeholders}) ORDER BY task_id, sequence LIMIT ?",
                    [*tasks, maximum + 1],
                ).fetchall()
                events = [ledger._from_row(row) for row in rows]
            except duckdb.InterruptException as exc:
                raise TimeoutError("context scan deadline exceeded") from exc
            finally:
                timer.cancel()
            if time.monotonic() >= deadline:
                raise TimeoutError("context scan deadline exceeded")
    return events


def compute_context(
    ledger: LedgerStore, request: ViewRequest, *, authorized_tasks: tuple[str, ...],
    redactor: SecretRedactor, reuse: bool = False, artifact_reader: ArtifactReader | None = None,
    source_ledgers: Mapping[str, LedgerStore] | None = None,
    semantic_search: LanceMemorySearch | None = None,
    known_sources: set[str] | None = None,
) -> ViewResult:
    """Execute only finite reviewed builtins; no evaluation/import of model code."""
    start = time.monotonic()
    deadline = start + request.timeout_seconds
    from . import semantic

    versions = {"python": platform.python_version()}
    for dependency in ("pydantic", "duckdb", "lancedb", "pyarrow"):
        try:
            versions[dependency] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            versions[dependency] = "unavailable"
    program_hash = _hash({"source": inspect.getsource(sys.modules[__name__]) + inspect.getsource(semantic)
                          + inspect.getsource(LanceMemorySearch) + inspect.getsource(SecretRedactor),
                          "program": request.program, "version": request.version,
                          "exposure": EXPOSURE_VERSION, "fields": {k: sorted(v) for k, v in _FIELDS.items()},
                          "tool_fields": sorted(_TOOL_FIELDS), "request_contract": ViewRequest.model_json_schema(),
                          "output_contract": ViewResult.model_json_schema(), "dependencies": versions})
    parameters = request.model_dump(mode="json", exclude={"cursor"})
    parameters["embedding_version"] = semantic_search.embedding_version if semantic_search else None
    parameters["redaction_identity"] = _hash({"known_secrets": sorted(redactor.known_secrets),
                                             "high_entropy": redactor.redact_high_entropy_values})
    execution_hash = _hash(parameters)
    manifest: dict[str, dict[str, Any]] = {}

    def result(data: dict[str, Any], status: Any = "ok", evidence: tuple[str, ...] = (),
               cursor: str | None = None) -> ViewResult:
        return ViewResult(
            view_id=_hash({"execution": execution_hash, "data": data, "cursor": cursor}),
            task_id=request.task_id, program=request.program, version=request.version,
            watermark=int(manifest.get(request.task_id, {}).get("watermark", 0)),
            data=data, evidence_event_ids=evidence, status=status, truncated=status == "partial",
            next_cursor=cursor, program_hash=program_hash, execution_hash=execution_hash,
            source_manifest=manifest,
        )

    tasks = tuple(sorted(set(request.source_tasks or (request.task_id,))))
    if len(tasks) > 16 or not set(tasks) <= set(authorized_tasks):
        return result({"reason": "source scope is not authorized"}, "denied")
    if request.version != 1 or request.program not in CONTEXT_PROGRAMS | (REVIEWED_PROGRAMS if reuse else set()):
        return result({"reason": "program/version is not available"}, "unavailable")
    if request.retrieval != "keyword" and (semantic_search is None or not request.query):
        return result({"reason": "semantic retrieval requires an explicit provider and query"}, "unavailable")
    if request.retrieval != "keyword" and request.program in {"event.read", "artifact.read"}:
        return result({"reason": "exact reads do not apply semantic ranking"}, "unavailable")
    previous: dict[str, Any] | None = None
    if request.cursor:
        try:
            previous = json.loads(base64.urlsafe_b64decode(request.cursor.encode()))
            if not isinstance(previous, dict):
                return result({"reason": "invalid cursor"}, "unavailable")
            if previous["parameters"] != _hash(parameters) or previous["program"] != program_hash:
                return result({"reason": "cursor does not match query or program"}, "unavailable")
        except (ValueError, KeyError, TypeError):
            return result({"reason": "invalid cursor"}, "unavailable")
    try:
        from harness.ledger.store import DuckDbLedgerStore

        if (
            isinstance(ledger, DuckDbLedgerStore)
            and request.program in {"events.count", "failures.by_kind"}
            and not source_ledgers and len(tasks) == 1 and request.watermark is None
            and request.recorded_before is None and request.as_of is None
            and request.observed_after is None and not request.query
            and request.retrieval == "keyword" and not request.kinds and not request.statuses
        ):
            watermark, stream_hash, groups = ledger.event_counts(tasks[0])
            manifest[tasks[0]] = {"watermark": watermark, "hash": stream_hash}
            visible_groups = [row for row in groups if row[1] in _FIELDS or (
                row[0] == "tool_receipt" and row[1] in {"tool.read", "tool.bash", "tool.edit", "tool.write", "tool.python", "tool.execute_code"}
            )]
            if request.program == "failures.by_kind":
                visible_groups = [row for row in visible_groups if row[2] in {"failed", "timeout", "blocked"}]
            counts = dict(sorted((kind, sum(count for _, candidate, _, count in visible_groups if candidate == kind))
                                 for kind in {row[1] for row in visible_groups}))
            statuses = dict(sorted((status, sum(count for _, _, candidate, count in visible_groups if candidate == status))
                                   for status in {row[2] for row in visible_groups}))
            data = {"count": sum(counts.values()), "by_kind": counts, "by_status": statuses,
                    "complete": True, "evidence_manifest_hash": _hash([counts, statuses])}
            execution_hash = _hash({"parameters": parameters, "sources": manifest, "program": program_hash})
            return result(data)
        if source_ledgers:
            events = []
            scan_budget = [request.max_scan_events]
            for task in tasks:
                events.extend(bounded_events(source_ledgers.get(task, ledger), (task,),
                                             maximum=request.max_scan_events - len(events), deadline=deadline,
                                             scan_budget=scan_budget))
        else:
            events = bounded_events(ledger, tasks, maximum=request.max_scan_events, deadline=deadline)
        visible: list[dict[str, Any]] = []
        for task in tasks:
            source = [event for event in events if event.task_id == task and event.kind not in {"memory.retrieval", "memory.summary"}]
            if not source and (task != request.task_id or (known_sources is not None and task in known_sources)):
                return result({"reason": "selected source evidence is unavailable or erased"}, "unavailable")
            if source and known_sources is not None:
                known_sources.add(task)
            boundary = max((event.sequence for event in source), default=0)
            if request.watermark is not None:
                boundary = min(boundary, request.watermark)
            if previous:
                boundary = int(previous["manifest"][task]["watermark"])
            retained = [event for event in source if event.sequence <= boundary]
            stream_hash = ""
            for event in retained:
                stream_hash = extend_event_hash(stream_hash, event.event_id, event.payload_hash)
            manifest[task] = {"watermark": boundary, "hash": stream_hash}
            if previous and manifest[task] != previous["manifest"][task]:
                return result({"reason": "snapshot evidence changed or was erased"}, "unavailable")
            for event in retained:
                if time.monotonic() >= deadline:
                    raise TimeoutError("context construction deadline exceeded")
                if request.recorded_before and event.recorded_at > request.recorded_before:
                    continue
                if request.as_of and event.observed_at > request.as_of:
                    continue
                if request.observed_after and event.observed_at < request.observed_after:
                    continue
                row = project(event, redactor)
                if row is None or (request.kinds and event.kind not in request.kinds):
                    continue
                if request.statuses and event.status not in request.statuses:
                    continue
                if request.retrieval == "keyword" and request.query and request.query.casefold() not in canonical_json(row).casefold():
                    continue
                visible.append(row)
        visible.sort(key=lambda row: (row["task_id"], row["sequence"]))
        ranked_partial = False
        if request.retrieval != "keyword":
            from .semantic import search_bounded

            assert semantic_search is not None and request.query is not None
            safe_rows = {row["event_id"]: row for row in visible}
            safe_events = [event.model_copy(update={"payload": safe_rows[event.event_id]["payload"]})
                           for event in events if event.event_id in safe_rows]
            ranked_ids = search_bounded(semantic_search, safe_events, request.query,
                                        request.retrieval, deadline - time.monotonic())
            visible = [safe_rows[event_id] for event_id in dict.fromkeys(ranked_ids) if event_id in safe_rows]
            ranked_partial = True  # semantic top-k is never an exact full-set predicate
        execution_hash = _hash({"parameters": parameters, "sources": manifest, "program": program_hash})
        if request.program in {"events.count", "failures.by_kind"}:
            if request.program == "failures.by_kind":
                visible = [row for row in visible if row["status"] in {"failed", "timeout", "blocked"}]
            counts = dict(sorted(Counter(row["kind"] for row in visible).items()))
            statuses = dict(sorted(Counter(row["status"] for row in visible).items()))
            data = {"count": len(visible), "by_kind": counts, "by_status": statuses, "complete": not ranked_partial,
                    "evidence_manifest_hash": _hash([counts, statuses])}
            if len(canonical_json(data).encode()) > request.max_bytes:
                return result({"reason": "aggregate output exceeds budget", "complete": False}, "partial")
            return result(data, "partial" if ranked_partial else "ok")
        if request.program in {"event.read", "artifact.read"}:
            visible = [row for row in visible if row["event_id"] == request.event_id]
            if not visible:
                return result({"reason": "event is unavailable in the authorized projection"}, "unavailable")
        if request.program == "event.read" and (
            request.byte_limit is not None or request.byte_offset or
            len(canonical_json(visible[0]).encode()) > request.max_bytes // 2
        ):
            encoded = canonical_json(visible[0]).encode()
            offset = request.byte_offset
            if offset > len(encoded):
                return result({"reason": "event byte offset is out of range"}, "unavailable")
            end = min(len(encoded), offset + min(request.byte_limit or 4000, request.max_bytes // 4))
            # Return exact UTF-8 bytes; cursor offsets always land on character boundaries.
            while end > offset and end < len(encoded) and encoded[end] & 0xC0 == 0x80:
                end -= 1
            try:
                text = encoded[offset:end].decode("utf-8")
            except UnicodeDecodeError:
                return result({"reason": "event offset must be a UTF-8 boundary"}, "unavailable")
            return result({"event_id": visible[0]["event_id"], "text": text,
                           "byte_offset": offset, "next_byte_offset": end if end < len(encoded) else None,
                           "total_bytes": len(encoded), "representation_hash": hashlib.sha256(encoded).hexdigest()},
                          "partial" if end < len(encoded) else "ok", (visible[0]["event_id"],))
        if request.program == "artifact.read":
            uri = visible[0]["payload"].get("artifact_uri")
            if not uri or uri != request.artifact_uri or artifact_reader is None:
                return result({"reason": "artifact is not authorized or reader unavailable"}, "unavailable")
            data = redactor.redact(artifact_reader(uri, request.offset, request.limit))
            if time.monotonic() >= deadline:
                raise TimeoutError("artifact read deadline exceeded")
            if len(canonical_json(data).encode()) > request.max_bytes:
                return result({"reason": "artifact range exceeds output budget"}, "partial")
            return result(data, evidence=(visible[0]["event_id"],))
        offset = int(previous["offset"]) if previous else 0
        if offset < 0 or offset > len(visible):
            return result({"reason": "invalid cursor offset"}, "unavailable")
        page: list[dict[str, Any]] = []
        for row in visible[offset:offset + request.limit]:
            if len(canonical_json({"events": [*page, row]}).encode()) > request.max_bytes:
                break
            page.append(row)
        if not page and visible[offset:]:
            return result({"reason": "event exceeds page budget; use event.read with byte ranges",
                           "event_id": visible[offset]["event_id"]}, "partial")
        more = offset + len(page) < len(visible)
        cursor = base64.urlsafe_b64encode(canonical_json({
            "parameters": _hash(parameters), "program": program_hash, "manifest": manifest,
            "offset": offset + len(page),
        }).encode()).decode() if more else None
        page_data: dict[str, Any] = {"events": page}
        if ranked_partial:
            page_data["coverage"] = "semantic_top_k"
        return result(page_data, "partial" if more or ranked_partial else "ok",
                      tuple(row["event_id"] for row in page), cursor)
    except TimeoutError as exc:
        return result({"reason": str(exc)}, "timeout")
    except OverflowError as exc:
        return result({"reason": str(exc), "complete": False}, "partial")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return result({"reason": "evidence unavailable", "error_type": type(exc).__name__}, "unavailable")
