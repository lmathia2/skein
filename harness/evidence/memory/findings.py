"""Rebuild a bounded advisory working set from versioned canonical notes."""

from __future__ import annotations

from typing import Any

from harness.evidence.ledger.models import canonical_json

from .models import MemoryFinding, ReadEvidence, ViewRequest


def source_observations(rows: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    hashes, touched, unknown = {}, {}, {}
    for row in rows:
        task, sequence, payload = row["task_id"], row["sequence"], row["payload"]
        for path, digest in payload.get("content_hashes", {}).items():
            hashes[task, path] = (sequence, digest)
        read = payload.get("read_evidence")
        if isinstance(read, dict):
            evidence = ReadEvidence.model_validate(read)
            hashes[task, evidence.path] = (sequence, evidence.sha256)
        for path in payload.get("changed_paths", ()):
            touched[task, path] = sequence
        if payload.get("workspace_may_have_changed") and (
            payload.get("operation") not in {"fs.edit", "fs.write"} or not payload.get("changed_paths")
        ):
            unknown[task] = sequence
    return hashes, touched, unknown


def source_freshness(dependency: dict[str, Any], observations: tuple[dict, dict, dict], task_id: str) -> dict[str, Any]:
    """Compare recorded observations only; never assert current filesystem freshness."""
    read = ReadEvidence.model_validate(dependency)
    hashes, touched, unknown = observations
    boundary, observed_hash = hashes.get((task_id, read.path), (0, read.sha256))
    uncertain = max(touched.get((task_id, read.path), 0), unknown.get(task_id, 0))
    status = ("revalidation_required" if uncertain > boundary else
              "historical_snapshot" if observed_hash == read.sha256 else "changed_since_capture")
    return {**dependency, "status": status, "observed_sha256": observed_hash, "observation_sequence": boundary}


def select_findings(rows: list[dict[str, Any]], request: ViewRequest) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    latest = {row["task_id"]: row for row in rows if row["kind"] == "memory.note"}
    available = {row["event_id"] for row in rows}
    for row in rows:
        payload = row["payload"]
        available.update(ref for ref in (payload.get("artifact_uri"), payload.get("result_artifact_uri"),
                                         *payload.get("artifact_refs", ())) if isinstance(ref, str))
    candidates: list[dict[str, Any]] = []
    focus = set(request.focus)
    observations = source_observations(rows)
    for task, note in latest.items():
        for entry in note["payload"].get("entries", []):
            finding = MemoryFinding.model_validate(entry["finding"])
            if finding.status == "superseded":
                continue
            dependencies = [source_freshness(item, observations, task) for item in entry.get("source_dependencies", [])]
            freshness = next((state for state in ("revalidation_required", "changed_since_capture")
                              if any(item["status"] == state for item in dependencies)), "historical_snapshot")
            candidates.append({
                "finding": finding.model_dump(mode="json"), "revision": entry["revision"],
                "source_dependencies": dependencies,
                "source_task_id": task, "note_event_id": note["event_id"],
                "authority": "advisory", "freshness": freshness,
                "applicability": "current_task_advisory" if task == request.task_id else "prior_run_requires_current_validation",
                "provenance": "unsupported" if not finding.evidence_refs else
                "available" if set(finding.evidence_refs) <= available else "unavailable",
            })
    candidates.sort(key=lambda item: (
        item["source_task_id"] != request.task_id,
        -len(focus.intersection((*item["finding"]["task_links"], *item["finding"]["related_paths"]))),
        item["finding"]["status"] != "disputed",
        item["finding"]["kind"] not in {"open_question", "next_action"},
        -item["revision"], item["source_task_id"], item["finding"]["id"],
    ))
    data: dict[str, Any] = {"findings": [], "omitted_count": len(candidates), "complete": not candidates,
                            "recovery": "memory note read; memory event --event-id NOTE_EVENT_ID"}
    for candidate in candidates:
        proposed = {**data, "findings": [*data["findings"], candidate],
                    "omitted_count": len(candidates) - len(data["findings"]) - 1, "complete": False}
        if len(data["findings"]) < request.limit and len(canonical_json(proposed).encode()) <= request.max_bytes:
            data = proposed
    data["complete"] = data["omitted_count"] == 0
    if len(canonical_json(data).encode()) > request.max_bytes:
        data.pop("recovery")
    evidence = tuple(dict.fromkeys(item["note_event_id"] for item in data["findings"]))
    return data, "ok" if data["complete"] else "partial", evidence
