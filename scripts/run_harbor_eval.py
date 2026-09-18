#!/usr/bin/env python3
"""Run pinned Skein Harbor-compatible samples with Pier and retain all traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.experiments import harbor_task_path
from harness.core.config import SkeinConfig, load_harness_composition

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = {
    "smoke": ("evaluation-smoke-v1.json", 6),
    "broader": ("evaluation-ablation-v1.json", 10),
    "ablation": ("evaluation-ablation-v1.json", 18),
    "pilot": ("evaluation-pilot-v1.json", 42),
    "confirm": ("evaluation-confirm-v1.json", 105),
    "full": ("evaluation-confirm-v1.json", 105),
}
CONTRACT_FIELDS = (
    "schema_version",
    "suite",
    "benchmark",
    "manifest_sha256",
    "cohort_sha256",
    "cohort_group",
    "model",
    "provider",
    "agent_import_path",
    "reasoning",
    "config",
    "max_output_tokens",
    "max_task_input_tokens",
    "max_iterations",
    "attempts",
    "concurrency",
    "harbor_retries",
    "git_revision",
    "git_diff_sha256",
)
_APPEND_LOCK = threading.Lock()
_PROCESS_LOCK = threading.Lock()
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()


class TrackioRun:
    def __init__(
        self,
        *,
        project: str,
        name: str,
        group: str | None,
        space_id: str | None,
        config: dict[str, Any],
    ) -> None:
        try:
            import trackio
        except ImportError as error:
            raise SystemExit("Trackio is not installed; run uv sync --extra tracking") from error
        self._trackio = trackio
        self._lock = threading.Lock()
        self._step = 0
        self._totals: dict[str, float] = {}
        self._run = trackio.init(
            project=project, name=name, group=group, space_id=space_id, config=config
        )

    def log(self, record: dict[str, Any]) -> None:
        metrics = trial_tracking_metrics(record)
        with self._lock:
            self._step += 1
            for name in (
                "official_reward",
                "cost_usd",
                "input_tokens",
                "uncached_input_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "output_tokens",
                "active_wall_time_seconds",
                "end_to_end_wall_time_seconds",
                "complete",
                "timed_out",
                "error_count",
                "recovered",
                "resumed",
            ):
                self._totals[name] = self._totals.get(name, 0.0) + metrics.get(name, 0.0)
            metrics.update({f"total_{name}": value for name, value in self._totals.items()})
            metrics["official_pass_rate"] = self._totals["official_reward"] / self._step
            try:
                self._run.log(metrics, step=self._step)
                if metrics["error_count"] or not metrics["complete"]:
                    self._run.alert(
                        title=f"Harbor trial issue: {record.get('task_id', 'unknown')}",
                        text=(
                            f"status={record.get('status')} returncode={record.get('returncode')} "
                            f"timed_out={bool(record.get('timed_out'))} "
                            f"errors={record.get('errors') or []}"
                        ),
                        level=self._trackio.AlertLevel.ERROR,
                    )
            except Exception as error:
                print(f"Trackio logging failed: {type(error).__name__}", file=sys.stderr)

    def alert_exception(self, task_id: str, error: BaseException) -> None:
        with self._lock:
            try:
                self._run.alert(
                    title=f"Harbor runner exception: {task_id}",
                    text=f"exception_type={type(error).__name__}",
                    level=self._trackio.AlertLevel.ERROR,
                )
            except Exception as alert_error:
                print(f"Trackio alert failed: {type(alert_error).__name__}", file=sys.stderr)

    def finish(self) -> None:
        self._run.finish()


def trial_tracking_metrics(record: dict[str, Any]) -> dict[str, float]:
    trials = record.get("trial_metrics") or []
    count = len(trials)

    def total(name: str) -> float:
        return sum(float(metric.get(name) or 0) for metric in trials)

    task_cost = total("cost_usd")
    total_tokens = sum(total(name) for name in (
        "uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"
    ))
    return {
        "task_index": float(record.get("task_index") or 0),
        "retry": float(record.get("retry") or 0),
        "complete": float(record.get("status") == "complete"),
        "timed_out": float(bool(record.get("timed_out"))),
        "error_count": float(len(record.get("errors") or [])),
        "returncode": float(record.get("returncode") or 0),
        "recovered": float(bool(record.get("recovered"))),
        "resumed": float(bool(record.get("resumed"))),
        "trial_count": float(count),
        "official_reward": total("official_reward") / count if count else 0.0,
        "task_cost_usd": task_cost,
        "cost_per_trial_usd": task_cost / count if count else 0.0,
        "total_tokens": total_tokens,
        "tokens_per_usd": total_tokens / task_cost if task_cost else 0.0,
        **{
            name: total(name)
            for name in (
                "cost_usd",
                "input_tokens",
                "uncached_input_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "output_tokens",
                "active_wall_time_seconds",
                "end_to_end_wall_time_seconds",
            )
        },
    }


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:80] or "task"


def dotenv_value(path: Path, wanted: str) -> str | None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == wanted:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


def inventory(root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return result


def result_summary(root: Path) -> tuple[list[str], list[int], list[str], list[dict[str, Any]]]:
    paths, rewards, errors, trial_metrics = [], [], [], []
    for path in sorted(root.rglob("result.json")):
        paths.append(str(path.relative_to(root)))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            exception = payload.get("exception_info")
            if isinstance(exception, dict):
                errors.append(str(exception.get("exception_type") or "exception"))
            reward = payload.get("verifier_result", {}).get("rewards", {}).get("reward")
            if reward in (0, 1):
                rewards.append(int(reward))
                agent = payload.get("agent_result") or {}
                metadata = agent.get("metadata") or {}
                pi_usage = (metadata.get("pi_code_tool") or {}).get("usage") or {}
                skein = metadata.get("skein") or {}
                metrics = skein.get("metrics") or {}
                trial_metrics.append(
                    {
                        "official_reward": int(reward),
                        "active_wall_time_seconds": _duration_seconds(
                            payload.get("agent_execution")
                        ),
                        "end_to_end_wall_time_seconds": _duration_seconds(payload),
                        "input_tokens": metrics.get("input_tokens", agent.get("n_input_tokens")),
                        "uncached_input_tokens": pi_usage.get("input"),
                        "cache_read_tokens": metrics.get(
                            "cache_read_tokens", agent.get("n_cache_tokens")
                        ),
                        "cache_write_tokens": pi_usage.get("cache_write"),
                        "output_tokens": metrics.get(
                            "output_tokens", agent.get("n_output_tokens")
                        ),
                        "reasoning_tokens": metrics.get("reasoning_tokens"),
                        "cost_usd": skein.get("api_equivalent_cost_usd", agent.get("cost_usd")),
                        "skein_status": skein.get("status"),
                        "skein_error_code": (skein.get("error") or {}).get("code"),
                    }
                )
        except (OSError, ValueError, AttributeError):
            pass
    return paths, rewards, errors, trial_metrics


def _duration_seconds(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    try:
        started = datetime.fromisoformat(str(payload["started_at"]).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(payload["finished_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (finished - started).total_seconds())


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_row(path: Path, value: dict[str, Any]) -> None:
    with _APPEND_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_or_validate_metadata(path: Path, value: dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(dump(value), encoding="utf-8")
        return
    existing = json.loads(path.read_text(encoding="utf-8"))
    changed = [name for name in CONTRACT_FIELDS if existing.get(name) != value.get(name)]
    if changed:
        raise SystemExit(
            f"{path} belongs to a different evaluation contract "
            f"({', '.join(changed)} changed); choose another --jobs-dir"
        )


def harbor_binary() -> str:
    path = shutil.which("harbor") or str(ROOT / ".venv/bin/harbor")
    if not Path(path).is_file():
        raise SystemExit("Harbor is not installed; run ./install.sh --minimal --dev --eval")
    return path


def pier_binary() -> str:
    path = shutil.which("pier") or str(Path.home() / ".local/bin/pier")
    if not Path(path).is_file():
        raise SystemExit("Pier is not installed; run uv tool install git+https://github.com/datacurve-ai/pier")
    return path


def pier_environment(env: dict[str, str]) -> dict[str, str]:
    """Let Pier's isolated executable import this checkout and its dependencies."""

    paths = [str(ROOT), *(path for path in sys.path if path)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    return {**env, "PYTHONPATH": os.pathsep.join(dict.fromkeys(paths))}


def validate_harbor_config(path: str) -> None:
    composition = load_harness_composition((ROOT / path).resolve())
    config = composition.harness.config
    if not isinstance(config, SkeinConfig):
        raise SystemExit("Harbor evaluation requires the skein_v1 harness")


def docker_ready() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("Docker CLI is required; start Docker Desktop or Colima first")
    check = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        raise SystemExit("Docker daemon is not reachable; start Docker Desktop or Colima first")
    compose = subprocess.run(
        ["docker", "compose", "version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if compose.returncode:
        raise SystemExit("Docker Compose v2 is required by Harbor")
    buildx = subprocess.run(
        ["docker", "buildx", "version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if buildx.returncode:
        raise SystemExit("Docker Buildx is required by Harbor")


def validate_jobs_dir(path: Path) -> None:
    if sys.platform == "darwin" and path.is_relative_to("/private"):
        raise SystemExit(
            f"{path} is not a reliable Docker Desktop bind mount; "
            "use a path under your home directory, such as ~/skein-eval-results"
        )


def execute(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout: Any,
    stderr: Any,
    timeout_seconds: int,
) -> tuple[int, bool]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )
    with _PROCESS_LOCK:
        _ACTIVE_PROCESSES.add(process)
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        stderr.write(f"\nSkein watchdog timed out after {timeout_seconds}s\n")
        stderr.flush()
        terminate_process(process)
        return 124, True
    except BaseException:
        terminate_process(process)
        raise
    finally:
        with _PROCESS_LOCK:
            _ACTIVE_PROCESSES.discard(process)


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def stop_active_processes() -> None:
    with _PROCESS_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for process in processes:
        terminate_process(process)


def cached_task(task: dict[str, Any]) -> Path:
    path = harbor_task_path(task["harbor_task"], task["artifact_sha256"])
    if path.is_dir():
        return path
    download = subprocess.run(
        [harbor_binary(), "tasks", "download", task["harbor_task"], "--cache"]
    )
    if download.returncode or not path.is_dir():
        raise SystemExit(
            f"downloaded {task['harbor_task']} does not match frozen digest "
            f"{task['artifact_sha256']}"
        )
    return path


def prepared_task(task: dict[str, Any], source: Path, root: Path) -> tuple[Path, str | None]:
    """Use one content-addressed verifier image for every trial of a DeepSWE task."""

    root.mkdir(parents=True, exist_ok=True)
    config = tomllib.loads((source / "task.toml").read_text(encoding="utf-8"))
    verifier_environment = config.get("verifier", {}).get("environment", {})
    if task["benchmark"] != "deep_swe" or verifier_environment.get("docker_image"):
        return source, verifier_environment.get("docker_image")

    image = f"skein-deepswe-verifier:{task['artifact_sha256']}"
    if subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--tag",
                image,
                str(source / "tests"),
            ],
            check=True,
        )
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()
    if not image_id.startswith("sha256:"):
        raise SystemExit(f"Docker returned an invalid verifier image ID for {image}")

    target = root / task["artifact_sha256"]
    expected = f'docker_image = "{image_id}"'
    if not target.is_dir() or expected not in (target / "task.toml").read_text(encoding="utf-8"):
        temporary = Path(tempfile.mkdtemp(prefix="task-", dir=root))
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        task_toml = temporary / "task.toml"
        text = task_toml.read_text(encoding="utf-8")
        header = "[verifier.environment]\n"
        if header not in text:
            raise SystemExit(f"{source} has no separate verifier environment")
        task_toml.write_text(text.replace(header, f"{header}{expected}\n", 1), encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        temporary.replace(target)
    return target, image_id


def run_command(
    task: dict[str, Any],
    args: argparse.Namespace,
    attempts: int,
    job_dir: Path,
    task_path: Path,
) -> list[str]:
    command = [
        pier_binary(),
        "run",
        "--path",
        str(task_path),
        "--agent-import-path",
        args.agent_import_path,
        "--model",
        args.model,
        "--n-concurrent",
        "1",
        "--n-attempts",
        str(attempts),
        "--max-retries",
        "0",
        "--no-delete",
        "--quiet",
        "--yes",
        "--job-name",
        job_dir.name,
        "--jobs-dir",
        str(job_dir),
    ]
    if args.agent_import_path == "harness.adapters.pier:SkeinPierAgent":
        command += [
            "--agent-kwarg", f"provider={args.provider}",
            "--agent-kwarg", f"config={args.config}",
            "--agent-kwarg", f"max_iterations={args.max_iterations}",
            "--agent-kwarg", f"max_task_input_tokens={args.max_task_input_tokens}",
            "--agent-kwarg", f"wall_time_seconds={task['expected_runtime_seconds']}",
        ]
    if getattr(args, "per_trial_timeout_seconds", None) is not None:
        command += ["--agent-kwarg", f"execution_timeout_seconds={args.per_trial_timeout_seconds}"]
    if args.reasoning is not None:
        command += ["--agent-kwarg", f"reasoning={args.reasoning}"]
    if args.max_output_tokens is not None and args.agent_import_path in {
        "harness.adapters.pier:SkeinPierAgent",
        "scripts.pi_code_tool_harbor:PiCodeToolPierAgent",
        "scripts.pi_code_tool_harbor:PiSkeinPtcPierAgent",
    }:
        command += ["--agent-kwarg", f"max_output_tokens={args.max_output_tokens}"]
    if args.provider == "openrouter" and args.agent_import_path == "harness.adapters.pier:SkeinPierAgent":
        command += ["--agent-kwarg", f"api_key_env={args.api_key_env}"]
    if task.get("benchmark") == "deep_swe" and args.agent_import_path == "harness.adapters.pier:SkeinPierAgent":
        command += ["--agent-kwarg", "commit_workspace=true"]
    return command


def completed_task(
    output: Path, prefix: str
) -> tuple[Path, list[str], list[int], list[dict[str, Any]]] | None:
    for task_dir in sorted(p for p in output.iterdir() if p.is_dir() and p.name.startswith(prefix)):
        if any(task_dir.glob("*/result.json")):
            paths, rewards, errors, trial_metrics = result_summary(task_dir)
            if rewards and not errors:
                return task_dir, paths, rewards, trial_metrics
    return None


def next_attempt_dir(output: Path, prefix: str) -> Path:
    numbers = []
    for path in output.glob(f"{prefix}-attempt-*"):
        try:
            numbers.append(int(path.name.rsplit("-", 1)[1]))
        except ValueError:
            continue
    return output / f"{prefix}-attempt-{max(numbers, default=0) + 1:02d}"


def incomplete_job(output: Path, prefix: str) -> Path | None:
    for task_dir in sorted(p for p in output.iterdir() if p.is_dir() and p.name.startswith(prefix)):
        for config in sorted(task_dir.glob("*/config.json")):
            job_dir = config.parent
            if not (job_dir / "result.json").exists():
                return job_dir
    return None


def task_watchdog_seconds(args, task, attempts):
    per_trial = getattr(args, "per_trial_timeout_seconds", None)
    if per_trial is not None:
        # Pi enforces each execution deadline; reserve setup/verifier time per attempt.
        return attempts * (per_trial + 1800)
    return args.timeout_seconds or (int(task["expected_runtime_seconds"]) + 1800)


def run_task(
    index: int,
    total: int,
    task: dict[str, Any],
    *,
    args: argparse.Namespace,
    attempts: int,
    output: Path,
    ledger: Path,
    complete: frozenset[str],
    env: dict[str, str],
    on_record: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    key = f"{args.suite}:{task['task_id']}:{task['artifact_sha256']}:{args.config}:{attempts}"
    if key in complete:
        print(f"[{index}/{total}] skip {task['task_id']} (complete)")
        return True
    prefix = f"{index:03d}-{safe_name(task['task_id'])}"
    recovered = completed_task(output, prefix)
    if recovered is not None:
        task_dir, result_paths, rewards, trial_metrics = recovered
        record = {
                "schema_version": "skein-harbor-run-record-v1",
                "key": key,
                "task_index": index,
                "suite": args.suite,
                "task_id": task["task_id"],
                "benchmark": task["benchmark"],
                "artifact_sha256": task["artifact_sha256"],
                "attempts": attempts,
                "returncode": 0,
                "status": "complete",
                "recovered": True,
                "task_dir": str(task_dir),
                "result_paths": result_paths,
                "rewards": rewards,
                "trial_metrics": trial_metrics,
                "errors": [],
                "files": inventory(task_dir),
            }
        append_row(ledger, record)
        if on_record:
            on_record(record)
        print(f"[{index}/{total}] recover {task['task_id']} (complete)")
        return True
    source_task = cached_task(task)
    prepared_root = output / "prepared-tasks"
    prepared_root.mkdir(exist_ok=True)
    task_path, verifier_image = prepared_task(task, source_task, prepared_root)
    for retry in range(args.retries + 1):
        resumed = incomplete_job(output, prefix)
        if resumed is not None:
            task_dir = resumed.parent
            command = [pier_binary(), "job", "resume", "--job-path", str(resumed)]
        else:
            task_dir = next_attempt_dir(output, prefix)
            task_dir.mkdir(parents=True, exist_ok=True)
            command = run_command(task, args, attempts, task_dir, task_path)
        append_row(
            task_dir / "commands.jsonl",
            {"started_at": datetime.now(UTC).isoformat(), "command": command},
        )
        (task_dir / "task.json").write_text(
            dump({**task, "runtime_verifier_image": verifier_image}), encoding="utf-8"
        )
        started = time.monotonic()
        timeout_seconds = task_watchdog_seconds(args, task, attempts)
        with (
            (task_dir / "pier.stdout.log").open("a", encoding="utf-8") as stdout,
            (task_dir / "pier.stderr.log").open("a", encoding="utf-8") as stderr,
        ):
            returncode, timed_out = execute(
                command,
                cwd=ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout_seconds=timeout_seconds,
            )
        result_paths, rewards, errors, trial_metrics = result_summary(task_dir)
        status = "complete" if returncode == 0 and rewards and not errors else "incomplete"
        record = {
                "schema_version": "skein-harbor-run-record-v1",
                "key": key,
                "task_index": index,
                "retry": retry,
                "suite": args.suite,
                "task_id": task["task_id"],
                "benchmark": task["benchmark"],
                "artifact_sha256": task["artifact_sha256"],
                "attempts": attempts,
                "returncode": returncode,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "status": status,
                "resumed": resumed is not None,
                "wall_time_seconds": round(time.monotonic() - started, 3),
                "task_dir": str(task_dir),
                "result_paths": result_paths,
                "rewards": rewards,
                "trial_metrics": trial_metrics,
                "errors": errors,
                "files": inventory(task_dir),
            }
        append_row(ledger, record)
        if on_record:
            on_record(record)
        print(
            f"[{index}/{total}] {task['task_id']}: "
            f"{status} returncode={returncode} rewards={rewards}"
        )
        if status == "complete":
            return True
        if retry < args.retries:
            print(f"  retrying ({retry + 1}/{args.retries})")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(MANIFESTS), default="smoke")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--agent-import-path", default="harness.adapters.pier:SkeinPierAgent")
    parser.add_argument("--model", default="meta/muse-spark-1.2-contributor")
    parser.add_argument(
        "--reasoning",
        default="xhigh",
        help="reasoning effort, or provider-default to leave it unset",
    )
    parser.add_argument("--config", default="harness/core/config/profiles/four-tool.yaml")
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument(
        "--benchmark", choices=("deep_swe", "swe_atlas_qna", "terminal_bench")
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--cohort-file", type=Path)
    parser.add_argument("--cohort-group", choices=("core", "all"), default="core")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--per-trial-timeout-seconds", type=int, help="Pi execution budget per trial; replaces the shared watchdog with attempts * (budget + 1800s setup/verifier allowance)")
    parser.add_argument("--max-iterations", type=int, default=1_000)
    parser.add_argument("--max-output-tokens", type=int, default=16_384)
    parser.add_argument(
        "--max-task-input-tokens",
        type=int,
        default=1_000_000_000,
        help="cumulative Skein input ceiling; the default is effectively disabled",
    )
    parser.add_argument(
        "--provider-defaults",
        action="store_true",
        help="leave reasoning effort and output-token limit unset",
    )
    parser.add_argument("--jobs-dir", type=Path)
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--trackio-project", help="log this campaign to a Trackio project")
    parser.add_argument("--trackio-run-name", help="Trackio run name; defaults to the jobs directory name")
    parser.add_argument("--trackio-group", help="optional Trackio group, such as an experiment ID")
    parser.add_argument("--trackio-space-id", help="optional Hugging Face Space ID for remote sync")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.per_trial_timeout_seconds is not None:
        if args.per_trial_timeout_seconds < 1 or not args.agent_import_path.startswith("scripts.pi_code_tool_harbor:"):
            parser.error("--per-trial-timeout-seconds requires a positive budget and a Pi adapter")
        if args.timeout_seconds is not None:
            parser.error("use either --timeout-seconds or --per-trial-timeout-seconds")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    if not 1 <= args.max_iterations <= 1_000:
        parser.error("--max-iterations must be between 1 and 1000")
    if args.provider_defaults or args.reasoning == "provider-default":
        args.reasoning = None
    if args.provider_defaults:
        args.max_output_tokens = None
    if args.max_output_tokens is not None and not 256 <= args.max_output_tokens <= 131_072:
        parser.error("--max-output-tokens must be between 256 and 131072")
    if not 8_000 <= args.max_task_input_tokens <= 1_000_000_000:
        parser.error("--max-task-input-tokens must be between 8000 and 1000000000")
    validate_harbor_config(args.config)
    manifest_name, limit = MANIFESTS[args.suite]
    manifest_path = ROOT / "tests/eval/manifests" / manifest_name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = manifest["tasks"][:limit]
    if args.benchmark:
        tasks = [task for task in tasks if task["benchmark"] == args.benchmark]
    if args.task_id:
        wanted = set(args.task_id)
        tasks = [task for task in tasks if task["task_id"] in wanted]
        missing = wanted - {task["task_id"] for task in tasks}
        if missing:
            parser.error(f"task IDs are not in {args.suite}: {', '.join(sorted(missing))}")
    if args.cohort_file:
        cohort = json.loads(args.cohort_file.read_text(encoding="utf-8"))
        if (cohort.get("schema_version") != "skein-deep-swe-cohort-v1"
                or len(cohort.get("core_task_ids", [])) != 6
                or len(cohort.get("additional_task_ids", [])) != 6):
            parser.error("cohort must pin six core and six additional DeepSWE tasks")
        if cohort["source_manifest_sha256"] != manifest["manifest_sha256"]:
            parser.error("cohort source manifest hash does not match the selected suite")
        digest = hashlib.sha256(dump({k: v for k, v in cohort.items() if k != "cohort_sha256"}).encode()).hexdigest()
        if digest != cohort["cohort_sha256"]:
            parser.error("cohort hash does not match its content")
        ids = cohort["core_task_ids"] + (cohort["additional_task_ids"] if args.cohort_group == "all" else [])
        indexed = {task["task_id"]: task for task in tasks}
        if len(ids) != len(set(ids)) or any(
            task_id not in indexed or indexed[task_id]["artifact_sha256"] != cohort["task_artifact_sha256"][task_id]
            for task_id in ids
        ):
            parser.error("cohort task IDs or artifact hashes do not match the selected suite")
        tasks = [indexed[task_id] for task_id in ids]
    if args.limit is not None:
        tasks = tasks[: args.limit]
    attempts = args.attempts or (2 if args.suite == "full" else 1)
    if attempts < 1:
        parser.error("--attempts must be positive")
    if args.plan:
        print(
            dump(
                {
                    "suite": args.suite,
                    "benchmark": args.benchmark,
                    "manifest": str(manifest_path),
                    "manifest_sha256": manifest["manifest_sha256"],
                    "tasks": [task["task_id"] for task in tasks],
                    "model": args.model,
                    "provider": args.provider,
                    "agent_import_path": args.agent_import_path,
                    "config": args.config,
                    "jobs_dir": str(args.jobs_dir),
                    "attempts": attempts,
                    "concurrency": args.concurrency,
                    "retries": args.retries,
                    "timeout_seconds": args.timeout_seconds,
                    "per_trial_timeout_seconds": args.per_trial_timeout_seconds,
                    "completion_checklist": os.environ.get("PTC_COMPLETION_CHECKLIST"),
                    "reasoning": args.reasoning,
                    "max_output_tokens": args.max_output_tokens,
                    "max_task_input_tokens": args.max_task_input_tokens,
                    "max_iterations": args.max_iterations,
                }
            )
        )
        return 0

    output = (
        (args.jobs_dir or Path.home() / "skein-eval-results" / args.suite).expanduser().resolve()
    )
    validate_jobs_dir(output)
    docker_ready()
    env = pier_environment(os.environ.copy())
    if args.provider == "openrouter" and not env.get(args.api_key_env):
        dotenv = args.dotenv.expanduser().resolve()
        if not dotenv.is_file():
            raise SystemExit(f"missing {args.api_key_env}; export it or provide --dotenv")
        if dotenv.stat().st_mode & 0o077:
            raise SystemExit(f"refusing broad permissions on {dotenv}; run chmod 600 {dotenv}")
        value = dotenv_value(dotenv, args.api_key_env)
        if not value:
            raise SystemExit(f"{args.api_key_env} is missing or empty in {dotenv}")
        env[args.api_key_env] = value

    output.mkdir(parents=True, exist_ok=True)
    ledger = output / "runs.jsonl"
    complete = {
        str(row.get("key")) for row in ledger_rows(ledger) if row.get("status") == "complete"
    }
    metadata = {
        "schema_version": "skein-harbor-run-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "suite": args.suite,
        "benchmark": args.benchmark,
        "manifest": str(manifest_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "cohort_sha256": cohort["cohort_sha256"] if args.cohort_file else None,
        "cohort_group": args.cohort_group if args.cohort_file else None,
        "model": args.model,
        "provider": args.provider,
        "agent_import_path": args.agent_import_path,
        "reasoning": args.reasoning,
        "config": args.config,
        "max_output_tokens": args.max_output_tokens,
        "max_task_input_tokens": args.max_task_input_tokens,
        "max_iterations": args.max_iterations,
        "attempts": attempts,
        "retries": args.retries,
        "concurrency": args.concurrency,
        "harbor_retries": 0,
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
        ).hexdigest(),
        "trackio": {
            "project": args.trackio_project,
            "run_name": args.trackio_run_name or output.name,
            "group": args.trackio_group,
            "space_id": args.trackio_space_id,
        }
        if args.trackio_project
        else None,
    }
    if "PTC_COMPLETION_CHECKLIST" in os.environ:
        metadata["completion_checklist"] = os.environ["PTC_COMPLETION_CHECKLIST"]
    if args.per_trial_timeout_seconds is not None:
        metadata["per_trial_timeout_seconds"] = args.per_trial_timeout_seconds
    write_or_validate_metadata(output / "run-metadata.json", metadata)

    tracker = None
    if args.trackio_project:
        tracker = TrackioRun(
            project=args.trackio_project,
            name=args.trackio_run_name or output.name,
            group=args.trackio_group,
            space_id=args.trackio_space_id,
            config={name: metadata.get(name) for name in CONTRACT_FIELDS},
        )

    work = tuple(enumerate(tasks, 1))

    def run(item: tuple[int, dict[str, Any]]) -> bool:
        index, task = item
        try:
            return run_task(
                index,
                len(tasks),
                task,
                args=args,
                attempts=attempts,
                output=output,
                ledger=ledger,
                complete=frozenset(complete),
                env=env,
                on_record=tracker.log if tracker else None,
            )
        except BaseException as error:
            if tracker:
                tracker.alert_exception(task["task_id"], error)
            raise

    try:
        if args.concurrency == 1:
            succeeded = True
            for item in work:
                task_succeeded = run(item)
                succeeded = task_succeeded and succeeded
                if not task_succeeded and args.stop_on_error:
                    break
            return 0 if succeeded else 1
        executor = ThreadPoolExecutor(max_workers=args.concurrency)
        futures = [executor.submit(run, item) for item in work]
        try:
            succeeded = True
            ordered = as_completed(futures) if args.stop_on_error else futures
            for future in ordered:
                task_succeeded = future.result()
                succeeded = task_succeeded and succeeded
                if not task_succeeded and args.stop_on_error:
                    stop_active_processes()
                    for pending in futures:
                        pending.cancel()
                    break
        except BaseException:
            stop_active_processes()
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return 0 if succeeded else 1
    finally:
        if tracker:
            tracker.finish()


if __name__ == "__main__":
    sys.exit(main())
