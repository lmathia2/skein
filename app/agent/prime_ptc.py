"""Prime-native execution adapted to Skein's owned task event stream."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext

from harness.config import NotebookPtcConfig
from harness.environment.async_call import run_managed_thread
from harness.repl.prime import PrimeRuntime
from harness.safety.redaction import SecretRedactor
from harness.state import EventStore
from harness.state.events import HarnessEvent

from .config import HarnessSettings
from .ptc import PtcSession
from .streaming import PublicReplies


def build_prime_session(
    settings: HarnessSettings, *, config: NotebookPtcConfig, events: EventStore,
    runtime_identity: Callable[[ToolContext | None], tuple[str | None, str | None]],
    require_verification: Callable[[ToolContext | None], None], replies: PublicReplies | None,
    redactor: SecretRedactor,
    conversation_id: str | None = None, prior_events: tuple[HarnessEvent, ...] = (),
    state_root: Path | None = None,
) -> PtcSession:
    if not config.prime_native_execution or not settings.project_trusted:
        raise ValueError("prime_repl requires prime_native_execution=true and explicit project trust")
    if importlib.util.find_spec("dill") is None:
        raise NotImplementedError("prime_repl requires the ptc-prime extra (dill)")
    identity = conversation_id or settings.task_id_override
    if not identity:
        raise ValueError("Prime PTC requires an owned task or conversation identity")
    directory = (state_root or settings.state_root) / "prime" / hashlib.sha256(identity.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    owner = (directory / "owner.lock").open("a+b")
    try:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        owner.close()
        raise RuntimeError("Prime PTC conversation already has an active owner") from None
    runtime = PrimeRuntime(settings.workspace, max_output_bytes=config.max_output_bytes)
    execution_lock = asyncio.Lock()
    closed = False
    task_id = settings.task_id_override or identity
    restored = False

    def append(kind: str, payload: dict[str, Any], key: str | None = None) -> None:
        events.append(task_id, kind, payload, idempotency_key=key)

    async def execute_code(
        code: str, timeout_seconds: int = config.default_timeout_seconds,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Execute Python in a persistent Prime-native process and retain selected output."""
        nonlocal task_id, restored
        if closed:
            raise RuntimeError("Prime PTC session is closed")
        if not 1 <= timeout_seconds <= config.max_timeout_seconds or len(code.encode()) > 128000:
            raise ValueError("Prime code or timeout exceeds configured limits")
        if replies:
            replies.guard_tool(tool_context)
        require_verification(tool_context)
        async with execution_lock:
            scope, invocation = runtime_identity(tool_context)
            if scope and scope != settings.task_id_override:
                raise ValueError("Prime task is outside this owned run")
            task_id = scope or task_id
            call_id = getattr(tool_context, "function_call_id", None)
            attempt = hashlib.sha256(f"{invocation}\0{call_id}".encode()).hexdigest() if call_id else uuid4().hex
            history = [*prior_events, *events.read(task_id)]
            source_hash = hashlib.sha256(code.encode()).hexdigest()
            submitted = [event for event in history if event.kind == "prime.cell_submitted"]
            terminals = {event.payload["attempt_id"]: event for event in history
                         if event.kind == "prime.cell_terminal"}
            previous = next((event for event in submitted if event.payload["attempt_id"] == attempt), None)
            if previous:
                if previous.payload["source_sha256"] != source_hash:
                    raise ValueError("Prime attempt identity reused with different source")
                terminal = terminals.get(attempt)
                if terminal is None:
                    return {"status": "blocked", "reconciliation_required": True,
                            "model_text": "Prior Prime attempt has no terminal receipt; automatic replay refused."}
                return {**terminal.payload["result"], "replayed": True}
            if any(event.payload["attempt_id"] not in terminals for event in submitted):
                return {"status": "blocked", "reconciliation_required": True,
                        "model_text": "An interrupted Prime attempt requires reconciliation."}
            if any(event.payload.get("effect") == "unknown" for event in terminals.values()):
                return {"status": "blocked", "reconciliation_required": True,
                        "model_text": "Prime native effects require reconciliation."}
            if not restored:
                await run_managed_thread(runtime.start)
                snapshots = [event for event in history if event.kind == "prime.state_snapshotted"]
                if submitted and (not snapshots or snapshots[-1].payload["attempt_id"] != submitted[-1].payload["attempt_id"]):
                    runtime.close()
                    return {"status": "blocked", "reconciliation_required": True,
                            "model_text": "Latest Prime cell has no committed snapshot; stale state restore refused."}
                if snapshots:
                    result = await run_managed_thread(runtime.restore, snapshots[-1].payload["snapshot"], directory)
                    if result.get("status") != "ok":
                        runtime.close()
                        raise RuntimeError("Prime state restore failed")
                    append("prime.state_restored", {"result": redactor.redact(result), "runtime_epoch": runtime.epoch})
                restored = True
            append("prime.cell_submitted", {
                "attempt_id": attempt, "cell_id": attempt, "source_sha256": source_hash,
                "source": redactor.redact_text(code), "runtime_epoch": runtime.epoch,
                "capability_access": "native",
            }, f"prime:{attempt}:submitted")
            try:
                pending = asyncio.create_task(asyncio.to_thread(
                    runtime.request, "execute", id=attempt, code=code, timeout=timeout_seconds,
                ))
                try:
                    raw = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    runtime.interrupt()
                    try:
                        await asyncio.wait_for(asyncio.shield(pending), timeout=2)
                    except (TimeoutError, RuntimeError):
                        runtime.close()
                    finally:
                        await asyncio.gather(pending, return_exceptions=True)
                    raise
            except BaseException as error:
                runtime.close()
                restored = False
                append("prime.cell_terminal", {"attempt_id": attempt, "effect": "unknown", "result": {
                    "status": "timeout" if isinstance(error, TimeoutError) else "error",
                    "model_text": "Prime execution interrupted; effects require reconciliation.",
                    "reconciliation_required": True,
                }}, f"prime:{attempt}:terminal")
                raise
            result = redactor.redact({
                "status": raw["status"], "stdout": raw["stdout"], "stderr": raw["stderr"],
                "model_text": "\n".join(str(raw.get(key, "")) for key in ("stdout", "stderr", "result", "error", "background") if raw.get(key)),
                "displays": raw["displays"], "truncated": raw.get("truncated", False),
                "runtime_epoch": runtime.epoch, "capability_access": "native",
            })
            append("prime.cell_terminal", {"attempt_id": attempt, "effect": "native_untracked", "result": result}, f"prime:{attempt}:terminal")
            try:
                snapshot = await run_managed_thread(runtime.snapshot, directory)
                append("prime.state_snapshotted", {"attempt_id": attempt, "snapshot": snapshot}, f"prime:{attempt}:snapshot")
            except BaseException:
                # Do not continue with warm values which restart cannot recover.
                runtime.close()
                restored = False
                append("prime.snapshot_failed", {"attempt_id": attempt})
                raise
            return result

    def close() -> None:
        nonlocal closed
        if not closed:
            closed = True
            try:
                runtime.close()
            finally:
                owner.close()

    async def before_model(_context: CallbackContext) -> LlmResponse | None:
        history = [*prior_events, *events.read(task_id)]
        terminal = {event.payload["attempt_id"]: event for event in history
                    if event.kind == "prime.cell_terminal"}
        submissions = [event for event in history if event.kind == "prime.cell_submitted"]
        snapshots = [event for event in history if event.kind == "prime.state_snapshotted"]
        if any(event.kind == "prime.cell_submitted" and event.payload["attempt_id"] not in terminal
               for event in history) or any(event.payload.get("effect") == "unknown" for event in terminal.values()) or (
                   submissions and (not snapshots or snapshots[-1].payload["attempt_id"] != submissions[-1].payload["attempt_id"])
               ):
            raise RuntimeError("Prime execution requires reconciliation before continuation or completion")
        return None

    return PtcSession(tool=execute_code, execute_code=execute_code, close=close, before_model=before_model)
