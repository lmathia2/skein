from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from harness.cli import main, prepare_run
from harness.state import JsonlEventStore
from harness.tracing import TraceSpan, TraceStore


def _repository(root: Path) -> Path:
    root.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=root, check=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=root, check=True)
    return root


def test_hello_command_prints_greeting(capsys) -> None:
    exit_code = main(["hello"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"message": "hello"}


def test_eval_run_prints_and_persists_one_versioned_result(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from harness.evals import runner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    observed = []

    def run(request):
        observed.append(request)
        return runner.EvaluationRunResult(
            task_id=request.task_id,
            run_id="run-1",
            status="answered",
            wall_time_ms=12,
            artifacts=runner.EvaluationArtifacts(
                state_root=state,
                result=state / "evaluation" / "result.json",
            ),
        )

    monkeypatch.setattr(runner, "run_evaluation_sync", run)

    exit_code = main(
        [
            "eval-run",
            "--workspace",
            str(workspace),
            "--state-root",
            str(state),
            "--task-id",
            "smoke-read",
            "--provider",
            "openrouter",
            "--model",
            "meta/muse-spark-1.2-contributor",
            "--reasoning",
            "xhigh",
            "--api-key-env",
            "EVAL_OPENROUTER_KEY",
            "Inspect the fixture",
        ]
    )

    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert len(lines) == 1
    assert json.loads(lines[0])["schema_version"] == "skein-eval-run-v1"
    assert json.loads((state / "evaluation" / "result.json").read_text()) == json.loads(lines[0])
    assert observed[0].provider == "openrouter"
    assert observed[0].model == "meta/muse-spark-1.2-contributor"
    assert observed[0].reasoning == "xhigh"
    assert observed[0].api_key_env == "EVAL_OPENROUTER_KEY"


def test_prepare_run_sets_workspace_identity_environment(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository")
    preparation = prepare_run(
        repository=repository,
        task_id="task-123",
        prompt="Fix the app",
        state_root=tmp_path / "state",
        harness_root=tmp_path,
    )

    assert preparation.workspace.path.exists()
    assert preparation.environment["SKEIN_WORKSPACE"] == preparation.workspace.path.as_posix()
    assert preparation.environment["SKEIN_BASE_REVISION"]
    assert preparation.command == ("agents-cli", "run", "Fix the app")
    payload = json.loads(preparation.to_json())
    assert payload["workspace"]["task_id"] == "task-123"


def test_prepare_cli_prints_machine_readable_launch_contract(
    tmp_path: Path, capsys
) -> None:
    repository = _repository(tmp_path / "repository")
    exit_code = main(
        [
            "prepare",
            "--repository",
            str(repository),
            "--task-id",
            "task-123",
            "--state-root",
            str(tmp_path / "state"),
            "Fix the app",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == ["agents-cli", "run", "Fix the app"]


def test_codex_status_is_redacted_and_does_not_require_network(tmp_path: Path, capsys) -> None:
    exit_code = main(["codex", "--state-root", str(tmp_path / "state"), "status"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "authenticated": False,
        "provider": "openai_codex",
    }


def test_codex_models_reports_saved_default_without_private_state(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    import harness.codex
    from harness.codex import CodexModel, CodexSelection

    monkeypatch.setattr(harness.codex, "credential_manager", lambda state_root: object())
    monkeypatch.setattr(
        harness.codex,
        "discover_codex_models",
        lambda manager: (
            CodexModel(id="gpt-fast", display_name="GPT Fast", priority=1),
        ),
    )
    monkeypatch.setattr(
        harness.codex,
        "load_codex_selection",
        lambda state_root: CodexSelection(
            model="gpt-fast", reasoning="low", client_version=None
        ),
    )

    exit_code = main(["codex", "--state-root", str(tmp_path / "state"), "models"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_model"] == "gpt-fast"
    assert "credential" not in payload


def test_codex_login_cancellation_is_clean(tmp_path: Path, capsys, monkeypatch) -> None:
    from harness.ai.codex_auth import CodexOAuthClient

    monkeypatch.setattr(
        CodexOAuthClient,
        "start_device_authorization",
        lambda self: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    exit_code = main(
        ["codex", "--state-root", str(tmp_path / "state"), "login", "--no-browser"]
    )

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.out == ""
    assert captured.err == "Codex operation cancelled.\n"


def test_codex_login_jsonl_streams_only_public_device_events(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from harness.ai.codex_auth import (
        CodexCredential,
        CodexDeviceAuthorization,
        CodexOAuthClient,
    )

    monkeypatch.setattr(
        CodexOAuthClient,
        "start_device_authorization",
        lambda self: CodexDeviceAuthorization(
            device_auth_id="private-device-id",
            user_code="ABCD-EFGH",
            interval_seconds=1,
        ),
    )
    monkeypatch.setattr(
        CodexOAuthClient,
        "complete_device_authorization",
        lambda self, authorization: CodexCredential(
            access_token="private-access-token",
            refresh_token="private-refresh-token",
            expires_at_ms=4_000_000,
            account_id="account-123456",
        ),
    )

    exit_code = main(
        [
            "codex",
            "--state-root",
            str(tmp_path / "state"),
            "login",
            "--no-browser",
            "--jsonl",
        ]
    )

    assert exit_code == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0] == {
        "type": "device_code",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://auth.openai.com/codex/device",
    }
    assert events[1]["type"] == "authenticated"
    serialized = json.dumps(events)
    assert "private-device-id" not in serialized
    assert "private-access-token" not in serialized
    assert "private-refresh-token" not in serialized


def test_serve_codex_prints_selected_model_without_requiring_login(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"

    exit_code = main(
        [
            "serve-codex",
            "--workspace",
            str(workspace),
            "--state-root",
            str(state),
            "--model",
            "gpt-5.3-codex-spark",
            "--print-config",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Model: gpt-5.3-codex-spark" in captured.err
    payload = json.loads(captured.out)
    assert payload["workspace"] == workspace.as_posix()
    assert payload["state_root"] == state.as_posix()
    assert payload["coding_model"] == {
        "name": "gpt-5.3-codex-spark",
        "provider": "openai_codex",
        "readiness": "authentication_required",
        "role": "coding",
    }


def test_trace_export(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    traces = TraceStore(state / "traces.db")
    traces.append(
        TraceSpan(
            span_id="span-1",
            task_id="task-1",
            sequence=1,
            correlation_id="run-1",
            category="tool",
            phase="success",
            name="read",
            timestamp=datetime.now(UTC).isoformat(),
            content_hash="a" * 64,
            payload_json='{"type":"object"}',
            idempotency_key="span-1",
        )
    )
    assert main(
        ["trace-export", "--state-root", str(state), "--task-id", "task-1"]
    ) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["task_id"] == "task-1"


def test_tuning_export_is_deterministic_and_excludes_authority_surfaces(capsys) -> None:
    assert main(["tuning-export"]) == 0
    first = capsys.readouterr().out
    assert main(["tuning-export"]) == 0
    second = capsys.readouterr().out

    assert first == second
    payload = json.loads(first)
    paths = {parameter["path"] for parameter in payload["parameters"]}
    assert "harness.config.agents.coding_worker.instruction" in paths
    assert "harness.config.workflow.progress.replan_after_no_progress" in paths
    assert "harness.config.context.work_packet_tokens" in paths
    assert "harness.config.notebook_ptc.batching_instruction" in paths
    assert not any("safety" in path or "sandbox" in path for path in paths)
    assert payload["primary_objective"] == {
        "metric": "outcome_passed",
        "direction": "maximize",
    }
    assert "trace-export" in payload["trace_export_command"]


def test_ledger_backfill_cli_reports_replay_equality(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    JsonlEventStore(state / "events").append("task", "task.created", {"goal": "test"})
    assert main(["ledger-backfill", "--state-root", str(state)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["matched"] is True
    assert report["imported_records"]["harness_events"] == 1


def test_notebook_cli_requires_nb_cli_for_rendering(tmp_path: Path, capsys, monkeypatch) -> None:
    state = tmp_path / "state"
    JsonlEventStore(state / "events").append(
        "task-1",
        "task.created",
        {"ledger": {"goal": "Explain state", "acceptance_criteria": []}},
    )
    monkeypatch.setattr("harness.cli.shutil.which", lambda command: None)

    assert main(["notebook", "--state-root", str(state), "--task-id", "task-1"]) == 1
    assert "nb-cli is required" in capsys.readouterr().err


def test_notebook_cli_delegates_rendering_to_nb_cli(tmp_path: Path, capsys, monkeypatch) -> None:
    state = tmp_path / "state"
    JsonlEventStore(state / "events").append(
        "task-1",
        "task.created",
        {"ledger": {"goal": "Explain state", "acceptance_criteria": []}},
    )
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("harness.cli.shutil.which", lambda command: "/venv/bin/nb")
    monkeypatch.setattr("harness.cli.subprocess.run", fake_run)

    assert main(
        [
            "notebook",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
            "--cell-index",
            "-1",
        ]
    ) == 0
    notebook = next((state / "notebooks").glob("*.ipynb"))
    assert calls == [
        (
            [
                "/venv/bin/nb",
                "read",
                notebook.as_posix(),
                "--no-output",
                "--cell-index",
                "-1",
            ],
            False,
        )
    ]

    assert main(["notebook", "--state-root", str(state)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0].endswith(".ipynb")


def test_steer_cli_queues_without_exposing_content_and_reports_status(
    tmp_path: Path,
    capsys,
) -> None:
    state = tmp_path / "state"
    assert main(
        [
            "steer",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
            "--idempotency-key",
            "user-message-1",
            "Use the public parser API",
        ]
    ) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["delivery"] == "next_model_boundary"
    assert receipt["message"]["status"] == "queued"
    assert "content" not in receipt["message"]

    assert main(
        [
            "steering-status",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
        ]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["pending"] is True
    assert status["counts"] == {"acked": 0, "leased": 0, "queued": 1}
    assert "content" not in status["messages"][0]

    assert main(
        [
            "steering-status",
            "--state-root",
            str(state),
            "--task-id",
            "task-1",
            "--include-content",
        ]
    ) == 0
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["messages"][0]["content"] == "Use the public parser API"
