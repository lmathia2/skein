"""Side-effect-free ADK agent builders shared by factory and legacy bootstrap."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext
from google.genai import types

from harness.core.config import GenerationConfig, NotebookPtcConfig, ToolSurfaceConfig
from harness.core.models.agent_step import StructuredAgentStep
from harness.evidence.memory.models import ReadEvidence
from harness.evidence.state import EventKind, EventStore, JsonlEventStore
from harness.evidence.state.events import HarnessEvent
from harness.execution.approvals.waiting import ApprovalWaiter
from harness.execution.environment.async_call import run_managed_thread
from harness.execution.environment.runtime import LocalRepositoryRuntime
from harness.execution.safety.redaction import SecretRedactor
from harness.execution.tools.adk_adapter import AdkCodingTools, create_adk_tools
from harness.ptc.notebook.artifacts import put_artifact

from .config import HarnessSettings
from .ptc import RegisteredCapability, build_notebook_session
from .streaming import PublicReplies

LOGGER = logging.getLogger(__name__)

ToolFunction = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CodingWorkerBundle:
    agent: Agent
    read: ToolFunction
    bash: ToolFunction
    edit: ToolFunction
    write: ToolFunction
    execute_code: ToolFunction | None = None
    close: Callable[[], Awaitable[None] | None] | None = None
    kernel_status: Callable[[], dict[str, Any]] | None = None


def build_coding_worker(
    settings: HarnessSettings,
    model: BaseLlm,
    *,
    tools: AdkCodingTools | None = None,
    generation_config: GenerationConfig | None = None,
    tool_config: ToolSurfaceConfig | None = None,
    ptc_config: NotebookPtcConfig | None = None,
    event_store: EventStore | None = None,
    capture_read_evidence: bool = False,
    approvals: ApprovalWaiter | None = None,
    replies: PublicReplies | None = None,
    capabilities: dict[
        str, RegisteredCapability | Callable[[dict[str, Any]], dict[str, Any]]
    ]
    | None = None,
    conversation_notebook_id: str | None = None,
    prior_notebook_events: tuple[HarnessEvent, ...] = (),
    notebook_root: Path | None = None,
    workspace_fingerprint: Callable[[], str] | None = None,
    redactor: SecretRedactor | None = None,
    bounded_work_batches: bool = True,
    thin_loop: bool = False,
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
    active_redactor = redactor or SecretRedactor()
    fingerprint_workspace = workspace_fingerprint or LocalRepositoryRuntime(
        settings.workspace
    ).fingerprint

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

    def _model_result(result: dict[str, Any]) -> dict[str, Any]:
        """Keep program-only ingress out of direct model tool responses."""

        return {
            key: value for key, value in result.items()
            if key not in {"data", "ui_details"}
        }

    def observe_effect(task_id: str | None, operation: str, result: dict[str, Any]) -> dict[str, Any]:
        if capture_read_evidence and task_id:
            metadata = result.get("ui_details") or {}
            active_event_store.append(task_id, EventKind.WORKSPACE_EFFECT_OBSERVED, active_redactor.redact({
                "operation": operation, "status": result.get("status"),
                "changed_paths": sorted(set(result.get("changed_paths", [])))[:128],
                "content_hashes": dict(sorted(result.get("content_hashes", {}).items())[:128]),
                "workspace_may_have_changed": result.get("status") != "blocked" and
                not (operation == "shell.run" and (metadata.get("memory") is True or
                     str(metadata.get("virtual_operation", "")).startswith("search."))),
            }))
        return _model_result(result)

    async def read(
        path: str,
        offset: int = 1,
        limit: int = read_default_lines,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Read a bounded range from a workspace file or recoverable artifact URI."""

        if replies is not None:
            replies.guard_tool(tool_context)
        task_scope, _ = _runtime_identity(tool_context)
        if settings.task_id_override and task_scope != settings.task_id_override:
            raise ValueError("read evidence task is outside this run")
        result = active_redactor.redact(await run_managed_thread(
            _invoke_tool,
            "read",
            lambda: active_tools.read(path=path, offset=offset, limit=limit),
        ))
        data = result.get("data")
        if capture_read_evidence and task_scope and result.get("status") == "ok" and isinstance(data, dict) and all(
            key in data for key in ReadEvidence.model_fields
        ):
            evidence = ReadEvidence.model_validate({key: data[key] for key in ReadEvidence.model_fields})
            content = (json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            uri = put_artifact(settings.state_root / "artifacts" / "sha256", content)
            active_event_store.append(task_scope, EventKind.READ_OBSERVED, {
                "operation": "fs.read", "status": "ok", "read_evidence": evidence.model_dump(),
                "source_coverage": evidence.source_coverage(data.get("total_lines")),
                "result_artifact_uri": uri, "result_media_type": "application/json",
                "result_bytes": len(content), "artifact_refs": [uri],
            })
        return _model_result(result)

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
                return observe_effect(task_scope, "shell.run", await run_managed_thread(invoke))
            return _model_result({**result, "approval_required": False,
                    "model_text": f"Command not executed: approval {decision.status}."}
            )
        return observe_effect(task_scope, "shell.run", result)

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
        return observe_effect(task_scope, "fs.edit", await run_managed_thread(
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
        ))

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
        return observe_effect(task_scope, "fs.write", await run_managed_thread(
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
        ))

    ptc_session = (
        build_notebook_session(
            settings, active_ptc_config=active_ptc_config,
            active_event_store=active_event_store, active_tools=active_tools,
            approvals=approvals, replies=replies, capability_handlers=capability_handlers,
            conversation_notebook_id=conversation_notebook_id,
            prior_notebook_events=prior_notebook_events, notebook_root=notebook_root,
            fingerprint_workspace=fingerprint_workspace,
            read_default_lines=read_default_lines, bash_default_timeout=bash_default_timeout,
            runtime_identity=_runtime_identity, require_verification=_require_verification,
            redactor=active_redactor,
            bounded_work_batches=bounded_work_batches,
        )
        if active_ptc_config.enabled
        else None
    )

    model_tools: list[Any] = [ptc_session.tool] if ptc_session else [read, bash, edit, write]
    if ptc_session is not None and thin_loop:
        async def code(
            code: str,
            tool_context: ToolContext | None = None,
        ) -> dict[str, Any]:
            """Run one persistent Python cell with Pi-compatible workspace helpers."""

            return await ptc_session.tool(code=code, tool_context=tool_context)

        model_tools = [code]
    generation = active_generation_config.model_dump(exclude_none=True)
    stable_request_prefix: bytes | None = None

    async def before_model(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
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
        if ptc_session is not None and ptc_session.before_model is not None:
            response = await ptc_session.before_model(callback_context)
            if response is not None:
                return response
        if replies is not None:
            await replies.before_model(callback_context, llm_request)
        return None

    agent = Agent(
        name="coding_worker",
        model=model,
        description=(
            ptc_session.description
            if ptc_session is not None
            else "Owns one complete coding run with four composable tools."
        ),
        static_instruction=settings.static_instruction,
        instruction="",
        tools=model_tools,
        include_contents="default" if ptc_session is not None else "none",
        output_schema=(
            None if thin_loop else StructuredAgentStep
            if getattr(getattr(model, "capabilities", None), "output_schema_and_tools", False)
            else None
        ),
        generate_content_config=(
            types.GenerateContentConfig(**generation) if generation else None
        ),
        before_model_callback=before_model,
        after_model_callback=replies.after_model if replies is not None else None,
        after_agent_callback=ptc_session.after_agent if ptc_session is not None else None,
    )
    return CodingWorkerBundle(
        agent=agent,
        read=read,
        bash=bash,
        edit=edit,
        write=write,
        execute_code=ptc_session.execute_code if ptc_session is not None else None,
        close=ptc_session.close if ptc_session is not None else None,
        kernel_status=ptc_session.kernel_status if ptc_session is not None else None,
    )


__all__ = ["CodingWorkerBundle", "build_coding_worker"]
