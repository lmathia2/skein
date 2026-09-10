"""Shared per-run ADK execution and durable public event coordination."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import aclosing, nullcontext, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from google.adk import Runner
from google.adk.agents._streaming_mode import StreamingMode
from google.adk.agents.run_config import RunConfig
from google.genai import types

from harness.adapters.adk.persistence import AdkServiceBundle
from harness.adapters.providers.controls import (
    LocalProviderControls,
    ProviderControlError,
    ProviderControlRequest,
)
from harness.adapters.providers.selection import ModelChoice
from harness.core.agent import (
    AgentSnapshot,
    ControlCommand,
    ControlReceipt,
    HarnessControlHooks,
    HarnessDescriptor,
    HarnessRegistry,
    ModelReadiness,
    PublicModelStatus,
    RuntimeCapability,
    SteeringCommand,
)
from harness.core.config import HarnessComposition, RuntimeBindings, SkeinConfig
from harness.evidence.ledger import LedgerBackedEventStore, open_ledger
from harness.evidence.state import CheckpointStore, JsonlEventStore, ToolReceipt, ToolReceiptStore
from harness.evidence.state.recovery import validate_recovery_evidence
from harness.execution.approvals import ApprovalStore
from harness.execution.approvals.waiting import ApprovalWaiter
from harness.execution.environment.runtime import LocalRepositoryRuntime
from harness.execution.safety import SecretRedactor

from .adk_mapper import AdkAgUiNormalizer
from .models import MODEL_METADATA, ModelControlError, ModelControls
from .ownership import WorkspaceOwner
from .protocol import (
    AgUiEvent,
    AgUiEventType,
    ApprovalRequestMessage,
    CancelTaskMessage,
    ModelRequestMessage,
    PauseTaskMessage,
    ServerEnvelope,
    SessionRequestMessage,
    StartTaskMessage,
    SteerTaskMessage,
)
from .registry import (
    DurableRunEventJournal,
    RunEventBroker,
    RunRecord,
    SqliteRunEventStore,
)
from .sessions import ConversationController


@dataclass(frozen=True, slots=True)
class PublicEventBatch:
    """One replay-stable group produced from a single runtime event."""

    source_key: str
    events: tuple[AgUiEvent, ...]


class RunExecution(Protocol):
    """One isolated harness assembly and ADK Runner."""

    def events(self) -> AsyncIterator[PublicEventBatch]: ...

    @property
    def coding_model_status(self) -> PublicModelStatus | None: ...

    async def steer(self, command: SteeringCommand) -> ControlReceipt: ...

    async def pause(self, command: ControlCommand) -> ControlReceipt: ...

    async def snapshot(self) -> AgentSnapshot: ...

    async def aclose(self) -> None: ...


class RunExecutionFactory(Protocol):
    @property
    def descriptor(self) -> HarnessDescriptor: ...

    @property
    def coding_model_status(self) -> PublicModelStatus | None: ...

    @property
    def run_metadata(self) -> Mapping[str, str]: ...

    async def create(self, record: RunRecord) -> RunExecution: ...


class RunInitializationError(RuntimeError):
    """A run could not be assembled; its public detail is intentionally generic."""


class RunLivenessError(RuntimeError):
    """A run stopped producing observable progress within its configured budget."""

    def __init__(self, *, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RunLivenessPolicy:
    """Server-owned deadlines; values are seconds and independent of model prompts."""

    first_event_timeout: float = 120
    idle_timeout: float = 180
    total_timeout: float = 1_800
    first_event_retries: int = 1
    close_timeout: float = 15

    def __post_init__(self) -> None:
        if min(
            self.first_event_timeout,
            self.idle_timeout,
            self.total_timeout,
            self.close_timeout,
        ) <= 0:
            raise ValueError("run liveness timeouts must be positive")
        if self.first_event_retries < 0:
            raise ValueError("first_event_retries cannot be negative")
        if self.total_timeout < self.first_event_timeout:
            raise ValueError("total_timeout cannot be shorter than first_event_timeout")


class AdkRunExecution:
    """Thin ADK Runner adapter for one durable run."""

    def __init__(
        self,
        *,
        record: RunRecord,
        runner: Runner,
        app_name: str,
        session_service: Any,
        controls: HarnessControlHooks | None,
        max_llm_calls: int,
        coding_model_status: PublicModelStatus | None = None,
        explicit_public_messages: bool = False,
        approvals: ApprovalWaiter | None = None,
        close_callback: Callable[[], Awaitable[None] | None] | None = None,
        resume: bool = False,
    ) -> None:
        self.record = record
        self.runner = runner
        self.app_name = app_name
        self.session_service = session_service
        self.controls = controls
        self.max_llm_calls = max_llm_calls
        self._coding_model_status = coding_model_status
        self._explicit_public_messages = explicit_public_messages
        self._closed = False
        self.approvals = approvals
        self._close_callback = close_callback
        self._resume = resume

    @property
    def coding_model_status(self) -> PublicModelStatus | None:
        return self._coding_model_status

    async def _ensure_session(self) -> None:
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.record.user_id,
            session_id=self.record.session_id,
        )
        if session is None:
            await self.session_service.create_session(
                app_name=self.app_name,
                user_id=self.record.user_id,
                session_id=self.record.session_id,
                state={
                    "invocation_id": self.record.invocation_id,
                    "run_id": self.record.run_id,
                    "task_id": self.record.run_id,
                    "thread_id": self.record.thread_id,
                },
            )

    async def events(self) -> AsyncIterator[PublicEventBatch]:
        await self._ensure_session()
        normalizer = AdkAgUiNormalizer(
            run_id=self.record.run_id,
            thread_id=self.record.thread_id,
            explicit_public_messages=self._explicit_public_messages,
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=self.record.input)],
        )
        generator = self.runner.run_async(
            user_id=self.record.user_id,
            session_id=self.record.session_id,
            invocation_id=self.record.invocation_id,
            new_message=None if self._resume else message,
            state_delta=None if self._resume else {
                "invocation_id": self.record.invocation_id,
                "run_id": self.record.run_id,
                "task_id": self.record.run_id,
                "thread_id": self.record.thread_id,
            },
            run_config=RunConfig(
                streaming_mode=StreamingMode.SSE,
                max_llm_calls=self.max_llm_calls,
            ),
            yield_user_message=False,
        )
        async with aclosing(generator) as adk_events:
            responding_reported = False
            reasoning_activity_reported = False
            event_id_occurrences: dict[str, int] = {}
            async for event in adk_events:
                occurrence = event_id_occurrences.get(event.id, 0)
                event_id_occurrences[event.id] = occurrence + 1
                normalized = normalizer.push(event)
                has_reasoning_activity = any(
                    getattr(part, "thought", False)
                    for part in (
                        (event.content.parts or ()) if event.content is not None else ()
                    )
                )
                if (
                    not responding_reported
                    and self.coding_model_status is not None
                    and event.model_version
                    and not event.error_code
                ):
                    responding_reported = True
                    normalized = (
                        AgUiEvent(
                            type=AgUiEventType.CUSTOM,
                            thread_id=self.record.thread_id,
                            run_id=self.record.run_id,
                            name="coding.model.status",
                            value=self.coding_model_status.model_copy(
                                update={"readiness": ModelReadiness.RESPONDING}
                            ).model_dump(mode="json"),
                        ),
                        *normalized,
                    )
                if has_reasoning_activity and not reasoning_activity_reported:
                    reasoning_activity_reported = True
                    normalized = (
                        *normalized,
                        AgUiEvent(
                            type=AgUiEventType.CUSTOM,
                            thread_id=self.record.thread_id,
                            run_id=self.record.run_id,
                            name="coding.model.activity",
                            value={"phase": "reasoning"},
                        ),
                    )
                if normalized:
                    yield PublicEventBatch(
                        source_key=f"adk:{event.id}:{occurrence}",
                        events=normalized,
                    )
                elif event.model_version and not event.error_code:
                    # Hidden reasoning and provider bookkeeping are still proof
                    # of activity. Renew liveness without persisting or
                    # broadcasting one event per private model token.
                    yield PublicEventBatch(
                        source_key=f"adk-activity:{event.id}:{occurrence}",
                        events=(),
                    )
        trailing = normalizer.finish()
        if trailing:
            yield PublicEventBatch(
                source_key="server:normalizer-finish",
                events=trailing,
            )

    async def steer(self, command: SteeringCommand) -> ControlReceipt:
        if self.controls is None:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"steer:{command.run_id}",
                detail="selected harness does not expose steering controls",
            )
        return await self.controls.steer(command)

    async def pause(self, command: ControlCommand) -> ControlReceipt:
        if self.controls is None:
            return ControlReceipt(
                accepted=False,
                command_id=command.idempotency_key or f"pause:{command.run_id}",
                detail="selected harness does not expose pause controls",
            )
        return await self.controls.pause(command)

    async def snapshot(self) -> AgentSnapshot:
        if self.controls is None:
            return AgentSnapshot(run_id=self.record.run_id, sequence=0, state={})
        return await self.controls.snapshot(self.record.run_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.runner.close()
        finally:
            if self._close_callback is not None:
                if inspect.iscoroutinefunction(self._close_callback):
                    await self._close_callback()
                else:
                    closed = await asyncio.to_thread(self._close_callback)
                    if inspect.isawaitable(closed):
                        await closed


class AdkRunExecutionFactory:
    """Build one isolated harness assembly while sharing ADK persistence services."""

    def __init__(
        self,
        *,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
        registry: HarnessRegistry,
        services: AdkServiceBundle,
        startup_coding_model_status: PublicModelStatus | None = None,
    ) -> None:
        self.composition = composition
        self.bindings = bindings
        self.registry = registry
        self.services = services
        self._coding_model_status = startup_coding_model_status
        implementation = composition.harness.implementation
        self._descriptor = registry.descriptor(implementation)

    @property
    def descriptor(self) -> HarnessDescriptor:
        return self._descriptor

    @property
    def coding_model_status(self) -> PublicModelStatus | None:
        return self._coding_model_status

    @property
    def run_metadata(self) -> Mapping[str, str]:
        return {
            "coding.behavior_sha256": self.composition.behavior_sha256,
            "coding.composition_sha256": self.composition.composition_sha256,
            "coding.harness_api_version": str(self.descriptor.api_version),
            "coding.harness_implementation": self.descriptor.implementation,
            "coding.workspace_identity": hashlib.sha256(
                self.bindings.workspace.expanduser().resolve().as_posix().encode()
            ).hexdigest(),
        }

    async def create(self, record: RunRecord) -> AdkRunExecution:
        composition = self.composition
        if MODEL_METADATA in record.metadata:
            adapter = self.registry.model_configuration(self.descriptor.implementation)
            if adapter is None:
                raise ValueError("harness does not support the recorded model choice")
            choice = ModelChoice.model_validate_json(record.metadata[MODEL_METADATA])
            config = adapter.with_coding_model(composition.harness.config, choice.apply(adapter.coding_model(composition.harness.config)))
            composition = composition.model_copy(update={"harness": composition.harness.model_copy(update={"config": config})})
        state_root = self.bindings.state_root.expanduser().resolve() / "runs" / record.run_id
        resume = record.status == "running"
        if resume:
            config = composition.harness.config
            if not isinstance(config, SkeinConfig) or config.adk.recovery != "safe_auto":
                raise ValueError("automatic recovery is disabled")
            for key in ("coding.behavior_sha256", "coding.workspace_identity", "coding.harness_implementation"):
                if record.metadata.get(key) != self.run_metadata.get(key):
                    raise ValueError("recovery configuration/workspace ownership changed")
            repository = LocalRepositoryRuntime(self.bindings.workspace)
            if not repository.manifest().base_revision:
                raise ValueError("safe recovery requires an initialized Git workspace fingerprint")
            session = await self.services.session_service.get_session(
                app_name=composition.app.name, user_id=record.user_id, session_id=record.session_id,
            )
            if session is None or not any(event.invocation_id == record.invocation_id for event in session.events):
                raise ValueError("ADK invocation history is unavailable")
            published_id = session.state.get("checkpoint_id")
            checkpoint = CheckpointStore(state_root / "state.db").get(str(published_id)) if published_id else None
            if checkpoint is None:
                raise ValueError("no published checkpoint for recovery")
            operational_events = JsonlEventStore(state_root / "events")
            canonical = open_ledger(state_root, config.memory.ledger)
            task_events = LedgerBackedEventStore(
                operational_events, canonical, repair=False,
            ).read(record.run_id)
            if task_events != operational_events.read(record.run_id):
                raise ValueError("canonical task evidence is missing or erased")
            receipts = ToolReceiptStore(state_root / "managed-tools.db").for_task(record.run_id)
            canonical_receipts = {
                str(event.payload["tool_call_id"]): ToolReceipt.model_validate(event.payload)
                for event in canonical.read(record.run_id) if event.source == "tool_receipt"
            }
            if canonical_receipts != {receipt.tool_call_id: receipt for receipt in receipts}:
                raise ValueError("canonical tool intents/receipts are missing or corrupt")
            validate_recovery_evidence(
                checkpoint, task_events,
                receipts,
                invocation_id=record.invocation_id, session_id=record.session_id,
                workspace_fingerprint=repository.fingerprint(),
            )
            if session.state.get("context_epoch") != checkpoint.context_epoch:
                raise ValueError("context epoch has not published its checkpoint boundary")
            spent = max(int(session.state.get("estimated_task_input_tokens", 0)), max((
                int(event.payload.get("estimated_task_input_tokens", 0)) for event in task_events
                if event.kind == "execution.model_budget_reserved"), default=0))
            limit = min(config.context.max_task_input_tokens, min((
                int(event.payload["task_input_token_limit"]) for event in task_events
                if event.kind == "execution.model_budget_reserved"), default=config.context.max_task_input_tokens))
            if spent >= limit:
                raise ValueError("task input budget is exhausted")
            approvals = ApprovalStore(state_root / "approvals.db")
            if any(approvals.list(task_id=record.run_id, status=status, limit=1)
                   for status in ("pending", "expired")):
                raise ValueError("approval requires explicit operator action")
        run_bindings = self.bindings.model_copy(
            update={
                "invocation_id": record.invocation_id,
                "state_root": state_root,
                "task_id": record.run_id,
                "interactive_approvals": record.metadata.get("interactive_approvals") == "true",
                "conversation_id": record.thread_id,
                "user_id": record.user_id,
                "prior_task_ids": tuple(json.loads(record.metadata.get("coding.memory.source_runs", "[]"))),
                "prior_state_roots": tuple(
                    self.bindings.state_root.expanduser().resolve() / "runs" / run_id
                    for run_id in json.loads(record.metadata.get("coding.memory.source_runs", "[]"))
                ),
                "notebook_prior_task_ids": tuple(json.loads(record.metadata.get("coding.memory.notebook_runs", "[]"))),
                "notebook_prior_state_roots": tuple(
                    self.bindings.state_root.expanduser().resolve() / "runs" / run_id
                    for run_id in json.loads(record.metadata.get("coding.memory.notebook_runs", "[]"))
                ),
                "notebook_state_root": self.bindings.state_root.expanduser().resolve() / "conversations" /
                    hashlib.sha256(f"{record.user_id}\0{record.thread_id}".encode()).hexdigest() / "notebooks",
            }
        )
        assembly = await asyncio.to_thread(
            self.registry.build,
            composition,
            run_bindings,
        )
        runner = Runner(
            app=assembly.app,
            session_service=self.services.session_service,
            artifact_service=self.services.artifact_service,
            memory_service=self.services.memory_service,
            auto_create_session=False,
        )
        config = composition.harness.config
        coding_model_name = assembly.build_info.models.get("coding")
        coding_model_provider = assembly.build_info.model_providers.get("coding")
        coding_model_status = (
            PublicModelStatus(
                provider=coding_model_provider,
                name=coding_model_name,
                readiness=ModelReadiness.ADAPTER_INITIALIZED,
            )
            if coding_model_name is not None and coding_model_provider is not None
            else None
        )
        return AdkRunExecution(
            record=record,
            runner=runner,
            app_name=assembly.app.name,
            session_service=self.services.session_service,
            controls=assembly.controls,
            max_llm_calls=5_000,
            coding_model_status=coding_model_status,
            explicit_public_messages=assembly.explicit_public_messages,
            approvals=assembly.approvals,
            close_callback=assembly.close,
            resume=resume,
        )


@dataclass(slots=True)
class _ActiveRun:
    execution: RunExecution
    task: asyncio.Task[None]


class RunCoordinator:
    """Own run lifecycle, controls, durable publication, and gap-free attachment."""

    def __init__(
        self,
        *,
        store: SqliteRunEventStore,
        broker: RunEventBroker,
        execution_factory: RunExecutionFactory,
        redactor: SecretRedactor | None = None,
        liveness: RunLivenessPolicy | None = None,
        provider_controls: LocalProviderControls | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self.journal = DurableRunEventJournal(store, broker)
        self.execution_factory = execution_factory
        self.redactor = redactor or SecretRedactor()
        self.liveness = liveness or RunLivenessPolicy()
        self._active: dict[str, _ActiveRun] = {}
        self._recovering: dict[str, asyncio.Task[None]] = {}
        self._workspace_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self.conversations = ConversationController(self)
        self.provider_controls = provider_controls
        self.models = (ModelControls(execution_factory, self.conversations.store)
            if isinstance(execution_factory, AdkRunExecutionFactory) and execution_factory.registry.model_configuration(execution_factory.descriptor.implementation)
            else None)
        self._workspace_owner: WorkspaceOwner | None = None

    def _claim_workspace(self) -> None:
        if self._workspace_owner is None and isinstance(self.execution_factory, AdkRunExecutionFactory):
            self._workspace_owner = WorkspaceOwner(self.execution_factory.bindings.workspace)

    def _release_workspace_if_idle(self) -> None:
        if not self._active and not self._recovering and self._workspace_owner is not None:
            self._workspace_owner.close()
            self._workspace_owner = None

    @property
    def descriptor(self) -> HarnessDescriptor:
        descriptor = self.execution_factory.descriptor
        capabilities = descriptor.capabilities | {RuntimeCapability.SESSIONS, RuntimeCapability.SESSION_HISTORY,
                                                  RuntimeCapability.CANCEL, RuntimeCapability.REPLAY}
        if self.provider_controls is not None:
            capabilities |= {RuntimeCapability.PROVIDER_CONTROLS}
        if self.models is not None:
            capabilities |= {RuntimeCapability.MODEL_SELECTION}
        if isinstance(self.execution_factory, AdkRunExecutionFactory):
            capabilities |= {RuntimeCapability.RESOURCES}
        return descriptor.model_copy(update={"capabilities": capabilities})

    async def session_request(self, message: SessionRequestMessage, *, user_id: str) -> dict[str, object]:
        return await self.conversations.request(message, user_id)

    async def provider_request(self, message: ProviderControlRequest, *, user_id: str) -> dict[str, object]:
        if self.provider_controls is None:
            raise ProviderControlError("Provider controls are not supported")
        return await self.provider_controls.request(message, user_id=user_id)

    async def model_request(self, message: ModelRequestMessage, *, user_id: str) -> dict[str, object]:
        if self.models is None:
            raise ModelControlError("Model selection is not supported by this harness")
        return await self.models.request(message, user_id=user_id)

    async def resource_request(self) -> dict[str, object]:
        factory = self.execution_factory
        if not isinstance(factory, AdkRunExecutionFactory):
            raise ValueError("resource inventory is not supported")
        inventory = await asyncio.to_thread(factory.registry.resources, factory.composition, factory.bindings)
        bindings = factory.bindings
        data = {"workspace": str(bindings.workspace.expanduser().resolve()),
                "state_root": str(bindings.state_root.expanduser().resolve()),
                "configuration_root": str((bindings.configuration_root or bindings.workspace).expanduser().resolve()),
                "run_database": str(self.store.database.resolve()), "project_trusted": bindings.project_trusted,
                "scope": "available_for_next_turn", "items": [], "warnings": []}
        if inventory is not None:
            data.update(inventory.model_dump(mode="json"))
        else:
            data["warnings"] = ["This harness does not expose resource details."]
        public = self.redactor.redact(data)
        if len(json.dumps(public, ensure_ascii=False).encode()) > 512_000:
            raise ValueError("resource inventory exceeds the public response limit")
        return cast(dict[str, object], public)

    async def approval_request(self, message: ApprovalRequestMessage, *, user_id: str) -> dict[str, object]:
        record = self._owned_run(message.run_id, user_id)
        if any(record.metadata.get(key) != self.execution_factory.run_metadata.get(key)
               for key in ("coding.workspace_identity", "coding.harness_implementation")):
            raise ValueError("run belongs to another workspace or harness")
        active = self._active.get(record.run_id)
        waiter = getattr(active.execution, "approvals", None) if active else None
        if message.operation == "list":
            data = {"run_id": record.run_id, "requests": waiter.pending() if waiter else [], "status": record.status}
        else:
            if active is None or active.task.done() or active.task.cancelling() or not isinstance(waiter, ApprovalWaiter):
                raise ValueError("run is no longer accepting approval decisions")
            assert message.approval_id is not None and message.fingerprint is not None and message.decision is not None
            result = await waiter.decide(message.approval_id, message.fingerprint, message.decision, actor=user_id)
            data = {"run_id": record.run_id, "request": result.model_dump(mode="json")}
        public = self.redactor.redact(data)
        if len(json.dumps(public, ensure_ascii=False).encode()) > 512_000:
            raise ValueError("approval response exceeds its public size limit")
        return cast(dict[str, object], public)

    @property
    def coding_model_status(self) -> PublicModelStatus | None:
        return self.models.public_model(self.models.default()) if self.models is not None else self.execution_factory.coding_model_status

    @staticmethod
    def _default_thread_id(user_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"thread\0{user_id}\0{idempotency_key}".encode()).hexdigest()[:32]
        return f"thread-{digest}"

    def _owned_run(self, run_id: str, user_id: str) -> RunRecord:
        record = self.store.get_run(run_id)
        if record is None or record.user_id != user_id:
            raise KeyError(f"unknown run: {run_id}")
        return record

    def is_terminal_run(self, run_id: str, *, user_id: str) -> bool:
        return self._owned_run(run_id, user_id).status in {"completed", "failed", "cancelled"}

    def recover_interrupted_runs(self) -> None:
        """Single-owner server startup: preserve history, never replay unknown effects."""
        for run_id in self.store.active_run_ids():
            if run_id in self._active or run_id in self._recovering:
                continue
            record = self.store.get_run(run_id)
            if record is None or record.status not in {"queued", "running"}:
                continue
            factory = self.execution_factory
            if (isinstance(factory, AdkRunExecutionFactory)
                and isinstance(factory.composition.harness.config, SkeinConfig)
                and factory.composition.harness.config.adk.recovery == "safe_auto"
                and record.status == "running"):
                self._recovering[run_id] = asyncio.create_task(self._recover(record))
                continue
            error = "server restarted during an active run; automatic rerun refused"
            self.journal.terminalize(run_id, status="failed", event=AgUiEvent(
                type=AgUiEventType.RUN_ERROR, thread_id=record.thread_id, run_id=run_id,
                code="server_restarted", message=error), source_key="server:run-error",
                expected_status="queued" if record.status == "queued" else "running", error=error)

    async def _recover(self, record: RunRecord) -> None:
        try:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(record.created_at)).total_seconds()
            if elapsed >= self.liveness.total_timeout:
                raise ValueError("original run deadline expired")
            async with self._workspace_lock:
                self._claim_workspace()
                execution = await self.execution_factory.create(record)
                task = asyncio.current_task()
                assert task is not None
                self._active[record.run_id] = _ActiveRun(execution=execution, task=task)
                await self._drive(record, execution, workspace_locked=True)
        except asyncio.CancelledError:
            current = self.store.get_run(record.run_id)
            if current is not None and current.status == "running":
                self.journal.terminalize(record.run_id, status="cancelled", event=AgUiEvent(
                    type=AgUiEventType.RUN_FINISHED, thread_id=record.thread_id, run_id=record.run_id,
                    result={"status": "cancelled"}), source_key="server:run-finished", expected_status="running")
            raise
        except Exception as error:
            detail = self.redactor.redact_text(str(error))[:4_096]
            self.journal.terminalize(record.run_id, status="failed", event=AgUiEvent(
                type=AgUiEventType.RUN_ERROR, thread_id=record.thread_id, run_id=record.run_id,
                code="recovery_blocked", message=detail), source_key="server:run-error",
                expected_status="running", error=detail)
        finally:
            self._recovering.pop(record.run_id, None)
            self._release_workspace_if_idle()

    def _close_unstarted_recovery(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        if record is not None and record.status == "running" and run_id not in self._active:
            self.journal.terminalize(run_id, status="cancelled", event=AgUiEvent(
                type=AgUiEventType.RUN_FINISHED, thread_id=record.thread_id, run_id=run_id,
                result={"status": "cancelled"}), source_key="server:run-finished", expected_status="running")
        self._recovering.pop(run_id, None)
        self._release_workspace_if_idle()

    async def start(
        self,
        message: StartTaskMessage,
        *,
        user_id: str,
        queued_follow_up: bool = False,
    ) -> tuple[RunRecord, bool]:
        thread_id = message.thread_id or self._default_thread_id(user_id, message.idempotency_key)
        if (
            not queued_follow_up
            and self.conversations.store.pending(user_id, thread_id)
            and self.conversations.store.run_for_key(user_id, message.idempotency_key) is None
        ):
            raise ValueError("conversation has pending follow-ups; continue or remove them first")
        if any(key.startswith("coding.") for key in message.metadata):
            raise ValueError("coding.* metadata is reserved for the server")
        prior = self.conversations.store.run_for_key(user_id, message.idempotency_key)
        if prior is not None:
            if any(prior.metadata.get(key) != self.execution_factory.run_metadata.get(key)
                   for key in ("coding.workspace_identity", "coding.harness_implementation")):
                raise ValueError("run belongs to a different workspace or harness")
            original_client_metadata = {key: value for key, value in prior.metadata.items() if not key.startswith("coding.")}
            if original_client_metadata != message.metadata:
                raise ValueError("run idempotency key was reused with different metadata")
            if json.loads(prior.metadata.get("coding.memory.explicit_sources", "[]")) != list(message.source_run_ids):
                raise ValueError("run idempotency key was reused with different source selection")
            metadata = dict(prior.metadata)
        else:
            metadata = {**message.metadata, **self.execution_factory.run_metadata}
            factory = self.execution_factory
            if isinstance(factory, AdkRunExecutionFactory) and isinstance(factory.composition.harness.config, SkeinConfig):
                config = factory.composition.harness.config
                enabled = config.memory.prior_runs
                continuity = config.notebook_ptc.enabled and config.notebook_ptc.continuity == "conversation"
                if message.source_run_ids and not enabled:
                    raise ValueError("explicit memory sources require prior-run recall")
                sources = self.conversations.store.memory_sources(user_id, thread_id) if enabled or continuity else []
                for source_id in message.source_run_ids:
                    source = self._owned_run(source_id, user_id)
                    if source.status not in {"completed", "failed", "cancelled"}:
                        raise ValueError("memory source is still active")
                    if source not in sources:
                        sources.append(source)
                expected_workspace = metadata.get("coding.workspace_identity")
                if any(source.metadata.get("coding.workspace_identity") != expected_workspace for source in sources):
                    raise ValueError("memory source belongs to another workspace")
                metadata["coding.memory.source_runs"] = json.dumps([source.run_id for source in sources] if enabled else [])
                metadata["coding.memory.explicit_sources"] = json.dumps(message.source_run_ids)
                metadata["coding.memory.notebook_runs"] = json.dumps([
                    source.run_id for source in sources if source.thread_id == thread_id
                ])
            elif message.source_run_ids:
                raise ValueError("selected harness does not support memory source selection")
            if self.models is not None:
                metadata.update(self.models.run_metadata(user_id, thread_id))
        record, created = self.store.create_run(
            request_id=message.request_id,
            idempotency_key=message.idempotency_key,
            thread_id=thread_id,
            user_id=user_id,
            input=message.input,
            metadata=metadata,
        )
        async with self._lifecycle_lock:
            if record.run_id in self._recovering:
                return record, created
            if record.status == "running" and record.run_id not in self._active:
                restart_error = "server restarted during an active run; automatic rerun refused"
                self.journal.terminalize(
                    record.run_id,
                    status="failed",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_ERROR,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        code="server_restarted",
                        message=restart_error,
                    ),
                    source_key="server:run-error",
                    expected_status="running",
                    error=restart_error,
                )
                record = self.store.get_run(record.run_id)
                assert record is not None
                return record, created
            if record.status == "queued" and record.run_id not in self._active:
                try:
                    self._claim_workspace()
                    execution = await self.execution_factory.create(record)
                except Exception as error:
                    self._release_workspace_if_idle()
                    safe_error = self.redactor.redact_text(str(error))[:4_096]
                    self.journal.terminalize(
                        record.run_id,
                        status="failed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_ERROR,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            code="runtime_initialization_failed",
                            message=safe_error or "runtime initialization failed",
                        ),
                        source_key="server:run-error",
                        expected_status="queued",
                        error=safe_error,
                    )
                    raise RunInitializationError("task initialization failed") from error
                task = asyncio.create_task(
                    self._drive(record, execution),
                    name=f"agent-run:{record.run_id}",
                )
                self._active[record.run_id] = _ActiveRun(
                    execution=execution,
                    task=task,
                )
        return record, created

    async def _bounded_events(
        self,
        execution: RunExecution,
        *,
        total_deadline: float,
    ) -> AsyncIterator[PublicEventBatch]:
        """Iterate in the caller task so ADK contextvars are detached safely."""

        iterator = execution.events()
        received = False
        try:
            while True:
                remaining = total_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RunLivenessError(
                        code="run_total_timeout",
                        message="run exceeded its total execution deadline",
                        retryable=False,
                    )
                phase_timeout = (
                    self.liveness.idle_timeout
                    if received
                    else self.liveness.first_event_timeout
                )
                timeout = min(remaining, phase_timeout)
                try:
                    async with asyncio.timeout(timeout):
                        batch = await anext(iterator)
                except StopAsyncIteration:
                    return
                except TimeoutError:
                    total_expired = remaining <= phase_timeout
                    raise RunLivenessError(
                        code=(
                            "run_total_timeout"
                            if total_expired
                            else (
                                "model_idle_timeout"
                                if received
                                else "model_first_event_timeout"
                            )
                        ),
                        message=(
                            "run exceeded its total execution deadline"
                            if total_expired
                            else (
                                "model stopped producing events before the idle deadline"
                                if received
                                else "model did not produce an event before the startup deadline"
                            )
                        ),
                        retryable=not received and not total_expired,
                    ) from None
                received = True
                yield batch
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                with suppress(Exception):
                    await close()

    async def _close_execution(self, execution: RunExecution) -> str | None:
        """Bound cleanup and convert provider cleanup defects into durable warnings."""

        try:
            async with asyncio.timeout(self.liveness.close_timeout):
                await execution.aclose()
        except TimeoutError:
            return "runtime cleanup exceeded its deadline"
        except Exception as error:
            return self.redactor.redact_text(str(error))[:1_000] or type(error).__name__
        return None

    def _record_cleanup_warning(self, record: RunRecord, detail: str, attempt: int) -> None:
        self.journal.append_event(
            record.run_id,
            AgUiEvent(
                type=AgUiEventType.CUSTOM,
                thread_id=record.thread_id,
                run_id=record.run_id,
                name="coding.run.cleanup_warning",
                value={"attempt": attempt, "detail": detail},
            ),
            source_key=f"server:cleanup-warning:{attempt}",
        )

    async def _drive(self, record: RunRecord, execution: RunExecution, *, workspace_locked: bool = False) -> None:
        current_execution = execution
        current_execution_closed = False
        attempt = 0
        try:
            async with (nullcontext() if workspace_locked else self._workspace_lock):
                total_deadline = (
                    asyncio.get_running_loop().time() + self.liveness.total_timeout
                )
                if record.status == "running":
                    total_deadline -= (datetime.now(UTC) - datetime.fromisoformat(record.created_at)).total_seconds()
                else:
                    self.store.update_status(record.run_id, "running", expected_status="queued")
                run_started = AgUiEvent(
                    type=AgUiEventType.RUN_STARTED,
                    thread_id=record.thread_id,
                    run_id=record.run_id,
                )
                if current_execution.coding_model_status is not None:
                    run_started = AgUiEvent(
                        type=AgUiEventType.RUN_STARTED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        metadata={
                            "coding.model": self.redactor.redact(
                                current_execution.coding_model_status.model_dump(mode="json")
                            )
                        },
                    )
                self.journal.append_event(
                    record.run_id,
                    run_started,
                    source_key="server:run-started",
                )
                result: object | None = None
                runtime_error: str | None = None
                while True:
                    try:
                        async for batch in self._bounded_events(
                            current_execution,
                            total_deadline=total_deadline,
                        ):
                            durable_events = tuple(
                                event
                                for event in batch.events
                                if event.type != AgUiEventType.RUN_ERROR
                            )
                            source_keys = tuple(
                                f"{batch.source_key}:{index}"
                                for index in range(len(durable_events))
                            )
                            if durable_events:
                                self.journal.append_events(
                                    record.run_id,
                                    durable_events,
                                    source_keys=source_keys,
                                )
                            for event in batch.events:
                                if event.type == AgUiEventType.RUN_ERROR:
                                    runtime_error = event.message or "ADK run failed"
                                elif (
                                    event.type == AgUiEventType.CUSTOM
                                    and event.name == "coding.workflow.output"
                                ):
                                    result = event.value
                        break
                    except RunLivenessError as error:
                        if not error.retryable or attempt >= self.liveness.first_event_retries:
                            raise
                        attempt += 1
                        self.journal.append_event(
                            record.run_id,
                            AgUiEvent(
                                type=AgUiEventType.CUSTOM,
                                thread_id=record.thread_id,
                                run_id=record.run_id,
                                name="coding.run.liveness_retry",
                                value={"attempt": attempt, "reason": error.code},
                            ),
                            source_key=f"server:liveness-retry:{attempt}",
                        )
                        cleanup_warning = await self._close_execution(current_execution)
                        current_execution_closed = True
                        if cleanup_warning:
                            self._record_cleanup_warning(record, cleanup_warning, attempt)
                        retry_record = self.store.get_run(record.run_id) if isinstance(self.execution_factory, AdkRunExecutionFactory) else record
                        assert retry_record is not None
                        current_execution = await self.execution_factory.create(retry_record)
                        current_execution_closed = False
                        active = self._active.get(record.run_id)
                        if active is not None:
                            active.execution = current_execution
                cleanup_warning = await self._close_execution(current_execution)
                current_execution_closed = True
                if cleanup_warning:
                    self._record_cleanup_warning(
                        record,
                        cleanup_warning,
                        attempt + 1,
                    )
                if runtime_error is not None:
                    self.journal.terminalize(
                        record.run_id,
                        status="failed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_ERROR,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            code="adk_run_failed",
                            message=runtime_error,
                        ),
                        source_key="server:run-error",
                        expected_status="running",
                        error=runtime_error,
                    )
                else:
                    self.journal.terminalize(
                        record.run_id,
                        status="completed",
                        event=AgUiEvent(
                            type=AgUiEventType.RUN_FINISHED,
                            thread_id=record.thread_id,
                            run_id=record.run_id,
                            result=result,
                        ),
                        source_key="server:run-finished",
                        expected_status="running",
                    )
        except asyncio.CancelledError:
            if not current_execution_closed:
                cleanup_warning = await self._close_execution(current_execution)
                current_execution_closed = True
                if cleanup_warning:
                    self._record_cleanup_warning(
                        record,
                        cleanup_warning,
                        attempt + 1,
                    )
            current = self.store.get_run(record.run_id)
            if current is not None and current.status in {"queued", "running"}:
                self.journal.append_event(
                    record.run_id,
                    AgUiEvent(
                        type=AgUiEventType.CUSTOM,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        name="coding.run.cancelled",
                        value={"cancellation": "best_effort"},
                    ),
                    source_key="server:run-cancelled",
                )
                self.journal.terminalize(
                    record.run_id,
                    status="cancelled",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_FINISHED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        result={"status": "cancelled"},
                    ),
                    source_key="server:run-finished",
                    expected_status=("queued" if current.status == "queued" else "running"),
                )
            raise
        except RunLivenessError as error:
            if not current_execution_closed:
                cleanup_warning = await self._close_execution(current_execution)
                current_execution_closed = True
                if cleanup_warning:
                    self._record_cleanup_warning(
                        record,
                        cleanup_warning,
                        attempt + 1,
                    )
            current = self.store.get_run(record.run_id)
            if current is not None and current.status in {"queued", "running"}:
                self.journal.terminalize(
                    record.run_id,
                    status="failed",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_ERROR,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        code=error.code,
                        message=str(error),
                    ),
                    source_key="server:run-error",
                    expected_status=(
                        "queued" if current.status == "queued" else "running"
                    ),
                    error=str(error),
                )
        except Exception as error:
            safe_error = self.redactor.redact_text(str(error))[:4_096]
            if not current_execution_closed:
                cleanup_warning = await self._close_execution(current_execution)
                current_execution_closed = True
                if cleanup_warning:
                    self._record_cleanup_warning(
                        record,
                        cleanup_warning,
                        attempt + 1,
                    )
            current = self.store.get_run(record.run_id)
            if current is not None and current.status in {"queued", "running"}:
                self.journal.terminalize(
                    record.run_id,
                    status="failed",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_ERROR,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        code="runtime_failed",
                        message=safe_error or "runtime failed",
                    ),
                    source_key="server:run-error",
                    expected_status=("queued" if current.status == "queued" else "running"),
                    error=safe_error or "runtime failed",
                )
        finally:
            try:
                if not current_execution_closed:
                    await self._close_execution(current_execution)
            finally:
                self._active.pop(record.run_id, None)
                self._release_workspace_if_idle()
                await self.conversations.after_turn(record)

    async def wait(self, run_id: str) -> RunRecord:
        recovering = self._recovering.get(run_id)
        if recovering is not None:
            with suppress(asyncio.CancelledError):
                await recovering
        active = self._active.get(run_id)
        if active is not None:
            with suppress(asyncio.CancelledError):
                await active.task
        record = self.store.get_run(run_id)
        if record is None:
            raise KeyError(f"unknown run: {run_id}")
        return record

    async def steer(
        self,
        message: SteerTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        if active is None:
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail="run is not active",
            )
        return await active.execution.steer(
            SteeringCommand(
                run_id=message.run_id,
                content=message.content,
                priority=message.priority,
                idempotency_key=message.idempotency_key,
            )
        )

    async def pause(
        self,
        message: PauseTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        if active is None:
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail="run is not active",
            )
        return await active.execution.pause(
            ControlCommand(
                run_id=message.run_id,
                idempotency_key=message.idempotency_key,
            )
        )

    async def cancel(
        self,
        message: CancelTaskMessage,
        *,
        user_id: str,
    ) -> ControlReceipt:
        record = self._owned_run(message.run_id, user_id)
        active = self._active.get(message.run_id)
        recovering = self._recovering.get(message.run_id)
        if active is None and recovering is not None:
            recovering.cancel()
            with suppress(asyncio.CancelledError):
                await recovering
            self._close_unstarted_recovery(message.run_id)
            return ControlReceipt(accepted=True, command_id=message.idempotency_key,
                                  detail="recovery cancelled before execution")
        if active is None:
            if record.status == "queued":
                self.journal.terminalize(
                    record.run_id,
                    status="cancelled",
                    event=AgUiEvent(
                        type=AgUiEventType.RUN_FINISHED,
                        thread_id=record.thread_id,
                        run_id=record.run_id,
                        result={"status": "cancelled"},
                    ),
                    source_key="server:run-finished",
                    expected_status="queued",
                )
                return ControlReceipt(
                    accepted=True,
                    command_id=message.idempotency_key,
                    detail="queued run cancelled",
                )
            return ControlReceipt(
                accepted=False,
                command_id=message.idempotency_key,
                detail=f"run is already {record.status}",
            )
        active.task.cancel()
        with suppress(asyncio.CancelledError):
            await active.task
        await self._close_unstarted(message.run_id, active)
        return ControlReceipt(
            accepted=True,
            command_id=message.idempotency_key,
            detail=(
                "cancellation accepted; an already-running synchronous subprocess "
                "may continue until its sandbox call returns"
            ),
        )

    async def _close_unstarted(self, run_id: str, active: _ActiveRun) -> None:
        # A task cancelled before its coroutine enters never executes its finally
        # block. Close the allocated Runner and make its queued record terminal.
        record = self.store.get_run(run_id)
        if record is None or record.status != "queued":
            return
        await self._close_execution(active.execution)
        self._active.pop(run_id, None)
        self._release_workspace_if_idle()
        self.journal.terminalize(run_id, status="cancelled", event=AgUiEvent(
            type=AgUiEventType.RUN_FINISHED, thread_id=record.thread_id, run_id=run_id,
            result={"status": "cancelled"}), source_key="server:run-finished", expected_status="queued")

    async def attach(
        self,
        run_id: str,
        *,
        user_id: str,
        after_sequence: int = 0,
    ) -> AsyncIterator[ServerEnvelope]:
        """Replay a fixed snapshot, then stream live events without a race gap."""

        self._owned_run(run_id, user_id)
        subscription = self.broker.subscribe(run_id)
        cursor = after_sequence
        high_water: int | None = None
        try:
            while True:
                page = self.store.replay_page(
                    run_id,
                    after_sequence=cursor,
                    high_water_sequence=high_water,
                )
                if high_water is None:
                    high_water = page.high_water_sequence
                for envelope in page.events:
                    cursor = envelope.sequence
                    yield envelope
                if not page.has_more:
                    break
            current = self._owned_run(run_id, user_id)
            if current.status in {"completed", "cancelled", "failed"}:
                tail = self.store.replay(run_id, after_sequence=cursor)
                for envelope in tail:
                    cursor = envelope.sequence
                    yield envelope
                return
            async for envelope in subscription:
                if envelope.sequence <= cursor:
                    continue
                cursor = envelope.sequence
                yield envelope
                if envelope.event.type in {
                    AgUiEventType.RUN_FINISHED,
                    AgUiEventType.RUN_ERROR,
                }:
                    return
        finally:
            self.broker.unsubscribe(run_id, subscription)

    def acknowledge(self, run_id: str, *, user_id: str, through_sequence: int) -> None:
        self._owned_run(run_id, user_id)
        high_water = self.store.replay_page(
            run_id,
            after_sequence=through_sequence,
            limit=1,
        ).high_water_sequence
        if through_sequence > high_water:
            raise ValueError("acknowledgement exceeds the durable high-water sequence")

    async def aclose(self) -> None:
        self.conversations.closed = True
        recovering = tuple(self._recovering.items())
        for _, task in recovering:
            task.cancel()
        for run_id, task in recovering:
            with suppress(asyncio.CancelledError):
                await task
            self._close_unstarted_recovery(run_id)
        active = tuple(self._active.items())
        for _, item in active:
            item.task.cancel()
        for run_id, item in active:
            with suppress(asyncio.CancelledError):
                await item.task
            await self._close_unstarted(run_id, item)
        if self.provider_controls is not None:
            await self.provider_controls.aclose()
        if self.models is not None:
            await self.models.aclose()
        if self._workspace_owner is not None:
            self._workspace_owner.close()


__all__ = [
    "AdkRunExecution",
    "AdkRunExecutionFactory",
    "PublicEventBatch",
    "RunCoordinator",
    "RunExecution",
    "RunExecutionFactory",
    "RunInitializationError",
    "RunLivenessError",
    "RunLivenessPolicy",
]
