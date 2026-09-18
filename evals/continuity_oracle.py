"""Host-owned fixture verdict through the existing managed command boundary."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evals.memory_audit import audit_answer_evidence
from evals.prior_evidence import PriorEvidence
from harness.evidence.ledger.models import LedgerEvent
from harness.evidence.memory.findings import source_freshness, source_observations
from harness.evidence.memory.models import ReadEvidence
from harness.execution.sandbox import CommandSandbox, SandboxRequest, SandboxResult

ORACLE_COMMAND = "python -m unittest discover -s __skein_host_oracle__"


def _answer_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or len(value.encode()) > 512 or any(ord(char) < 32 for char in value) or "\\" in value or path.is_absolute()
            or path.as_posix() != value or any(part in {".", ".."} for part in path.parts)
            or not path.parts):
        raise ValueError("answer path must be a bounded normalized workspace-relative path")
    return value


class AnswerSpec(BaseModel):
    """Host-only expected value and evidence window for one requested artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    expected: Any
    required: list[ReadEvidence] = Field(min_length=1, max_length=128)
    checkpoint: int = Field(ge=0, strict=True)
    validations: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(default_factory=list, max_length=8)

    _path = field_validator("path")(_answer_path)


def completed_validations(events: list[LedgerEvent], command: str, required: list[ReadEvidence]) -> list[LedgerEvent]:
    """Match host-observed successful validation to its completed broker operation."""
    requests = {}
    terminals = {}
    for event in events:
        if event.kind == "capability.requested":
            identity = event.payload.get("operation_id")
            if not identity or identity in requests:
                raise ValueError("validation audit requires unique broker requests")
            requests[identity] = event
        if event.kind in {"capability.completed", "capability.failed", "capability.blocked"}:
            terminals.setdefault(event.payload.get("operation_id"), []).append(event)
    valid = []
    for event in events:
        p = event.payload
        if event.kind != "execution.validation_observed" or p.get("command") != command:
            continue
        request = requests.get(p.get("operation_id"))
        outcomes = terminals.get(p.get("operation_id"), [])
        if len(outcomes) != 1 or request is None:
            raise ValueError("validation observation has no unique broker operation")
        terminal = outcomes[0]
        if (p.get("command_sha256") != hashlib.sha256(command.encode()).hexdigest()
                or not request.sequence < terminal.sequence < event.sequence
                or request.payload.get("operation") != "shell.run" or terminal.payload.get("operation") != "shell.run"
                or request.payload.get("command_sha256") != p.get("command_sha256")
                or terminal.payload.get("command_sha256") != p.get("command_sha256")
                or request.payload.get("arguments_sha256") != terminal.payload.get("arguments_sha256")):
            raise ValueError("validation observation disagrees with its broker identity")
        result = p.get("result", {})
        if (terminal.kind == "capability.completed" and terminal.payload.get("status") == "ok"
                and terminal.payload.get("effect") not in {"unknown", "native_untracked"}
                and result.get("status") == "ok" and type(result.get("exit_code")) is int and result["exit_code"] == 0
                and not result.get("truncated") and not result.get("omitted_bytes")
                and p.get("workspace_before") and p.get("workspace_before") == p.get("workspace_after")):
            observations = source_observations([
                {"task_id": e.task_id, "sequence": e.sequence, "kind": e.kind, "payload": e.payload}
                for e in events if e.sequence < request.sequence and e.kind in {
                    "capability.completed", "capability.failed", "read.observed", "workspace.effect_observed",
                    "execution.validation_observed"}])
            versions = [source_freshness(read.model_dump(), observations, event.task_id) for read in required]
            if all(v["observation_sequence"] > 0 and v["status"] == "historical_snapshot" for v in versions):
                valid.append(event)
    return valid


def audit_answer_contracts(
    events: Iterable[LedgerEvent], specifications: list[AnswerSpec], cuts: list[int],
    *, prior: PriorEvidence | None = None,
) -> dict[str, Any]:
    """Check each submitted artifact's own evidence and declared cut interval."""
    if not 1 <= len(specifications) <= 16 or len({spec.path for spec in specifications}) != len(specifications):
        raise ValueError("one to sixteen unique answer contracts required")
    if any(type(cut) is not int or cut < 1 for cut in cuts) or cuts != sorted(set(cuts)):
        raise ValueError("answer windows require increasing checkpoint sequences")
    retained = sorted(events, key=lambda event: event.sequence)
    published_cuts = sorted(event.sequence for event in retained if event.kind == "compaction.created")
    if cuts != published_cuts:
        raise ValueError("answer windows must include every published checkpoint")
    reports = {}
    for spec in specifications:
        audit = audit_answer_evidence(retained, required=spec.required, answer_path=spec.path, prior=prior)
        validations = {command: completed_validations(retained, command, spec.required) for command in spec.validations}
        start = cuts[spec.checkpoint] if spec.checkpoint < len(cuts) else None
        end = cuts[spec.checkpoint + 1] if spec.checkpoint + 1 < len(cuts) else None
        for answer in audit["answers"]:
            answer["within_window"] = bool(start is not None and answer.get("write_request_sequence", 0) > start
                                           and (end is None or answer["sequence"] < end))
            answer["validations"] = {command: [event.event_id for event in receipts
                                               if event.sequence < answer.get("write_request_sequence", 0)]
                                     for command, receipts in validations.items()}
            answer["validations_completed"] = all(answer["validations"].values())
        latest = audit["answers"][-1] if audit["answers"] else {}
        reports[spec.path] = {**audit, "checkpoint": spec.checkpoint, "after_sequence": start,
                              "before_sequence": end,
                              "latest_supported": latest.get("status") == "available" and bool(latest.get("within_window")) and bool(latest.get("validations_completed")),
                              "all_submissions_supported": bool(audit["answers"]) and all(
                                  a["status"] == "available" and a["within_window"] and a["validations_completed"] for a in audit["answers"])}
    return {"version": "multi-answer-evidence-v3", "artifacts": reports,
            "all_latest_supported": all(a["latest_supported"] for a in reports.values()),
            "all_submissions_supported": all(a["all_submissions_supported"] for a in reports.values())}


def check_answer_artifacts(workspace: Path, expected_json: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Bounded value checks; return actual hashes, never expected values/hashes."""
    if not 1 <= len(expected_json) <= 16:
        raise ValueError("one to sixteen answer artifacts required")
    checks = {}
    for path, expected in expected_json.items():
        _answer_path(path)
        passed, digest = False, None
        answer = workspace / path
        try:
            # Reject aliases in every component, including an in-workspace parent.
            parents = [workspace.joinpath(*PurePosixPath(path).parts[:i])
                       for i in range(1, len(PurePosixPath(path).parts) + 1)]
            if (not any(p.is_symlink() for p in parents) and answer.is_file()
                    and answer.stat().st_size < 4096):
                with answer.open("rb") as stream:
                    raw = stream.read(4096)
                if len(raw) < 4096:
                    digest = hashlib.sha256(raw).hexdigest()
                    actual = json.loads(raw)
                    passed = json.dumps(actual, sort_keys=True, separators=(",", ":"), allow_nan=False) == expected
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        checks[path] = {"passed": passed, "sha256": digest}
    return checks


class HostOracleSandbox:
    """One exact virtual test target; all other commands retain their sandbox.

    No oracle file or guessable expected-answer hash is published to the worker.
    Both model-requested checks and outer verification use this same adapter.
    This is an evaluation backend, not a replacement for production verification.
    """

    def __init__(self, delegate: CommandSandbox, expected: Any,
                 evidence_check: Callable[[dict[str, str]], bool] | None = None, *,
                 additional_answers: Mapping[str, Any] | None = None) -> None:
        self.workspace: Path = delegate.workspace
        self._delegate = delegate
        additional = dict(additional_answers or {})
        if "answer.json" in additional or len(additional) > 15:
            raise ValueError("additional answers must be unique and total at most sixteen")
        self._expected = {_answer_path(path): json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                          for path, value in {"answer.json": expected, **additional}.items()}
        self._evidence_check = evidence_check

    def check_artifacts(self) -> dict[str, dict[str, Any]]:
        return check_answer_artifacts(self.workspace, self._expected)

    def execute(self, request: SandboxRequest) -> SandboxResult:
        if request.command != ORACLE_COMMAND:
            return self._delegate.execute(request)
        started = time.monotonic()
        checks = self.check_artifacts()
        passed = all(check["passed"] for check in checks.values())
        evidence_missing = False
        # Corrupt/unreadable evidence is an infrastructure error, not an ordinary
        # wrong-answer verdict that a model should try to repair by guessing.
        if passed and self._evidence_check is not None:
            evidence_missing = not self._evidence_check({path: check["sha256"] for path, check in checks.items()})
            passed = not evidence_missing
        return SandboxResult(
            status="ok" if passed else "error", exit_code=0 if passed else 1,
            stdout="Host-owned answer check passed." if passed else "",
            stderr="" if passed else (
                "Required source/validation evidence or the assigned checkpoint window was not established for a managed answer write. "
                "A successful validation must be bound to the required source versions at its dispatch. Establish current source identity "
                "before the required check and acquire completed evidence before submitting an answer; later evidence cannot justify earlier writes."
                if evidence_missing else "Answer is missing, invalid, or does not match the requested source evidence."),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
