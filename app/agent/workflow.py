"""Durable ADK 2.x workflow around the bounded coding worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from google.adk import Context, Event, Workflow
from google.adk.agents import BaseAgent
from google.adk.events import EventActions
from google.adk.workflow import BaseNode, node

from harness.approvals.waiting import ApprovalWaiter
from harness.context import estimate_tokens
from harness.environment import RepositoryRuntime
from harness.environment.async_call import run_managed_thread
from harness.models.agent_step import AgentStep
from harness.models.checkpoint import Checkpoint
from harness.models.ledger import TaskLedger
from harness.models.task import TaskPhase, TaskRequest
from harness.notebook import materialize_notebook, reduce_notebook
from harness.orchestration import (
    HarnessRoute,
    build_work_packet,
    create_initial_ledger,
    decide_route,
    parse_agent_step,
    parse_task_request,
    reduce_agent_step,
    replan_ledger,
    resume_for_steering,
    task_id_for,
)
from harness.orchestration.runtime import can_answer_directly
from harness.sandbox import MANAGED_COMMAND_ENVIRONMENT
from harness.state import (
    CheckpointStore,
    EventKind,
    EventStore,
    SteeringQueue,
    ToolReceiptStore,
    rebuild_ledger,
    register_action_batch,
    verification_fingerprint,
)
from harness.state.recovery import validate_recovery_evidence
from harness.telemetry import MetricsStore, TaskOutcomeSample
from harness.verification import (
    CommandResult,
    ManagedValidationExecutor,
    ValidationCommand,
    build_report,
    check_scope,
    discover_validation_plan,
    enforce_test_count,
    passes_recorded_baseline,
)
from harness.workspace import GitWorktreeManager

from .config import HarnessSettings
from .presentation import conversation_history, message_event, result_events
from .skills import SkillRuntimeContext, build_skill_context
from .streaming import PublicReplies


@dataclass(frozen=True, slots=True)
class SkeinWorkflowDependencies:
    settings: HarnessSettings
    event_store: EventStore
    steering_queue: SteeringQueue
    checkpoint_store: CheckpointStore
    metrics_store: MetricsStore
    workspace_manager: GitWorktreeManager | None
    repository: RepositoryRuntime
    coding_worker: BaseAgent
    validation_executor: Callable[[str], ManagedValidationExecutor]
    static_prefix_hash: str
    static_prefix_tokens: int
    work_packet_tokens: int
    max_task_input_tokens: int
    work_packet_section_tokens: dict[str, int]
    progress_history_limit: int
    progress_replan_threshold: int
    progress_human_threshold: int
    steering_batch_limit: int
    steering_enabled: bool
    steering_at_work_batch_boundary: bool
    approvals: ApprovalWaiter | None = None
    replies: PublicReplies | None = None


@dataclass(slots=True)
class _RunState:
    request: TaskRequest
    ledger: TaskLedger
    task_id: str
    session_id: str | None
    history: str
    compaction_summary: str
    compaction_id: str | None
    owner: str
    max_iterations: int
    initial_fingerprint: str
    skill_runtime: SkillRuntimeContext


@dataclass(slots=True)
class _VerificationTransition:
    ledger: TaskLedger
    result: str | None = None


def _session_id(ctx: Context) -> str | None:
    direct = getattr(ctx, "session_id", None)
    if direct:
        return str(direct)
    session = getattr(ctx, "session", None)
    identifier = getattr(session, "id", None)
    return str(identifier) if identifier else None


def _workspace_fingerprint(deps: SkeinWorkflowDependencies, task_id: str) -> str:
    manager = deps.workspace_manager
    if manager is not None and manager.load(task_id) is not None:
        return manager.fingerprint(task_id)
    return deps.repository.fingerprint()


_LEDGER_DUPLICATE_EVENT_KINDS = {
    EventKind.TASK_CREATED,
    EventKind.LEDGER_PATCHED,
    EventKind.CHECKPOINT_CREATED,
    EventKind.MESSAGE_RECORDED,
}


def _render_recent_events(deps: SkeinWorkflowDependencies, task_id: str) -> list[str]:
    rendered: list[str] = []
    events = [
        event
        for event in deps.event_store.read(task_id)
        if event.kind not in _LEDGER_DUPLICATE_EVENT_KINDS
    ][-deps.settings.recent_event_limit :]
    for event in events:
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        if len(payload) > 1_000:
            payload = payload[:1_000] + "…"
        rendered.append(f"{event.sequence}. {event.kind}: {payload}")
    return rendered


def _latest_compaction(
    deps: SkeinWorkflowDependencies,
    task_id: str,
) -> tuple[str, str | None]:
    for event in reversed(deps.event_store.read(task_id)):
        if event.kind == EventKind.COMPACTION_CREATED:
            return str(event.payload.get("summary", "")), event.event_id
    return "", None


def _ledger_patch(before: TaskLedger, after: TaskLedger) -> dict[str, Any]:
    previous = before.model_dump(mode="json")
    current = after.model_dump(mode="json")
    return {
        "set_fields": {key: value for key, value in current.items() if previous.get(key) != value}
    }


def _criterion_review_action(ledger: TaskLedger, step: AgentStep) -> str:
    claimed = {claim.criterion: claim.evidence for claim in step.completion_claims}
    rows = [
        f"- {criterion}: " + (
            "; ".join(claimed[criterion])
            if claimed.get(criterion)
            else "MISSING concrete implementation or test evidence"
        )
        for criterion in ledger.acceptance_criteria
    ]
    return (
        "Perform the single criterion-gap review. Preserve the complete original requirements. "
        "Try to falsify weak or missing rows with omitted, default, boundary, and interacting "
        "inputs; fix confirmed defects and return updated completion_claims. Do not repeat broad "
        "exploration. When every row has concrete implementation and test evidence and the "
        "targeted checks pass, request verification immediately.\n"
        + "\n".join(rows)
    )


def _with_workspace_observations(
    ledger: TaskLedger,
    step: AgentStep,
    modified: list[str],
    action_fingerprints: list[str],
    history_limit: int = 40,
) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["files_modified"] = modified
    data["files_read"] = list(dict.fromkeys([*data.get("files_read", []), *step.files_in_focus]))
    observed = TaskLedger.model_validate(data)
    return register_action_batch(observed, action_fingerprints, history_limit=history_limit)


def _consume_tool_action_fingerprints(ctx: Context) -> list[str]:
    """Consume monotonic tool observations written by the metrics plugin."""

    raw_history = ctx.state.get("tool_action_fingerprints", [])
    last_sequence = int(ctx.state.get("workflow_tool_action_sequence", 0) or 0)
    observed: list[tuple[int, str]] = []
    if isinstance(raw_history, list):
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            try:
                sequence = int(item.get("sequence", 0))
            except (TypeError, ValueError):
                continue
            fingerprint = item.get("fingerprint")
            if sequence > last_sequence and isinstance(fingerprint, str):
                observed.append((sequence, fingerprint))
    observed.sort()
    if observed:
        ctx.state["workflow_tool_action_sequence"] = observed[-1][0]
    return [fingerprint for _, fingerprint in observed]


def _reserve_task_input_budget(
    state: Any,
    *,
    projected_tokens: int,
    limit: int,
) -> tuple[bool, int]:
    """Atomically reserve an estimated model-call input budget in ADK state."""

    used = int(state.get("estimated_task_input_tokens", 0) or 0)
    if used + projected_tokens > limit:
        return False, used
    state["estimated_task_input_tokens"] = used + projected_tokens
    return True, used


def _save_checkpoint(
    deps: SkeinWorkflowDependencies,
    *,
    task_id: str,
    ledger: TaskLedger,
    session_id: str | None,
    compaction_id: str | None,
    invocation_id: str,
) -> Checkpoint:
    fingerprint = _workspace_fingerprint(deps, task_id)
    ledger_json = ledger.model_dump_json()
    event_stream = deps.event_store.read(task_id)
    context_epoch = next((str(event.payload["context_epoch"]) for event in reversed(event_stream)
                          if event.payload.get("context_epoch") is not None), None)
    receipts = ToolReceiptStore(deps.settings.state_root / "managed-tools.db").for_task(task_id)
    latest = deps.checkpoint_store.latest(task_id)
    checkpoint_id = hashlib.sha256(
        f"{task_id}\0{invocation_id}\0{compaction_id}\0{context_epoch}\0{ledger.iteration}\0{fingerprint}\0{ledger_json}".encode()
    ).hexdigest()[:32]
    if latest is not None and latest.checkpoint_id == checkpoint_id:
        return latest
    checkpoint = Checkpoint(
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        session_id=session_id or "unknown",
        invocation_id=invocation_id,
        branch_id=ledger.branch_id,
        parent_checkpoint_id=(latest.checkpoint_id if latest else None),
        workspace_id=ledger.workspace_id,
        base_revision=ledger.base_revision,
        git_tree_hash=fingerprint,
        ledger_version=event_stream[-1].sequence,
        ledger_hash=hashlib.sha256(ledger_json.encode()).hexdigest(),
        event_stream_hash=hashlib.sha256(
            "\n".join(event.model_dump_json() for event in event_stream).encode()
        ).hexdigest(),
        receipt_stream_hash=hashlib.sha256(
            "\n".join(receipt.model_dump_json() for receipt in receipts).encode()
        ).hexdigest(),
        context_epoch=context_epoch,
        compaction_id=compaction_id,
        created_at=datetime.now(UTC),
    )
    deps.event_store.append(
        task_id,
        EventKind.CHECKPOINT_CREATED,
        {
            "checkpoint_id": checkpoint.checkpoint_id,
            "workspace_fingerprint": fingerprint,
            "ledger_hash": checkpoint.ledger_hash,
        },
        idempotency_key=f"checkpoint:{checkpoint.checkpoint_id}",
    )
    deps.checkpoint_store.save(checkpoint)
    return checkpoint


def _event_count(deps: SkeinWorkflowDependencies, task_id: str, kind: EventKind) -> int:
    return sum(1 for event in deps.event_store.read(task_id) if event.kind == kind)


def _record_message(
    deps: SkeinWorkflowDependencies,
    *,
    task_id: str,
    role: Literal["user", "assistant"],
    content: str,
    idempotency_key: str,
) -> None:
    """Record public prose, then refresh an existing notebook projection."""

    if not content:
        return
    deps.event_store.append(
        task_id,
        EventKind.MESSAGE_RECORDED,
        {"role": role, "content": content},
        idempotency_key=idempotency_key,
    )
    notebook_id = hashlib.sha256(task_id.encode()).hexdigest()[:32]
    notebook_path = deps.settings.state_root / "notebooks" / f"{notebook_id}.ipynb"
    if notebook_path.exists():
        materialize_notebook(
            reduce_notebook(deps.event_store.read(task_id), notebook_id),
            notebook_path,
        )


def _record_outcome(
    deps: SkeinWorkflowDependencies,
    *,
    task_id: str,
    ledger: TaskLedger,
    status: Literal["complete", "answered", "blocked", "failed"],
    passed: bool,
    started: float,
    tests_passed: int = 0,
    tests_failed: int = 0,
) -> None:
    events = deps.event_store.read(task_id)
    deps.metrics_store.record_outcome(
        TaskOutcomeSample(
            task_id=task_id,
            status=status,
            passed=passed,
            iterations=ledger.iteration,
            compactions=sum(1 for event in events if event.kind == EventKind.COMPACTION_CREATED),
            replans=sum(
                1
                for event in events
                if event.idempotency_key and event.idempotency_key.startswith("replan:")
            ),
            user_interventions=sum(
                1 for event in events if event.kind == EventKind.STEERING_RECEIVED
            ),
            changed_files=len(ledger.files_modified),
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            wall_time_ms=int((time.monotonic() - started) * 1000),
        )
    )


def _malformed_step(error: ValueError) -> AgentStep:
    return AgentStep(
        status="blocked",
        progress=[],
        next_action="Retry with a model that supports the required AgentStep schema",
        decisions=[],
        questions=["The coding run ended without a valid AgentStep result."],
        discovered_constraints=[str(error)[:1_000]],
        files_in_focus=[],
        completion_claims=[],
    )


def _set_model_call_state(
    ctx: Context,
    *,
    task_id: str,
    dynamic_tokens: int,
    stable_prefix_hash: str,
    static_prefix_tokens: int,
    task_input_token_limit: int | None = None,
    steering_owner: str | None = None,
    steering_packet_message_ids: tuple[str, ...] = (),
) -> None:
    """Expose current packet identity to ADK callbacks before the model call."""

    state_delta: dict[str, Any] = {
        "task_id": task_id,
        "stable_instruction_sha256": stable_prefix_hash,
        "static_prefix_tokens_estimate": static_prefix_tokens,
        "dynamic_context_tokens_estimate": dynamic_tokens,
    }
    if task_input_token_limit is not None:
        state_delta["task_input_token_limit"] = task_input_token_limit
    if steering_owner is not None:
        state_delta["steering_owner"] = steering_owner
        state_delta["steering_packet_message_ids"] = list(steering_packet_message_ids)
    ctx.state.update(state_delta)


def _set_skill_state(
    ctx: Context,
    runtime: SkillRuntimeContext,
) -> None:
    ctx.state.update(
        {
            "skill_selection_initialized": True,
            "skill_context_text": runtime.text,
            "selected_skill_names": list(runtime.selected_names),
            "selected_skill_hashes": list(runtime.selected_hashes),
        }
    )


def _skill_runtime_from_state(ctx: Context) -> SkillRuntimeContext | None:
    state = ctx.state
    if not bool(state.get("skill_selection_initialized", False)):
        return None
    return SkillRuntimeContext(
        text=str(state.get("skill_context_text", "")),
        selected_names=tuple(state.get("selected_skill_names", ())),
        selected_hashes=tuple(state.get("selected_skill_hashes", ())),
    )


async def _verify_task(
    deps: SkeinWorkflowDependencies,
    ctx: Context,
    node_input: dict[str, Any],
) -> dict[str, Any]:
    """Run deterministic checks; model claims are evidence, not verdicts."""

    request = TaskRequest.model_validate(node_input["request"])
    ledger = TaskLedger.model_validate(node_input["ledger"])
    claims = node_input.get("claims", [])
    manifest = deps.repository.manifest()
    modified = deps.repository.changed_paths(ledger.base_revision)
    plan = discover_validation_plan(
        manifest,
        modified,
        allowed_paths=getattr(request, "permitted_paths", None),
        forbidden_paths=getattr(request, "forbidden_paths", []),
    )
    for command in getattr(request, "verification_requirements", []):
        plan.commands.append(
            ValidationCommand(
                category="custom",
                command=command,
                source="task verification requirement",
                strength="behavioral",
            )
        )
    evidence_map: dict[str, list[str]] = {
        claim["criterion"]: list(claim.get("evidence", [])) for claim in claims
    }
    executor = deps.validation_executor(ledger.task_id)
    baseline_results = {
        str(event.payload["command"]): CommandResult.model_validate(
            event.payload["result"]
        )
        for event in deps.event_store.read(ledger.task_id)
        if event.kind == "validation.baseline" and event.payload.get("valid") is True
    }

    async def execute_validation(command: ValidationCommand, operation_id: str) -> CommandResult:
        events = deps.event_store.read(ledger.task_id)
        workspace_before = _workspace_fingerprint(deps, ledger.task_id)
        command_sha256 = hashlib.sha256(command.command.encode()).hexdigest()
        prior = next((event for event in events if event.kind == "execution.validation_completed"
                      and event.payload.get("operation_id") == operation_id), None)
        if prior is not None:
            if prior.payload.get("workspace_after") != _workspace_fingerprint(deps, ledger.task_id):
                raise ValueError("validation replay requires workspace reconciliation")
            return CommandResult.model_validate(prior.payload["result"])
        if any(event.kind == "execution.validation_requested" and event.payload.get("operation_id") == operation_id
               for event in events):
            raise ValueError("interrupted validation requires reconciliation")
        cached = next(
            (
                event
                for event in reversed(events)
                if event.kind in {
                    "execution.validation_completed", "execution.validation_observed"
                }
                and event.payload.get("command_sha256") == command_sha256
                and event.payload.get("workspace_after") == workspace_before
                and event.payload.get("result", {}).get("status") in {"ok", "error"}
                and (
                    event.kind == "execution.validation_completed"
                    or event.payload.get("environment") == dict(MANAGED_COMMAND_ENVIRONMENT)
                )
            ),
            None,
        )
        deps.event_store.append(ledger.task_id, "execution.validation_requested", {
            "operation_id": operation_id, "command": command.command,
            "command_sha256": command_sha256, "workspace_before": workspace_before,
        }, idempotency_key=f"validation:{operation_id}:requested")
        if cached is None:
            result = await run_managed_thread(executor, command)
        elif cached.kind == "execution.validation_observed":
            observed = cached.payload["result"]
            result = CommandResult(
                category=command.category,
                command=command.command,
                source="complete PTC shell receipt",
                status="ok",
                exit_code=observed.get("exit_code"),
                stdout=str(observed.get("stdout", "")),
                duration_ms=int(observed.get("duration_ms", 0)),
                artifact_uri=observed.get("artifact_uri"),
            )
        else:
            result = CommandResult.model_validate(cached.payload["result"])
        deps.event_store.append(ledger.task_id, "execution.validation_completed", {
            "operation_id": operation_id, "result": result.model_dump(mode="json"),
            "workspace_after": _workspace_fingerprint(deps, ledger.task_id),
            "command_sha256": command_sha256,
            "cached": cached is not None,
            "source_operation_id": cached.payload.get("operation_id") if cached else None,
        }, idempotency_key=f"validation:{operation_id}:completed")
        return result

    command_results = []
    for index, command in enumerate(plan.commands):
        operation_id = f"{ctx.get_invocation_context().invocation_id}:{ledger.iteration}:{index}"
        result = await execute_validation(command, operation_id)
        if deps.approvals is not None and result.status == "blocked" and result.approval_request_id:
            decision = await deps.approvals.wait(result.approval_request_id, ledger.task_id)
            if decision.status == "approved":
                result = await execute_validation(command, operation_id + ":approved")
            else:
                result = result.model_copy(
                    update={
                        "stderr": f"Validation command not executed: approval {decision.status}."
                    }
                )
        result = enforce_test_count(command, result).model_copy(
            update={
                "required": command.required,
                "targeted": command.targeted,
                "strength": command.effective_strength,
            }
        )
        command_results.append(result)
        if (
            command.required
            and not result.passed
            and not passes_recorded_baseline(
                result, baseline_results.get(command.command)
            )
        ):
            break
    report = build_report(
        criteria=ledger.acceptance_criteria,
        results=command_results,
        scope_violations=check_scope(
            plan.changed_paths,
            allowed_paths=plan.allowed_paths,
            forbidden_paths=plan.forbidden_paths,
        ),
        criterion_evidence=evidence_map,
        required_strength=request.verification_level,
        changed_paths=plan.changed_paths,
        baseline_results=baseline_results,
    )
    return {
        "report": report.model_dump(mode="json"),
        "commands": [result.model_dump(mode="json") for result in command_results],
        "changed_paths": modified,
        "workspace_fingerprint": _workspace_fingerprint(deps, ledger.task_id),
    }


async def _ensure_validation_baseline(
    deps: SkeinWorkflowDependencies,
    task_id: str,
) -> None:
    """Record repository test behavior before the coding worker mutates it."""

    events = deps.event_store.read(task_id)
    if any(event.kind == "validation.baseline.completed" for event in events):
        return
    commands = [
        command
        for command in discover_validation_plan(deps.repository.manifest(), []).commands
        if command.category == "test"
    ]
    executor = deps.validation_executor(task_id)
    initial_workspace = _workspace_fingerprint(deps, task_id)
    for index, command in enumerate(commands):
        deps.event_store.append(
            task_id,
            "validation.baseline_requested",
            {
                "command": command.command,
                "workspace_fingerprint": initial_workspace,
            },
            idempotency_key=f"validation-baseline:{index}:requested",
        )
        result = enforce_test_count(
            command,
            await run_managed_thread(executor, command),
        ).model_copy(
            update={
                "required": command.required,
                "targeted": command.targeted,
                "strength": command.effective_strength,
            }
        )
        workspace_after = _workspace_fingerprint(deps, task_id)
        deps.event_store.append(
            task_id,
            "validation.baseline",
            {
                "command": command.command,
                "result": result.model_dump(mode="json"),
                "workspace_fingerprint": initial_workspace,
                "valid": workspace_after == initial_workspace,
            },
            idempotency_key=f"validation-baseline:{index}",
        )
        if workspace_after != initial_workspace:
            break
    deps.event_store.append(
        task_id,
        "validation.baseline.completed",
        {"commands": len(commands), "workspace_fingerprint": initial_workspace},
        idempotency_key="validation-baseline:completed",
    )


def _initialize_run(
    deps: SkeinWorkflowDependencies,
    ctx: Context,
    node_input: str | dict[str, Any],
) -> tuple[_RunState, bool, bool]:
    """Restore durable task state and prepare the stable run-scoped inputs."""

    request = parse_task_request(node_input)
    session_id = _session_id(ctx)
    settings = deps.settings
    history = conversation_history(
        ctx.session.events,
        invocation_id=ctx.get_invocation_context().invocation_id,
        max_tokens=deps.work_packet_section_tokens.get("CONVERSATION", 2_000),
    )
    task_id = settings.task_id_override or task_id_for(request, session_id)
    state_reset = ctx.state.get("harness_task_id") != task_id
    if state_reset:
        ctx.state.update(
            {
                "harness_task_id": task_id,
                "estimated_task_input_tokens": 0,
                "tool_action_fingerprints": [],
                "workflow_tool_action_sequence": 0,
                "verification_required_task": None,
                "skill_selection_initialized": False,
                "skill_context_text": "",
                "selected_skill_names": [],
                "selected_skill_hashes": [],
                "steering_packet_message_ids": [],
            }
        )

    manifest = deps.repository.manifest()
    events = deps.event_store.read(task_id)
    ctx.state["estimated_task_input_tokens"] = max(
        int(ctx.state.get("estimated_task_input_tokens", 0) or 0),
        max((int(event.payload.get("estimated_task_input_tokens", 0)) for event in events
             if event.kind == "execution.model_budget_reserved"), default=0),
    )
    if events:
        ledger = rebuild_ledger(events)
        if ledger.mode == "coding" and request.mode == "auto":
            request = TaskRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "mode": "coding",
                    "acceptance_criteria": ledger.acceptance_criteria,
                }
            )
    else:
        ledger = create_initial_ledger(
            request,
            task_id=task_id,
            base_revision=settings.base_revision_override or manifest.base_revision or "unknown",
            workspace_id=settings.workspace_id_override or settings.workspace.as_posix(),
            branch_id=manifest.branch or "detached",
        )
        deps.event_store.append(
            task_id,
            EventKind.TASK_CREATED,
            {"ledger": ledger.model_dump(mode="json")},
            idempotency_key="task-created",
        )

    invocation_id = ctx.get_invocation_context().invocation_id
    _record_message(
        deps,
        task_id=task_id,
        role="user",
        content=request.goal,
        idempotency_key=f"message:user:{invocation_id}",
    )

    latest_checkpoint = deps.checkpoint_store.latest(task_id)
    current_fingerprint = _workspace_fingerprint(deps, task_id)
    workspace_diverged = latest_checkpoint is not None and latest_checkpoint.git_tree_hash != current_fingerprint
    if workspace_diverged and latest_checkpoint is not None and latest_checkpoint.invocation_id == invocation_id:
        try:
            validate_recovery_evidence(
                latest_checkpoint, deps.event_store.read(task_id),
                ToolReceiptStore(settings.state_root / "managed-tools.db").for_task(task_id),
                invocation_id=invocation_id, session_id=session_id or "unknown",
                workspace_fingerprint=current_fingerprint,
            )
        except ValueError:
            pass
        else:
            workspace_diverged = False
    if latest_checkpoint is not None and workspace_diverged:
        previous = ledger
        ledger = TaskLedger.model_validate({
            **ledger.model_dump(mode="python"),
            "next_action": (
                "Reconcile workspace changes made after the latest checkpoint before "
                "continuing implementation"
            ),
        })
        deps.event_store.append(
            task_id,
            EventKind.WORKSPACE_INITIALIZED,
            {
                "checkpoint_id": latest_checkpoint.checkpoint_id,
                "expected_fingerprint": latest_checkpoint.git_tree_hash,
                "actual_fingerprint": current_fingerprint,
                "requires_reconciliation": True,
            },
            idempotency_key=f"workspace-reconcile:{current_fingerprint}",
        )
        deps.event_store.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"workspace-reconcile-ledger:{current_fingerprint}",
        )

    compaction_summary, compaction_id = _latest_compaction(deps, task_id)
    if latest_checkpoint is None:
        _save_checkpoint(
            deps,
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
            invocation_id=invocation_id,
        )

    skill_runtime = _skill_runtime_from_state(ctx)
    skill_initialized = skill_runtime is None
    if skill_runtime is None:
        try:
            skill_runtime = build_skill_context(
                goal=ledger.goal,
                next_action=ledger.next_action or "",
                settings=settings,
            )
        except Exception as error:
            skill_runtime = SkillRuntimeContext()
            deps.event_store.append(
                task_id,
                EventKind.ACTION_RECORDED,
                {"kind": "skill_loading_failed", "error_type": type(error).__name__},
                idempotency_key=f"skill-loading-failed:{type(error).__name__}",
            )
        _set_skill_state(ctx, skill_runtime)

    skill_event = {
        "kind": "skills_selected",
        "names": list(skill_runtime.selected_names),
        "hashes": list(skill_runtime.selected_hashes),
    }
    skill_event_hash = hashlib.sha256(json.dumps(skill_event, sort_keys=True).encode()).hexdigest()[
        :16
    ]
    deps.event_store.append(
        task_id,
        EventKind.ACTION_RECORDED,
        skill_event,
        idempotency_key=f"skills-selected:{skill_event_hash}",
    )
    max_iterations = min(
        settings.max_iterations,
        int(getattr(request, "max_iterations", None) or settings.max_iterations),
    )
    return (
        _RunState(
            request=request,
            ledger=ledger,
            task_id=task_id,
            session_id=session_id,
            history=history,
            compaction_summary=compaction_summary,
            compaction_id=compaction_id,
            owner=f"{settings.worker_id}:{session_id or task_id}",
            max_iterations=max_iterations,
            initial_fingerprint=current_fingerprint,
            skill_runtime=skill_runtime,
        ),
        state_reset,
        skill_initialized,
    )


def _answer_result(
    deps: SkeinWorkflowDependencies,
    *,
    ledger: TaskLedger,
    step: AgentStep,
    owner: str,
    session_id: str | None,
    compaction_id: str | None,
    invocation_id: str,
    started: float,
) -> tuple[TaskLedger, str]:
    previous = ledger
    ledger = TaskLedger.model_validate({
        **ledger.model_dump(mode="python"),
        "status": "answered",
        "next_action": None,
        "iteration": ledger.iteration + 1,
    })
    deps.event_store.append(
        ledger.task_id,
        EventKind.LEDGER_PATCHED,
        _ledger_patch(previous, ledger),
        idempotency_key=f"answer:{ledger.iteration}",
    )
    delivered = deps.steering_queue.leased_by(ledger.task_id, owner)
    if delivered:
        deps.steering_queue.ack([item.message_id for item in delivered], owner)
    _save_checkpoint(
        deps,
        task_id=ledger.task_id,
        ledger=ledger,
        session_id=session_id,
        compaction_id=compaction_id,
        invocation_id=invocation_id,
    )
    _record_outcome(
        deps,
        task_id=ledger.task_id,
        ledger=ledger,
        status="answered",
        passed=False,
        started=started,
    )
    _record_message(
        deps,
        task_id=ledger.task_id,
        role="assistant",
        content=step.message,
        idempotency_key=f"message:assistant:answer:{ledger.iteration}",
    )
    return ledger, json.dumps({"status": "answered", "message": step.message})


def _blocked_result(
    deps: SkeinWorkflowDependencies,
    *,
    ledger: TaskLedger,
    started: float,
) -> str:
    reason = ledger.blockers[-1] if ledger.blockers else "Human input required"
    deps.event_store.append(
        ledger.task_id,
        EventKind.TASK_BLOCKED,
        {"reason": reason},
        idempotency_key=f"blocked:{ledger.iteration}",
    )
    _record_outcome(
        deps,
        task_id=ledger.task_id,
        ledger=ledger,
        status="blocked",
        passed=False,
        started=started,
    )
    return json.dumps(
        {
            "status": "blocked",
            "task_id": ledger.task_id,
            "questions": ledger.open_questions,
            "blockers": ledger.blockers,
            "metrics": deps.metrics_store.task_summary(ledger.task_id),
        },
        sort_keys=True,
    )


async def _verification_transition(
    deps: SkeinWorkflowDependencies,
    ctx: Context,
    verify_node: BaseNode,
    *,
    request: TaskRequest,
    ledger: TaskLedger,
    step: AgentStep,
    session_id: str | None,
    compaction_id: str | None,
    started: float,
) -> _VerificationTransition:
    verification = await ctx.run_node(
        verify_node,
        node_input={
            "request": request.model_dump(mode="json"),
            "ledger": ledger.model_dump(mode="json"),
            "claims": [claim.model_dump(mode="json") for claim in step.completion_claims],
        },
    )
    report = verification["report"]
    deps.event_store.append(
        ledger.task_id,
        EventKind.VERIFICATION_COMPLETED,
        verification,
        idempotency_key=f"verify:{ledger.iteration}",
    )
    if not report["passed"]:
        fingerprint = verification_fingerprint(report)
        workspace = verification.get("workspace_fingerprint") or _workspace_fingerprint(
            deps, ledger.task_id
        )
        consecutive = 0
        for event in reversed(deps.event_store.read(ledger.task_id)):
            if event.kind != EventKind.VERIFICATION_COMPLETED:
                continue
            event_report = event.payload.get("report", {})
            if (
                event_report.get("passed")
                or event.payload.get("workspace_fingerprint", workspace) != workspace
                or verification_fingerprint(event_report) != fingerprint
            ):
                break
            consecutive += 1
        blocked_on_verification = consecutive >= 2
        previous = ledger
        blockers = list(ledger.blockers)
        if blocked_on_verification:
            blockers.append(
                "Verification failed twice on an unchanged workspace; autonomous retry stopped"
            )
        ledger = TaskLedger.model_validate({
            **ledger.model_dump(mode="python"),
            "phase": "blocked" if blocked_on_verification else "implement",
            "status": "needs_input" if blocked_on_verification else "active",
            "blockers": list(dict.fromkeys(blockers)),
            "next_action": report.get("recommended_next_action"),
        })
        deps.event_store.append(
            ledger.task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"verification-failed:{ledger.iteration}",
        )
        _save_checkpoint(
            deps,
            task_id=ledger.task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
            invocation_id=ctx.get_invocation_context().invocation_id,
        )
        result = (
            _blocked_result(deps, ledger=ledger, started=started)
            if blocked_on_verification
            else None
        )
        return _VerificationTransition(ledger=ledger, result=result)

    pending_steering = (
        deps.steering_enabled
        and deps.steering_at_work_batch_boundary
        and deps.steering_queue.has_pending(ledger.task_id)
    )
    if pending_steering:
        previous = ledger
        ledger = resume_for_steering(ledger)
        deps.event_store.append(
            ledger.task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"steering-completion-fence:{ledger.iteration}",
        )
        _save_checkpoint(
            deps,
            task_id=ledger.task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
            invocation_id=ctx.get_invocation_context().invocation_id,
        )
        return _VerificationTransition(ledger=ledger)

    deps.event_store.append(
        ledger.task_id,
        EventKind.TASK_FINISHED,
        {"verification": report},
        idempotency_key="task-finished",
    )
    ledger = TaskLedger.model_validate({
        **ledger.model_dump(mode="python"),
        "phase": "complete",
        "status": "complete",
    })
    _save_checkpoint(
        deps,
        task_id=ledger.task_id,
        ledger=ledger,
        session_id=session_id,
        compaction_id=compaction_id,
        invocation_id=ctx.get_invocation_context().invocation_id,
    )
    _record_outcome(
        deps,
        task_id=ledger.task_id,
        ledger=ledger,
        status="complete",
        passed=True,
        started=started,
        tests_passed=int(report.get("tests_passed", 0)),
        tests_failed=int(report.get("tests_failed", 0)),
    )
    _record_message(
        deps,
        task_id=ledger.task_id,
        role="assistant",
        content=step.message,
        idempotency_key=f"message:assistant:complete:{ledger.iteration}",
    )
    return _VerificationTransition(
        ledger=ledger,
        result=json.dumps(
            {
                "status": "complete",
                "message": step.message,
                "task_id": ledger.task_id,
                "changed_paths": verification["changed_paths"],
                "verification": report,
                "metrics": deps.metrics_store.task_summary(ledger.task_id),
            },
            sort_keys=True,
        ),
    )


async def _orchestrate_owned(
    deps: SkeinWorkflowDependencies,
    ctx: Context,
    node_input: str | dict[str, Any],
    *,
    verify_node: BaseNode,
) -> AsyncGenerator[Event | str, None]:
    started = time.monotonic()
    reply_stream = (
        deps.replies.for_invocation(ctx.get_invocation_context().invocation_id)
        if deps.replies
        else None
    )
    run, state_reset, skill_initialized = _initialize_run(deps, ctx, node_input)
    request = run.request
    ledger = run.ledger
    task_id = run.task_id
    session_id = run.session_id
    history = run.history
    compaction_summary = run.compaction_summary
    compaction_id = run.compaction_id
    owner = run.owner
    max_iterations = run.max_iterations
    current_fingerprint = run.initial_fingerprint
    skill_runtime = run.skill_runtime
    settings = deps.settings
    # Flush parent state before child tools update it, and publish selected skill
    # names before the worker starts. Public events exclude skill bodies and hashes.
    if state_reset:
        yield Event()
    if skill_initialized:
        yield Event()

    if request.mode == "coding" and ledger.iteration == 0:
        await _ensure_validation_baseline(deps, task_id)
        current_fingerprint = _workspace_fingerprint(deps, task_id)

    while ledger.iteration < max_iterations:
        leased = (
            deps.steering_queue.lease(
                task_id,
                owner,
                limit=deps.steering_batch_limit,
                lease_seconds=settings.task_lease_seconds,
            )
            if deps.steering_enabled and deps.steering_at_work_batch_boundary
            else []
        )
        steering = [message.content for message in leased]
        for message in leased:
            deps.event_store.append(
                task_id,
                EventKind.STEERING_RECEIVED,
                {"message_id": message.message_id, "content": message.content},
                idempotency_key=f"steering:{message.message_id}",
            )

        manifest = deps.repository.manifest()
        packet = build_work_packet(
            ledger,
            conversation=history,
            selected_skills=skill_runtime.text,
            repository_manifest=manifest.to_compact_text(),
            compaction_summary=compaction_summary,
            recent_events=_render_recent_events(deps, task_id),
            steering_messages=steering,
            max_tokens=deps.work_packet_tokens,
            section_token_limits=deps.work_packet_section_tokens,
        )
        dynamic_tokens = estimate_tokens(packet)
        total_context_estimate = deps.static_prefix_tokens + dynamic_tokens

        task_input_budget = min(
            deps.max_task_input_tokens,
            request.max_input_tokens or deps.max_task_input_tokens,
        )
        budget_reserved, estimated_task_input = _reserve_task_input_budget(
            ctx.state,
            projected_tokens=total_context_estimate,
            limit=task_input_budget,
        )
        if not budget_reserved:
            reason = (
                "Task input-token budget exhausted before the next model call "
                f"({estimated_task_input} used, {total_context_estimate} projected, "
                f"{task_input_budget} allowed)"
            )
            deps.event_store.append(
                task_id,
                EventKind.TASK_BLOCKED,
                {"reason": reason, "kind": "input_token_budget"},
                idempotency_key=f"input-budget:{ledger.iteration}",
            )
            _record_outcome(
                deps,
                task_id=task_id,
                ledger=ledger,
                status="blocked",
                passed=False,
                started=started,
            )
            yield json.dumps(
                {
                    "status": "blocked",
                    "task_id": task_id,
                    "blockers": [reason],
                    "metrics": deps.metrics_store.task_summary(task_id),
                },
                sort_keys=True,
            )
            return

        deps.event_store.append(
            task_id, "execution.model_budget_reserved",
            {"invocation_id": ctx.get_invocation_context().invocation_id,
             "estimated_task_input_tokens": int(ctx.state["estimated_task_input_tokens"]),
             "task_input_token_limit": task_input_budget},
            idempotency_key=f"model-budget:{ctx.get_invocation_context().invocation_id}:{ctx.state['estimated_task_input_tokens']}",
        )
        _set_model_call_state(
            ctx,
            task_id=task_id,
            dynamic_tokens=dynamic_tokens,
            stable_prefix_hash=deps.static_prefix_hash,
            static_prefix_tokens=deps.static_prefix_tokens,
            task_input_token_limit=task_input_budget,
            steering_owner=owner,
            steering_packet_message_ids=tuple(message.message_id for message in leased),
        )
        ctx.state["task_phase"] = ledger.phase.value
        work_batch_id = str(ledger.iteration + 1)
        ctx.state["ptc_work_batch_id"] = work_batch_id

        async def allow_reply(step: AgentStep, active_request: TaskRequest = request) -> bool:
            eligible = can_answer_directly(
                active_request,
                step,
                verification_required=ctx.state.get("verification_required_task") == task_id,
                workspace_unchanged=True,
            )
            return (
                eligible
                and (
                    await asyncio.to_thread(_workspace_fingerprint, deps, task_id)
                    == current_fingerprint
                )
                and not (deps.steering_enabled and deps.steering_queue.has_pending(task_id))
            )

        if reply_stream is not None:
            reply_stream.prepare(allow_reply)
        try:
            raw_step = await ctx.run_node(deps.coding_worker, node_input=packet)
        except BaseException:
            owned = deps.steering_queue.leased_by(task_id, owner)
            deps.steering_queue.release(
                [message.message_id for message in owned],
                owner,
            )
            raise
        try:
            step = parse_agent_step(raw_step)
        except ValueError as error:
            deps.event_store.append(
                task_id,
                EventKind.ACTION_RECORDED,
                {"kind": "malformed_agent_step", "error": str(error)[:2_000]},
                idempotency_key=f"malformed-step:{ledger.iteration + 1}",
            )
            step = _malformed_step(error)

        if reply_stream is not None:
            step = reply_stream.finish(step)

        batch_yield = next(
            (
                event.payload
                for event in reversed(deps.event_store.read(task_id))
                if event.kind == EventKind.WORK_BATCH_YIELDED
                and str(event.payload.get("work_batch_id", "")) == work_batch_id
            ),
            None,
        )
        if isinstance(batch_yield, dict) and step.status == "blocked":
            step = step.model_copy(
                update={
                    "status": "continue",
                    "next_action": "Audit criterion gaps, then continue from the durable notebook.",
                    "questions": [],
                }
            )
        elif step.status == "continue":
            # The ADK coding worker owns its complete model/tool loop. A final
            # response cannot hand ordinary coding work back to this workflow.
            step = step.model_copy(
                update={
                    "status": "blocked",
                    "next_action": "Resume with a terminal answer, verification request, or blocker",
                    "questions": [
                        *step.questions,
                        "The coding run stopped before requesting verification.",
                    ],
                }
            )

        if step.status == "answer":
            # Tool effects and explicit task obligations cannot be waived by a
            # model choosing a conversational status. Bind effects to this task.
            allowed = can_answer_directly(
                request,
                step,
                verification_required=(ctx.state.get("verification_required_task") == task_id),
                workspace_unchanged=(_workspace_fingerprint(deps, task_id) == current_fingerprint),
            )
            if not allowed:
                if reply_stream is not None and reply_stream.started:
                    raise ValueError(
                        "Workspace or verification obligations changed during a streamed reply"
                    )
                step = step.model_copy(update={"status": "verify"})
            elif deps.steering_enabled and deps.steering_queue.has_pending(task_id):
                if reply_stream is not None and reply_stream.started:
                    _record_message(
                        deps,
                        task_id=task_id,
                        role="assistant",
                        content=step.message,
                        idempotency_key=f"message:assistant:steering:{ledger.iteration + 1}",
                    )
                    yield message_event(step.message)
                step = step.model_copy(update={"status": "continue", "message": ""})
            else:
                ledger, result = _answer_result(
                    deps,
                    ledger=ledger,
                    step=step,
                    owner=owner,
                    session_id=session_id,
                    compaction_id=compaction_id,
                    invocation_id=ctx.get_invocation_context().invocation_id,
                    started=started,
                )
                yield result
                return

        if step.status in {"verify", "done"} and request.mode == "auto":
            request = TaskRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "mode": "coding",
                }
            )
            before_promotion = ledger
            ledger = TaskLedger.model_validate(
                {
                    **ledger.model_dump(mode="python"),
                    "mode": "coding",
                    "acceptance_criteria": request.acceptance_criteria,
                }
            )
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(before_promotion, ledger),
                idempotency_key="coding-contract-established",
            )

        previous = ledger
        ledger = reduce_agent_step(ledger, step)
        action_fingerprints = _consume_tool_action_fingerprints(ctx)
        ledger = _with_workspace_observations(
            ledger,
            step,
            deps.repository.changed_paths(ledger.base_revision),
            action_fingerprints,
            deps.progress_history_limit,
        )
        deps.event_store.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"agent-step:{ledger.iteration}",
        )
        if isinstance(batch_yield, dict):
            previous = ledger
            first_review = not ledger.counterexample_review_completed
            update: dict[str, Any] = {
                "phase": "review" if first_review else "implement",
                "status": "active",
                "next_action": (
                    _criterion_review_action(ledger, step)
                    if first_review
                    else "Continue from the durable notebook with the smallest remaining action."
                ),
            }
            if first_review:
                update["counterexample_review_completed"] = True
            ledger = TaskLedger.model_validate(
                {**ledger.model_dump(mode="python"), **update}
            )
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"work-batch-review:{batch_yield.get('work_batch_id', ledger.iteration)}",
            )
        delivered = deps.steering_queue.leased_by(task_id, owner)
        if delivered:
            deps.steering_queue.ack(
                [message.message_id for message in delivered],
                owner,
            )

        pending_steering = (
            deps.steering_enabled
            and deps.steering_at_work_batch_boundary
            and deps.steering_queue.has_pending(task_id)
        )
        if pending_steering:
            previous = ledger
            ledger = resume_for_steering(ledger)
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"steering-safe-point:{ledger.iteration}",
            )
        elif (
            step.status in {"verify", "done"}
            and (
                ctx.state.get("verification_required_task") == task_id
                or _workspace_fingerprint(deps, task_id) != current_fingerprint
            )
            and not ledger.counterexample_review_completed
        ):
            previous = ledger
            ledger = TaskLedger.model_validate({
                **ledger.model_dump(mode="python"),
                "phase": "review",
                "status": "active",
                "counterexample_review_completed": True,
                "next_action": _criterion_review_action(ledger, step),
            })
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key="counterexample-review",
            )
            step = step.model_copy(update={"message": ""})
        route = (
            HarnessRoute.CONTINUE
            if ledger.phase == TaskPhase.REVIEW
            else decide_route(
                ledger,
                step,
                pending_steering=pending_steering,
                replan_after_no_progress=deps.progress_replan_threshold,
                block_after_no_progress=deps.progress_human_threshold,
            )
        )
        if step.message and route == HarnessRoute.CONTINUE:
            _record_message(
                deps,
                task_id=task_id,
                role="assistant",
                content=step.message,
                idempotency_key=f"message:assistant:step:{ledger.iteration}",
            )
            yield message_event(step.message)
        checkpoint = _save_checkpoint(
            deps,
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
            invocation_id=ctx.get_invocation_context().invocation_id,
        )
        yield Event(
            actions=EventActions(
                state_delta={
                    "task_id": task_id,
                    "task_ledger": ledger.model_dump(mode="json"),
                    "task_route": route.value,
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "workspace_fingerprint": checkpoint.git_tree_hash,
                    "stable_instruction_sha256": deps.static_prefix_hash,
                    "static_prefix_tokens_estimate": deps.static_prefix_tokens,
                    "dynamic_context_tokens_estimate": dynamic_tokens,
                }
            )
        )

        if route == HarnessRoute.BLOCKED:
            yield _blocked_result(deps, ledger=ledger, started=started)
            return

        if route == HarnessRoute.REPLAN:
            previous = ledger
            ledger = replan_ledger(ledger)
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"replan:{ledger.iteration}",
            )
            _save_checkpoint(
                deps,
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
                invocation_id=ctx.get_invocation_context().invocation_id,
            )
            continue

        if route == HarnessRoute.VERIFY:
            transition = await _verification_transition(
                deps,
                ctx,
                verify_node,
                request=request,
                ledger=ledger,
                step=step,
                session_id=session_id,
                compaction_id=compaction_id,
                started=started,
            )
            ledger = transition.ledger
            if transition.result is not None:
                yield transition.result
                return

    deps.event_store.append(
        task_id,
        EventKind.TASK_BLOCKED,
        {"reason": f"Iteration limit {max_iterations} reached"},
        idempotency_key="iteration-limit",
    )
    _record_outcome(
        deps,
        task_id=task_id,
        ledger=ledger,
        status="blocked",
        passed=False,
        started=started,
    )
    yield json.dumps(
        {
            "status": "blocked",
            "task_id": task_id,
            "reason": f"Iteration limit {max_iterations} reached",
            "metrics": deps.metrics_store.task_summary(task_id),
        },
        sort_keys=True,
    )


def build_root_agent(deps: SkeinWorkflowDependencies) -> Workflow:
    """Build closure-bound nodes so concurrent assemblies cannot share state."""

    @node
    async def verify_task(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        return await _verify_task(deps, ctx, node_input)

    @node(rerun_on_resume=True)
    async def orchestrate(
        ctx: Context,
        node_input: str,
    ) -> AsyncGenerator[Event | str, None]:
        # ADK Runner supplies the root user message as ``types.Content``. Its
        # FunctionNode adapter unwraps that message only when this boundary
        # directly expects ``str``; structured requests remain supported as
        # JSON text by ``parse_task_request``.
        try:
            async for event in _orchestrate_owned(
                deps,
                ctx,
                node_input,
                verify_node=verify_task,
            ):
                if isinstance(event, str):
                    for public_event in result_events(json.loads(event)):
                        yield public_event
                else:
                    yield event
        finally:
            if deps.replies is not None:
                deps.replies.release(ctx.get_invocation_context().invocation_id)

    return Workflow(
        name="coding_harness",
        edges=[("START", orchestrate)],
    )


__all__ = ["SkeinWorkflowDependencies", "build_root_agent"]
