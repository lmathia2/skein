"""Managed validation executor using the same boundary as model shell commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from harness.evidence.telemetry import MetricsStore, ToolUsageSample
from harness.execution.approvals import ApprovalStore
from harness.execution.safety import ApprovalAction, ApprovalPolicy, CommandRisk, SecretRedactor
from harness.execution.sandbox import MANAGED_COMMAND_ENVIRONMENT, CommandSandbox, SandboxRequest

from .contracts import CommandResult, ValidationCommand


def _fingerprint(validation: ValidationCommand) -> str:
    canonical = json.dumps(
        {
            "category": validation.category,
            "command": validation.command,
            "source": validation.source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ManagedValidationExecutor:
    """Run completion checks under approval, sandbox, redaction, and telemetry."""

    def __init__(
        self,
        root: Path,
        *,
        state_root: Path,
        task_id: str,
        sandbox: CommandSandbox,
        policy: ApprovalPolicy | None = None,
        known_secrets: Sequence[str] = (),
    ) -> None:
        self.root = root.resolve()
        self.task_id = task_id
        self.redactor = SecretRedactor(known_secrets=known_secrets)
        self.approvals = ApprovalStore(state_root / "approvals.db")
        self.metrics = MetricsStore(state_root / "metrics.db")
        self.sandbox = sandbox
        # Approval fingerprints must never leak from one task into another.
        self.policy = replace(policy or ApprovalPolicy(), approved_fingerprints=set())

    def _record(self, validation: ValidationCommand, result: CommandResult) -> None:
        payload = result.model_dump(mode="json")
        self.metrics.record_tool_usage(
            ToolUsageSample(
                task_id=self.task_id,
                invocation_id="verification",
                tool_name=f"verify:{validation.category}",
                status=result.status,
                arguments_hash=_fingerprint(validation),
                result_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                duration_ms=result.duration_ms,
                model_visible_bytes=len(
                    (result.stdout + result.stderr).encode("utf-8", errors="replace")
                ),
                omitted_bytes=result.omitted_bytes,
            )
        )

    def _blocked(
        self,
        validation: ValidationCommand,
        *,
        risk: CommandRisk,
        reason: str,
        request_id: str | None,
    ) -> CommandResult:
        details = f"Validation command not executed ({risk.value}): {reason}."
        if request_id:
            details += (
                f" Approval request: {request_id}. Review in the TUI with /approvals."
            )
        result = CommandResult(
            category=validation.category,
            command=validation.command,
            source=validation.source,
            required=validation.required,
            strength=validation.effective_strength,
            status="blocked",
            exit_code=126,
            stderr=details,
            approval_request_id=request_id,
        )
        self._record(validation, result)
        return result

    def __call__(self, validation: ValidationCommand) -> CommandResult:
        fingerprint = _fingerprint(validation)
        persisted = self.approvals.for_fingerprint(self.task_id, fingerprint)
        policy = self.policy
        if persisted and persisted.status == "approved":
            policy = replace(policy, approved_fingerprints=policy.approved_fingerprints | {fingerprint})
        elif persisted and persisted.status == "denied":
            return self._blocked(
                validation,
                risk=CommandRisk(persisted.risk),
                reason=(
                    f"approval {persisted.status} by {persisted.decided_by or 'reviewer'}"
                    + (
                        f": {persisted.decision_note}"
                        if persisted.decision_note
                        else ""
                    )
                ),
                request_id=persisted.request_id,
            )

        decision = policy.decide(
            validation.command,
            fingerprint=fingerprint,
            workspace=self.root,
        )
        if decision.action != ApprovalAction.ALLOW:
            request_id: str | None = None
            if decision.action == ApprovalAction.REQUIRE_APPROVAL:
                request = self.approvals.request(
                    task_id=self.task_id,
                    fingerprint=fingerprint,
                    operation=self.redactor.redact_text(validation.command),
                    risk=decision.risk.value,
                    reason=(
                        f"{decision.reason}; required by {validation.source}"
                    ),
                )
                request_id = request.request_id
            return self._blocked(
                validation,
                risk=decision.risk,
                reason=decision.reason,
                request_id=request_id,
            )

        sandbox_result = self.sandbox.execute(
            SandboxRequest(
                command=validation.command,
                timeout_seconds=validation.timeout_seconds,
                environment=MANAGED_COMMAND_ENVIRONMENT,
            )
        )
        result = CommandResult(
            category=validation.category,
            command=validation.command,
            source=validation.source,
            required=validation.required,
            strength=validation.effective_strength,
            status=sandbox_result.status,
            exit_code=sandbox_result.exit_code,
            stdout=self.redactor.redact_text(sandbox_result.stdout),
            stderr=self.redactor.redact_text(sandbox_result.stderr),
            duration_ms=sandbox_result.duration_ms,
            truncated=sandbox_result.truncated,
            omitted_bytes=sandbox_result.omitted_bytes,
            artifact_uri=sandbox_result.artifact_uri,
        )
        self._record(validation, result)
        return result
