"""Execute validation plans and produce evidence-based completion reports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

from harness.core.models.task import ValidationResult
from harness.core.models.verification import (
    CriterionEvidence,
    EvidenceReference,
    VerificationReport,
)

from .contracts import VerificationStrength
from .models import CommandResult, ValidationCommand, ValidationPlan
from .scope import check_scope


class CommandExecutor(Protocol):
    def __call__(self, command: ValidationCommand, /) -> CommandResult: ...


def enforce_test_count(
    command: ValidationCommand, result: CommandResult
) -> CommandResult:
    """Fail closed when a discovered test runner succeeds without running tests."""

    if not result.passed or command.minimum_test_count == 0:
        return result
    output = f"{result.stdout}\n{result.stderr}"
    counts = [int(value) for value in re.findall(r"Ran\s+(\d+)\s+tests?", output)]
    observed = max(counts, default=0)
    if observed >= command.minimum_test_count:
        return result
    diagnostic = (
        f"expected at least {command.minimum_test_count} test(s), observed {observed}"
    )
    return result.model_copy(
        update={
            "status": "error",
            "exit_code": 1,
            "stderr": f"{result.stderr.rstrip()}\n{diagnostic}".strip(),
        }
    )


def _test_counts(results: list[CommandResult]) -> tuple[int, int]:
    passed = failed = 0
    for result in results:
        if result.category != "test":
            continue
        text = f"{result.stdout}\n{result.stderr}"
        passed_matches = re.findall(r"(\d+)\s+passed", text)
        failed_matches = re.findall(r"(\d+)\s+failed", text)
        passed += sum(int(value) for value in passed_matches)
        failed += sum(int(value) for value in failed_matches)
        if not passed_matches and not failed_matches:
            failed += int(not result.passed)
    return passed, failed


def _diagnostic(result: CommandResult) -> str:
    body = result.stderr.strip() or result.stdout.strip()
    if len(body) > 1_000:
        body = body[:1_000] + "\n[diagnostic truncated]"
    return f"{result.category} failed: {result.command}\n{body}".strip()


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FAILURE_PATTERNS = (
    re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE),
    re.compile(r"^\s*FAIL\s+([^\n]+?)(?:\s+\[|$)", re.MULTILINE),
    re.compile(r"^\s*--- FAIL:\s+(\S+)", re.MULTILINE),
    re.compile(r"^test\s+(\S+)\s+\.\.\.\s+FAILED", re.MULTILINE),
    re.compile(r"^\s*\d+\)\s+([^\n]+)", re.MULTILINE),
)


def failure_identifiers(result: CommandResult) -> list[str]:
    """Extract stable failing-test identities from common test runners."""

    text = _ANSI_ESCAPE.sub("", f"{result.stdout}\n{result.stderr}")
    return sorted(
        {
            " ".join(match.group(1).strip().split())
            for pattern in _FAILURE_PATTERNS
            for match in pattern.finditer(text)
            if match.group(1).strip()
        }
    )


def passes_recorded_baseline(
    result: CommandResult,
    baseline: CommandResult | None,
) -> bool:
    """Accept the same known failing tests as no-regression evidence only."""

    if baseline is None or baseline.passed:
        return False
    current_failures = set(failure_identifiers(result))
    return bool(
        current_failures
        and current_failures <= set(failure_identifiers(baseline))
    )


_STRENGTH_ORDER = {"none": -1, "syntax": 0, "static": 1, "behavioral": 2}
_EXECUTABLE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt",
    ".kts", ".php", ".py", ".pyi", ".rb", ".rs", ".scala", ".sh",
    ".swift", ".ts", ".tsx",
}


def _required_strength(
    changed_paths: list[str],
    requested: VerificationStrength | Literal["auto"],
) -> VerificationStrength:
    if requested != "auto":
        return requested
    if any(Path(path).suffix.lower() in _EXECUTABLE_SUFFIXES for path in changed_paths):
        return "behavioral"
    return "static"


def _verified_references(
    results: list[CommandResult],
    required_strength: VerificationStrength,
) -> tuple[list[EvidenceReference], Literal["none", "syntax", "static", "behavioral"]]:
    passed = [result for result in results if result.passed]
    achieved: Literal["none", "syntax", "static", "behavioral"] = "none"
    for result in passed:
        if _STRENGTH_ORDER[result.strength] > _STRENGTH_ORDER[achieved]:
            achieved = result.strength
    references = [
        EvidenceReference(
            kind="artifact" if result.artifact_uri else "command_result",
            reference=f"validation:{index}",
            command_sha256=hashlib.sha256(result.command.encode()).hexdigest(),
            validation_index=index,
            category=result.category,
            strength=result.strength,
            artifact_uri=result.artifact_uri,
        )
        for index, result in enumerate(results)
        if result.passed
        and _STRENGTH_ORDER[result.strength] >= _STRENGTH_ORDER[required_strength]
    ]
    return references, achieved


def build_report(
    *,
    criteria: list[str],
    results: list[CommandResult],
    scope_violations: list[str],
    criterion_evidence: Mapping[str, list[str]] | None = None,
    changed_paths: list[str] | None = None,
    required_strength: VerificationStrength | Literal["auto"] = "auto",
    baseline_results: Mapping[str, CommandResult] | None = None,
) -> VerificationReport:
    evidence_map = criterion_evidence or {}
    changed = changed_paths or []
    required = _required_strength(changed, required_strength)
    baselines = baseline_results or {}
    effective: list[CommandResult] = []
    new_failures: set[str] = set()
    fixed_failures: set[str] = set()
    baseline_relative_commands: list[str] = []
    for result in results:
        baseline = baselines.get(result.command)
        current_failures = set(failure_identifiers(result))
        baseline_failures = set(failure_identifiers(baseline)) if baseline else set()
        new_failures.update(current_failures - baseline_failures)
        fixed_failures.update(baseline_failures - current_failures)
        baseline_relative = passes_recorded_baseline(result, baseline)
        if baseline_relative:
            baseline_relative_commands.append(result.command)
        effective.append(
            result.model_copy(update={"status": "ok", "exit_code": 0})
            if baseline_relative
            else result
        )
    required_commands_passed = all(
        result.passed for result in effective if result.required
    )
    evidence_results = [
        result
        for result in effective
        if result.command not in baseline_relative_commands
    ]
    verified_references, achieved = _verified_references(evidence_results, required)
    strength_satisfied = _STRENGTH_ORDER[achieved] >= _STRENGTH_ORDER[required]
    criteria_rows = [
        CriterionEvidence(
            criterion=criterion,
            satisfied=(
                required_commands_passed
                and strength_satisfied
            ),
            claimed_evidence=list(evidence_map.get(criterion, [])),
            evidence=list(verified_references),
            notes=(
                "No model completion claim recorded; satisfaction is bound to "
                "environmental verification"
                if not evidence_map.get(criterion)
                else (
                    f"No successful {required} verification was executed"
                    if not strength_satisfied
                    else None
                )
            ),
        )
        for criterion in criteria
    ]
    diagnostics = [
        _diagnostic(result)
        for result, accepted in zip(results, effective, strict=True)
        if not accepted.passed
    ]
    if not strength_satisfied:
        diagnostics.append(
            f"completion requires {required} verification; strongest successful "
            f"check was {achieved}"
        )
    tests_passed, tests_failed = _test_counts(results)
    passed = (
        required_commands_passed
        and not scope_violations
        and strength_satisfied
        and all(row.satisfied for row in criteria_rows)
    )
    next_action: str | None = None
    if scope_violations:
        next_action = "Revert or justify changes outside the permitted scope"
    elif new_failures:
        failures = sorted(new_failures)
        shown = ", ".join(failures[:8])
        remainder = f" (+{len(failures) - 8} more)" if len(failures) > 8 else ""
        next_action = (
            "Fix only the reported failing tests, then rerun targeted verification: "
            f"{shown}{remainder}"
        )
    elif not strength_satisfied:
        next_action = f"Add and pass a trusted {required} verification command"
    elif diagnostics and not passed:
        next_action = "Fix the first failing validation command and rerun verification"
    elif any(not row.satisfied for row in criteria_rows):
        next_action = "Add environmental verification for each unsatisfied criterion"

    return VerificationReport(
        passed=passed,
        criteria=criteria_rows,
        commands_run=[result.command for result in results],
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        scope_violations=scope_violations,
        unresolved_diagnostics=diagnostics,
        changed_paths=changed,
        validations=[
            ValidationResult(
                command=result.command,
                exit_code=result.exit_code,
                passed=accepted.passed,
                summary=(
                    "passed relative to recorded baseline"
                    if result.command in baseline_relative_commands
                    else "passed" if accepted.passed else "failed"
                ),
                duration_ms=result.duration_ms,
                artifact_uri=result.artifact_uri,
            )
            for result, accepted in zip(results, effective, strict=True)
        ],
        new_failures=sorted(new_failures),
        fixed_failures=sorted(fixed_failures),
        baseline_relative_commands=baseline_relative_commands,
        required_strength=required,
        achieved_strength=achieved,
        recommended_next_action=next_action,
    )


def run_validation_plan(
    root: Path,
    plan: ValidationPlan,
    *,
    acceptance_criteria: list[str],
    criterion_evidence: Mapping[str, list[str]] | None = None,
    executor: CommandExecutor,
    stop_on_failure: bool = True,
    required_strength: VerificationStrength | Literal["auto"] = "auto",
    baseline_results: Mapping[str, CommandResult] | None = None,
) -> tuple[VerificationReport, list[CommandResult]]:
    results: list[CommandResult] = []
    for command in plan.commands:
        raw_result = executor(command)
        result = enforce_test_count(command, raw_result).model_copy(
            update={
                "required": command.required,
                "strength": command.effective_strength,
            }
        )
        results.append(result)
        if (
            stop_on_failure
            and command.required
            and not result.passed
            and not passes_recorded_baseline(result, (baseline_results or {}).get(command.command))
        ):
            break
    violations = check_scope(
        plan.changed_paths,
        allowed_paths=plan.allowed_paths,
        forbidden_paths=plan.forbidden_paths,
    )
    return (
        build_report(
            criteria=acceptance_criteria,
            results=results,
            scope_violations=violations,
            criterion_evidence=criterion_evidence,
            changed_paths=plan.changed_paths,
            required_strength=required_strength,
            baseline_results=baseline_results,
        ),
        results,
    )
