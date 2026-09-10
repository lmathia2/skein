from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

from evals import runner
from harness.adapters.adk.runtime.protocol import AgUiEvent, AgUiEventType, ServerEnvelope
from harness.core.config import SkeinConfig, load_harness_composition
from harness.core.models import TaskRequest


def _repository(root: Path, *, dirty: bool = False) -> Path:
    root.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=root, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=root, check=True)
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=root, check=True)
    if dirty:
        (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    return root


def _request(tmp_path: Path, workspace: Path) -> runner.EvaluationRunRequest:
    return runner.EvaluationRunRequest(
        workspace=workspace,
        state_root=tmp_path / "state",
        auth_state_root=tmp_path / "auth",
        task_id="smoke-1",
        prompt="Fix the fixture",
        model="gpt-5.6-luna",
        reasoning="max",
    )


def test_evaluation_config_pins_luna_max_without_auth_state(tmp_path: Path) -> None:
    request = _request(tmp_path, tmp_path).model_copy(update={"max_output_tokens": 16_384})

    path, composition = runner.prepare_evaluation_config(request)
    loaded = load_harness_composition(path)
    config = loaded.harness.config
    assert isinstance(config, SkeinConfig)
    model = config.models[config.agents["coding_worker"].model]

    assert model.provider == "openai_codex"
    assert model.name == "gpt-5.6-luna"
    assert model.reasoning == "max"
    assert config.agents["coding_worker"].generation.max_output_tokens == 16_384
    assert composition.server.idle_timeout_seconds == request.wall_time_seconds
    assert composition.behavior_sha256 == loaded.behavior_sha256
    assert str(request.auth_state_root) not in path.read_text(encoding="utf-8")
    assert not config.safety.allow_network
    assert not config.safety.allow_unknown_commands


def test_isolated_evaluation_delegates_authority_to_the_task_environment(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, tmp_path).model_copy(
        update={"isolated_environment_authority": True}
    )

    _, composition = runner.prepare_evaluation_config(request)
    config = composition.harness.config

    assert isinstance(config, SkeinConfig)
    assert config.safety.allow_dependency_install
    assert config.safety.allow_network
    assert config.safety.allow_git_history_mutation
    assert config.safety.allow_unknown_commands


def test_evaluation_config_pins_openrouter_model_and_secret_reference(tmp_path: Path) -> None:
    request = _request(tmp_path, tmp_path).model_copy(
        update={
            "provider": "openrouter",
            "model": "meta/muse-spark-1.2-contributor",
            "reasoning": "xhigh",
            "api_key_env": "EVAL_OPENROUTER_KEY",
        }
    )

    path, composition = runner.prepare_evaluation_config(request)
    config = composition.harness.config
    assert isinstance(config, SkeinConfig)
    model = config.models[config.agents["coding_worker"].model]

    assert model.provider == "openrouter"
    assert model.name == "meta/muse-spark-1.2-contributor"
    assert model.reasoning == "xhigh"
    assert model.api_key is not None and model.api_key.env == "EVAL_OPENROUTER_KEY"
    assert "EVAL_OPENROUTER_KEY" in path.read_text(encoding="utf-8")


def test_evaluation_config_preserves_provider_generation_defaults(tmp_path: Path) -> None:
    request = _request(tmp_path, tmp_path).model_copy(
        update={"provider": "openrouter", "model": "openai/gpt-5.5", "reasoning": None}
    )

    _, composition = runner.prepare_evaluation_config(request)
    config = composition.harness.config
    assert isinstance(config, SkeinConfig)
    model = config.models[config.agents["coding_worker"].model]

    assert model.reasoning is None
    assert config.agents["coding_worker"].generation.max_output_tokens is None


def test_evaluation_rejects_a_dirty_workspace_before_model_start(tmp_path: Path) -> None:
    request = _request(tmp_path, _repository(tmp_path / "repo", dirty=True))

    result = asyncio.run(runner.run_evaluation(request))

    assert result.status == "failed"
    assert result.error is not None and result.error.code == "dirty_workspace"
    assert not (request.state_root / "server" / "runs.db").exists()


def test_evaluation_fails_closed_on_missing_or_unverified_results(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _repository(tmp_path / "repo")

    class Store:
        def __init__(self) -> None:
            self.result: object | None = None
            self.event_type = AgUiEventType.RUN_FINISHED

        def replay(self, run_id, after_sequence=0):
            event = (
                AgUiEvent(
                    type=AgUiEventType.RUN_ERROR,
                    thread_id="thread",
                    run_id=run_id,
                    code="run_total_timeout",
                    message="total timeout",
                )
                if self.event_type == AgUiEventType.RUN_ERROR
                else AgUiEvent(
                    type=AgUiEventType.RUN_FINISHED,
                    thread_id="thread",
                    run_id=run_id,
                    result=self.result,
                )
            )
            return (
                ServerEnvelope(
                    sequence=1,
                    run_id=run_id,
                    durable=True,
                    event=event,
                ),
            )

    class Coordinator:
        def __init__(self) -> None:
            self.store = Store()
            self.record = SimpleNamespace(run_id="run-1", status="completed", error=None)
            self.created = True
            self.messages = []

        async def start(self, message, *, user_id):
            self.messages.append(message)
            return self.record, self.created

        async def wait(self, run_id):
            return self.record

        async def aclose(self):
            return None

    coordinator = Coordinator()

    def build(**kwargs):
        return SimpleNamespace(
            coordinator=coordinator,
            composition=load_harness_composition(kwargs["config_path"]),
        )

    monkeypatch.setattr(runner, "build_server_assembly", build)
    request = _request(tmp_path, workspace)

    missing = asyncio.run(runner.run_evaluation(request))
    assert missing.error is not None and missing.error.code == "missing_result"
    submitted = TaskRequest.model_validate_json(coordinator.messages[-1].input)
    assert submitted.mode == "coding"
    assert submitted.verification_level == "behavioral"
    assert submitted.acceptance_criteria == ["Fix the fixture"]

    request = request.model_copy(update={"state_root": tmp_path / "state-2"})
    coordinator.store.result = {"status": "complete", "changed_paths": ["app.py"]}
    unverified = asyncio.run(runner.run_evaluation(request))
    assert unverified.error is not None and unverified.error.code == "unverified_completion"

    request = request.model_copy(update={"state_root": tmp_path / "state-3"})
    coordinator.created = False
    stale = asyncio.run(runner.run_evaluation(request))
    assert stale.error is not None and stale.error.code == "state_not_fresh"

    request = request.model_copy(update={"state_root": tmp_path / "state-4"})
    coordinator.created = True
    coordinator.record.status = "failed"
    coordinator.store.event_type = AgUiEventType.RUN_ERROR
    timed_out = asyncio.run(runner.run_evaluation(request))
    assert timed_out.error is not None and timed_out.error.code == "run_total_timeout"
    assert timed_out.exit_code == 124


def test_evaluation_redacts_initialization_errors(tmp_path: Path, monkeypatch) -> None:
    workspace = _repository(tmp_path / "repo")
    monkeypatch.setattr(runner, "discover_known_secrets", lambda: ("private-token",))
    monkeypatch.setattr(
        runner,
        "build_server_assembly",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad private-token")),
    )

    result = asyncio.run(runner.run_evaluation(_request(tmp_path, workspace)))

    assert result.error is not None
    assert "private-token" not in result.model_dump_json()
    assert "<redacted>" in result.error.message


def test_evaluation_exit_codes_are_machine_readable(tmp_path: Path) -> None:
    artifacts = runner.EvaluationArtifacts(
        state_root=tmp_path,
        result=tmp_path / "result.json",
    )

    assert (
        runner.EvaluationRunResult(
            task_id="ok", status="answered", wall_time_ms=0, artifacts=artifacts
        ).exit_code
        == 0
    )
    assert (
        runner.EvaluationRunResult(
            task_id="blocked", status="blocked", wall_time_ms=0, artifacts=artifacts
        ).exit_code
        == 2
    )
    assert (
        runner.EvaluationRunResult(
            task_id="timeout",
            status="failed",
            wall_time_ms=0,
            artifacts=artifacts,
            error=runner.EvaluationError(code="run_total_timeout", message="timed out"),
        ).exit_code
        == 124
    )
