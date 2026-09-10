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
    "model",
    "provider",
    "reasoning",
    "config",
    "max_output_tokens",
    "max_task_input_tokens",
    "attempts",
    "concurrency",
    "harbor_retries",
    "git_revision",
    "git_diff_sha256",
)
_APPEND_LOCK = threading.Lock()
_PROCESS_LOCK = threading.Lock()
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()


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
                        "cache_read_tokens": metrics.get(
                            "cache_read_tokens", agent.get("n_cache_tokens")
                        ),
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
        "harness.adapters.pier:SkeinPierAgent",
        "--model",
        args.model,
        "--agent-kwarg",
        f"provider={args.provider}",
        "--agent-kwarg",
        f"config={args.config}",
        "--agent-kwarg",
        "max_iterations=24",
        "--agent-kwarg",
        f"max_task_input_tokens={args.max_task_input_tokens}",
        "--agent-kwarg",
        "wall_time_seconds=5400",
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
    if args.reasoning is not None:
        command += ["--agent-kwarg", f"reasoning={args.reasoning}"]
    if args.max_output_tokens is not None:
        command += ["--agent-kwarg", f"max_output_tokens={args.max_output_tokens}"]
    if args.provider == "openrouter":
        command += ["--agent-kwarg", f"api_key_env={args.api_key_env}"]
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
) -> bool:
    key = f"{args.suite}:{task['task_id']}:{task['artifact_sha256']}:{args.config}:{attempts}"
    if key in complete:
        print(f"[{index}/{total}] skip {task['task_id']} (complete)")
        return True
    prefix = f"{index:03d}-{safe_name(task['task_id'])}"
    recovered = completed_task(output, prefix)
    if recovered is not None:
        task_dir, result_paths, rewards, trial_metrics = recovered
        append_row(
            ledger,
            {
                "schema_version": "skein-harbor-run-record-v1",
                "key": key,
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
            },
        )
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
        timeout_seconds = args.timeout_seconds or (int(task["expected_runtime_seconds"]) + 1800)
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
        append_row(
            ledger,
            {
                "schema_version": "skein-harbor-run-record-v1",
                "key": key,
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
                "wall_time_seconds": round(time.monotonic() - started, 3),
                "task_dir": str(task_dir),
                "result_paths": result_paths,
                "rewards": rewards,
                "trial_metrics": trial_metrics,
                "errors": errors,
                "files": inventory(task_dir),
            },
        )
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
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--max-output-tokens", type=int, default=16_384)
    parser.add_argument("--max-task-input-tokens", type=int, default=2_000_000)
    parser.add_argument(
        "--provider-defaults",
        action="store_true",
        help="leave reasoning effort and output-token limit unset",
    )
    parser.add_argument("--jobs-dir", type=Path)
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
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
                    "attempts": attempts,
                    "concurrency": args.concurrency,
                    "retries": args.retries,
                    "timeout_seconds": args.timeout_seconds,
                    "reasoning": args.reasoning,
                    "max_output_tokens": args.max_output_tokens,
                    "max_task_input_tokens": args.max_task_input_tokens,
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
        "model": args.model,
        "provider": args.provider,
        "reasoning": args.reasoning,
        "config": args.config,
        "max_output_tokens": args.max_output_tokens,
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
    }
    write_or_validate_metadata(output / "run-metadata.json", metadata)

    work = tuple(enumerate(tasks, 1))

    def run(item: tuple[int, dict[str, Any]]) -> bool:
        index, task = item
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
        )

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


if __name__ == "__main__":
    sys.exit(main())
