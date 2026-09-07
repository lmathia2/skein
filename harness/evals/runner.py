"""One-shot, machine-readable execution for external evaluation frameworks."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from harness.config import (
    DEFAULT_COMPOSITION_PATH,
    HarnessComposition,
    SkeinConfig,
    load_harness_composition,
    parse_harness_composition,
)
from harness.models.verification import VerificationReport
from harness.safety import SecretRedactor
from harness.server.bootstrap import ServerAssembly, build_server_assembly
from harness.server.protocol import AgUiEventType, StartTaskMessage
from harness.state import EventKind, JsonlEventStore
from harness.telemetry import MetricsStore
from harness.telemetry.adk_plugin import pricing_from_env
from harness.tools.adk_adapter import discover_known_secrets

EVAL_RESULT_SCHEMA_VERSION = "skein-eval-run-v1"
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
EvaluationStatus = Literal["complete", "answered", "blocked", "failed", "cancelled"]
AssemblyBuilder = Callable[..., ServerAssembly]


class EvaluationRunRequest(BaseModel):
    """Validated one-shot invocation; volatile paths never enter static instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: Path
    state_root: Path
    auth_state_root: Path
    task_id: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=50_000)
    provider: Literal["openai_codex", "openrouter"] = "openai_codex"
    model: str = Field(min_length=1, max_length=128)
    reasoning: ReasoningEffort | None = None
    api_key_env: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{1,127}$",
    )
    config_template: Path = DEFAULT_COMPOSITION_PATH
    client_version: str | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=1_000)
    max_task_input_tokens: int | None = Field(
        default=None, ge=8_000, le=1_000_000_000
    )
    max_output_tokens: int | None = Field(default=None, ge=256, le=131_072)
    wall_time_seconds: float = Field(default=1_800, gt=0, le=86_400)
    trust_project: bool = False
    isolated_environment_authority: bool = False


class EvaluationModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    name: str
    reasoning: str | None = None
    client_version: str | None = None
    behavior_sha256: str
    composition_sha256: str


class EvaluationArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_root: Path
    result: Path
    config: Path | None = None
    run_database: Path | None = None
    run_state_root: Path | None = None
    events: Path | None = None
    metrics: Path | None = None
    traces: Path | None = None


class EvaluationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


class SubscriptionLimitObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interrupted: bool = False
    detail: str | None = None


class EvaluationRunResult(BaseModel):
    """Versioned result printed once and also persisted beside the run artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-eval-run-v1"] = EVAL_RESULT_SCHEMA_VERSION
    task_id: str
    run_id: str | None = None
    status: EvaluationStatus
    final_answer: str = ""
    verified: bool = False
    changed_paths: tuple[str, ...] = ()
    verification: VerificationReport | None = None
    model: EvaluationModelIdentity | None = None
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    api_equivalent_cost_usd: float | None = Field(default=None, ge=0)
    subscription_limit: SubscriptionLimitObservation = Field(
        default_factory=SubscriptionLimitObservation
    )
    wall_time_ms: int = Field(ge=0)
    artifacts: EvaluationArtifacts
    error: EvaluationError | None = None

    @property
    def exit_code(self) -> int:
        if self.status in {"complete", "answered"} and self.error is None:
            return 0
        if self.status == "blocked":
            return 2
        if self.status == "cancelled":
            return 130
        if self.error is not None and "timeout" in self.error.code:
            return 124
        return 1


class EvaluationRunFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def prepare_evaluation_config(request: EvaluationRunRequest) -> tuple[Path, HarnessComposition]:
    """Resolve one immutable eval config from an existing checked-in profile."""

    source = load_harness_composition(request.config_template)
    payload = source.model_dump(mode="json")
    harness = payload["harness"]
    config = harness["config"]
    assert isinstance(config, dict)
    agents = config["agents"]
    models = config["models"]
    assert isinstance(agents, dict) and isinstance(models, dict)
    coding_model_key = agents["coding_worker"]["model"]
    retry = models[coding_model_key]["retry"]
    selected: dict[str, object] = {
        "provider": request.provider,
        "name": request.model,
        "reasoning": request.reasoning,
        "retry": retry,
    }
    if request.provider == "openrouter":
        selected["api_key"] = {"env": request.api_key_env or "OPENROUTER_API_KEY"}
    if request.client_version is not None:
        selected["client_version"] = request.client_version
    models[coding_model_key] = selected
    if request.max_iterations is not None:
        config["workflow"]["max_iterations"] = request.max_iterations
    if request.max_task_input_tokens is not None:
        config["context"]["max_task_input_tokens"] = request.max_task_input_tokens
    if request.max_output_tokens is not None:
        agents["coding_worker"]["generation"]["max_output_tokens"] = (
            request.max_output_tokens
        )
    if request.isolated_environment_authority:
        safety = config["safety"]
        assert isinstance(safety, dict)
        safety.update(
            {
                "allow_dependency_install": True,
                "allow_network": True,
                "allow_git_history_mutation": True,
                "allow_unknown_commands": True,
            }
        )

    server = payload["server"]
    assert isinstance(server, dict)
    server["use_saved_model_default"] = False
    server["total_timeout_seconds"] = request.wall_time_seconds
    server["first_event_timeout_seconds"] = min(
        float(server["first_event_timeout_seconds"]), request.wall_time_seconds
    )
    server["idle_timeout_seconds"] = request.wall_time_seconds

    composition = parse_harness_composition(payload)
    destination = request.state_root.expanduser().resolve() / "evaluation" / "config.yaml"
    _atomic_write(
        destination,
        yaml.safe_dump(composition.model_dump(mode="json"), sort_keys=False),
    )
    return destination, composition


def write_evaluation_result(result: EvaluationRunResult) -> None:
    _atomic_write(result.artifacts.result, result.model_dump_json(indent=2) + "\n")


def _git(command: tuple[str, ...], workspace: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:500]
        raise EvaluationRunFailure("invalid_workspace", detail or "workspace is not a Git repository")
    return completed.stdout.strip()


def _validate_workspace(workspace: Path) -> str:
    resolved = workspace.expanduser().resolve()
    if not resolved.is_dir():
        raise EvaluationRunFailure("invalid_workspace", f"workspace is not a directory: {resolved}")
    revision = _git(("git", "rev-parse", "HEAD"), resolved)
    if _git(("git", "status", "--porcelain"), resolved):
        raise EvaluationRunFailure(
            "dirty_workspace",
            "evaluation workspace must be clean before the trial starts",
        )
    return revision


def _run_id(task_id: str) -> str:
    return hashlib.sha256(f"run\0evaluation\0{task_id}".encode()).hexdigest()[:32]


def _model_identity(composition: HarnessComposition) -> EvaluationModelIdentity:
    config = composition.harness.config
    if not isinstance(config, SkeinConfig):
        raise TypeError("evaluation runner requires the skein_v1 configuration")
    agent = config.agents["coding_worker"]
    model = config.models[agent.model]
    return EvaluationModelIdentity(
        provider=model.provider,
        name=model.name,
        reasoning=model.reasoning,
        client_version=model.client_version,
        behavior_sha256=composition.behavior_sha256,
        composition_sha256=composition.composition_sha256,
    )


def _artifact_paths(state_root: Path, run_id: str | None = None) -> EvaluationArtifacts:
    root = state_root.expanduser().resolve()
    run_root = root / "runs" / run_id if run_id is not None else None
    return EvaluationArtifacts(
        state_root=root,
        result=root / "evaluation" / "result.json",
        config=root / "evaluation" / "config.yaml",
        run_database=root / "server" / "runs.db",
        run_state_root=run_root,
        events=run_root / "events" if run_root is not None else None,
        metrics=run_root / "metrics.db" if run_root is not None else None,
        traces=run_root / "traces.db" if run_root is not None else None,
    )


def _public_answer(assembly: ServerAssembly, run_id: str) -> str:
    messages: dict[str, list[str]] = {}
    roles: dict[str, str] = {}
    order: list[str] = []
    for envelope in assembly.coordinator.store.replay(run_id):
        event = envelope.event
        if event.type == AgUiEventType.TEXT_MESSAGE_START and event.message_id:
            messages.setdefault(event.message_id, [])
            roles[event.message_id] = event.role or "assistant"
            order.append(event.message_id)
        elif event.type == AgUiEventType.TEXT_MESSAGE_CONTENT and event.message_id:
            messages.setdefault(event.message_id, []).append(str(event.delta or ""))
    for message_id in reversed(order):
        if roles.get(message_id) == "assistant":
            return "".join(messages.get(message_id, ()))
    return ""


def _public_result(assembly: ServerAssembly, run_id: str) -> Mapping[str, object] | None:
    for envelope in reversed(assembly.coordinator.store.replay(run_id)):
        event = envelope.event
        if event.type == AgUiEventType.RUN_FINISHED and isinstance(event.result, Mapping):
            return event.result
    return None


def _public_error(assembly: ServerAssembly, run_id: str) -> tuple[str | None, str | None]:
    for envelope in reversed(assembly.coordinator.store.replay(run_id)):
        event = envelope.event
        if event.type == AgUiEventType.RUN_ERROR:
            return event.code, event.message
    return None, None


def _private_run_data(
    artifacts: EvaluationArtifacts,
    run_id: str,
) -> tuple[str, VerificationReport | None, tuple[str, ...], dict[str, float | int | str | None]]:
    answer = ""
    verification: VerificationReport | None = None
    changed_paths: tuple[str, ...] = ()
    if artifacts.events is not None and artifacts.events.exists():
        for event in JsonlEventStore(artifacts.events).read(run_id):
            if event.kind == EventKind.MESSAGE_RECORDED and event.payload.get("role") == "assistant":
                answer = str(event.payload.get("content", ""))
            elif event.kind == EventKind.VERIFICATION_COMPLETED:
                report = event.payload.get("report")
                if isinstance(report, Mapping):
                    verification = VerificationReport.model_validate(report)
                changed = event.payload.get("changed_paths")
                if isinstance(changed, list):
                    changed_paths = tuple(sorted(str(path) for path in changed))
    metrics: dict[str, float | int | str | None] = {}
    if artifacts.metrics is not None and artifacts.metrics.exists():
        metrics = MetricsStore(artifacts.metrics).task_summary(run_id)
    return answer, verification, changed_paths, metrics


def _is_subscription_limit(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in ("429", "rate limit", "usage limit", "credit exhausted", "quota")
    )


async def run_evaluation(
    request: EvaluationRunRequest,
    *,
    assembly_builder: AssemblyBuilder | None = None,
    validate_workspace: bool = True,
) -> EvaluationRunResult:
    """Run Skein once in place and return a fail-closed structured result."""

    started = time.monotonic()
    state_root = request.state_root.expanduser().resolve()
    expected_run_id = _run_id(request.task_id)
    artifacts = _artifact_paths(state_root, expected_run_id)
    composition: HarnessComposition | None = None
    assembly: ServerAssembly | None = None
    redactor = SecretRedactor(known_secrets=discover_known_secrets())
    try:
        if validate_workspace:
            _validate_workspace(request.workspace)
        config_path, composition = prepare_evaluation_config(request)
        assembly = (assembly_builder or build_server_assembly)(
            workspace=request.workspace,
            state_root=state_root,
            auth_state_root=request.auth_state_root,
            config_path=config_path,
            trust_project=request.trust_project,
        )
        message = StartTaskMessage(
            type="task.start",
            request_id=request.task_id,
            idempotency_key=request.task_id,
            thread_id=f"evaluation:{request.task_id}",
            input=request.prompt,
            metadata={"evaluation.task_id": request.task_id},
        )
        record, created = await assembly.coordinator.start(message, user_id="evaluation")
        if not created:
            raise EvaluationRunFailure(
                "state_not_fresh",
                "task ID already exists in this state root; use a fresh state root",
            )
        record = await assembly.coordinator.wait(record.run_id)
        artifacts = _artifact_paths(state_root, record.run_id)
        public = _public_result(assembly, record.run_id)
        public_answer = _public_answer(assembly, record.run_id)
        answer, verification, changed_paths, metrics = _private_run_data(
            artifacts,
            record.run_id,
        )
        error_code, error_message = _public_error(assembly, record.run_id)
        error_message = error_message or record.error
        if record.status == "failed":
            status: EvaluationStatus = "failed"
        elif record.status == "cancelled":
            status = "cancelled"
        elif public is None:
            status = "failed"
            error_code = "missing_result"
            error_message = "run terminated without a structured workflow result"
        else:
            raw_status = str(public.get("status", "failed"))
            status = (
                cast(EvaluationStatus, raw_status)
                if raw_status in {"complete", "answered", "blocked"}
                else "failed"
            )
        if public is not None and not changed_paths:
            changed = public.get("changed_paths")
            if isinstance(changed, list):
                changed_paths = tuple(sorted(str(path) for path in changed))
        verified = verification.passed if verification is not None else False
        if status == "complete" and not verified:
            status = "failed"
            error_code = "unverified_completion"
            error_message = "completion lacked passing deterministic verification evidence"
        interrupted = _is_subscription_limit(error_message)
        pricing = pricing_from_env()
        api_cost = (
            float(metrics.get("cost_usd", 0.0) or 0.0)
            if request.provider == "openrouter" or request.model in pricing
            else None
        )
        safe_error = redactor.redact_text(error_message) if error_message else None
        return EvaluationRunResult(
            task_id=request.task_id,
            run_id=record.run_id,
            status=status,
            final_answer=answer or public_answer,
            verified=verified,
            changed_paths=changed_paths,
            verification=verification,
            model=_model_identity(composition),
            metrics=metrics,
            api_equivalent_cost_usd=api_cost,
            subscription_limit=SubscriptionLimitObservation(
                interrupted=interrupted,
                detail=safe_error if interrupted else None,
            ),
            wall_time_ms=int((time.monotonic() - started) * 1_000),
            artifacts=artifacts,
            error=(
                EvaluationError(
                    code=error_code or "run_failed",
                    message=safe_error or "run failed",
                    retryable=interrupted,
                )
                if status in {"failed", "cancelled"}
                else None
            ),
        )
    except EvaluationRunFailure as error:
        return EvaluationRunResult(
            task_id=request.task_id,
            run_id=expected_run_id,
            status="failed",
            model=_model_identity(composition) if composition is not None else None,
            wall_time_ms=int((time.monotonic() - started) * 1_000),
            artifacts=artifacts,
            error=EvaluationError(
                code=error.code,
                message=redactor.redact_text(str(error)),
                retryable=error.retryable,
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        error_code = "initialization_failed"
        detail = str(error)
        if assembly is not None:
            recorded_code, recorded_detail = _public_error(assembly, expected_run_id)
            error_code = recorded_code or error_code
            detail = recorded_detail or detail
        safe = redactor.redact_text(detail)[:4_096] or type(error).__name__
        interrupted = _is_subscription_limit(safe)
        return EvaluationRunResult(
            task_id=request.task_id,
            run_id=expected_run_id,
            status="failed",
            model=_model_identity(composition) if composition is not None else None,
            wall_time_ms=int((time.monotonic() - started) * 1_000),
            artifacts=artifacts,
            subscription_limit=SubscriptionLimitObservation(
                interrupted=interrupted,
                detail=safe if interrupted else None,
            ),
            error=EvaluationError(
                code=error_code,
                message=safe,
                retryable=interrupted,
            ),
        )
    finally:
        if assembly is not None:
            await assembly.coordinator.aclose()


def run_evaluation_sync(
    request: EvaluationRunRequest,
    *,
    assembly_builder: AssemblyBuilder | None = None,
    validate_workspace: bool = True,
) -> EvaluationRunResult:
    return asyncio.run(
        run_evaluation(
            request,
            assembly_builder=assembly_builder,
            validate_workspace=validate_workspace,
        )
    )


__all__ = [
    "EVAL_RESULT_SCHEMA_VERSION",
    "EvaluationArtifacts",
    "EvaluationError",
    "EvaluationModelIdentity",
    "EvaluationRunRequest",
    "EvaluationRunResult",
    "SubscriptionLimitObservation",
    "prepare_evaluation_config",
    "run_evaluation",
    "run_evaluation_sync",
    "write_evaluation_result",
]
