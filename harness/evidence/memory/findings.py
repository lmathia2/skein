"""Rebuild a bounded advisory working set from versioned canonical notes."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from harness.evidence.ledger.models import canonical_json

from .models import MemoryFinding, ReadEvidence, ViewRequest


def _receipt_proves_no_workspace_change(
    receipt: dict[str, Any] | None, *, task_id: str, receipt_id: str | None,
) -> bool:
    """Accept only a complete canonical bash receipt with an exact stable fingerprint."""
    if not isinstance(receipt, dict) or not isinstance(receipt_id, str):
        return False
    before, after = receipt.get("workspace_before"), receipt.get("workspace_after")
    if not (
        receipt.get("task_id") == task_id
        and receipt.get("tool_call_id") == receipt_id
        and receipt.get("tool_name") == "bash"
        and receipt.get("status") == "completed"
        and isinstance(before, str)
        and re.fullmatch(r"[0-9a-f]{64}", before)
        and before == after
    ):
        return False
    raw_result = receipt.get("result_json")
    if not isinstance(raw_result, str):
        return False
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(result, dict)
        and result.get("status") == "ok"
        and type(result.get("exit_code")) is int
        and result["exit_code"] == 0
        and not result.get("truncated")
        and not result.get("omitted_bytes")
    )


def source_observations(rows: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    hashes, touched, unknown = {}, {}, {}
    receipts = {
        (row["task_id"], row["payload"].get("tool_call_id")): row["payload"]
        for row in rows
        if row["kind"] == "tool.bash" and row.get("status") == "completed"
        and isinstance(row.get("payload"), dict)
        and isinstance(row["payload"].get("tool_call_id"), str)
    }
    pending_requests: dict[tuple[str, str], tuple[int, int]] = {}
    validation_candidates: dict[tuple[str, str], tuple[int, int]] = {}
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
        # Successful single-file no-ops still identify the observed file hash.
        # An empty changed-path list is not an unknown workspace-wide mutation.
        known_noop = (row["kind"] == "capability.completed" and payload.get("operation") in {"fs.write", "fs.edit"}
                      and payload.get("status") == "ok" and payload.get("effect") == "observed"
                      and not payload.get("changed_paths") and len(payload.get("content_hashes", {})) == 1
                      and all(isinstance(path, str) and path and isinstance(digest, str)
                              and re.fullmatch(r"[0-9a-f]{64}", digest)
                              for path, digest in payload["content_hashes"].items()))
        operation_id = payload.get("operation_id")
        key = (task, operation_id) if isinstance(operation_id, str) else None
        known_scoped_file_effect = (
            row["kind"] == "capability.completed"
            and payload.get("operation") in {"fs.write", "fs.edit"}
            and payload.get("status") == "ok"
            and payload.get("effect") in {"observed", "changed"}
            and (
                known_noop
                or (
                    bool(payload.get("changed_paths"))
                    and all(isinstance(path, str) and path for path in payload["changed_paths"])
                )
            )
        )
        if known_scoped_file_effect and key:
            requested = pending_requests.pop(key, None)
            if requested and unknown.get(task) == requested[0]:
                # Replace only this request's provisional workspace-wide
                # uncertainty with its terminal path-specific observations.
                unknown[task] = requested[1]
        if payload.get("workspace_may_have_changed") and (
            payload.get("operation") not in {"fs.edit", "fs.write"} or not payload.get("changed_paths")
        ) and not known_noop:
            previous = unknown.get(task, 0)
            unknown[task] = sequence
            if row["kind"] == "capability.requested" and key:
                pending_requests[key] = (sequence, previous)
            elif (row["kind"] == "capability.completed" and payload.get("operation") == "shell.run"
                  and payload.get("status") == "ok" and payload.get("effect") == "observed" and key):
                requested = pending_requests.pop(key, None)
                restore = requested[1] if requested and previous == requested[0] else previous
                receipt_id = payload.get("receipt_id")
                receipt = receipts.get((task, receipt_id)) if isinstance(receipt_id, str) else None
                if _receipt_proves_no_workspace_change(receipt, task_id=task, receipt_id=receipt_id):
                    unknown[task] = restore
                else:
                    validation_candidates[key] = (sequence, restore)
            else:
                # An unrelated unknown effect prevents an older validation from
                # clearing the task-wide watermark.
                for candidate in [item for item in validation_candidates if item[0] == task]:
                    validation_candidates.pop(candidate, None)
        if row["kind"] == "execution.validation_observed" and isinstance(payload.get("operation_id"), str):
            key = (task, payload["operation_id"])
            candidate = validation_candidates.get(key)
            if candidate is None:
                continue
            terminal_sequence, previous = candidate
            result = payload.get("result", {})
            if (unknown.get(task) == terminal_sequence and sequence > terminal_sequence
                    and isinstance(result, dict) and result.get("status") == "ok"
                    and type(result.get("exit_code")) is int and result["exit_code"] == 0
                    and not result.get("truncated") and not result.get("omitted_bytes")
                    and isinstance(payload.get("workspace_before"), str) and payload["workspace_before"]
                    and payload["workspace_before"] == payload.get("workspace_after")):
                # Retract only this successful command's provisional invalidation.
                # Earlier or intervening unknown effects remain unknown; this is
                # advisory freshness, never execution reconciliation/admission.
                unknown[task] = previous
                validation_candidates.pop(key)
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
    selected_tasks = set(request.source_tasks or (request.task_id,))
    latest = {row["task_id"]: row for row in rows if row["kind"] == "memory.note" and row["task_id"] in selected_tasks}
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
            current = {}
            if task != request.task_id:
                checked = [source_freshness(item, observations, request.task_id)
                           for item in entry.get("source_dependencies", [])]
                states = {"unobserved" if item["observation_sequence"] == 0 else item["status"] for item in checked}
                status = next((state for state in ("revalidation_required", "changed_since_capture", "unobserved")
                               if state in states), "matching_observations" if checked else "no_source_dependencies")
                current = {"consumer_versions": {
                    "task_id": request.task_id, "status": status,
                    "scope": "recorded_version_identity_not_finding_truth",
                    "sources": [{"path": item["path"],
                                 "observed_sha256": item["observed_sha256"],
                                 "observation_sequence": item["observation_sequence"],
                                 "status": item["status"]}
                                for item in checked if item["observation_sequence"]],
                }, "reuse": {
                    "strategy": "reference_in_place", "local_note_scope": "new_current_task_learning",
                    "source_note_command": "memory event --tasks " + shlex.quote(task) +
                                           " --event-id " + shlex.quote(note["event_id"]),
                }}
            candidates.append({
                "finding": finding.model_dump(mode="json"), "revision": entry["revision"],
                "source_dependencies": dependencies,
                "source_task_id": task, "note_event_id": note["event_id"],
                "authority": "advisory", "freshness": freshness,
                "applicability": "current_task_advisory" if task == request.task_id else
                "prior_run_version_observed" if current["consumer_versions"]["status"] == "matching_observations" else
                "prior_run_requires_current_validation",
                **current,
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
