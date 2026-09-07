"""Deterministic progress accounting and no-progress routing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from harness.models.ledger import TaskLedger


class ProgressRoute(StrEnum):
    CONTINUE = "continue"
    REPLAN = "replan"
    NEEDS_INPUT = "needs_input"
    VERIFY = "verify"


def action_fingerprint(
    tool_name: str,
    arguments: dict[str, Any],
    result_hash: str | None = None,
) -> str:
    payload = {
        "tool": tool_name,
        "arguments": arguments,
        "result_hash": result_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def verification_fingerprint(report: Mapping[str, Any]) -> str:
    """Fingerprint semantic verification evidence, excluding volatile metadata."""

    validations = report.get("validations", [])
    stable_validations = []
    if isinstance(validations, list):
        for validation in validations:
            if not isinstance(validation, Mapping):
                continue
            stable_validations.append(
                {
                    key: validation.get(key)
                    for key in ("category", "command", "required", "strength", "status", "exit_code", "summary")
                }
            )
    criteria = report.get("criteria", [])
    stable_criteria = []
    if isinstance(criteria, list):
        for criterion in criteria:
            if isinstance(criterion, Mapping):
                stable_criteria.append(
                    {key: criterion.get(key) for key in ("criterion", "satisfied")}
                )
    return action_fingerprint(
        "verification",
        {
            "passed": report.get("passed"),
            "commands_run": report.get("commands_run", []),
            "validations": stable_validations,
            "criteria": stable_criteria,
            "tests_passed": report.get("tests_passed", 0),
            "tests_failed": report.get("tests_failed", 0),
            "scope_violations": report.get("scope_violations", []),
            "unresolved_diagnostics": report.get("unresolved_diagnostics", []),
            "required_strength": report.get("required_strength"),
            "achieved_strength": report.get("achieved_strength"),
        },
    )


def register_action(
    ledger: TaskLedger,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result_hash: str | None = None,
    history_limit: int = 20,
) -> TaskLedger:
    fingerprint = action_fingerprint(tool_name, arguments, result_hash)
    data = ledger.model_dump(mode="python")
    history = list(data.get("recent_action_fingerprints", []))
    repeated = bool(history and history[-1] == fingerprint)
    data["no_progress_count"] = int(data.get("no_progress_count", 0)) + 1 if repeated else 0
    history.append(fingerprint)
    data["recent_action_fingerprints"] = history[-history_limit:]
    data["iteration"] = int(data.get("iteration", 0)) + 1
    return TaskLedger.model_validate(data)


def register_action_batch(
    ledger: TaskLedger,
    fingerprints: list[str],
    *,
    history_limit: int = 40,
) -> TaskLedger:
    """Update stagnation from environment-observed actions, not model prose."""

    data = ledger.model_dump(mode="python")
    history = list(data.get("recent_action_fingerprints", []))
    if not fingerprints:
        data["no_progress_count"] = int(data.get("no_progress_count", 0)) + 1
    else:
        repeated = all(fingerprint in history for fingerprint in fingerprints)
        data["no_progress_count"] = (
            int(data.get("no_progress_count", 0)) + 1 if repeated else 0
        )
        history.extend(fingerprints)
        data["recent_action_fingerprints"] = history[-history_limit:]
    return TaskLedger.model_validate(data)


def route_for_progress(
    ledger: TaskLedger,
    *,
    replan_threshold: int = 2,
    human_threshold: int = 4,
) -> ProgressRoute:
    if ledger.status == "needs_input" or (
        getattr(ledger, "blockers", None) and not getattr(ledger, "next_action", None)
    ):
        return ProgressRoute.NEEDS_INPUT
    if ledger.status == "verifying" or ledger.phase == "verify":
        return ProgressRoute.VERIFY
    if ledger.no_progress_count >= human_threshold:
        return ProgressRoute.NEEDS_INPUT
    if ledger.no_progress_count >= replan_threshold:
        return ProgressRoute.REPLAN
    return ProgressRoute.CONTINUE
