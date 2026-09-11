"""PTC session adapters with implementation-owned execution and persistence."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext
from google.genai import types

from harness.core.config import NotebookPtcConfig
from harness.evidence.state import EventKind, EventStore
from harness.evidence.state.events import HarnessEvent
from harness.execution.approvals.waiting import ApprovalWaiter
from harness.execution.environment.async_call import run_managed_thread
from harness.execution.repo import build_repository_manifest
from harness.execution.sandbox import MANAGED_COMMAND_ENVIRONMENT
from harness.execution.tools.adk_adapter import AdkCodingTools
from harness.execution.tools.output import bound_output, compact_tool_result
from harness.ptc.notebook import (
    NotebookCell,
    externalize_mime_bundle,
    materialize_notebook,
    put_artifact,
    reduce_notebook,
)
from harness.ptc.repl import PersistentPythonWorker, default_help_catalog
from harness.verification.contracts import is_reusable_validation_command

from .config import HarnessSettings
from .streaming import PublicReplies


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
class PtcSession:
    tool: Any
    description: str
    execute_code: Callable[..., Awaitable[dict[str, Any]]] | None = None
    close: Callable[[], Awaitable[None] | None] | None = None
    before_model: Callable[[CallbackContext], Awaitable[LlmResponse | None]] | None = None
    after_agent: Callable[[CallbackContext], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    description: str
    arguments: Mapping[str, object]
    result: Mapping[str, object]
    effect: str = "unknown"
    approval: str = "handler-owned"


def build_notebook_session(
    settings: HarnessSettings,
    *,
    active_ptc_config: NotebookPtcConfig,
    active_event_store: EventStore,
    active_tools: AdkCodingTools,
    approvals: ApprovalWaiter | None,
    replies: PublicReplies | None,
    capability_handlers: Mapping[
        str, RegisteredCapability | Callable[[dict[str, Any]], dict[str, Any]]
    ],
    conversation_notebook_id: str | None,
    prior_notebook_events: tuple[HarnessEvent, ...],
    notebook_root: Path | None,
    fingerprint_workspace: Callable[[], str],
    read_default_lines: int,
    bash_default_timeout: int,
    runtime_identity: Callable[[ToolContext | None], tuple[str | None, str | None]],
    require_verification: Callable[[ToolContext | None], None],
) -> PtcSession:
    _runtime_identity = runtime_identity
    _require_verification = require_verification
    registered = {
        name: (
            value
            if isinstance(value, RegisteredCapability)
            else RegisteredCapability(
                handler=value,
                description="Registered external capability",
                arguments={"type": "object"},
                result={"type": "object"},
            )
        )
        for name, value in sorted(capability_handlers.items())
    }
    help_catalog = default_help_catalog()
    for name, capability in registered.items():
        help_catalog[f"mcp.{name}"] = {
            "signature": f"agent.mcp.call({name!r}, arguments)",
            "description": capability.description,
            "arguments": dict(capability.arguments),
            "result": dict(capability.result),
            "effect": capability.effect,
            "approval": capability.approval,
        }
    commands = [
        {"kind": item.kind, "command": item.command, "source": item.source}
        for item in build_repository_manifest(settings.workspace).commands
        if (settings.workspace / item.source).is_file()
    ]
    help_catalog["cli"] = {
        "description": "Project commands verified from repository manifests",
        "commands": sorted(
            commands,
            key=lambda item: (item["kind"], item["command"], item["source"]),
        ),
    }
    python_worker = PersistentPythonWorker(
        max_output_bytes=active_ptc_config.max_output_bytes,
        help_catalog=help_catalog,
    )
    restored_kernel_epoch: str | None = None
    active_notebooks: dict[str, str] = {}
    batch_cells: dict[tuple[str, str], int] = {}
    batch_changed: set[tuple[str, str]] = set()

    def notebook_events(task_id: str) -> list[HarnessEvent]:
        return [*prior_notebook_events, *active_event_store.read(task_id)]

    class _RestoreBroker:
        @staticmethod
        def _blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise PermissionError("capabilities are disabled while restoring replay-safe cells")

        read = write = edit = bash = call = parallel = _blocked

    class _CellBroker:
        def __init__(
            self,
            *,
            task_id: str,
            invocation_id: str | None,
            notebook_id: str,
            cell_id: str,
            attempt_id: str,
            work_batch_id: str,
            event_loop: asyncio.AbstractEventLoop,
        ) -> None:
            self.task_id = task_id
            self.invocation_id = invocation_id
            self.notebook_id = notebook_id
            self.cell_id = cell_id
            self.attempt_id = attempt_id
            self.work_batch_id = work_batch_id
            self.event_loop = event_loop
            self.call_index = 0
            self.operations: list[str] = []
            self.effects: list[str] = []
            self.artifact_refs: set[str] = set()

        def _reserve(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if self.call_index >= active_ptc_config.max_capability_calls_per_cell:
                raise RuntimeError(
                    "capability call limit exceeded "
                    f"({active_ptc_config.max_capability_calls_per_cell} per cell)"
                )
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
                "work_batch_id": self.work_batch_id,
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
            return common

        def _record_error(self, common: dict[str, Any], error: BaseException) -> None:
            operation_id = str(common["operation_id"])
            effect = "none" if common["operation"] == "fs.read" else "unknown"
            active_event_store.append(
                self.task_id,
                EventKind.CAPABILITY_FAILED,
                {
                    **common,
                    "status": "failed",
                    "effect": effect,
                    "error": type(error).__name__,
                    "result_hash": hashlib.sha256(type(error).__name__.encode()).hexdigest(),
                },
                idempotency_key=f"capability:{operation_id}:failed",
            )
            self.effects.append(effect)

        def _record_result(self, common: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
            result = {
                **result,
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
            }
            operation_id = str(common["operation_id"])
            status = str(result.get("status", "error"))
            if status == "blocked":
                kind, effect = EventKind.CAPABILITY_BLOCKED, "none"
            elif status == "ok":
                kind = EventKind.CAPABILITY_COMPLETED
                effect = "changed" if result.get("changed_paths") else "observed"
            else:
                kind = EventKind.CAPABILITY_FAILED
                effect = "none" if common["operation"] == "fs.read" else "unknown"
            refs = {
                str(value)
                for key in ("artifact_uri", "artifact_uris")
                for value in (
                    result.get(key, []) if isinstance(result.get(key), list) else [result.get(key)]
                )
                if isinstance(value, str) and value.startswith(("artifact://", "file://"))
            }
            self.artifact_refs.update(refs)
            self.effects.append(effect)
            result_hash = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            active_event_store.append(
                self.task_id,
                kind,
                {
                    **common,
                    "status": status,
                    "effect": effect,
                    "result_hash": result_hash,
                    "artifact_refs": sorted(refs),
                    "truncated": bool(result.get("truncated")),
                    "omitted_bytes": max(0, int(result.get("omitted_bytes", 0))),
                },
                idempotency_key=f"capability:{operation_id}:terminal",
            )
            return result

        def _call(
            self,
            operation: str,
            arguments: dict[str, Any],
            invoke: Callable[[], dict[str, Any]],
        ) -> dict[str, Any]:
            common = self._reserve(operation, arguments)
            try:
                result = invoke()
            except Exception as error:
                self._record_error(common, error)
                return {
                    "status": "error",
                    "data": {},
                    "model_text": f"{type(error).__name__}: {error}",
                }
            return self._record_result(common, result)

        def parallel(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not isinstance(operations, list) or not operations:
                raise ValueError("parallel operations must be a non-empty list")
            if len(operations) > active_ptc_config.max_parallel_reads:
                raise ValueError(
                    f"parallel supports at most {active_ptc_config.max_parallel_reads} reads"
                )
            normalized: list[dict[str, Any]] = []
            for item in operations:
                if not isinstance(item, dict) or item.get("operation") != "fs.read":
                    raise PermissionError("parallel currently permits only fs.read")
                arguments = item.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise TypeError("parallel operation arguments must be a mapping")
                unexpected = set(arguments) - {"path", "offset", "limit"}
                if unexpected or not isinstance(arguments.get("path"), str):
                    raise ValueError("invalid fs.read arguments in parallel batch")
                normalized.append(arguments)

            if self.call_index + len(normalized) > active_ptc_config.max_capability_calls_per_cell:
                raise RuntimeError(
                    "capability call limit exceeded "
                    f"({active_ptc_config.max_capability_calls_per_cell} per cell)"
                )

            def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
                return active_tools.read(
                    path=arguments["path"],
                    offset=arguments.get("offset", 1),
                    limit=arguments.get("limit", read_default_lines),
                )

            reserved = [self._reserve("fs.read", arguments) for arguments in normalized]
            with ThreadPoolExecutor(max_workers=len(normalized)) as executor:
                futures = [executor.submit(invoke, arguments) for arguments in normalized]
                results: list[dict[str, Any]] = []
                for common, future in zip(reserved, futures, strict=True):
                    try:
                        results.append(self._record_result(common, future.result()))
                    except Exception as error:
                        self._record_error(common, error)
                        results.append(
                            {
                                "status": "error",
                                "data": {},
                                "model_text": f"fs.read failed: {type(error).__name__}: {error}",
                            }
                        )
                return results

        def read(
            self, path: str, offset: int = 1, limit: int = read_default_lines
        ) -> dict[str, Any]:
            return self._call(
                "fs.read",
                {"path": path, "offset": offset, "limit": limit},
                lambda: active_tools.read(path=path, offset=offset, limit=limit),
            )

        def bash(self, command: str, timeout_seconds: int = bash_default_timeout) -> dict[str, Any]:
            reusable = is_reusable_validation_command(command)
            workspace_before = fingerprint_workspace() if reusable else None

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

            result = self._call(
                "shell.run",
                {"command": command, "timeout_seconds": timeout_seconds},
                invoke,
            )
            workspace_after = fingerprint_workspace() if reusable else None
            if (
                workspace_before is not None
                and result.get("status") == "ok"
                and result.get("exit_code") == 0
                and not result.get("truncated")
                and not result.get("omitted_bytes")
                and workspace_before == workspace_after
            ):
                active_event_store.append(
                    self.task_id,
                    "execution.validation_observed",
                    {
                        "operation_id": f"{self.attempt_id}:{self.call_index}",
                        "receipt_id": result.get("receipt_id"),
                        "command": command,
                        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                        "environment": dict(MANAGED_COMMAND_ENVIRONMENT),
                        "workspace_before": workspace_before,
                        "workspace_after": workspace_after,
                        "work_batch_id": self.work_batch_id,
                        "result": {
                            "status": "ok",
                            "exit_code": result.get("exit_code"),
                            "stdout": str(
                                (result.get("data") or {}).get(
                                    "stdout", result.get("model_text", "")
                                )
                            ),
                            "duration_ms": int(result.get("duration_ms", 0)),
                            "artifact_uri": result.get("artifact_uri"),
                        },
                    },
                    idempotency_key=f"validation-observed:{self.attempt_id}:{self.call_index}",
                )
            return result

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
            registered_capability = registered.get(capability)
            if registered_capability is None:
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
                lambda: registered_capability.handler(arguments),
            )

        @property
        def effect(self) -> str:
            if "unknown" in self.effects:
                return "unknown"
            if "changed" in self.effects:
                return "changed"
            return "observed" if "observed" in self.effects else "none"

    async def _execute_code(
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
                    f"timeout_seconds must be between 1 and {active_ptc_config.max_timeout_seconds}"
                ),
            }
        if len(code.encode()) > 128_000:
            return {"status": "error", "model_text": "Python cell exceeds 128000 UTF-8 bytes"}
        if replies is not None:
            replies.guard_tool(tool_context)
        _require_verification(tool_context)
        task_scope, invocation_id = _runtime_identity(tool_context)
        if settings.task_id_override and task_scope and task_scope != settings.task_id_override:
            raise ValueError("notebook PTC task is outside this owned run")
        task_id = task_scope or "unscoped"
        notebook_id = conversation_notebook_id or hashlib.sha256(task_id.encode()).hexdigest()[:32]
        active_notebooks[task_id] = notebook_id
        function_call_id = getattr(tool_context, "function_call_id", None)
        attempt_id = (
            hashlib.sha256(f"{invocation_id}\0{function_call_id}".encode()).hexdigest()
            if function_call_id
            else uuid4().hex
        )
        cell_id = attempt_id
        work_batch_id = str(tool_context.state.get("ptc_work_batch_id", "")) if tool_context else ""
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
        previous_attempt = next(
            (
                cell
                for cell in reduce_notebook(notebook_events(task_id), notebook_id).cells
                if isinstance(cell, NotebookCell) and cell.attempt_id == attempt_id
            ),
            None,
        )
        unresolved = next(
            (
                cell
                for cell in reduce_notebook(notebook_events(task_id), notebook_id).cells
                if isinstance(cell, NotebookCell) and cell.status == "effect_unknown"
            ),
            None,
        )
        if unresolved is not None and (
            previous_attempt is None or unresolved.attempt_id != previous_attempt.attempt_id
        ):
            return {
                "status": "blocked",
                "reconciliation_required": True,
                "cell_id": unresolved.cell_id,
                "model_text": "A prior timed-out Python cell has an unknown effect; reconcile it before more execution.",
            }
        if previous_attempt is not None:
            if previous_attempt.source != code:
                return {
                    "status": "blocked",
                    "model_text": "Python operation identity reused with different source",
                }
            if previous_attempt.status != "completed":
                return {
                    "status": "blocked",
                    "reconciliation_required": True,
                    "model_text": "Interrupted Python cell requires reconciliation; automatic replay refused",
                }
            return {
                "status": "ok",
                "replayed": True,
                "cell_id": cell_id,
                "attempt_id": attempt_id,
                "kernel_epoch": kernel_epoch,
                "model_text": "Cell already completed; effects were not repeated. Inspect durable history for its outputs.",
                "state_available": previous_attempt.replay_policy == "safe",
            }
        replay_policy = _replay_policy(code)
        cell_payload = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "source": code,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "replay_policy": replay_policy,
            "phase": str(tool_context.state.get("task_phase", "")) if tool_context else "",
            "work_batch_id": work_batch_id,
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
        notebook_path = (
            notebook_root or settings.state_root / "notebooks"
        ) / f"{notebook_id}.ipynb"
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
            work_batch_id=work_batch_id,
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
        state_preserved = result.failure_stage in {"parse", "source_validation"}
        if result.status == "error" and not state_preserved:
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
            "work_batch_id": work_batch_id,
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
                "stage": result.failure_stage,
                "line": result.error_line,
                "source": result.error_source,
                "state_preserved": state_preserved,
            }
        active_event_store.append(
            task_id,
            terminal_kind,
            terminal_payload,
            idempotency_key=f"repl-cell:{attempt_id}:terminal",
        )
        batch_key = (task_id, work_batch_id)
        batch_cells[batch_key] = batch_cells.get(batch_key, 0) + 1
        if effect == "changed":
            batch_changed.add(batch_key)
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
            "failure_stage": result.failure_stage,
            "error_type": result.error_type,
            "error_line": result.error_line,
            "error_source": result.error_source,
            "state_preserved": state_preserved,
        }

    async def execute_code(
        code: str,
        timeout_seconds: int = active_ptc_config.default_timeout_seconds,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        result = await _execute_code(code, timeout_seconds, tool_context)
        compact = compact_tool_result(
            result,
            max_chars=active_ptc_config.max_output_bytes,
        )
        for key in (
            "failure_stage",
            "error_type",
            "error_line",
            "error_source",
            "state_preserved",
        ):
            if result.get(key) is not None:
                compact[key] = result[key]
        return compact

    def close() -> None:
        assert python_worker is not None
        try:
            for task_id, notebook_id in sorted(active_notebooks.items()):
                notebook_state = reduce_notebook(notebook_events(task_id), notebook_id)
                notebook_path = (
                    notebook_root or settings.state_root / "notebooks"
                ) / f"{notebook_id}.ipynb"
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

    async def before_model(callback_context: CallbackContext) -> LlmResponse | None:
        if callback_context is not None:
            state = callback_context.state
            task_id = str(state.get("task_id") or settings.task_id_override or "unscoped")
            work_batch_id = str(state.get("ptc_work_batch_id", ""))
            if work_batch_id:
                batch_key = (task_id, work_batch_id)
                if batch_key not in batch_cells:
                    terminal_kinds = {
                        EventKind.REPL_CELL_COMPLETED,
                        EventKind.REPL_CELL_FAILED,
                        EventKind.REPL_CELL_TIMEOUT,
                    }
                    prior = [
                        event
                        for event in active_event_store.read(task_id)
                        if event.kind in terminal_kinds
                        and str(event.payload.get("work_batch_id", "")) == work_batch_id
                    ]
                    batch_cells[batch_key] = len(prior)
                    if any(event.payload.get("effect") == "changed" for event in prior):
                        batch_changed.add(batch_key)
                count = batch_cells[batch_key]
                reason = None
                if count >= active_ptc_config.max_cells_per_batch:
                    reason = "max_cells"
                elif (
                    count >= active_ptc_config.no_progress_cells_per_batch
                    and batch_key not in batch_changed
                ):
                    reason = "no_workspace_change"
                if reason is not None:
                    payload = {
                        "work_batch_id": work_batch_id,
                        "cell_count": count,
                        "reason": reason,
                        "workspace_changed": batch_key in batch_changed,
                    }
                    active_event_store.append(
                        task_id,
                        EventKind.WORK_BATCH_YIELDED,
                        payload,
                        idempotency_key=f"work-batch-yield:{work_batch_id}",
                    )
                    structured = {
                        "status": "blocked",
                        "message": "",
                        "progress": [],
                        "next_action": "Continue from the durable notebook after host review.",
                        "decisions": [],
                        "questions": [],
                        "discovered_constraints": [],
                        "files_in_focus": [],
                        "completion_claims": [],
                    }
                    return LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=json.dumps(structured))],
                        ),
                        turn_complete=True,
                        custom_metadata={"skein_work_batch_yield": True},
                    )
        return None

    return PtcSession(
        tool=execute_code,
        description="Executes Skein notebook PTC in one persistent CPython tool.",
        execute_code=execute_code,
        close=close,
        before_model=before_model,
    )
