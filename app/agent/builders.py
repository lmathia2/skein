"""Side-effect-free ADK agent builders shared by factory and legacy bootstrap."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import ToolContext
from google.genai import types

from harness.approvals.waiting import ApprovalWaiter
from harness.config import GenerationConfig, NotebookPtcConfig, ToolSurfaceConfig
from harness.environment.async_call import run_managed_thread
from harness.models.agent_step import StructuredAgentStep
from harness.notebook import (
    NotebookCell,
    externalize_mime_bundle,
    materialize_notebook,
    put_artifact,
    reduce_notebook,
)
from harness.repl import PersistentPythonWorker
from harness.state import EventKind, EventStore, JsonlEventStore
from harness.state.events import HarnessEvent
from harness.tools.adk_adapter import AdkCodingTools, create_adk_tools
from harness.tools.output import bound_output

from .config import HarnessSettings
from .streaming import PublicReplies

LOGGER = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


def _replay_policy(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "safe"  # Failed cells are never restored.
    nodes = tuple(ast.walk(tree))
    if any(isinstance(node, ast.Name) and node.id == "agent" for node in nodes):
        return "never"
    # Replay only self-contained data construction. Calls, imports, definitions,
    # attribute/subscript access, and loaded names can depend on external or skipped
    # state, including user-defined magic methods with effects.
    unsafe = (
        ast.AsyncFunctionDef,
        ast.AsyncWith,
        ast.Attribute,
        ast.Await,
        ast.Call,
        ast.ClassDef,
        ast.Delete,
        ast.FunctionDef,
        ast.Global,
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.Nonlocal,
        ast.Raise,
        ast.Subscript,
        ast.Try,
        ast.With,
        ast.Yield,
        ast.YieldFrom,
    )
    if any(isinstance(node, unsafe) for node in nodes):
        return "requires_reconciliation"
    if any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) for node in nodes):
        return "requires_reconciliation"
    return "safe"


@dataclass(frozen=True, slots=True)
class CodingWorkerBundle:
    agent: Agent
    read: ToolFunction
    bash: ToolFunction
    edit: ToolFunction
    write: ToolFunction
    python: ToolFunction | None = None
    close: Callable[[], None] | None = None


def build_coding_worker(
    settings: HarnessSettings,
    model: BaseLlm,
    *,
    tools: AdkCodingTools | None = None,
    generation_config: GenerationConfig | None = None,
    tool_config: ToolSurfaceConfig | None = None,
    ptc_config: NotebookPtcConfig | None = None,
    event_store: EventStore | None = None,
    approvals: ApprovalWaiter | None = None,
    replies: PublicReplies | None = None,
    capabilities: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    conversation_notebook_id: str | None = None,
    prior_notebook_events: tuple[HarnessEvent, ...] = (),
    notebook_root: Path | None = None,
) -> CodingWorkerBundle:
    active_tools = tools or create_adk_tools(
        settings.workspace,
        state_root=settings.state_root,
    )
    active_tool_config = tool_config or ToolSurfaceConfig()
    active_generation_config = generation_config or GenerationConfig()
    active_ptc_config = ptc_config or NotebookPtcConfig()
    active_event_store = event_store or JsonlEventStore(settings.state_root / "events")

    read_default_lines = active_tool_config.read_default_lines
    bash_default_timeout = active_tool_config.bash_default_timeout_seconds
    capability_handlers = capabilities or {}

    def _invoke_tool(operation: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Keep expected and unexpected tool failures inside the tool protocol."""

        try:
            return call()
        except Exception as error:
            LOGGER.info(
                "model tool %s returned a recoverable %s",
                operation,
                type(error).__name__,
            )
            raw_message = " ".join(str(error).split())
            message = raw_message[:1_000]
            return {
                "status": "error",
                "model_text": f"{operation} failed: {type(error).__name__}: {message}",
                "truncated": len(raw_message) > 1_000,
                "omitted_bytes": max(
                    0,
                    len(raw_message.encode()) - len(message.encode()),
                ),
                "ui_details": {
                    "error_type": type(error).__name__,
                    "recoverable": True,
                },
            }

    def _runtime_identity(
        tool_context: ToolContext | None,
    ) -> tuple[str | None, str | None]:
        if tool_context is None:
            return settings.task_id_override, None
        task_id = tool_context.state.get("task_id") or settings.task_id_override
        invocation_id = getattr(tool_context, "invocation_id", None)
        return (
            str(task_id) if task_id else None,
            str(invocation_id) if invocation_id else None,
        )

    def _require_verification(tool_context: ToolContext | None) -> None:
        if tool_context is not None:
            task_scope, _ = _runtime_identity(tool_context)
            tool_context.state["verification_required_task"] = task_scope

    async def read(
        path: str,
        offset: int = 1,
        limit: int = read_default_lines,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read a bounded range from a workspace file or recoverable artifact URI."""

        if replies is not None:
            replies.guard_tool(tool_context)
        del tool_context
        return await run_managed_thread(
            _invoke_tool,
            "read",
            lambda: active_tools.read(path=path, offset=offset, limit=limit),
        )

    async def bash(
        command: str,
        timeout_seconds: int = bash_default_timeout,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run a bounded command or an in-process indexed search operation."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        # Even apparently read-only shell can contain redirections/substitutions.
        # The direct-answer path conservatively permits only the read primitive.
        _require_verification(tool_context)
        def invoke() -> dict[str, Any]:
            return _invoke_tool("bash", lambda: active_tools.bash(
                command=command,
                timeout_seconds=timeout_seconds,
                task_scope=task_scope,
                invocation_id=invocation_id,
                operation_id=getattr(tool_context, "function_call_id", None),
            ))
        result = await run_managed_thread(invoke)
        if approvals is not None and result.get("approval_required") is True:
            decision = await approvals.wait(str(result["approval_request_id"]), task_scope or "")
            if decision.status == "approved":
                return await run_managed_thread(invoke)
            return {**result, "approval_required": False,
                    "model_text": f"Command not executed: approval {decision.status}."}
        return result

    async def edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one exact, unique preimage in a workspace file."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        return await run_managed_thread(
            _invoke_tool,
            "edit",
            lambda: active_tools.edit(
                path=path,
                old_text=old_text,
                new_text=new_text,
                expected_sha256=expected_sha256,
                task_scope=task_scope,
                invocation_id=invocation_id,
                operation_id=getattr(tool_context, "function_call_id", None),
            ),
        )

    async def write(
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Atomically write a complete file with optimistic concurrency."""

        task_scope, invocation_id = _runtime_identity(tool_context)
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        return await run_managed_thread(
            _invoke_tool,
            "write",
            lambda: active_tools.write(
                path=path,
                content=content,
                expected_sha256=expected_sha256,
                expected_absent=expected_absent,
                task_scope=task_scope,
                invocation_id=invocation_id,
                operation_id=getattr(tool_context, "function_call_id", None),
            ),
        )

    python_worker = (
        PersistentPythonWorker(max_output_bytes=active_ptc_config.max_output_bytes)
        if active_ptc_config.enabled
        else None
    )
    restored_kernel_epoch: str | None = None
    active_notebooks: dict[str, str] = {}

    def notebook_events(task_id: str) -> list[HarnessEvent]:
        return [*prior_notebook_events, *active_event_store.read(task_id)]

    class _RestoreBroker:
        @staticmethod
        def _blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise PermissionError("capabilities are disabled while restoring replay-safe cells")

        read = write = edit = bash = call = _blocked

    class _CellBroker:
        def __init__(
            self,
            *,
            task_id: str,
            invocation_id: str | None,
            notebook_id: str,
            cell_id: str,
            attempt_id: str,
            event_loop: asyncio.AbstractEventLoop,
        ) -> None:
            self.task_id = task_id
            self.invocation_id = invocation_id
            self.notebook_id = notebook_id
            self.cell_id = cell_id
            self.attempt_id = attempt_id
            self.event_loop = event_loop
            self.call_index = 0
            self.operations: list[str] = []
            self.effects: list[str] = []
            self.artifact_refs: set[str] = set()

        def _call(
            self,
            operation: str,
            arguments: dict[str, Any],
            invoke: Callable[[], dict[str, Any]],
        ) -> dict[str, Any]:
            self.call_index += 1
            self.operations.append(operation)
            operation_id = f"{self.attempt_id}:{self.call_index}"
            arguments_hash = hashlib.sha256(
                json.dumps(arguments, sort_keys=True, default=str).encode()
            ).hexdigest()
            common = {
                "notebook_id": self.notebook_id,
                "cell_id": self.cell_id,
                "attempt_id": self.attempt_id,
                "operation_id": operation_id,
                "operation": operation,
                "arguments_sha256": arguments_hash,
            }
            active_event_store.append(
                self.task_id,
                EventKind.CAPABILITY_REQUESTED,
                common,
                idempotency_key=f"capability:{operation_id}:requested",
            )
            try:
                result = invoke()
            except Exception as error:
                active_event_store.append(
                    self.task_id,
                    EventKind.CAPABILITY_FAILED,
                    {**common, "status": "failed", "effect": "unknown", "error": type(error).__name__},
                    idempotency_key=f"capability:{operation_id}:failed",
                )
                self.effects.append("unknown")
                raise
            status = str(result.get("status", "error"))
            if status == "blocked":
                kind, effect = EventKind.CAPABILITY_BLOCKED, "none"
            elif status == "ok":
                kind = EventKind.CAPABILITY_COMPLETED
                effect = "changed" if result.get("changed_paths") else "observed"
            else:
                kind, effect = EventKind.CAPABILITY_FAILED, "unknown"
            refs = {
                str(value)
                for key in ("artifact_uri", "artifact_uris")
                for value in (
                    result.get(key, [])
                    if isinstance(result.get(key), list)
                    else [result.get(key)]
                )
                if isinstance(value, str) and value.startswith(("artifact://", "file://"))
            }
            self.artifact_refs.update(refs)
            self.effects.append(effect)
            active_event_store.append(
                self.task_id,
                kind,
                {**common, "status": status, "effect": effect, "artifact_refs": sorted(refs)},
                idempotency_key=f"capability:{operation_id}:terminal",
            )
            return result

        def read(self, path: str, offset: int = 1, limit: int = read_default_lines) -> dict[str, Any]:
            return self._call(
                "fs.read",
                {"path": path, "offset": offset, "limit": limit},
                lambda: active_tools.read(path=path, offset=offset, limit=limit),
            )

        def bash(self, command: str, timeout_seconds: int = bash_default_timeout) -> dict[str, Any]:
            def invoke() -> dict[str, Any]:
                result = active_tools.bash(
                    command=command,
                    timeout_seconds=timeout_seconds,
                    task_scope=self.task_id,
                    invocation_id=self.invocation_id,
                    operation_id=f"{self.attempt_id}:{self.call_index}",
                )
                if approvals is not None and result.get("approval_required") is True:
                    decision = asyncio.run_coroutine_threadsafe(
                        approvals.wait(str(result["approval_request_id"]), self.task_id),
                        self.event_loop,
                    ).result()
                    if decision.status == "approved":
                        return active_tools.bash(
                            command=command,
                            timeout_seconds=timeout_seconds,
                            task_scope=self.task_id,
                            invocation_id=self.invocation_id,
                            operation_id=f"{self.attempt_id}:{self.call_index}",
                        )
                    return {
                        **result,
                        "approval_required": False,
                        "model_text": f"Command not executed: approval {decision.status}.",
                    }
                return result

            return self._call(
                "shell.run",
                {"command": command, "timeout_seconds": timeout_seconds},
                invoke,
            )

        def edit(
            self,
            path: str,
            old_text: str,
            new_text: str,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            return self._call(
                "fs.edit",
                {
                    "path": path,
                    "old_text_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
                    "new_text_sha256": hashlib.sha256(new_text.encode()).hexdigest(),
                    "expected_sha256": expected_sha256,
                },
                lambda: active_tools.edit(
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                    expected_sha256=expected_sha256,
                    task_scope=self.task_id,
                    invocation_id=self.invocation_id,
                    operation_id=f"{self.attempt_id}:{self.call_index}",
                ),
            )

        def write(
            self,
            path: str,
            content: str,
            expected_sha256: str | None = None,
            expected_absent: bool = False,
        ) -> dict[str, Any]:
            return self._call(
                "fs.write",
                {
                    "path": path,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "expected_sha256": expected_sha256,
                    "expected_absent": expected_absent,
                },
                lambda: active_tools.write(
                    path=path,
                    content=content,
                    expected_sha256=expected_sha256,
                    expected_absent=expected_absent,
                    task_scope=self.task_id,
                    invocation_id=self.invocation_id,
                    operation_id=f"{self.attempt_id}:{self.call_index}",
                ),
            )

        def call(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            handler = capability_handlers.get(capability)
            if handler is None:
                return self._call(
                    "mcp.call",
                    {"capability": capability},
                    lambda: {
                        "status": "blocked",
                        "model_text": f"Unknown or unavailable capability: {capability}",
                    },
                )
            return self._call(
                "mcp.call",
                {
                    "capability": capability,
                    "arguments_sha256": hashlib.sha256(
                        json.dumps(arguments, sort_keys=True, default=str).encode()
                    ).hexdigest(),
                },
                lambda: handler(arguments),
            )

        @property
        def effect(self) -> str:
            if "unknown" in self.effects:
                return "unknown"
            if "changed" in self.effects:
                return "changed"
            return "observed" if self.effects else "none"

    async def python(
        code: str,
        timeout_seconds: int = active_ptc_config.default_timeout_seconds,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Append and execute one durable programmatic-tool-calling notebook cell."""

        nonlocal restored_kernel_epoch

        if python_worker is None:
            return {"status": "blocked", "model_text": "notebook-native PTC is disabled"}
        worker = python_worker
        if not 1 <= timeout_seconds <= active_ptc_config.max_timeout_seconds:
            return {
                "status": "error",
                "model_text": (
                    "timeout_seconds must be between 1 and "
                    f"{active_ptc_config.max_timeout_seconds}"
                ),
            }
        if len(code.encode()) > 128_000:
            return {"status": "error", "model_text": "Python cell exceeds 128000 UTF-8 bytes"}
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        task_scope, invocation_id = _runtime_identity(tool_context)
        task_id = task_scope or "unscoped"
        notebook_id = conversation_notebook_id or hashlib.sha256(task_id.encode()).hexdigest()[:32]
        active_notebooks[task_id] = notebook_id
        function_call_id = getattr(tool_context, "function_call_id", None)
        attempt_id = (
            hashlib.sha256(f"{invocation_id}\0{function_call_id}".encode()).hexdigest()
            if function_call_id else uuid4().hex
        )
        cell_id = attempt_id
        kernel_epoch = await asyncio.to_thread(lambda: worker.kernel_epoch)
        if restored_kernel_epoch != kernel_epoch:
            previous = reduce_notebook(notebook_events(task_id), notebook_id)
            restored_cells: list[str] = []
            for prior_cell in previous.cells:
                if not isinstance(prior_cell, NotebookCell):
                    continue
                if prior_cell.status != "completed" or prior_cell.replay_policy != "safe":
                    continue
                restored = await run_managed_thread(
                    worker.execute,
                    prior_cell.source,
                    _RestoreBroker(),
                    min(timeout_seconds, active_ptc_config.default_timeout_seconds),
                    cell_id=prior_cell.cell_id,
                    replay_policy=prior_cell.replay_policy,
                )
                if restored.status != "ok":
                    return {
                        "status": "blocked",
                        "model_text": "Could not restore a replay-safe notebook cell",
                        "cell_id": prior_cell.cell_id,
                        "error_type": restored.error_type,
                    }
                restored_cells.append(prior_cell.cell_id)
            if restored_cells:
                active_event_store.append(
                    task_id,
                    EventKind.REPL_STATE_RESTORED,
                    {
                        "projection_notebook_id": notebook_id,
                        "kernel_epoch": kernel_epoch,
                        "restored_cell_ids": restored_cells,
                    },
                    idempotency_key=f"repl-restore:{kernel_epoch}",
                )
            restored_kernel_epoch = kernel_epoch
        previous_attempt = next((
            cell for cell in reduce_notebook(notebook_events(task_id), notebook_id).cells
            if isinstance(cell, NotebookCell) and cell.attempt_id == attempt_id
        ), None)
        if previous_attempt is not None:
            if previous_attempt.source != code:
                return {"status": "blocked", "model_text": "Python operation identity reused with different source"}
            if previous_attempt.status != "completed":
                return {"status": "blocked", "reconciliation_required": True,
                        "model_text": "Interrupted Python cell requires reconciliation; automatic replay refused"}
            return {"status": "ok", "replayed": True, "cell_id": cell_id,
                    "attempt_id": attempt_id, "kernel_epoch": kernel_epoch,
                    "model_text": "Cell already completed; effects were not repeated. Inspect durable history for its outputs.",
                    "state_available": previous_attempt.replay_policy == "safe"}
        replay_policy = _replay_policy(code)
        cell_payload = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "source": code,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "replay_policy": replay_policy,
            "phase": str(tool_context.state.get("task_phase", "")) if tool_context else "",
        }
        active_event_store.append(
            task_id,
            EventKind.NOTEBOOK_CELL_ADDED,
            cell_payload,
            idempotency_key=f"notebook-cell:{attempt_id}",
        )
        active_event_store.append(
            task_id,
            EventKind.REPL_CELL_SUBMITTED,
            cell_payload,
            idempotency_key=f"repl-cell:{attempt_id}:submitted",
        )
        notebook_path = (notebook_root or settings.state_root / "notebooks") / f"{notebook_id}.ipynb"
        await asyncio.to_thread(
            materialize_notebook,
            reduce_notebook(notebook_events(task_id), notebook_id),
            notebook_path,
        )
        broker = _CellBroker(
            task_id=task_id,
            invocation_id=invocation_id,
            notebook_id=notebook_id,
            cell_id=cell_id,
            attempt_id=attempt_id,
            event_loop=asyncio.get_running_loop(),
        )
        result = await run_managed_thread(
            worker.execute,
            code,
            broker,
            timeout_seconds,
            cell_id=cell_id,
            replay_policy=replay_policy,
        )
        if result.status == "error":
            # A Python exception may follow successful assignments. Discard the
            # epoch so the next cell restores only previously committed safe cells.
            await asyncio.to_thread(worker.reset)
        if result.status == "ok":
            terminal_kind = EventKind.REPL_CELL_COMPLETED
        elif result.status == "timeout":
            terminal_kind = EventKind.REPL_CELL_TIMEOUT
        else:
            terminal_kind = EventKind.REPL_CELL_FAILED
        effect = "unknown" if result.effect_unknown else broker.effect
        display_data = result.display_data
        if display_data is not None:
            display_data, display_refs = externalize_mime_bundle(
                display_data,
                artifact_root=settings.state_root / "artifacts" / "sha256",
                max_inline_bytes=active_ptc_config.max_output_bytes,
            )
            broker.artifact_refs.update(display_refs)
        terminal_payload: dict[str, Any] = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "artifact_refs": sorted(broker.artifact_refs),
            "capability_count": broker.call_index,
            "capability_operations": list(broker.operations),
            "state": {
                "count": result.state_count,
                "delta": list(result.state_delta),
                "manifest": list(result.state_manifest),
            },
        }
        if display_data is not None:
            terminal_payload["display"] = display_data
        elif result.value_repr is not None:
            terminal_payload["display"] = {"text/plain": result.value_repr}
        if result.error_type is not None:
            terminal_payload["exception"] = {
                "ename": result.error_type,
                "evalue": result.error_message or "",
                "traceback": list(result.traceback),
            }
        active_event_store.append(
            task_id,
            terminal_kind,
            terminal_payload,
            idempotency_key=f"repl-cell:{attempt_id}:terminal",
        )
        notebook_state = reduce_notebook(notebook_events(task_id), notebook_id)
        notebook_bytes = await asyncio.to_thread(
            materialize_notebook,
            notebook_state,
            notebook_path,
        )
        notebook_hash = hashlib.sha256(notebook_bytes).hexdigest()
        active_event_store.append(
            task_id,
            EventKind.NOTEBOOK_MATERIALIZED,
            {
                "projection_notebook_id": notebook_id,
                "path": str(notebook_path),
                "source_watermark": notebook_state.source_watermark,
                "content_sha256": notebook_hash,
            },
            idempotency_key=f"notebook-materialized:{attempt_id}",
        )
        visible = "\n".join(
            part
            for part in (
                result.stdout,
                result.stderr,
                result.value_repr,
                (
                    f"{result.error_type}: {result.error_message}"
                    if result.error_type is not None
                    else None
                ),
            )
            if part
        )
        bounded = bound_output(
            visible,
            max_chars=active_ptc_config.max_output_bytes,
            max_lines=400,
        )
        return {
            "status": result.status,
            "model_text": bounded.text,
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "notebook_path": str(notebook_path),
            "notebook_sha256": notebook_hash,
            "artifact_uris": sorted(broker.artifact_refs),
            "duration_ms": result.duration_ms,
            "truncated": result.output_truncated or bounded.truncated,
            "omitted_bytes": bounded.omitted_bytes,
            "state_count": result.state_count,
            "state_delta": list(result.state_delta),
        }

    def close() -> None:
        assert python_worker is not None
        try:
            for task_id, notebook_id in sorted(active_notebooks.items()):
                notebook_state = reduce_notebook(notebook_events(task_id), notebook_id)
                notebook_path = (notebook_root or settings.state_root / "notebooks") / f"{notebook_id}.ipynb"
                notebook_bytes = materialize_notebook(notebook_state, notebook_path)
                artifact_uri = put_artifact(
                    settings.state_root / "artifacts" / "sha256", notebook_bytes
                )
                payload: dict[str, Any] = {
                    "notebook_id": notebook_id,
                    "artifact_uri": artifact_uri,
                    "notebook_sha256": hashlib.sha256(notebook_bytes).hexdigest(),
                    "source_watermark": notebook_state.source_watermark,
                }
                code_cells = [
                    cell for cell in notebook_state.cells if isinstance(cell, NotebookCell)
                ]
                if code_cells:
                    payload["kernel_epoch"] = code_cells[-1].kernel_epoch
                active_event_store.append(
                    task_id,
                    EventKind.NOTEBOOK_SNAPSHOTTED,
                    payload,
                    idempotency_key=(
                        f"notebook-snapshot:{notebook_id}:{notebook_state.source_watermark}"
                    ),
                )
        finally:
            python_worker.close()

    model_tools: list[Any] = (
        [python] if active_ptc_config.enabled else [read, bash, edit, write]
    )
    generation = active_generation_config.model_dump(exclude_none=True)
    stable_request_prefix: bytes | None = None

    async def before_model(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> None:
        nonlocal stable_request_prefix
        config = llm_request.config.model_dump(
            mode="json",
            include={"system_instruction", "tools", "tool_config"},
            exclude_none=True,
        )
        current = json.dumps(
            {"model": llm_request.model, "config": config},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if stable_request_prefix is None:
            stable_request_prefix = current
        elif current != stable_request_prefix:
            raise RuntimeError("The coding worker's cache-stable request prefix changed")
        if replies is not None:
            await replies.before_model(callback_context, llm_request)

    agent = Agent(
        name="coding_worker",
        model=model,
        description=(
            "Executes notebook-native PTC in one persistent CPython tool."
            if active_ptc_config.enabled
            else "Owns one complete coding run with four composable tools."
        ),
        static_instruction=settings.static_instruction,
        instruction="",
        tools=model_tools,
        include_contents="default" if active_ptc_config.enabled else "none",
        output_schema=(
            StructuredAgentStep
            if getattr(getattr(model, "capabilities", None), "output_schema_and_tools", False)
            else None
        ),
        generate_content_config=(
            types.GenerateContentConfig(**generation) if generation else None
        ),
        before_model_callback=before_model,
        after_model_callback=replies.after_model if replies is not None else None,
    )
    return CodingWorkerBundle(
        agent=agent,
        read=read,
        bash=bash,
        edit=edit,
        write=write,
        python=python if active_ptc_config.enabled else None,
        close=close if python_worker is not None else None,
    )


__all__ = ["CodingWorkerBundle", "build_coding_worker"]
