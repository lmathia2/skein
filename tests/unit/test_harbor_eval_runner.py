import importlib.util
import json
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
        "config": "harness/config/profiles/four-tool.yaml",
        "max_output_tokens": 16_384,
        "api_key_env": "OPENROUTER_API_KEY",
    })()

    command = runner.run_command(
        {}, args, 1, tmp_path / "job", tmp_path / "deep-swe-task"
    )

    assert command[:5] == [
        runner.pier_binary(),
        "run",
        "--path",
        str(tmp_path / "deep-swe-task"),
        "--agent-import-path",
    ]
    assert "harness.evals.harbor:SkeinPierAgent" in command
    assert command[command.index("--model") + 1] == "openai/gpt-5.5"
    assert "max_task_input_tokens=1000000000" in command
    assert str(ROOT) in runner.pier_environment({})["PYTHONPATH"].split(":")


def test_provider_defaults_omit_reasoning_and_output_limit(tmp_path: Path) -> None:
    runner = load_runner()
    args = type("Args", (), {
        "model": "openai/gpt-5.5",
        "provider": "openrouter",
        "reasoning": None,
        "config": "harness/config/profiles/four-tool.yaml",
        "max_output_tokens": None,
        "api_key_env": "OPENROUTER_API_KEY",
    })()

    command = runner.run_command({}, args, 1, tmp_path / "job", tmp_path / "task")

    assert not any("reasoning=" in value for value in command)
    assert not any("max_output_tokens=" in value for value in command)


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


def test_failed_skein_result_is_not_treated_as_a_completed_harbor_job(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "001-example-attempt-01"
    result = task_dir / "job" / "trial" / "agent" / "skein-state" / "evaluation"
    result.mkdir(parents=True)
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

    assert load_runner().completed_task(tmp_path, "001-example") is None


def test_next_attempt_directory_survives_process_restart(tmp_path: Path) -> None:
    runner = load_runner()
    (tmp_path / "001-example-attempt-01").mkdir()
    (tmp_path / "001-example-attempt-03").mkdir()
    assert runner.next_attempt_dir(tmp_path, "001-example").name.endswith("attempt-04")
