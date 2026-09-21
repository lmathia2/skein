import importlib.util
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_harbor_eval.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_harbor_eval", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("suite", "count", "attempts"),
    [("smoke", 6, 1), ("broader", 10, 1), ("full", 105, 2)],
)
def test_frozen_suite_plans(suite: str, count: int, attempts: int) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--suite", suite, "--plan"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(completed.stdout)
    assert len(plan["tasks"]) == count
    assert plan["attempts"] == attempts


def test_pier_runs_without_interactive_progress() -> None:
    assert '"--quiet"' in SCRIPT.read_text(encoding="utf-8")


def test_runner_uses_the_same_pier_interface_as_mini_swe_agent(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "openai/gpt-5.5",
        "provider": "openrouter",
        "reasoning": "max",
        "config": "harness/core/config/profiles/four-tool.yaml",
        "max_output_tokens": 16_384,
        "max_task_input_tokens": 200_000,
        "max_iterations": 1_000,
        "api_key_env": "OPENROUTER_API_KEY",
        "agent_import_path": "harness.adapters.pier:SkeinPierAgent",
    })()

    command = runner.run_command(
        {"expected_runtime_seconds": 10_800},
        args,
        1,
        tmp_path / "job",
        tmp_path / "deep-swe-task",
    )

    assert command[:5] == [
        runner.pier_binary(),
        "run",
        "--path",
        str(tmp_path / "deep-swe-task"),
        "--agent-import-path",
    ]
    assert "harness.adapters.pier:SkeinPierAgent" in command
    assert command[command.index("--model") + 1] == "openai/gpt-5.5"
    assert "max_task_input_tokens=200000" in command
    assert "max_iterations=1000" in command
    assert "wall_time_seconds=10800" in command
    assert "--no-delete" in command
    assert str(ROOT) in runner.pier_environment({})["PYTHONPATH"].split(":")


def test_provider_defaults_omit_reasoning_and_output_limit(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "openai/gpt-5.5",
        "provider": "openrouter",
        "reasoning": None,
        "config": "harness/core/config/profiles/four-tool.yaml",
        "max_output_tokens": None,
        "max_task_input_tokens": 200_000,
        "max_iterations": 1_000,
        "api_key_env": "OPENROUTER_API_KEY",
        "agent_import_path": "harness.adapters.pier:SkeinPierAgent",
    })()

    command = runner.run_command(
        {"expected_runtime_seconds": 10_800},
        args,
        1,
        tmp_path / "job",
        tmp_path / "task",
    )

    assert not any("reasoning=" in value for value in command)
    assert not any("max_output_tokens=" in value for value in command)


def test_pi_adapter_receives_the_same_output_limit(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "meta/muse-spark-1.3-contributor", "provider": "openrouter",
        "reasoning": "xhigh", "config": "harness/core/config/profiles/four-tool.yaml",
        "max_output_tokens": 32_768, "max_task_input_tokens": 200_000,
        "max_iterations": 1_000, "api_key_env": "OPENROUTER_API_KEY",
        "agent_import_path": "scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent",
    })()
    command = runner.run_command(
        {"expected_runtime_seconds": 10_800}, args, 1, tmp_path / "job", tmp_path / "task")
    assert "max_output_tokens=32768" in command


def test_pi_adapter_receives_local_provider(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "Qwen3.8-27B-8bit", "provider": "mlx-dspark",
        "reasoning": None, "config": "harness/core/config/profiles/four-tool.yaml",
        "max_output_tokens": 32_768, "max_task_input_tokens": 200_000,
        "max_iterations": 1_000, "api_key_env": "OPENROUTER_API_KEY",
        "agent_import_path": "scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent",
    })()
    command = runner.run_command(
        {"expected_runtime_seconds": 10_800}, args, 1, tmp_path / "job", tmp_path / "task")
    assert "provider_name=mlx-dspark" in command
    assert not any("reasoning=" in value for value in command)


def test_deepswe_tasks_enable_submission_commit_packaging(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "openai/gpt-5.5", "provider": "openrouter", "reasoning": None,
        "config": "harness/core/config/profiles/four-tool.yaml", "max_output_tokens": None,
        "max_task_input_tokens": 200_000, "max_iterations": 1_000,
        "api_key_env": "OPENROUTER_API_KEY",
        "agent_import_path": "harness.adapters.pier:SkeinPierAgent",
    })()
    command = runner.run_command(
        {"benchmark": "deep_swe", "expected_runtime_seconds": 10_800}, args, 1,
        tmp_path / "job", tmp_path / "task")
    assert "commit_workspace=true" in command


def test_plan_accepts_bounded_campaign_concurrency() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--plan",
            "--concurrency",
            "3",
            "--max-task-input-tokens",
            "200000",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    plan = json.loads(completed.stdout)
    assert plan["concurrency"] == 3
    assert plan["max_task_input_tokens"] == 200_000


def test_trackio_metrics_use_the_ledger_record() -> None:
    runner = load_runner()

    metrics = runner.trial_tracking_metrics(
        {
            "task_index": 4,
            "retry": 1,
            "status": "complete",
            "timed_out": False,
            "trial_metrics": [
                {
                    "official_reward": 1,
                    "cost_usd": 0.12,
                    "input_tokens": 100,
                    "cache_read_tokens": 80,
                    "output_tokens": 20,
                    "active_wall_time_seconds": 30,
                    "end_to_end_wall_time_seconds": 40,
                }
            ],
        }
    )

    assert metrics == {
        "task_index": 4.0,
        "retry": 1.0,
        "complete": 1.0,
        "timed_out": 0.0,
        "error_count": 0.0,
        "returncode": 0.0,
        "recovered": 0.0,
        "resumed": 0.0,
        "trial_count": 1.0,
        "official_reward": 1.0,
        "task_cost_usd": 0.12,
        "cost_per_trial_usd": 0.12,
        "total_tokens": 100.0,
        "tokens_per_usd": 833.3333333333334,
        "cost_usd": 0.12,
        "input_tokens": 100.0,
        "uncached_input_tokens": 0.0,
        "cache_read_tokens": 80.0,
        "cache_write_tokens": 0.0,
        "output_tokens": 20.0,
        "active_wall_time_seconds": 30.0,
        "end_to_end_wall_time_seconds": 40.0,
    }


def test_plan_accepts_fail_fast_with_concurrency() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--plan",
            "--concurrency",
            "2",
            "--stop-on-error",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0


def test_plan_can_select_only_deepswe_tasks() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--benchmark",
            "deep_swe",
            "--plan",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    plan = json.loads(completed.stdout)
    assert plan["benchmark"] == "deep_swe"
    assert plan["tasks"] == ["textual-kitty-key-phases", "wazero-multi-module-snapshots"]
    assert plan["retries"] == 0


def test_deepswe_verifier_image_is_built_once_and_pinned(monkeypatch, tmp_path: Path) -> None:
    runner = load_runner()
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "tests" / "Dockerfile").write_text("FROM example@sha256:abc\n")
    (source / "task.toml").write_text(
        '[verifier]\nenvironment_mode = "separate"\n\n[verifier.environment]\n'
    )
    task = {"benchmark": "deep_swe", "artifact_sha256": "a" * 64}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if "inspect" in command else 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(
        runner.subprocess,
        "check_output",
        lambda *args, **kwargs: "sha256:" + "b" * 64 + "\n",
    )
    prepared, image = runner.prepared_task(task, source, tmp_path / "prepared")

    assert image == "sha256:" + "b" * 64
    assert f'docker_image = "{image}"' in (prepared / "task.toml").read_text()
    tag = f"skein-deepswe-verifier:{'a' * 64}"
    assert calls[1][:6] == [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "--tag",
        tag,
    ]
    assert "docker_image" not in (source / "task.toml").read_text()


def test_macos_rejects_private_jobs_dir(monkeypatch) -> None:
    runner = load_runner()
    monkeypatch.setattr(runner.sys, "platform", "darwin")

    with pytest.raises(SystemExit, match="not a reliable Docker Desktop bind mount"):
        runner.validate_jobs_dir(Path("/private/tmp/skein-evals"))


def test_provider_default_reasoning_keeps_explicit_output_limit() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--plan",
            "--reasoning",
            "provider-default",
            "--max-output-tokens",
            "32768",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(completed.stdout)
    assert plan["reasoning"] is None
    assert plan["max_output_tokens"] == 32_768


def test_metadata_rejects_changed_fixed_intelligence(tmp_path: Path) -> None:
    runner = load_runner()
    path = tmp_path / "run-metadata.json"
    metadata = {name: "fixed" for name in runner.CONTRACT_FIELDS}
    runner.write_or_validate_metadata(path, metadata)
    metadata["model"] = "changed"
    with pytest.raises(SystemExit, match="model changed"):
        runner.write_or_validate_metadata(path, metadata)


def test_plan_records_workflow_mode() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--plan",
            "--workflow-mode",
            "thin",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["workflow_mode"] == "thin"


def test_append_row_is_actual_resumable_jsonl(tmp_path: Path) -> None:
    runner = load_runner()
    path = tmp_path / "runs.jsonl"
    runner.append_row(path, {"key": "first", "status": "complete"})
    runner.append_row(path, {"key": "second", "status": "incomplete"})

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert runner.ledger_rows(path) == [
        {"key": "first", "status": "complete"},
        {"key": "second", "status": "incomplete"},
    ]


def test_watchdog_terminates_a_hung_job(tmp_path: Path) -> None:
    runner = load_runner()
    with (
        (tmp_path / "stdout").open("w", encoding="utf-8") as stdout,
        (tmp_path / "stderr").open("w", encoding="utf-8") as stderr,
    ):
        returncode, timed_out = runner.execute(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=ROOT,
            env={},
            stdout=stdout,
            stderr=stderr,
            timeout_seconds=1,
        )
    assert (returncode, timed_out) == (124, True)


def test_concurrent_shutdown_terminates_then_kills_hung_processes(monkeypatch) -> None:
    runner = load_runner()

    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("pier", timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = Process()
    signals = []
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    with runner._PROCESS_LOCK:
        runner._ACTIVE_PROCESSES.add(process)
    try:
        runner.stop_active_processes()
    finally:
        with runner._PROCESS_LOCK:
            runner._ACTIVE_PROCESSES.clear()

    assert signals == [(123, signal.SIGTERM), (123, signal.SIGKILL)]


def test_targeted_preflight_selects_one_task() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--suite",
            "smoke",
            "--plan",
            "--task-id",
            "modernize-scientific-stack",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["tasks"] == ["modernize-scientific-stack"]


def test_completed_harbor_job_is_recovered_without_rerun(tmp_path: Path) -> None:
    runner = load_runner()
    task_dir = tmp_path / "001-example-attempt-01"
    job_dir = task_dir / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps({"verifier_result": {"rewards": {"reward": 0}}}),
        encoding="utf-8",
    )
    recovered = runner.completed_task(tmp_path, "001-example")
    assert recovered is not None
    assert recovered[0] == task_dir
    assert recovered[2] == [0]


def test_scored_harbor_result_is_complete_even_when_skein_stopped_on_budget(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "001-example-attempt-01"
    result = task_dir / "job" / "trial" / "agent" / "skein-state" / "evaluation"
    result.mkdir(parents=True)
    (task_dir / "job" / "trial" / "result.json").write_text(
        json.dumps(
            {
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:02:00Z",
                "agent_execution": {
                    "started_at": "2026-01-01T00:00:10Z",
                    "finished_at": "2026-01-01T00:01:50Z",
                },
                "agent_result": {
                    "metadata": {
                        "skein": {
                            "status": "failed",
                            "error": {"code": "runtime_failed"},
                            "metrics": {"input_tokens": 200, "output_tokens": 30},
                        }
                    }
                },
                "verifier_result": {"rewards": {"reward": 1}},
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "job" / "result.json").write_text("{}", encoding="utf-8")
    (result / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "skein-eval-run-v1",
                "status": "failed",
                "error": {"code": "runtime_failed"},
            }
        ),
        encoding="utf-8",
    )

    recovered = load_runner().completed_task(tmp_path, "001-example")
    assert recovered is not None
    assert recovered[2] == [1]
    assert recovered[3] == [
        {
            "official_reward": 1,
            "active_wall_time_seconds": 100.0,
            "end_to_end_wall_time_seconds": 120.0,
                "input_tokens": 200,
                "uncached_input_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
            "output_tokens": 30,
            "reasoning_tokens": None,
            "cost_usd": None,
            "skein_status": "failed",
            "skein_error_code": "runtime_failed",
        }
    ]


def test_next_attempt_directory_survives_process_restart(tmp_path: Path) -> None:
    runner = load_runner()
    (tmp_path / "001-example-attempt-01").mkdir()
    (tmp_path / "001-example-attempt-03").mkdir()
    assert runner.next_attempt_dir(tmp_path, "001-example").name.endswith("attempt-04")


def test_per_trial_watchdog_does_not_share_one_attempt_budget():
    from types import SimpleNamespace
    runner = load_runner()
    args = SimpleNamespace(per_trial_timeout_seconds=6900, timeout_seconds=None)
    assert runner.task_watchdog_seconds(args, {'expected_runtime_seconds': 5400}, 3) == 26100
    args.per_trial_timeout_seconds = None
    args.timeout_seconds = 7200
    assert runner.task_watchdog_seconds(args, {'expected_runtime_seconds': 5400}, 3) == 7200
