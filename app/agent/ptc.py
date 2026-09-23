"""PTC session adapters with implementation-owned execution and persistence."""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import inspect
import json
import re
import shlex
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
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory.models import ReadEvidence
from harness.evidence.state import EventKind, EventStore
from harness.evidence.state.events import HarnessEvent
from harness.execution.approvals.waiting import ApprovalWaiter
from harness.execution.environment.async_call import run_managed_thread
from harness.execution.repo import build_repository_manifest
from harness.execution.safety.redaction import SecretRedactor
from harness.execution.sandbox import MANAGED_COMMAND_ENVIRONMENT
from harness.execution.tools.adk_adapter import AdkCodingTools, _ArtifactResolver
from harness.execution.tools.output import bound_output, compact_tool_result
from harness.ptc.notebook import (
    NotebookCell,
    externalize_mime_bundle,
    materialize_notebook,
    put_artifact,
    reduce_notebook,
)
from harness.ptc.repl import PersistentPythonWorker, default_help_catalog
from harness.ptc.repl.worker import READ_RESULT_RECIPE, project_live_binding
from harness.verification.contracts import is_reusable_validation_command

from .config import HarnessSettings
from .streaming import PublicReplies


def _line_ranges(lines: list[int]) -> list[list[int]]:
    """Return inclusive ranges for sorted line numbers."""
    ranges: list[list[int]] = []
    for line in lines:
        if ranges and ranges[-1][1] + 1 == line:
            ranges[-1][1] = line
        else:
            ranges.append([line, line])
    return ranges


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


def _read_reference(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    evidence = ReadEvidence.model_validate(payload["read_evidence"])
    uri = payload["result_artifact_uri"]
    if not isinstance(uri, str) or not re.fullmatch(r"artifact://sha256/[0-9a-f]{64}", uri):
        raise ValueError("completed read has an invalid artifact identity")
    coverage = payload["source_coverage"]
    if not isinstance(coverage, dict) or coverage != evidence.source_coverage(coverage.get("total_lines")):
        raise ValueError("completed read has inconsistent source coverage")
    return {"task_id": task_id, "operation_id": payload["operation_id"], "artifact_uri": uri,
            **evidence.model_dump(), "source_coverage": coverage}


def _completed_source_versions(
    events: list[HarnessEvent], task_id: str, source_sequence: int,
) -> tuple[dict[str, str], ...]:
    """Attest historical file versions from completed read receipts only."""
    versions: dict[tuple[str, str], dict[str, str]] = {}
    for event in events:
        if (event.task_id != task_id or event.sequence > source_sequence
                or event.kind != EventKind.CAPABILITY_COMPLETED
                or event.payload.get("operation") != "fs.read"
                or event.payload.get("status") != "ok"):
            continue
        reference = _read_reference(task_id, event.payload)
        key = (reference["path"], reference["sha256"])
        versions.setdefault(key, {
            "path": reference["path"], "sha256": reference["sha256"],
            "read_event_id": event.event_id,
        })
    return tuple(versions[key] for key in sorted(versions))


def _committed_value_summaries(
    values: dict[str, Any], manifest: tuple[dict[str, Any], ...], *,
    source_versions: tuple[dict[str, str], ...] = (), grounded: bool = False,
    max_bytes: int = 2400,
) -> dict[str, Any]:
    previous = {item.get("name"): item for item in manifest if isinstance(item, dict)}
    by_sha: dict[str, list[dict[str, str]]] = {}
    if grounded:
        for source in source_versions:
            by_sha.setdefault(source["sha256"], []).append(source)
    candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for name, value in sorted(values.items()):
        shape = ({"lines": len(value.splitlines()), "characters": len(value)} if type(value) is str else
                 {"keys": sorted(value)[:6], "items": len(value)} if type(value) is dict else
                 {"items": len(value)} if type(value) is list else {})
        description = str(previous.get(name, {}).get("description", ""))[:160]
        entry = {"name": name, "type": type(value).__name__, "shape": shape,
                 "description": description}
        refs = by_sha.get(hashlib.sha256(value.encode()).hexdigest(), []) if type(value) is str else []
        if refs:
            entry["historical_source_refs"] = refs[:2]
        if grounded:
            size = len(canonical_json(value).encode())
            priority = (0 if refs else 1 if type(value) is str and shape["lines"] >= 20
                        else 2 if description else 3, -size, name)
        else:
            priority = (0, 0, name)
        candidates.append((priority, entry))
    entries: list[dict[str, Any]] = []
    for _priority, entry in sorted(candidates, key=lambda item: item[0]):
        if len(canonical_json([*entries, entry]).encode()) > max_bytes:
            continue
        entries.append(entry)
    return {"program": "committed_value_summaries@2" if grounded else "committed_value_summaries@1",
            "bindings": entries,
            "omitted_count": len(values) - len(entries),
            "scope": "Previously committed plain values; historical data, not current source freshness or completion evidence."}


def _completed_attempt_reads(events: list[HarnessEvent], task_id: str, attempt_id: str) -> list[dict[str, Any]]:
    """Recover completed nested reads, never the failed cell's dirty bindings."""
    requests: dict[str, HarnessEvent] = {}
    completed: set[str] = set()
    reads = []
    for event in events:
        payload = event.payload
        if event.task_id != task_id or payload.get("attempt_id") != attempt_id:
            continue
        if payload.get("operation") != "fs.read":
            continue
        if event.kind not in {EventKind.CAPABILITY_REQUESTED, EventKind.CAPABILITY_COMPLETED}:
            continue
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("read receipt has no operation identity")
        if event.kind == EventKind.CAPABILITY_REQUESTED:
            if operation_id in requests:
                raise ValueError("duplicate read request identity")
            requests[operation_id] = event
        elif event.kind == EventKind.CAPABILITY_COMPLETED and payload.get("status") == "ok" and payload.get("read_evidence"):
            request = requests.get(operation_id)
            if operation_id in completed or request is None or request.sequence >= event.sequence or any(
                request.payload.get(key) != payload.get(key)
                for key in ("cell_id", "notebook_id", "arguments_sha256")
            ):
                raise ValueError("completed read has no matching request in this attempt")
            completed.add(operation_id)
            if payload.get("effect") not in {"none", "observed"}:
                continue
            reads.append({"source_event_id": event.event_id, "historical_read": _read_reference(task_id, payload)})
    return reads


def _state_updates(previous: list[dict[str, Any]], current: list[dict[str, Any]], max_bytes: int,
                   completed_reads: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Expose only changed supported descriptions; missing catalog entries aren't live."""
    def key(item: dict[str, Any]) -> str:
        return canonical_json([item.get("name"), item.get("selector", [])])

    before = {key(item): item for item in previous if item.get("description") or item.get("read_reference")}
    after = {key(item): item for item in current}
    before_order = {key(item): index for index, item in enumerate(previous)}
    updates = []
    for identity in sorted(set(before) | set(after), key=lambda identity: (
        not (identity in before and identity not in after),
        -before_order.get(identity, -1)
        if identity in before and identity not in after
        and before[identity].get("availability") == "live_retained_read" else 0,
        not bool((after.get(identity) or before.get(identity, {})).get("description")),
        identity,
    )):
        item = after.get(identity)
        if item == before.get(identity):
            continue
        if not item or not (item.get("description") or item.get("read_reference")):
            if identity not in before:
                continue
            old = before[identity]
            item = {"name": old["name"], "selector": old.get("selector", []), "availability": "association_invalidated"}
        item = project_live_binding(item)
        if len(updates) < 8 and len(canonical_json([*updates, item]).encode()) <= max_bytes:
            updates.append(item)
    # Reserve invalidation notices first; optional recovery must not displace them.
    if completed_reads:
        invalidated = [item for item in updates if item.get("availability") == "association_invalidated"]
        if invalidated:
            grouped = [{"availability": "association_invalidated", "bindings": [
                {"name": item["name"], "selector": item["selector"]} for item in invalidated
            ]}, *[item for item in updates if item not in invalidated]]
            if len(canonical_json(grouped).encode()) <= max_bytes:
                updates = grouped
        candidates = [*reversed(completed_reads), *[
            {"historical_read": before[key(item)]["read_reference"]}
            for item in invalidated if before[key(item)].get("read_reference")
        ]]
        addressed = set()
        for read in candidates:
            reference = read["historical_read"]
            uri = reference.get("artifact_uri")
            if not isinstance(uri, str) or not re.fullmatch(r"artifact://sha256/[0-9a-f]{64}", uri) or uri in addressed:
                continue
            recovered = {**read, "availability": "historical_read_only",
                         "recover_expression": f"agent.artifacts.load({uri!r})",
                         "recovery_kind": "historical_result_envelope_not_live_state"}
            if len(updates) < 8 and len(canonical_json([*updates, recovered]).encode()) <= max_bytes:
                updates.append(recovered)
                addressed.add(uri)
        return updates
    for index, item in enumerate(updates):
        if item.get("availability") != "association_invalidated":
            continue
        reference = before[key(item)].get("read_reference")
        uri = reference.get("artifact_uri") if isinstance(reference, dict) else None
        if not isinstance(uri, str) or not re.fullmatch(r"artifact://sha256/[0-9a-f]{64}", uri):
            continue  # Older/unaddressed descriptions cannot acquire a recovery handle.
        recovered = {**item, "historical_read": reference,
                     "recover_expression": f"agent.artifacts.load({uri!r})",
                     "recovery_kind": "historical_result_envelope_not_live_state"}
        proposed = [*updates[:index], recovered, *updates[index + 1:]]
        if len(canonical_json(proposed).encode()) <= max_bytes:
            updates = proposed
    return updates


def _state_update_notice(updates: list[dict[str, Any]], kernel_epoch: str, cell_id: str,
                         max_bytes: int, state_lost: bool) -> dict[str, Any]:
    historical = any(item.get("historical_read") for item in updates)
    notice = {"program": "ptc_state_updates@5", "kernel_epoch": kernel_epoch,
              "cell_id": cell_id, "entries": updates,
              "more": ("agent.artifacts.list(); agent.help('artifacts.load', details=True)"
                       if historical or state_lost else "agent.state.list(); agent.state.describe(name, preview=True)")}
    if historical:
        decoded = {**notice, "decode_completed_read": {
            "assign": "page = <entry.recover_expression>",
            "then_python": READ_RESULT_RECIPE,
            "contract": "page.data.text contains JSON bytes, NOT source text. Decode and compute in the same cell; do not print the wrapper. If source_text is None, handle non-ok status or finish exact byte paging (see help). saved_result retains original source coverage; recovery is not freshness or failed-cell completion.",
        }}
        if len(canonical_json(decoded).encode()) <= max_bytes:
            notice = decoded
    return notice


_STATE_UPDATES_PROGRAM_HASH = hashlib.sha256((READ_RESULT_RECIPE + "".join(inspect.getsource(function) for function in (
    _read_reference, _completed_attempt_reads, project_live_binding, _state_updates, _state_update_notice))).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PtcSession:
    tool: Any
    description: str
    execute_code: Callable[..., Awaitable[dict[str, Any]]] | None = None
    close: Callable[[], Awaitable[None] | None] | None = None
    before_model: Callable[[CallbackContext], Awaitable[LlmResponse | None]] | None = None
    after_agent: Callable[[CallbackContext], Awaitable[None]] | None = None
    kernel_status: Callable[[], dict[str, Any]] | None = None


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
    redactor: SecretRedactor,
    bounded_work_batches: bool = True,
) -> PtcSession:
    _runtime_identity = runtime_identity
    _require_verification = require_verification
    reserved_capabilities = {
        "code", "execute_code", "read", "write", "edit", "bash", "verify",
        "agent", "fs", "shell", "state", "artifacts", "mcp", "parallel", "help",
    }
    collisions = sorted(set(capability_handlers).intersection(reserved_capabilities))
    if collisions:
        raise ValueError(f"registered capability collides with PTC surface: {collisions}")
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
        "search": {
            "health": "search health",
            "grep": "search grep --pattern TEXT [--path PATH] [--mode literal|regex] [--case-sensitive] [--context 0..20] [--limit 1..50]",
            "find": "search find --pattern GLOB [--path PATH] [--limit 1..50]",
            "continue": "search grep|find --cursor CURSOR",
        },
        "commands": sorted(
            commands,
            key=lambda item: (item["kind"], item["command"], item["source"]),
        ),
    }
    python_worker = PersistentPythonWorker(
        max_output_bytes=active_ptc_config.max_output_bytes,
        help_catalog=help_catalog,
        state_recovery=(
            "snapshot" if active_ptc_config.state == "snapshot" else "replay_safe"
        ),
        snapshot_max_bytes=active_ptc_config.snapshot_max_bytes,
        capture_committed=active_ptc_config.recover_committed_values,
    )
    restored_kernel_epoch: str | None = None
    active_notebooks: dict[str, str] = {}
    batch_cells: dict[tuple[str, str], int] = {}
    batch_changed: set[tuple[str, str]] = set()
    retained_reads: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def notebook_events(task_id: str) -> list[HarnessEvent]:
        return [*prior_notebook_events, *active_event_store.read(task_id)]

    def task_artifact_uris(task_id: str) -> set[str]:
        def collect(value: Any) -> set[str]:
            if isinstance(value, dict):
                return set().union(*(collect(item) for item in value.values()), set())
            if isinstance(value, list):
                return set().union(*(collect(item) for item in value), set())
            if isinstance(value, str) and value.startswith("artifact://sha256/"):
                return {value}
            return set()

        return set().union(
            *(collect(event.payload) for event in active_event_store.read(task_id)), set()
        )

    class _RestoreBroker:
        @staticmethod
        def _blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise PermissionError("capabilities are disabled while restoring replay-safe cells")

        read = write = edit = bash = verify = call = parallel = _blocked
        artifacts_load = artifacts_list = artifacts_publish = _blocked

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
            kernel_epoch: str,
            event_loop: asyncio.AbstractEventLoop,
        ) -> None:
            self.task_id = task_id
            self.invocation_id = invocation_id
            self.notebook_id = notebook_id
            self.cell_id = cell_id
            self.attempt_id = attempt_id
            self.work_batch_id = work_batch_id
            self.kernel_epoch = kernel_epoch
            self.event_loop = event_loop
            self.call_index = 0
            self.operations: list[str] = []
            self.effects: list[str] = []
            self.artifact_refs: set[str] = set()
            self.model_artifact_refs: set[str] = set()

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
                "workspace_may_have_changed": operation not in {"fs.read", "artifacts.load", "artifacts.list", "artifacts.publish"},
            }
            if operation == "shell.run":
                common["command_sha256"] = hashlib.sha256(str(arguments.get("command", "")).encode()).hexdigest()
                try:
                    words = shlex.split(str(arguments.get("command", "")))
                except ValueError:
                    words = []
                common["discovery_kind"] = (
                    "search" if words[:1] in (["rg"], ["grep"], ["search"]) or words[:2] == ["git", "grep"]
                    else "memory" if words[:1] == ["memory"] else "unclassified_shell"
                )
                if common["discovery_kind"] == "memory":
                    # Reserved memory commands run in-process and cannot mutate
                    # the task workspace. Their request receipts must say so.
                    common["workspace_may_have_changed"] = False
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
            if common["operation"] == "shell.run":
                metadata = result.get("ui_details")
                if not isinstance(metadata, dict):
                    metadata = {}
                result = {**result, "result_kind": "managed" if metadata.get("memory") is True
                          or str(metadata.get("virtual_operation", "")).startswith("search.") else "process"}
            result = redactor.redact({
                **result,
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
            })
            assert isinstance(result, dict)
            result_bytes = (
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            ).encode()
            result_artifact_uri = (
                None
                if str(common["operation"]).startswith("artifacts.")
                else put_artifact(
                    settings.state_root / "artifacts" / "sha256",
                    result_bytes,
                )
            )
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
            if result.get("effect") in {"none", "observed", "changed", "unknown", "native_untracked"}:
                effect = result["effect"]
            refs = {
                str(value)
                for key in ("artifact_uri", "artifact_uris")
                for value in (
                    result.get(key, []) if isinstance(result.get(key), list) else [result.get(key)]
                )
                if isinstance(value, str) and value.startswith(("artifact://", "file://"))
            }
            self.model_artifact_refs.update(refs)
            if result_artifact_uri is not None:
                refs.add(result_artifact_uri)
            self.artifact_refs.update(refs)
            self.effects.append(effect)
            result_hash = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            metadata = result.get("ui_details") or {}
            payload = {
                **common,
                "status": status,
                "effect": effect,
                "result_hash": result_hash,
                "artifact_refs": sorted(refs),
                "truncated": bool(result.get("truncated")),
                "omitted_bytes": max(0, int(result.get("omitted_bytes", 0))),
                "changed_paths": sorted(set(result.get("changed_paths", [])))[:128],
                "content_hashes": dict(sorted(result.get("content_hashes", {}).items())[:128]),
                "workspace_may_have_changed": bool(common.get("workspace_may_have_changed")) and status != "blocked"
                and not (common["operation"] == "shell.run" and (metadata.get("memory") is True or
                         str(metadata.get("virtual_operation", "")).startswith("search."))),
            }
            receipt_id = result.get("receipt_id")
            if common["operation"] == "shell.run" and isinstance(receipt_id, str) and re.fullmatch(
                r"[0-9a-f]{64}", receipt_id
            ):
                # This is only an identity join. Memory independently validates
                # the canonical receipt before using its workspace fingerprints.
                payload["receipt_id"] = receipt_id
            if common["operation"] == "fs.read" and status == "ok":
                data = result.get("data", {})
                if isinstance(data, dict) and all(
                    key in data for key in ("path", "sha256", "offset", "returned_lines")
                ):
                    payload["read_evidence"] = {
                        key: data[key]
                        for key in ("path", "sha256", "offset", "returned_lines")
                    }
                    payload["source_coverage"] = ReadEvidence.model_validate(
                        payload["read_evidence"]).source_coverage(data.get("total_lines"))
                if isinstance(result.get("read_reuse"), dict):
                    payload["read_reuse"] = result["read_reuse"]
            if result_artifact_uri is not None:
                payload.update(
                    {
                        "result_artifact_uri": result_artifact_uri,
                        "result_media_type": "application/json",
                        "result_bytes": len(result_bytes),
                    }
                )
            active_event_store.append(
                self.task_id,
                kind,
                payload,
                idempotency_key=f"capability:{operation_id}:terminal",
            )
            if "read_evidence" in payload and result_artifact_uri is not None:
                result = {**result, "read_reference": _read_reference(self.task_id, payload)}
                data = result.get("data", {})
                scope = self.task_id if active_ptc_config.cross_epoch_read_reuse else self.kernel_epoch
                key = (scope, str(data.get("path", "")), str(data.get("sha256", "")))
                retained_reads.setdefault(key, []).append(result)
                while sum(map(len, retained_reads.values())) > 64:
                    oldest = next(iter(retained_reads))
                    retained_reads[oldest].pop(0)
                    if not retained_reads[oldest]:
                        retained_reads.pop(oldest)
            return result

        def _catalog_read(self, path: str, offset: int, limit: int) -> dict[str, Any]:
            """Reuse current-version captured lines; acquire only uncovered intervals."""
            if type(offset) is not int or type(limit) is not int or offset < 1 or not 1 <= limit <= 400:
                return active_tools.read(path=path, offset=offset, limit=limit)
            try:
                candidate = Path(path)
                candidate = candidate if candidate.is_absolute() else settings.workspace / candidate
                catalog_path = candidate.resolve(strict=False).relative_to(
                    settings.workspace.resolve()
                ).as_posix()
            except (OSError, ValueError):
                catalog_path = path
            scope = self.task_id if active_ptc_config.cross_epoch_read_reuse else self.kernel_epoch
            if not any(saved_scope == scope and saved_path == catalog_path
                       for saved_scope, saved_path, _digest in retained_reads):
                return active_tools.read(path=path, offset=offset, limit=limit)
            probe = active_tools.read(path=path, offset=1, limit=1)
            probe_data = probe.get("data", {}) if isinstance(probe, dict) else {}
            if probe.get("status") != "ok" or not all(
                isinstance(probe_data.get(key), expected)
                for key, expected in (("path", str), ("sha256", str), ("total_lines", int))
            ):
                return active_tools.read(path=path, offset=offset, limit=limit)
            key = (scope, probe_data["path"], probe_data["sha256"])
            prior = retained_reads.get(key, [])
            if not prior:
                return probe if offset == 1 and limit == 1 else active_tools.read(
                    path=path, offset=offset, limit=limit
                )

            total = probe_data["total_lines"]
            start = min(offset - 1, total) + 1
            end = min(start + limit, total + 1)
            lines: dict[int, str] = {}
            sources: dict[int, str] = {}
            for saved in prior:
                data = saved.get("data", {})
                reference = saved.get("read_reference", {})
                if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                    continue
                captured_start = data.get("offset")
                uri = reference.get("artifact_uri") if isinstance(reference, dict) else None
                if not isinstance(captured_start, int) or not isinstance(uri, str):
                    continue
                for index, text in enumerate(data["text"].splitlines(keepends=True)):
                    line = captured_start + index
                    if start <= line < end:
                        lines.setdefault(line, text)
                        sources.setdefault(line, uri)

            missing = [line for line in range(start, end) if line not in lines]
            ranges: list[tuple[int, int]] = []
            for line in missing:
                if ranges and ranges[-1][1] == line:
                    ranges[-1] = (ranges[-1][0], line + 1)
                else:
                    ranges.append((line, line + 1))
            fresh_results = [
                active_tools.read(path=path, offset=lo, limit=hi - lo) for lo, hi in ranges
            ]
            if any(result.get("status") != "ok" for result in fresh_results):
                return active_tools.read(path=path, offset=offset, limit=limit)
            for result in fresh_results:
                data = result.get("data", {})
                if (data.get("path") != key[1] or data.get("sha256") != key[2]
                        or not isinstance(data.get("offset"), int)
                        or not isinstance(data.get("text"), str)):
                    return active_tools.read(path=path, offset=offset, limit=limit)
                for index, text in enumerate(data["text"].splitlines(keepends=True)):
                    lines[data["offset"] + index] = text
            if any(line not in lines for line in range(start, end)):
                return active_tools.read(path=path, offset=offset, limit=limit)

            reused_lines = [line for line in range(start, end) if line in sources]
            reused_uris = sorted({sources[line] for line in reused_lines})
            retained_reference = next((saved.get("read_reference") for saved in reversed(prior)
                if isinstance(saved.get("data"), dict)
                and saved["data"].get("offset") == start
                and saved["data"].get("returned_lines") == end - start
                and isinstance(saved.get("read_reference"), dict)), None) if not missing else None
            reuse = {
                "reused_lines": len(reused_lines),
                "source_read_lines": len(missing),
                "identity_probe_lines": int(total > 0),
                "reused_ranges": _line_ranges(reused_lines),
                "source_read_ranges": _line_ranges(missing),
                "reused_artifact_uris": reused_uris,
            }
            fresh_text = "\n".join(
                str(result.get("model_text", "")) for result in fresh_results if result.get("model_text")
            )
            guidance = (
                f"{key[1]} lines {start}-{max(start, end - 1)}: reused {len(reused_lines)} "
                f"captured line(s), read {len(missing)} new line(s). "
                "Use the retained result for analysis; do not print already captured source. "
                "Resolve its returned read_handle with agent.state.cite(...) when citing a finding."
            )
            return {
                "status": "ok",
                "model_text": "\n\n".join(part for part in (guidance, fresh_text) if part),
                "data": {
                    "path": key[1], "text": "".join(lines[line] for line in range(start, end)),
                    "offset": start, "returned_lines": end - start, "total_lines": total,
                    "complete": start == 1 and end == total + 1,
                    "next_offset": end if end < total + 1 else None, "sha256": key[2],
                },
                "content_hashes": {key[1]: key[2]},
                "ui_details": {"path": key[1], "total_lines": total, "read_reuse": reuse},
                "read_reuse": reuse,
                "effect": "observed",
                **({"retained_read_reference": retained_reference}
                   if retained_reference is not None else {}),
            }

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
                return [{
                    "status": "error", "data": {}, "error_code": "invalid_arguments",
                    "model_text": f"Split this batch into at most {active_ptc_config.max_parallel_reads} "
                    "reads per agent.parallel call. No reads executed.",
                } for _ in operations]
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
                return self._catalog_read(
                    arguments["path"], arguments.get("offset", 1),
                    arguments.get("limit", read_default_lines),
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
                lambda: self._catalog_read(path, offset, limit),
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

        def verify(self, command: str, timeout_seconds: int = bash_default_timeout) -> dict[str, Any]:
            result = self.bash(f"bash -o pipefail -c {shlex.quote(command)}", timeout_seconds)
            active_event_store.append(self.task_id, "execution.ptc_verification", {
                "status": result.get("status"),
                "operation_id": f"{self.attempt_id}:{self.call_index}",
            })
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

        def artifacts_load(
            self, uri: str, offset: int = 0, limit: int = active_ptc_config.max_output_bytes
        ) -> dict[str, Any]:
            def invoke() -> dict[str, Any]:
                # Only explicit admission rejects are known no-effect outcomes.
                # Resolver/integrity and publication exceptions remain fail-closed.
                invalid = None
                if not isinstance(uri, str) or not re.fullmatch(r"artifact://sha256/[0-9a-f]{64}", uri):
                    invalid = "invalid content-addressed artifact URI; copy an exact URI from agent.artifacts.list()"
                elif type(offset) is not int or offset < 0:
                    invalid = "artifact offset must be a non-negative integer"
                elif type(limit) is not int or not 1 <= limit <= active_ptc_config.max_output_bytes:
                    invalid = f"artifact limit must be between 1 and {active_ptc_config.max_output_bytes}"
                if invalid:
                    return {"status": "error", "effect": "none", "error_code": "invalid_arguments",
                            "model_text": f"ValueError: {invalid}. No artifact was loaded."}
                if uri not in task_artifact_uris(self.task_id):
                    return {"status": "blocked", "effect": "none", "error_code": "artifact_not_authorized",
                            "model_text": "PermissionError: artifact is not referenced by this task. "
                            "Use agent.artifacts.list() and copy an exact authorized URI; no artifact was loaded."}
                content = _ArtifactResolver(
                    workspace=settings.workspace, state_root=settings.state_root,
                )._read_content(uri, max_source_bytes=16_000_000)
                # Check the complete payload before paging/encoding: neither a byte
                # boundary nor base64 may bypass model-visible secret redaction.
                decoded = content.decode("utf-8", errors="surrogateescape")
                if redactor.redact_text(decoded) != decoded:
                    return {"status": "blocked", "effect": "none", "error_code": "artifact_requires_redaction",
                            "model_text": "PermissionError: artifact requires redaction; exact recovery is unavailable"}
                selected = content[offset : offset + limit]
                try:
                    representation = {"encoding": "utf-8", "text": selected.decode("utf-8")}
                except UnicodeDecodeError:
                    # Preserve arbitrary bytes and split UTF-8 code points exactly;
                    # callers combine decoded pages before interpreting the document.
                    representation = {"encoding": "base64", "text": None,
                                      "base64": base64.b64encode(selected).decode("ascii")}
                return {
                    "status": "ok",
                    "data": {
                        "uri": uri,
                        **representation,
                        "offset": offset,
                        "returned_bytes": len(selected),
                        "total_bytes": len(content),
                        "complete": offset + len(selected) >= len(content),
                        "next_offset": (
                            None if offset + len(selected) >= len(content) else offset + len(selected)
                        ),
                    },
                    "model_text": f"loaded {len(selected)} of {len(content)} artifact bytes",
                    "artifact_uri": uri,
                }

            return self._call(
                "artifacts.load", {"uri": uri, "offset": offset, "limit": limit}, invoke
            )

        def artifacts_list(self) -> dict[str, Any]:
            def invoke() -> dict[str, Any]:
                published = {
                    str(event.payload.get("artifact_uri")): {
                        "name": event.payload.get("name"),
                        "description": event.payload.get("description"),
                        "published": True,
                    }
                    for event in active_event_store.read(self.task_id)
                    if event.kind == EventKind.ARTIFACT_PUBLISHED
                }
                items = [
                    {"uri": uri, **published.get(uri, {"published": False})}
                    for uri in sorted(task_artifact_uris(self.task_id))
                ]
                return {
                    "status": "ok",
                    "data": {"artifacts": items},
                    "model_text": f"{len(items)} task artifacts",
                }

            return self._call("artifacts.list", {}, invoke)

        def artifacts_publish(
            self, value: Any, name: str, description: str | None = None
        ) -> dict[str, Any]:
            def invoke() -> dict[str, Any]:
                normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name)).strip("._-")
                if not normalized or len(normalized) > 128:
                    return {"status": "error", "effect": "none", "error_code": "invalid_arguments",
                            "model_text": "ValueError: artifact name must normalize to 1-128 safe characters; nothing published"}
                if description is not None and len(str(description)) > 500:
                    return {"status": "error", "effect": "none", "error_code": "invalid_arguments",
                            "model_text": "ValueError: artifact description must be at most 500 characters; nothing published"}
                safe_value = redactor.redact(value)
                if isinstance(safe_value, bytes):
                    content, media_type = safe_value, "application/octet-stream"
                elif isinstance(safe_value, str):
                    content, media_type = safe_value.encode(), "text/plain; charset=utf-8"
                else:
                    content = (
                        json.dumps(
                            safe_value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        )
                        + "\n"
                    ).encode()
                    media_type = "application/json"
                uri = put_artifact(settings.state_root / "artifacts" / "sha256", content)
                active_event_store.append(
                    self.task_id,
                    EventKind.ARTIFACT_PUBLISHED,
                    {
                        "artifact_uri": uri,
                        "name": normalized,
                        "description": str(description) if description is not None else None,
                        "media_type": media_type,
                        "byte_size": len(content),
                        "host_visible": True,
                        "operation_id": f"{self.attempt_id}:{self.call_index}",
                    },
                    idempotency_key=f"artifact-published:{self.attempt_id}:{self.call_index}",
                )
                return {
                    "status": "ok",
                    "data": {"uri": uri, "name": normalized, "media_type": media_type},
                    "model_text": f"published artifact {normalized!r}: {uri}",
                    "artifact_uri": uri,
                }

            return self._call(
                "artifacts.publish",
                {
                    "name": str(name),
                    "description": str(description) if description is not None else None,
                },
                invoke,
            )

        @property
        def effect(self) -> str:
            if "unknown" in self.effects:
                return "unknown"
            if "changed" in self.effects:
                return "changed"
            return "observed" if "observed" in self.effects else "none"

    async def _restore_committed_checkpoint(
        task_id: str, notebook_id: str, kernel_epoch: str, *, timing: str,
    ) -> dict[str, Any] | None:
        """Restore one attested successful-cell plain checkpoint, never failed-cell state."""
        if not active_ptc_config.recover_committed_values or python_worker is None:
            return None
        checkpoint = next((event for event in reversed(notebook_events(task_id))
                           if event.kind == EventKind.REPL_STATE_CHECKPOINTED), None)
        if checkpoint is None or not checkpoint.payload.get("available"):
            return None
        identity = checkpoint.payload
        source = next((event for event in notebook_events(task_id)
                       if event.event_id == identity.get("source_event_id")), None)
        if (checkpoint.task_id != task_id or identity.get("notebook_id") != notebook_id
                or identity.get("grounded_bindings", False) != active_ptc_config.ground_committed_bindings
                or source is None or source.kind != EventKind.REPL_CELL_COMPLETED
                or source.sequence != identity.get("source_sequence")
                or source.payload.get("cell_id") != identity.get("source_cell_id")
                or source.sequence >= checkpoint.sequence):
            raise ValueError("committed-value checkpoint identity mismatch")
        digest = identity.get("checkpoint_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("committed-value checkpoint hash is invalid")
        content = (settings.state_root / "ptc-committed" / "sha256" / digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest or len(content) != identity.get("bytes"):
            raise ValueError("committed-value checkpoint content mismatch")
        captured = json.loads(content)
        if (not isinstance(captured, dict) or captured.get("schema") != "ptc-committed-plain-v1"
                or captured.get("task_id") != task_id or captured.get("notebook_id") != notebook_id
                or captured.get("source_event_id") != source.event_id
                or captured.get("source_cell_id") != source.payload.get("cell_id")
                or captured.get("source_sequence") != source.sequence
                or not isinstance(captured.get("values"), dict)
                or sorted(captured["values"]) != identity.get("selected_names")
                or captured.get("omitted_names") != identity.get("omitted_names")):
            raise ValueError("committed-value checkpoint captured history mismatch")
        values = captured["values"]
        source_versions = (_completed_source_versions(
            notebook_events(task_id), task_id, source.sequence)
            if active_ptc_config.ground_committed_bindings else ())
        summaries = _committed_value_summaries(
            values, tuple(source.payload.get("state", {}).get("manifest", [])),
            source_versions=source_versions,
            grounded=active_ptc_config.ground_committed_bindings)
        if summaries != identity.get("binding_summaries"):
            raise ValueError("committed-value checkpoint binding description mismatch")
        if identity.get("binding_program_hash") != hashlib.sha256(
            inspect.getsource(_committed_value_summaries).encode()).hexdigest():
            raise ValueError("committed-value checkpoint binding program mismatch")
        manifest = await run_managed_thread(python_worker.restore_plain, values, identity["source_cell_id"])
        if (any(not isinstance(item.get("name"), str) for item in manifest)
                or sorted(str(item["name"]) for item in manifest) != sorted(values)):
            raise ValueError("worker restored an incomplete committed-value manifest")
        by_name = {item["name"]: item for item in summaries["bindings"]}
        restored_manifest = [
            {**item, "description": by_name.get(item.get("name"), {}).get("description") or
             canonical_json(by_name.get(item.get("name"), {}).get("shape", {})),
             "historical_source_refs": by_name.get(item.get("name"), {}).get("historical_source_refs", []),
             "freshness": "historical_checkpoint", "availability": "live_plain_historical"}
            for item in manifest
        ]
        active_event_store.append(
            task_id, EventKind.REPL_STATE_RESTORED,
            {
                "projection_notebook_id": notebook_id, "kernel_epoch": kernel_epoch,
                "recovery_timing": timing,
                "restored_cell_ids": [identity["source_cell_id"]],
                "checkpoint_sha256": digest, "checkpoint_source_event_id": source.event_id,
                "omitted_names": identity["omitted_names"], "restored_names": sorted(values),
                "summary_omitted_names": sorted(set(values) - set(by_name)),
                "state": {"manifest": restored_manifest, "count": len(values)},
                "scope": "Previously committed plain values; historical, not source freshness or completion evidence.",
            },
            idempotency_key=f"repl-restore:{kernel_epoch}",
        )
        return {
            "kernel_epoch": kernel_epoch, "recovery_timing": timing,
            "bindings": summaries["bindings"], "restored_count": len(values),
            "summary_omitted_count": summaries["omitted_count"],
            "omitted_names": identity["omitted_names"],
            "scope": "Historical plain values only; current source version and completion still require independent evidence.",
        }

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
            restored_cells: list[str] = []
            recovery = await _restore_committed_checkpoint(
                task_id, notebook_id, kernel_epoch, timing="before_cell")
            if recovery is None:
                previous = reduce_notebook(notebook_events(task_id), notebook_id)
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
                        {"projection_notebook_id": notebook_id, "kernel_epoch": kernel_epoch,
                         "restored_cell_ids": restored_cells},
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
            previous_source_hash = next((event.payload.get("source_sha256") for event in notebook_events(task_id)
                                         if event.kind == EventKind.REPL_CELL_SUBMITTED and
                                         event.payload.get("attempt_id") == attempt_id), None)
            if (previous_source_hash or hashlib.sha256(previous_attempt.source.encode()).hexdigest()) != hashlib.sha256(code.encode()).hexdigest():
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
        retained_source = redactor.redact_text(code)
        replay_policy = _replay_policy(code) if retained_source == code else "never"
        cell_payload = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "source": retained_source,
            "source_sha256": hashlib.sha256(code.encode()).hexdigest(),
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
            kernel_epoch=kernel_epoch,
            event_loop=asyncio.get_running_loop(),
        )
        result = await run_managed_thread(
            worker.execute,
            code,
            broker,
            timeout_seconds,
            cell_id=cell_id,
            replay_policy=replay_policy,
            shell_timeout_margin=30 if not bounded_work_batches else None,
        )
        if active_ptc_config.recover_committed_values and result.status == "ok" and result.checkpoint_values is None:
            raise ValueError("successful worker cell omitted its committed plain-value checkpoint")
        state_preserved = result.state_preserved or result.failure_stage in {
            "parse",
            "source_validation",
        }
        execution_started = False if result.failure_stage in {"parse", "source_validation"} else (
            True if result.status == "ok" or result.failure_stage == "execution" else None
        )
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
        stdout = redactor.redact_text(result.stdout)
        stderr = redactor.redact_text(result.stderr)
        full_stdout = (
            redactor.redact_text(result.full_stdout) if result.full_stdout is not None else None
        )
        full_stderr = (
            redactor.redact_text(result.full_stderr) if result.full_stderr is not None else None
        )
        output_artifacts: list[dict[str, Any]] = []
        for stream, complete in (
            ("stdout", full_stdout),
            ("stderr", full_stderr),
        ):
            if complete is None:
                continue
            content = complete.encode()
            uri = put_artifact(settings.state_root / "artifacts" / "sha256", content)
            broker.artifact_refs.add(uri)
            broker.model_artifact_refs.add(uri)
            output_artifacts.append(
                {
                    "stream": stream,
                    "artifact_uri": uri,
                    "byte_size": len(content),
                    "media_type": "text/plain; charset=utf-8",
                }
            )
        display_data = redactor.redact(result.display_data) if result.display_data is not None else None
        if display_data is not None:
            display_data, display_refs = externalize_mime_bundle(
                display_data,
                artifact_root=settings.state_root / "artifacts" / "sha256",
                max_inline_bytes=active_ptc_config.max_output_bytes,
            )
            broker.artifact_refs.update(display_refs)
            broker.model_artifact_refs.update(display_refs)
        terminal_payload: dict[str, Any] = {
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "work_batch_id": work_batch_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "stdout": stdout,
            "stderr": stderr,
            "artifact_refs": sorted(broker.artifact_refs),
            "output_artifacts": output_artifacts,
            "capability_count": broker.call_index,
            "capability_operations": list(broker.operations),
            "state": {
                "count": result.state_count,
                "delta": list(result.state_delta),
                "manifest": redactor.redact(list(result.state_manifest)),
                "retained_read_uses": list(result.retained_read_uses),
            },
        }
        if display_data is not None:
            terminal_payload["display"] = display_data
        elif result.value_repr is not None:
            terminal_payload["display"] = {"text/plain": redactor.redact_text(result.value_repr)}
        state_events = active_event_store.read(task_id)
        prior_state_event = next((event for event in reversed(state_events)
                                  if event.kind == EventKind.REPL_CELL_COMPLETED
                                  and event.payload.get("kernel_epoch") == kernel_epoch), None)
        prior_state = prior_state_event.payload.get("state", {}).get("manifest", []) if prior_state_event else []
        current_state = terminal_payload["state"]["manifest"]
        if result.failure_stage in {"parse", "source_validation"}:
            current_state = prior_state
        elif result.status != "ok" and not state_preserved:
            current_state = []
        completed_reads = (_completed_attempt_reads(state_events, task_id, attempt_id)
                           if result.status != "ok" and execution_started is not False else [])
        updates = _state_updates(prior_state, current_state,
                                 min(2048, active_ptc_config.max_output_bytes // 4), completed_reads)
        state_notice = _state_update_notice(updates, kernel_epoch, cell_id,
                                            active_ptc_config.max_output_bytes // 2,
                                            result.status != "ok" and not state_preserved)
        terminal_payload["state_updates"] = updates
        terminal_payload["state_updates_view"] = {
            "program": "ptc_state_updates@5", "program_hash": _STATE_UPDATES_PROGRAM_HASH,
            "source_watermark": state_events[-1].sequence if state_events else 0,
            "prior_state_event_id": prior_state_event.event_id if prior_state_event else None,
            "current_state_policy": "prior" if execution_started is False else
                                    "empty" if result.status != "ok" and not state_preserved else "manifest",
            "completed_read_event_ids": [read["source_event_id"] for read in completed_reads],
            "input_hash": hashlib.sha256(canonical_json([prior_state, current_state, completed_reads]).encode()).hexdigest(),
            "content_hash": hashlib.sha256(canonical_json(updates).encode()).hexdigest(),
            "notice_hash": hashlib.sha256(canonical_json(state_notice).encode()).hexdigest(),
            "recipe_notice_max_bytes": active_ptc_config.max_output_bytes // 2,
            "max_bytes": min(2048, active_ptc_config.max_output_bytes // 4),
        }
        terminal_payload["execution_started"] = execution_started
        if result.error_type is not None:
            terminal_payload["exception"] = redactor.redact({
                "ename": result.error_type,
                "evalue": result.error_message or "",
                "traceback": list(result.traceback),
                "stage": result.failure_stage,
                "line": result.error_line,
                "source": result.error_source,
                "state_preserved": state_preserved,
            })
        terminal_event = active_event_store.append(
            task_id,
            terminal_kind,
            terminal_payload,
            idempotency_key=f"repl-cell:{attempt_id}:terminal",
        )
        if active_ptc_config.recover_committed_values and result.status == "ok":
            values = result.checkpoint_values or {}
            omitted_names = list(result.checkpoint_omitted_names)
            captured = {
                "schema": "ptc-committed-plain-v1", "task_id": task_id,
                "notebook_id": notebook_id, "source_event_id": terminal_event.event_id,
                "source_cell_id": cell_id, "source_sequence": terminal_event.sequence,
                "values": values, "omitted_names": omitted_names,
            }
            content = canonical_json(captured).encode()
            available = effect != "unknown" and len(content) <= active_ptc_config.snapshot_max_bytes
            checkpoint_payload: dict[str, Any] = {
                "notebook_id": notebook_id, "source_event_id": terminal_event.event_id,
                "source_cell_id": cell_id, "source_sequence": terminal_event.sequence,
                "kernel_epoch": kernel_epoch, "available": available,
                "grounded_bindings": active_ptc_config.ground_committed_bindings,
                "selected_names": sorted(values) if available else [],
                "omitted_names": omitted_names if available else sorted(set([*omitted_names, *values])),
            }
            if available:
                uri = put_artifact(settings.state_root / "ptc-committed" / "sha256", content)
                source_versions = (_completed_source_versions(
                    notebook_events(task_id), task_id, terminal_event.sequence)
                    if active_ptc_config.ground_committed_bindings else ())
                checkpoint_payload.update({
                    "checkpoint_sha256": uri.removeprefix("artifact://sha256/"),
                    "bytes": len(content),
                    "binding_summaries": _committed_value_summaries(
                        values, tuple(terminal_payload["state"]["manifest"]),
                        source_versions=source_versions,
                        grounded=active_ptc_config.ground_committed_bindings),
                    "binding_program_hash": hashlib.sha256(
                        inspect.getsource(_committed_value_summaries).encode()).hexdigest(),
                })
            else:
                checkpoint_payload["reason"] = "unknown_effect" if effect == "unknown" else "checkpoint_budget"
            active_event_store.append(
                task_id, EventKind.REPL_STATE_CHECKPOINTED, checkpoint_payload,
                idempotency_key=f"repl-checkpoint:{attempt_id}",
            )
        post_failure_recovery: dict[str, Any] | None = None
        latest_committed_checkpoint = next((event for event in reversed(notebook_events(task_id))
                                            if event.kind == EventKind.REPL_STATE_CHECKPOINTED), None)
        if (active_ptc_config.eager_committed_restore and result.status == "error"
                and not state_preserved and effect in {"none", "observed"}
                and latest_committed_checkpoint is not None
                and latest_committed_checkpoint.payload.get("available")):
            new_epoch = await asyncio.to_thread(lambda: worker.kernel_epoch)
            post_failure_recovery = await _restore_committed_checkpoint(
                task_id, notebook_id, new_epoch, timing="after_failed_cell")
            if post_failure_recovery is not None:
                restored_kernel_epoch = new_epoch
        batch_key = (task_id, work_batch_id)
        batch_cells[batch_key] = batch_cells.get(batch_key, 0) + 1
        if (
            tool_context is not None
            and tool_context.state.get("task_phase") == "review"
            and batch_cells[batch_key] >= active_ptc_config.review_cells_per_batch
        ):
            tool_context.state["ptc_host_yield_pending"] = True
        if effect == "changed":
            batch_changed.add(batch_key)
            if tool_context is not None and tool_context.state.get("task_phase") in {"understand", "plan"}:
                active_event_store.append(
                    task_id, EventKind.LEDGER_PATCHED,
                    {"set_fields": {"phase": "implement"}},
                    idempotency_key=f"implementation-phase:{work_batch_id}",
                )
                tool_context.state["task_phase"] = "implement"
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
        visible = redactor.redact_text("\n".join(
            part
            for part in (
                full_stdout or stdout,
                full_stderr or stderr,
                result.value_repr,
                (
                    f"{result.error_type}: {result.error_message}"
                    if result.error_type is not None
                    else None
                ),
            )
            if part
        ))
        if execution_started is False:
            visible = (
                "Cell rejected before execution: no lines, assignments, or capability calls ran. "
                "Existing bindings are unchanged; a same-name value is NOT the result of this rejected cell. "
                "Fix and resubmit the intended operation before using its result.\n" + visible
            )
        elif result.status != "ok" and not state_preserved:
            if post_failure_recovery is not None:
                visible = (
                    "The failed cell's partial assignments were discarded, but previously committed plain "
                    "bindings are already restored in the live worker before this response. Use the exact "
                    "restored names below in your next cell instead of rereading a whole unchanged file. "
                    "For a historical source ref, first read only offset=1 limit=1 and compare the current "
                    "whole-file SHA; reacquire changed source. Restored values do not reconcile unknown effects "
                    "or prove completion.\nRestored checkpoint: " + canonical_json(post_failure_recovery)
                    + "\nOriginal error: " + visible
                )
            else:
                visible = (
                    "Live bindings were discarded. Recover applicable completed read artifacts below; "
                    "they do not restore a failed calculation, prove current freshness, or reconcile unknown effects. "
                    "Do not replay effectful cells to rebuild variables.\n" + visible
                )
        visible = redactor.redact_text(visible)
        bounded = bound_output(
            visible,
            max_chars=active_ptc_config.max_output_bytes,
            max_lines=400 if bounded_work_batches else 2000,
        )
        model_text = bounded.text
        displaced_bytes = 0
        if output_artifacts:
            refs = ", ".join(item["artifact_uri"] for item in output_artifacts)
            notice = f"\n[complete output artifacts: {refs}]"
            preview = bound_output(
                visible,
                max_chars=max(1, active_ptc_config.max_output_bytes - len(notice)),
                max_lines=400 if bounded_work_batches else 2000,
            )
            model_text = preview.text + notice
            bounded = preview
        if updates and active_ptc_config.emit_state_updates:
            notice = "\nState updates (advisory; sources historical):\n" + canonical_json(state_notice)
            available = max(0, active_ptc_config.max_output_bytes - len(notice.encode()))
            if len(model_text.encode()) > available:
                uri = put_artifact(settings.state_root / "artifacts" / "sha256", visible.encode())
                broker.model_artifact_refs.add(uri)
                active_event_store.append(task_id, EventKind.TOOL_ARTIFACT_RECORDED, {
                    "tool_name": "code", "artifact_uri": uri, "content_hash": uri.rsplit("/", 1)[-1],
                }, idempotency_key=f"selected-output:{attempt_id}")
                notice += f"\n[complete selected output: {uri}]"
                available = max(0, active_ptc_config.max_output_bytes - len(notice.encode()))
            bounded = bound_output(model_text, max_chars=max(1, available), max_lines=396)
            selected = bounded.text.encode()[:available].decode(errors="ignore")
            displaced_bytes = max(0, len(model_text.encode()) - len(selected.encode()))
            model_text = selected + notice
        return {
            "status": result.status,
            "model_text": model_text,
            "notebook_id": notebook_id,
            "cell_id": cell_id,
            "attempt_id": attempt_id,
            "kernel_epoch": kernel_epoch,
            "effect": effect,
            "notebook_path": str(notebook_path),
            "notebook_sha256": notebook_hash,
            "artifact_uris": sorted(broker.model_artifact_refs),
            "output_artifacts": output_artifacts,
            "duration_ms": result.duration_ms,
            "truncated": result.output_truncated or bounded.truncated or bool(displaced_bytes),
            "omitted_bytes": max(bounded.omitted_bytes, displaced_bytes),
            "state_count": result.state_count,
            "state_delta": list(result.state_delta),
            "failure_stage": result.failure_stage,
            "execution_started": execution_started,
            "error_type": result.error_type,
            "error_line": result.error_line,
            "error_source": redactor.redact_text(result.error_source) if result.error_source else None,
            "state_preserved": state_preserved,
            "kernel": worker.kernel_status(),
        }

    async def execute_code(
        code: str,
        timeout_seconds: int = active_ptc_config.default_timeout_seconds,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        if tool_context is not None and tool_context.state.get("review_decision_pending"):
            tool_context.state["review_decision_rejections"] = int(
                tool_context.state.get("review_decision_rejections", 0) or 0) + 1
            active_event_store.append(
                str(tool_context.state.get("task_id") or settings.task_id_override or "unscoped"),
                EventKind.TOOL_CALL_REJECTED,
                {"reason": "bounded_review_decision_requires_structured_response",
                 "work_batch_id": str(tool_context.state.get("ptc_work_batch_id", "")),
                 "effect": "none"},
                idempotency_key=f"review-decision-tool-rejected:{tool_context.state.get('ptc_work_batch_id')}:{tool_context.state['review_decision_rejections']}",
            )
            return {"status": "blocked", "error": "The bounded review cell is already complete. "
                    "Do not call another tool in this decision batch. Return status verify with "
                    "completed claims, or status continue with the concrete defect and next targeted correction."}
        result = await _execute_code(code, timeout_seconds, tool_context)
        compact = compact_tool_result(
            result,
            max_chars=active_ptc_config.max_output_bytes,
            max_lines=400 if bounded_work_batches else 2000,
        )
        for key in (
            "failure_stage",
            "execution_started",
            "error_type",
            "error_line",
            "error_source",
            "state_preserved",
            "kernel",
        ):
            if key == "execution_started" and result.get("status") == "ok":
                continue  # Successful completion already establishes this; keep normal replies compact.
            if result.get(key) is not None:
                compact[key] = result[key]
        model_text = str(compact.get("model_text", ""))
        task_id = str(
            (tool_context.state.get("task_id") if tool_context is not None else None)
            or settings.task_id_override
            or "unscoped"
        )
        active_event_store.append(
            task_id,
            EventKind.RECOVERY_BOUNDARY,
            {
                "phase": "after_effect",
                "source_attempt_id": compact.get("attempt_id"),
                "status": compact.get("status"),
                "observed_effect": compact.get("effect", "none"),
                "kernel_epoch": compact.get("kernel", {}).get("epoch")
                if isinstance(compact.get("kernel"), dict)
                else None,
            },
        )
        active_event_store.append(
            task_id,
            EventKind.TOOL_PROJECTION_CREATED,
            {
                "projection_version": "ptc-v4.1-output@1",
                "source_attempt_id": compact.get("attempt_id"),
                "result_hash": compact.get("result_hash"),
                "model_visible_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
                "model_visible_bytes": len(model_text.encode()),
                "omitted_bytes": int(compact.get("omitted_bytes", 0)),
                "artifact_uris": compact.get("artifact_uris", []),
            },
        )
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
        if callback_context is not None and bounded_work_batches:
            state = callback_context.state
            if (state.get("review_decision_pending") and
                    int(state.get("review_decision_rejections", 0) or 0) > 1):
                structured = {"status": "blocked", "message": "", "progress": [],
                              "next_action": "Bounded review decision was not supplied.",
                              "decisions": [],
                              "questions": ["The model attempted another tool after its bounded review cell; "
                              "a fresh structured verify/continue/block decision is required."],
                              "discovered_constraints": [], "files_in_focus": [],
                              "completion_claims": []}
                return LlmResponse(content=types.Content(role="model", parts=[
                    types.Part.from_text(text=json.dumps(structured))]),
                    turn_complete=True, custom_metadata={"skein_work_batch_yield": True})
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
                if (
                    state.get("task_phase") == "review"
                    and count >= active_ptc_config.review_cells_per_batch
                ):
                    reason = "review_cell_limit"
                elif count >= active_ptc_config.max_cells_per_batch:
                    reason = "max_cells"
                elif (
                    count >= active_ptc_config.no_progress_cells_per_batch
                    and batch_key not in batch_changed
                ):
                    reason = "no_workspace_change"
                if reason is not None:
                    state["ptc_host_yield_pending"] = False
                    payload = {
                        "work_batch_id": work_batch_id,
                        "cell_count": count,
                        "reason": reason,
                        "workspace_changed": batch_key in batch_changed,
                        "task_phase": str(state.get("task_phase", "")),
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
        kernel_status=python_worker.kernel_status,
    )
