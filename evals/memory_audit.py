"""Reproducible read-coverage audit; fetched lines are not model-visible lines."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from evals import prior_evidence
from evals.prior_evidence import PriorEvidence
from harness.evidence.ledger import LedgerEvent
from harness.evidence.memory.models import ReadEvidence

VERSION = "read-coverage-v1"
EXPOSURE_VERSION = "source-equivalent-exposure-v2"


def _exposed_lines(text: str) -> Iterable[str]:
    """Decode known public recovery/read JSON, never arbitrary Python output."""
    for line in text.splitlines():
        yield line
        if not line.startswith("{") or len(line.encode()) > 128_000:
            continue
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, dict) or value.get("status") not in ("ok", "partial"):
            continue
        if value.get("program") == "read.recover" and isinstance(data.get("text"), str):
            yield from data["text"].splitlines()
        elif {"path", "sha256", "offset", "returned_lines", "text"} <= data.keys() and isinstance(data["text"], str):
            yield from data["text"].splitlines()
            if isinstance(value.get("model_text"), str):
                yield from value["model_text"].splitlines()


def audit_emissions(snapshots: list[dict[str, Any]], records: list[dict[str, Any]], *, cut_sequence: int) -> dict[str, Any]:
    """Conservative exact-line matching across selected output and decoded wire text.

    Unique nontrivial source lines only. Ambiguous, transformed, short and missing
    content remains unmapped; matching content is not proof of semantic rediscovery.
    Each provider_request record contains one complete request's public input text.
    """
    if len(snapshots) > 1000 or len(records) > 10000:
        raise ValueError("exposure audit exceeds record budget")
    if sum(len(str(item.get("text", "")).encode()) for item in [*snapshots, *records]) > 16_000_000:
        raise ValueError("exposure audit exceeds input byte budget")
    index: dict[str, set[tuple[str, str, int]]] = defaultdict(set)
    for snapshot in snapshots:
        read = ReadEvidence.model_validate(snapshot["read_evidence"])
        lines = snapshot["text"].splitlines()
        if len(lines) != read.returned_lines:
            raise ValueError("snapshot does not match captured range")
        for offset, line in enumerate(lines, read.offset):
            if len(line.strip()) >= 12:
                index[line].add((read.path, read.sha256, offset))
        if sum(len(items) for items in index.values()) > 100_000:
            raise ValueError("exposure source index exceeds line budget")
    counts: Counter[str] = Counter()
    routes: dict[str, Counter[str]] = defaultdict(Counter)
    emitted: set[tuple[str, str, int]] = set()
    before_cut: set[tuple[str, str, int]] = set()
    allowed = {"ptc_output", "direct_read", "shell_output", "artifact_output", "provider_request"}
    seen_ids: set[str] = set()
    for record in sorted(records, key=lambda item: (item["sequence"], item["id"])):
        if record["id"] in seen_ids or record["route"] not in allowed:
            raise ValueError("duplicate exposure identity or unknown route")
        seen_ids.add(record["id"])
        route = record["route"]
        request_seen: set[tuple[str, str, int]] = set()
        post_cut = record["sequence"] > cut_sequence
        for raw in _exposed_lines(record["text"]):
            line = re.sub(r"^\s*\d+ \| ", "", raw)  # direct read's exact line-number renderer
            choices = index.get(line, set())
            if len(choices) != 1:
                routes[route]["unmapped_nonempty_lines"] += bool(line.strip())
                counts["ambiguous_lines"] += len(choices) > 1
                continue
            identity = next(iter(choices))
            routes[route]["mapped_lines"] += 1
            if route == "provider_request":
                if post_cut:
                    counts["provider_transmitted_mapped_lines"] += 1
                    counts["provider_within_request_duplicate_lines"] += identity in request_seen
                request_seen.add(identity)
            else:
                if post_cut:
                    counts["post_cut_emitted_mapped_lines"] += 1
                    counts["post_cut_emitted_duplicate_lines"] += identity in emitted
                    counts["post_cut_pre_cut_duplicate_lines"] += identity in before_cut
                else:
                    before_cut.add(identity)
                emitted.add(identity)
    return {"version": EXPOSURE_VERSION, "cut_sequence": cut_sequence, "counts": dict(sorted(counts.items())),
            "routes": {route: dict(sorted(value.items())) for route, value in sorted(routes.items())},
            "coverage": "unique exact nontrivial source-equivalent lines; conservative, not exhaustive lineage",
            "unknown": ["short or ambiguous lines", "transformed output", "uncaptured or truncated requests"],
            "measurement_sha256": hashlib.sha256((inspect.getsource(audit_emissions) + inspect.getsource(_exposed_lines)).encode()).hexdigest(),
            "inputs_sha256": hashlib.sha256(json.dumps([snapshots, records], sort_keys=True).encode()).hexdigest()}


def merged(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union half-open intervals without double-counting overlapping reads."""
    result: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def audit_answer_evidence(
    events: Iterable[LedgerEvent], *, required: list[ReadEvidence], answer_path: str = "answer.json",
    prior: PriorEvidence | None = None,
) -> dict[str, Any]:
    """Audit completed source availability before each managed answer mutation.

    Requirements are host-frozen path/version/ranges, not model citations. This
    establishes availability in task history, NOT consumption, live heap survival,
    current filesystem freshness, or semantic dependence of an answer on a read.
    Write dispatch is observable; arbitrary Python answer construction is not.
    """
    ordered = sorted(events, key=lambda event: event.sequence)
    if not required or any(read.returned_lines < 1 for read in required):
        raise ValueError("nonempty decisive source ranges required")
    if len(ordered) > 100_000 or len(required) > 128:
        raise ValueError("answer evidence audit exceeds record budget")
    if len({e.task_id for e in ordered}) > 1 or len({e.sequence for e in ordered}) != len(ordered):
        raise ValueError("audit requires unique sequences from exactly one task")
    requests = {}
    reads: list[tuple[LedgerEvent, ReadEvidence]] = []
    unmapped: list[int] = []
    rows = []
    for event in ordered:
        payload = event.payload
        operation = payload.get("operation")
        identity = payload.get("operation_id")
        if event.kind == "capability.requested" and identity:
            if identity in requests:
                raise ValueError("duplicate capability request identity")
            requests[identity] = event
        if event.kind not in {"capability.completed", "read.observed"}:
            continue
        if event.kind == "capability.completed" and payload.get("status") != "ok":
            continue
        if operation == "fs.read":
            if payload.get("read_evidence"):
                reads.append((event, ReadEvidence.model_validate(payload["read_evidence"])))
            else:
                unmapped.append(event.sequence)
        # No guessed shell/program lineage. Recovery cannot enlarge a captured
        # range; foreign/prior artifacts need an explicit scope-aware mapping.
        elif operation not in {"fs.write", "fs.edit"}:
            unmapped.append(event.sequence)
        if answer_path not in {*payload.get("changed_paths", []), *payload.get("content_hashes", {})}:
            continue
        if operation not in {"fs.write", "fs.edit"}:
            continue
        request = requests.get(identity)
        if request is None or request.payload.get("operation") != operation or request.payload.get("arguments_sha256") != payload.get("arguments_sha256"):
            rows.append({"event_id": event.event_id, "sequence": event.sequence,
                         "status": "unknown", "reason": "unmatched answer mutation request"})
            continue
        boundary = request.sequence
        prior_reads = prior.reads_before(ordered, boundary) if prior is not None else []
        coverage = []
        for need in required:
            matches = [(e, read) for e, read in reads if e.sequence < boundary
                       and (read.path, read.sha256) == (need.path, need.sha256)]
            start, end = need.offset, need.offset + need.returned_lines
            intervals = merged((max(start, read.offset), min(end, read.offset + read.returned_lines))
                               for _, read in matches)
            inherited = [item for item in prior_reads if
                         (item["read_evidence"]["path"], item["read_evidence"]["sha256"]) == (need.path, need.sha256)]
            intervals = merged([*intervals, *((max(start, item["read_evidence"]["offset"]),
                                               min(end, item["read_evidence"]["offset"] + item["read_evidence"]["returned_lines"]))
                                              for item in inherited)])
            covered = sum(hi - lo for lo, hi in intervals)
            coverage.append({**need.model_dump(), "covered_lines": covered,
                             "complete": covered == need.returned_lines,
                             "event_ids": [e.event_id for e, read in matches
                                           if read.offset < end and read.offset + read.returned_lines > start],
                             **({"prior_evidence": inherited} if prior is not None else {})})
        unknown_routes = [seq for seq in unmapped if seq < boundary]
        complete = all(row["complete"] for row in coverage)
        rows.append({"event_id": event.event_id, "sequence": event.sequence,
                     "write_request_sequence": boundary,
                     "answer_sha256": payload.get("content_hashes", {}).get(answer_path),
                     "status": "available" if complete else "unknown" if unknown_routes else "missing",
                     "requirements": coverage, "unmapped_route_sequences": unknown_routes})
    return {"version": "answer-source-availability-v2" if prior is not None else "answer-source-availability-v1", "answer_path": answer_path,
            "task_id": ordered[0].task_id if ordered else None, "answers": rows,
            "first_answer": rows[0]["status"] if rows else "no_managed_answer",
            "last_answer": rows[-1]["status"] if rows else "no_managed_answer",
            "all_answers_source_available": bool(rows) and all(row["status"] == "available" for row in rows),
            "scope": "completed task-local ranges and explicitly mapped owned prior findings/recovery before write dispatch; not semantic use"
                     if prior is not None else "completed task-local source ranges before managed answer write dispatch; not semantic use",
            "limitations": ["unmapped shell/artifact/prior-run routes", "untracked answer mutations",
                            "requirements identify a frozen source version, not continuous freshness"],
            "requirements_sha256": hashlib.sha256(json.dumps(
                [read.model_dump() for read in required], sort_keys=True).encode()).hexdigest(),
            "events_sha256": hashlib.sha256("\n".join(
                event.model_dump_json() for event in ordered).encode()).hexdigest(),
            "measurement_sha256": hashlib.sha256((inspect.getsource(audit_answer_evidence)
                                                  + inspect.getsource(merged)
                                                  + (inspect.getsource(prior_evidence) if prior is not None else "")).encode()).hexdigest(),
            **({"prior_inputs_sha256": hashlib.sha256(canonical_prior(prior).encode()).hexdigest()}
               if prior is not None else {})}


def canonical_prior(prior: PriorEvidence) -> str:
    """Freeze host authorization and producer evidence as audit inputs, not grants."""
    return json.dumps({"current": prior.current.model_dump(mode="json"), "sources": [
        {"bindings": source.bindings.model_dump(mode="json"),
         "events": [event.model_dump(mode="json") for event in source.events],
         "receipts": [receipt.model_dump(mode="json") for receipt in source.receipts]}
        for source in prior.sources]}, sort_keys=True)


def audit_reads(events: Iterable[LedgerEvent], *, cut_sequence: int) -> dict[str, Any]:
    """Compare reads after one cut with earlier evidence in that same task.

    The cut is a canonical-ledger sequence, not a compatibility-store sequence.
    Multiple cuts can be audited separately; their post-cut totals are not additive.
    """
    ordered = sorted(events, key=lambda event: event.sequence)
    if len({event.task_id for event in ordered}) > 1:
        raise ValueError("audit exactly one task at a time")
    if len({event.sequence for event in ordered}) != len(ordered):
        raise ValueError("duplicate event sequence")
    prior: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    prior_paths: set[str] = set()
    all_ranges: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    operations: Counter[str] = Counter()
    requested: set[tuple[str, str]] = set()
    for event in ordered:
        operation = str(event.payload.get("operation", ""))
        if event.kind == "capability.requested":
            identity = (operation, str(event.payload.get("arguments_sha256", "")))
            if event.sequence > cut_sequence:
                category = str(event.payload.get("discovery_kind", operation))
                operations[category] += 1
                operations["repeated_discovery_requests"] += category == "search" and identity in requested
            requested.add(identity)
        if event.sequence > cut_sequence and event.kind == "capability.failed" and operation == "fs.read":
            operations["failed_file_reads"] += 1
        if event.kind not in {"capability.completed", "read.observed"} or event.payload.get("operation") != "fs.read":
            continue
        raw = event.payload.get("read_evidence")
        if raw is None:
            counts["unaddressed_reads"] += 1
            continue
        evidence = ReadEvidence.model_validate(raw)
        key = (evidence.path, evidence.sha256)
        start, end = evidence.offset, evidence.offset + evidence.returned_lines
        if event.sequence > cut_sequence:
            overlap = sum(max(0, min(end, hi) - max(start, lo)) for lo, hi in prior[key])
            earlier_overlap = sum(
                max(0, min(end, hi) - max(start, lo)) for lo, hi in all_ranges[key]
            )
            category = (
                "empty" if start == end else
                "fully_covered" if overlap == end - start else
                "partly_covered" if overlap else
                "new_range" if prior.get(key) else
                "different_version" if evidence.path in prior_paths else "new_path"
            )
            counts[category] += 1
            counts["post_cut_reads"] += 1
            counts["returned_lines"] += end - start
            counts["pre_cut_overlap_lines"] += overlap
            counts["all_earlier_overlap_lines"] += earlier_overlap
            rows.append({"event_id": event.event_id, "sequence": event.sequence,
                         **evidence.model_dump(), "category": category,
                         "pre_cut_overlap_lines": overlap,
                         "all_earlier_overlap_lines": earlier_overlap})
        else:
            prior[key] = merged([*prior[key], (start, end)])
            prior_paths.add(evidence.path)
            counts["pre_cut_reads"] += 1
        all_ranges[key] = merged([*all_ranges[key], (start, end)])
    return {
        "version": VERSION, "cut_sequence": cut_sequence,
        "task_id": ordered[0].task_id if ordered else None,
        "pre_cut_paths": len(prior_paths), "counts": dict(sorted(counts.items())),
        "reads": rows,
        "operations": dict(sorted(operations.items())),
        "exposure": {"model_visible_duplicate_lines": None,
                     "provider_retained_duplicate_lines": None,
                     "coverage": "fs.read receipts only; not a measure of model exposure",
                     "unmeasured_routes": ["shell excerpts", "artifact loads", "Python output"]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--cut-sequence", required=True, type=int)
    parser.add_argument("--exposure", type=Path, help="Frozen JSON containing snapshots and selected output/provider records")
    args = parser.parse_args()
    events = []
    digest = hashlib.sha256()
    size = 0
    with args.ledger.open("rb") as stream:
        while line := stream.readline(1_000_001):
            size += len(line)
            if len(line) > 1_000_000 or size > 64_000_000 or len(events) >= 100_000:
                raise ValueError("audit input exceeds bounded scan budget")
            digest.update(line)
            event = LedgerEvent.model_validate_json(line)
            if event.task_id == args.task:
                events.append(event)
    if not events or args.cut_sequence not in {event.sequence for event in events}:
        raise ValueError("task/cut not found in ledger")
    result = audit_reads(events, cut_sequence=args.cut_sequence)
    if args.exposure:
        with args.exposure.open("rb") as stream:
            encoded = stream.read(32_000_001)
        if len(encoded) > 32_000_000:
            raise ValueError("exposure artifact exceeds input budget")
        exposure = json.loads(encoded)
        result["exposure"] = audit_emissions(exposure["snapshots"], exposure["records"], cut_sequence=args.cut_sequence)
    result["source"] = {"path": str(args.ledger.resolve()), "sha256": digest.hexdigest()}
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
