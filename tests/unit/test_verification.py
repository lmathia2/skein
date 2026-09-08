from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.approvals import ApprovalStore
from harness.repo.discovery import BuildCommand, RepositoryManifest
from harness.sandbox import SandboxRequest, SandboxResult
from harness.verification import (
    CommandResult,
    ManagedValidationExecutor,
    ValidationCommand,
    ValidationPlan,
    check_scope,
    discover_validation_plan,
    run_validation_plan,
)
from harness.verification.contracts import is_reusable_validation_command
from harness.verification.managed import _fingerprint


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q", True),
        ("timeout 90 npx vitest run", True),
        ("go test ./...", True),
        ("npm test -- --runInBand", True),
        ("rg -n TODO src", False),
        ("git diff --check", False),
    ],
)
def test_reusable_validation_command_detection(command: str, expected: bool) -> None:
    assert is_reusable_validation_command(command) is expected


class _RecordingSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return SandboxResult(
            status="ok",
            exit_code=0,
            stdout="verification passed",
            stderr="",
            duration_ms=21,
        )


def test_validation_does_not_cache_an_expired_approval(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sandbox = _RecordingSandbox(tmp_path)
    executor = ManagedValidationExecutor(tmp_path, state_root=tmp_path / "state", task_id="task", sandbox=sandbox)
    validation = ValidationCommand(category="custom", command="command printf approved", source="fixture")
    request = executor.approvals.request(task_id="task", fingerprint=_fingerprint(validation), operation=validation.command,
        risk="unknown", reason="review", expires_at=(now + timedelta(seconds=60)).isoformat())
    executor.approvals.decide(request.request_id, decision="approved", actor="reviewer")
    assert executor(validation).passed
    monkeypatch.setattr(ApprovalStore, "_now", lambda self: now + timedelta(seconds=120))
    expired = executor(validation)
    assert expired.status == "blocked"
    assert expired.approval_request_id != request.request_id
    assert "requires explicit approval" in expired.stderr
    assert len(sandbox.requests) == 1
    assert executor.policy.approved_fingerprints == set()


def test_discovery_selects_syntax_targeted_tests_and_diff(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (tmp_path / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[
            BuildCommand("lint", "ruff check .", "pyproject.toml"),
            BuildCommand("test", "pytest", "pyproject.toml"),
        ],
    )
    plan = discover_validation_plan(manifest, ["auth.py"])
    assert [item.category for item in plan.commands] == ["syntax", "lint", "test", "diff"]
    assert not next(item for item in plan.commands if item.category == "lint").required
    test = next(item for item in plan.commands if item.category == "test")
    assert test.targeted
    assert "test_auth.py" in test.command


def test_metadata_free_python_change_runs_adjacent_unittest(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("def greet(): return 'hello'\n", encoding="utf-8")
    (tmp_path / "test_hello.py").write_text(
        "import unittest\nfrom hello import greet\n\n"
        "class TestGreet(unittest.TestCase):\n"
        "    def test_greet(self): self.assertEqual(greet(), 'hello')\n",
        encoding="utf-8",
    )
    plan = discover_validation_plan(
        RepositoryManifest(root=tmp_path), ["hello.py", "test_hello.py"]
    )
    tests = [command for command in plan.commands if command.category == "test"]
    assert len(tests) == 1
    assert tests[0].command == (
        "python -m unittest discover -s . -p test_hello.py -v"
    )
    assert tests[0].minimum_test_count == 1


def test_private_python_module_finds_publicly_named_test(tmp_path: Path) -> None:
    plan = discover_validation_plan(
        RepositoryManifest(
            root=Path("/remote/repository"),
            commands=[BuildCommand("test", "pytest", "pyproject.toml")],
            file_paths=frozenset({"src/pkg/_parser.py", "tests/test_parser.py"}),
        ),
        ["src/pkg/_parser.py"],
    )

    assert next(command for command in plan.commands if command.category == "test").command == (
        "pytest tests/test_parser.py"
    )


def test_discovery_runs_changed_test_files(tmp_path: Path) -> None:
    manifest = RepositoryManifest(
        root=Path("/remote/repository"),
        commands=[BuildCommand("test", "pytest", "pyproject.toml")],
        file_paths=frozenset({"src/parser.py", "tests/test_new_behavior.py"}),
    )

    plan = discover_validation_plan(
        manifest, ["src/parser.py", "tests/test_new_behavior.py"]
    )

    test = next(command for command in plan.commands if command.category == "test")
    assert test.command == "pytest tests/test_new_behavior.py"
    assert test.targeted


def test_metadata_free_unittest_must_execute_at_least_one_test(tmp_path: Path) -> None:
    plan = ValidationPlan(
        changed_paths=["hello.py"],
        commands=[ValidationCommand(category="test", command="python -m unittest -v",
            source="fixture", minimum_test_count=1)],
    )

    def execute(command):
        return CommandResult(category=command.category, command=command.command,
            exit_code=0, stderr="Ran 0 tests in 0.000s\n\nOK")

    report, results = run_validation_plan(
        tmp_path, plan, acceptance_criteria=["Implementation works"], executor=execute
    )
    assert not report.passed
    assert results[0].status == "error"
    assert "observed 0" in results[0].stderr


def test_scope_reports_forbidden_and_outside_paths() -> None:
    violations = check_scope(
        ["src/auth.py", "deployment/prod.tf", "README.md"],
        allowed_paths=["src/", "README.md"],
        forbidden_paths=["deployment/**"],
    )
    assert "forbidden path changed: deployment/prod.tf" in violations
    assert all("README.md" not in item for item in violations)


def test_report_requires_commands_scope_and_explicit_criterion_evidence(tmp_path: Path) -> None:
    manifest = RepositoryManifest(root=tmp_path)
    plan = discover_validation_plan(manifest, ["README.md"])

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
        )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Documentation explains setup"],
        criterion_evidence={
            "Documentation explains setup": ["README.md contains the setup section"]
        },
        executor=execute,
    )
    assert report.passed
    assert results[-1].category == "diff"


def test_failed_command_stops_ladder_and_recommends_fix(tmp_path: Path) -> None:
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[BuildCommand("test", "pytest", "pyproject.toml")],
    )
    plan = discover_validation_plan(manifest, [])

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=1 if command.category == "test" else 0,
            stderr="1 failed",
        )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Tests pass"],
        criterion_evidence={"Tests pass": ["pytest"]},
        executor=execute,
    )
    assert not report.passed
    assert len(results) == 1
    assert report.tests_failed == 1
    assert report.recommended_next_action


def test_failed_advisory_check_does_not_skip_behavioral_verification(tmp_path: Path) -> None:
    plan = ValidationPlan(
        changed_paths=["solver.py"],
        commands=[
            ValidationCommand(
                category="lint", command="missing-linter", source="fixture", required=False
            ),
            ValidationCommand(category="test", command="pytest", source="fixture"),
        ],
    )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Implementation works"],
        executor=lambda command: CommandResult(
            category=command.category,
            command=command.command,
            exit_code=1 if command.category == "lint" else 0,
            stdout="1 passed" if command.category == "test" else "",
        ),
    )

    assert report.passed
    assert [result.category for result in results] == ["lint", "test"]
    assert report.recommended_next_action is None


def test_verifier_uses_explicit_managed_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sandbox = _RecordingSandbox(tmp_path)
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    executor = ManagedValidationExecutor(
        tmp_path, state_root=tmp_path / "configured", task_id="test",
        sandbox=sandbox,
    )
    plan = discover_validation_plan(
        RepositoryManifest(root=tmp_path),
        ["README.md"],
    )

    report, results = run_validation_plan(
        tmp_path,
        plan,
        executor=executor,
        acceptance_criteria=["Verification is managed"],
        criterion_evidence={"Verification is managed": ["git diff --check"]},
    )

    assert not (tmp_path / "state").exists()
    assert report.passed
    assert [result.status for result in results] == ["ok"]
    assert len(sandbox.requests) == 1
    assert sandbox.requests[0].command == "git diff --check"
    assert sandbox.requests[0].environment["UV_OFFLINE"] == "1"
    assert sandbox.requests[0].environment["UV_NO_SYNC"] == "1"


def test_executable_change_cannot_pass_with_syntax_and_diff_only(tmp_path: Path) -> None:
    (tmp_path / "solver.py").write_text("print('wrong')\n", encoding="utf-8")
    plan = ValidationPlan(
        changed_paths=["solver.py"],
        commands=[
            ValidationCommand(
                category="syntax",
                command="python -m py_compile solver.py",
                source="changed Python files",
            ),
            ValidationCommand(
                category="diff",
                command="git diff --check",
                source="git",
            ),
        ],
    )

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
        )

    report, _ = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Solver returns the correct result"],
        criterion_evidence={"Solver returns the correct result": ["looks correct"]},
        executor=execute,
    )

    assert not report.passed
    assert report.required_strength == "behavioral"
    assert report.achieved_strength == "static"
    assert report.criteria[0].claimed_evidence == ["looks correct"]
    assert report.criteria[0].evidence == []
    assert "trusted behavioral" in (report.recommended_next_action or "")


def test_successful_behavioral_check_binds_typed_evidence(tmp_path: Path) -> None:
    plan = ValidationPlan(
        changed_paths=["solver.py"],
        commands=[
            ValidationCommand(
                category="test",
                command="pytest -q tests/test_solver.py",
                source="repository test configuration",
            )
        ],
    )

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
            stdout="1 passed",
        )

    report, _ = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Solver returns the correct result"],
        criterion_evidence={"Solver returns the correct result": ["arbitrary prose"]},
        executor=execute,
    )

    assert report.passed
    assert report.achieved_strength == "behavioral"
    reference = report.criteria[0].evidence[0]
    assert reference.reference == "validation:0"
    assert reference.category == "test"
    assert reference.strength == "behavioral"
    assert len(reference.command_sha256) == 64
    assert "arbitrary prose" not in reference.model_dump_json()


def test_baseline_failure_proves_no_regression_but_not_requested_behavior(
    tmp_path: Path,
) -> None:
    broad = ValidationCommand(
        category="test",
        command="pytest -q",
        source="repository test configuration",
    )
    targeted = ValidationCommand(
        category="test",
        command="pytest -q tests/test_solver.py",
        source="adjacent test",
        targeted=True,
    )
    baseline = CommandResult(
        category="test",
        command=broad.command,
        exit_code=1,
        stderr="FAILED tests/test_legacy.py::test_legacy - AssertionError",
    )

    no_regression, _ = run_validation_plan(
        tmp_path,
        ValidationPlan(changed_paths=["solver.py"], commands=[broad]),
        acceptance_criteria=["Solver works"],
        executor=lambda command: baseline,
        baseline_results={broad.command: baseline},
    )
    with_target, _ = run_validation_plan(
        tmp_path,
        ValidationPlan(changed_paths=["solver.py"], commands=[broad, targeted]),
        acceptance_criteria=["Solver works"],
        executor=lambda command: (
            baseline
            if command.command == broad.command
            else CommandResult(
                category="test", command=command.command, exit_code=0, stdout="1 passed"
            )
        ),
        baseline_results={broad.command: baseline},
    )

    assert not no_regression.passed
    assert no_regression.baseline_relative_commands == [broad.command]
    assert no_regression.new_failures == []
    assert no_regression.achieved_strength == "none"
    assert with_target.passed
    assert with_target.achieved_strength == "behavioral"


def test_environmental_evidence_does_not_require_model_completion_prose(
    tmp_path: Path,
) -> None:
    plan = ValidationPlan(
        changed_paths=["solver.py"],
        commands=[
            ValidationCommand(
                category="test",
                command="python held_out_verify.py",
                source="task verification requirement",
            )
        ],
    )

    report, _ = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Solver behavior matches the specification"],
        executor=lambda command: CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
        ),
    )

    assert report.passed
    assert report.criteria[0].claimed_evidence == []
    assert report.criteria[0].evidence[0].strength == "behavioral"


def test_syntax_only_completion_must_be_explicit(tmp_path: Path) -> None:
    plan = ValidationPlan(
        changed_paths=["generated.py"],
        commands=[
            ValidationCommand(
                category="custom",
                command="python -m py_compile generated.py",
                source="task verification requirement",
                strength="behavioral",
            )
        ],
    )

    def execute(command):
        return CommandResult(
            category=command.category,
            command=command.command,
            exit_code=0,
        )

    auto_report, _ = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Generated module is syntactically valid"],
        criterion_evidence={
            "Generated module is syntactically valid": ["python -m py_compile"]
        },
        executor=execute,
    )
    syntax_report, _ = run_validation_plan(
        tmp_path,
        plan,
        acceptance_criteria=["Generated module is syntactically valid"],
        criterion_evidence={
            "Generated module is syntactically valid": ["python -m py_compile"]
        },
        executor=execute,
        required_strength="syntax",
    )

    assert not auto_report.passed
    assert syntax_report.passed
