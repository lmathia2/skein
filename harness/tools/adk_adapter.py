"""ADK four-tool adapter: approvals, redaction, bounded output, and replay receipts."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from harness.approvals import ApprovalRequest, ApprovalStore
from harness.environment import LocalWorkspaceEnvironment, WorkspaceEnvironment
from harness.environment.runtime import LocalRepositoryRuntime
from harness.models import ToolEnvelope
from harness.repo import FffSearchService, SearchBackend, SearchError, SearchPage
from harness.safety import ApprovalAction, ApprovalPolicy, SecretRedactor
from harness.sandbox import (
    CommandSandbox,
    DockerSandbox,
    LocalSandbox,
    SandboxRequest,
    create_command_sandbox,
)
from harness.state import ToolReceipt, ToolReceiptStore
from harness.tools.coding import execute_edit, execute_read, execute_write
from harness.tools.output import bound_output
from harness.tools.search_command import (
    SearchCommand,
    SearchCommandParseError,
    parse_search_command,
)

_CONTENT_ARTIFACT_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.[A-Za-z0-9]{1,12}$")
_COMMAND_ARTIFACT_NAME = re.compile(r"^command-(?P<digest>[0-9a-f]{64})\.log$")
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_ARTIFACT_OUTPUT_CHARS = 31_000
_MAX_SEARCH_OUTPUT_CHARS = 12_000
_MAX_SEARCH_OUTPUT_LINES = 200

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdkCodingTools:
    read: Callable[..., dict[str, Any]]
    bash: Callable[..., dict[str, Any]]
    edit: Callable[..., dict[str, Any]]
    write: Callable[..., dict[str, Any]]


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("SKEIN_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "skein" / digest
    root.mkdir(parents=True, exist_ok=True)
    return root


def discover_known_secrets(additional_names: Sequence[str] = ()) -> list[str]:
    """Return sensitive environment values without exposing their names or values."""

    explicit_names = {
        name.strip()
        for name in os.getenv("SKEIN_REDACT_ENV_VARS", "").split(",")
        if name.strip()
    }
    explicit_names.update(additional_names)
    for name in os.environ:
        upper = name.upper()
        if any(
            marker in upper
            for marker in ("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN", "CLIENT_SECRET", "PASSWORD")
        ) or upper.endswith(("_TOKEN", "_SECRET", "_PRIVATE_KEY")):
            explicit_names.add(name)
    return [
        value for name in sorted(explicit_names) if (value := os.getenv(name)) and len(value) >= 4
    ]


def _canonical_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"status": "ok", "model_text": str(value)}


class _ArtifactResolver:
    """Resolve only harness-owned content-addressed artifacts for managed read."""

    def __init__(self, *, workspace: Path, state_root: Path) -> None:
        self.workspace_artifact_root = (workspace / ".artifacts" / "tool-output").resolve()
        self.command_artifact_root = (state_root / "artifacts" / "commands").resolve()

    @staticmethod
    def _confined_file(root: Path, candidate: Path) -> Path:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError("artifact URI is outside managed artifact roots") from exc
        if not resolved.is_file():
            raise ValueError("artifact URI does not identify a regular file")
        return resolved

    def _target(self, uri: str) -> tuple[Path, str]:
        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise ValueError("invalid artifact URI") from exc
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("artifact URI cannot contain credentials, query, or fragment")

        if parsed.scheme == "artifact":
            if parsed.netloc != "tool-output":
                raise ValueError("unsupported artifact collection")
            match = _CONTENT_ARTIFACT_NAME.fullmatch(parsed.path.removeprefix("/"))
            if match is None or parsed.path.count("/") != 1:
                raise ValueError("artifact URI must use a content-addressed filename")
            target = self._confined_file(
                self.workspace_artifact_root,
                self.workspace_artifact_root / parsed.path.removeprefix("/"),
            )
            return target, match.group("digest")

        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("file artifact URI must be local")
            decoded = unquote(parsed.path)
            if not decoded.startswith("/") or any(ord(char) < 32 for char in decoded):
                raise ValueError("file artifact URI must contain a safe absolute path")
            candidate = Path(decoded)
            match = _COMMAND_ARTIFACT_NAME.fullmatch(candidate.name)
            if match is None:
                raise ValueError("file artifact URI must use a command content hash")
            target = self._confined_file(self.command_artifact_root, candidate)
            return target, match.group("digest")

        raise ValueError("unsupported artifact URI scheme")

    def read(
        self, uri: str, *, offset: int, limit: int,
        max_source_bytes: int = _MAX_ARTIFACT_BYTES, deadline: float | None = None,
    ) -> dict[str, Any]:
        if offset < 1:
            raise ValueError("offset must be at least 1")
        if limit < 1 or limit > 400:
            raise ValueError("limit must be between 1 and 400 lines")
        target, expected_digest = self._target(uri)
        maximum = min(max_source_bytes, _MAX_ARTIFACT_BYTES)
        if maximum < 1 or target.stat().st_size > maximum:
            raise ValueError("artifact exceeds the managed read byte limit")
        chunks: list[bytes] = []
        size = 0
        with target.open("rb") as stream:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("artifact read deadline exceeded")
                chunk = stream.read(min(65_536, maximum + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ValueError("artifact exceeds the managed read byte limit")
                chunks.append(chunk)
        content = b"".join(chunks)
        size = len(content)
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("artifact content hash does not match its URI")
        if b"\x00" in content[:8_192]:
            raise ValueError("binary artifacts cannot be read as text")

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = min(offset - 1, len(lines))
        selected = lines[start : start + limit]
        rendered = "\n".join(
            f"{start + index + 1:>6} | {line}" for index, line in enumerate(selected)
        )
        bounded = bound_output(
            rendered,
            max_chars=_MAX_ARTIFACT_OUTPUT_CHARS,
            max_lines=400,
        )
        has_more = start + len(selected) < len(lines)
        header = (
            f"{uri}\nsha256: {actual_digest}\n"
            f"lines: {start + 1}-{start + len(selected)} of {len(lines)}"
        )
        if has_more:
            header += f"\n[more available: read offset={start + len(selected) + 1}]"
        selected_bytes = len("\n".join(selected).encode("utf-8", errors="replace"))
        omitted_bytes = max(0, size - selected_bytes) + min(
            selected_bytes,
            bounded.omitted_bytes,
        )
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("artifact rendering deadline exceeded")
        return {
            "status": "ok",
            "model_text": f"{header}\n\n{bounded.text}",
            "truncated": has_more or bounded.truncated,
            "omitted_bytes": omitted_bytes,
            "artifact_uri": uri,
            "content_hashes": {uri: actual_digest},
            "ui_details": {"artifact": True, "total_lines": len(lines)},
        }


class _ManagedTools:
    def __init__(
        self,
        workspace: Path,
        *,
        environment: WorkspaceEnvironment | None = None,
        state_root: Path | None = None,
        sandbox: CommandSandbox | None = None,
        search_backend: SearchBackend | None = None,
        search_mode: str | None = None,
        policy: ApprovalPolicy | None = None,
        known_secrets: Sequence[str] | None = None,
        task_scope: str | None = None,
        bash_max_timeout_seconds: int = 600,
        search_default_page_size: int = 20,
        search_max_page_size: int = 50,
        receipt_sink: Callable[[ToolReceipt], object] | None = None,
        approval_sink: Callable[[ApprovalRequest], object] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.environment = environment or LocalWorkspaceEnvironment(self.workspace)
        state_root = (
            state_root.expanduser().resolve()
            if state_root is not None
            else _state_root(self.workspace)
        )
        state_root.mkdir(parents=True, exist_ok=True)
        self.state_root = state_root
        resolved_secrets = (
            list(known_secrets) if known_secrets is not None else discover_known_secrets()
        )
        self.sandbox = sandbox or create_command_sandbox(
            self.workspace,
            state_root,
            known_secrets=resolved_secrets,
        )
        self.receipts = ToolReceiptStore(
            state_root / "managed-tools.db", on_save=receipt_sink
        )
        self.approvals = ApprovalStore(
            state_root / "approvals.db", on_change=approval_sink
        )
        self.redactor = SecretRedactor(known_secrets=resolved_secrets)
        self.artifacts = _ArtifactResolver(
            workspace=self.workspace,
            state_root=state_root,
        )
        approved = {
            item.strip()
            for item in os.getenv("SKEIN_APPROVED_COMMAND_FINGERPRINTS", "").split(",")
            if item.strip()
        }
        self.policy = policy or ApprovalPolicy(
            allow_dependency_install=_truthy("SKEIN_ALLOW_DEPENDENCY_INSTALL"),
            allow_network=_truthy("SKEIN_ALLOW_NETWORK"),
            allow_git_history_mutation=_truthy("SKEIN_ALLOW_GIT_MUTATION"),
            allow_unknown=_truthy("SKEIN_ALLOW_UNKNOWN_COMMANDS"),
            approved_fingerprints=approved,
        )
        self.task_scope = (
            task_scope
            or os.getenv("SKEIN_TASK_ID")
            or hashlib.sha256(self.workspace.as_posix().encode()).hexdigest()[:24]
        )
        self.bash_max_timeout_seconds = bash_max_timeout_seconds
        self.search_default_page_size = search_default_page_size
        self.search_max_page_size = search_max_page_size
        configured_search = (
            (
                search_mode
                if search_mode is not None
                else os.getenv("SKEIN_SEARCH_BACKEND", "auto")
            )
            .strip()
            .lower()
        )
        if configured_search not in {"auto", "disabled", "fff"}:
            raise ValueError("SKEIN_SEARCH_BACKEND must be auto, fff, or disabled")
        self.search_backend = search_backend
        self.search_unavailable_reason: str | None = None
        if search_backend is None and configured_search != "disabled":
            if isinstance(self.sandbox, (LocalSandbox, DockerSandbox)):
                self.search_backend = FffSearchService(self.workspace, state_root)
            else:
                self.search_unavailable_reason = (
                    "host-side FFF search is unavailable for a non-authoritative remote workspace"
                )
        elif search_backend is None:
            self.search_unavailable_reason = "FFF search is disabled by configuration"

    def _file_tool(
        self,
        operation: Callable[..., ToolEnvelope],
        **arguments: Any,
    ) -> ToolEnvelope:
        return operation(self.environment, **arguments)

    def _redact(self, value: Any) -> dict[str, Any]:
        normalized = _normalize_result(value)
        return self.redactor.redact(normalized)

    def _post_write_diagnostic(
        self,
        path: str,
        content: str | None = None,
    ) -> dict[str, str] | None:
        suffix = Path(path).suffix.lower()
        if suffix not in {".py", ".json"}:
            return None
        if content is None:
            encoded = self.environment.read_bytes(path)
            if len(encoded) > 2_000_000:
                return {
                    "check": "syntax",
                    "status": "skipped",
                    "message": "post-write syntax check skipped for file larger than 2 MB",
                }
            content = encoded.decode("utf-8")
        try:
            if suffix == ".py":
                ast.parse(content, filename=path)
                language = "Python"
            else:
                json.loads(content)
                language = "JSON"
        except (SyntaxError, json.JSONDecodeError) as error:
            line = getattr(error, "lineno", None)
            column = getattr(error, "offset", None) or getattr(error, "colno", None)
            location = (
                f" at line {line}, column {column}"
                if line is not None and column is not None
                else ""
            )
            return {
                "check": "syntax",
                "status": "error",
                "message": f"{type(error).__name__}{location}: {error.msg}",
            }
        return {
            "check": "syntax",
            "status": "ok",
            "message": f"{language} syntax is valid",
        }

    def _with_post_write_diagnostic(
        self,
        path: str,
        result: Any,
        *,
        content: str | None = None,
    ) -> Any:
        normalized = _normalize_result(result)
        if normalized.get("status") != "ok":
            return normalized
        try:
            diagnostic = self._post_write_diagnostic(path, content)
        except (OSError, UnicodeError, ValueError) as error:
            diagnostic = {
                "check": "syntax",
                "status": "skipped",
                "message": f"post-write syntax check unavailable: {type(error).__name__}",
            }
        if diagnostic is None:
            return normalized
        normalized["diagnostics"] = [diagnostic]
        normalized["model_text"] = (
            str(normalized.get("model_text", "")) + "\n\npost-write check: " + diagnostic["message"]
        ).strip()
        ui_details = dict(normalized.get("ui_details") or {})
        ui_details["post_write_check"] = diagnostic["status"]
        normalized["ui_details"] = ui_details
        return normalized

    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        if path.startswith(("artifact://", "file://")):
            return self._redact(self.artifacts.read(path, offset=offset, limit=limit))
        if "://" in path:
            raise ValueError("unsupported read URI scheme")
        return self._redact(self._file_tool(execute_read, path=path, offset=offset, limit=limit))

    def _persist_search_output(self, content: str) -> str:
        encoded = content.encode("utf-8", errors="replace")
        digest = hashlib.sha256(encoded).hexdigest()
        directory = self.workspace / ".artifacts" / "tool-output"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.txt"
        if not target.exists():
            target.write_bytes(encoded)
        return f"artifact://tool-output/{target.name}"

    def _search_result(self, page: SearchPage) -> dict[str, Any]:
        safe_text = self.redactor.redact_text(page.text)
        bounded = bound_output(
            safe_text,
            max_chars=_MAX_SEARCH_OUTPUT_CHARS,
            max_lines=_MAX_SEARCH_OUTPUT_LINES,
        )
        artifact_uri = None
        if bounded.truncated:
            try:
                artifact_uri = self._persist_search_output(safe_text)
            except OSError:
                LOGGER.exception("could not persist the bounded FFF output artifact")
        model_text = bounded.text
        if artifact_uri:
            model_text += f"\n\n[Full redacted page: {artifact_uri}]"
        return {
            "status": "ok",
            "model_text": model_text,
            "truncated": bounded.truncated,
            "omitted_bytes": bounded.omitted_bytes,
            "artifact_uri": artifact_uri,
            "next_cursor": page.cursor,
            "ui_details": {
                "virtual_operation": f"search.{page.operation}",
                "backend": "fff-search/0.10.5",
                "query_hash": page.query_hash,
                "cursor_available": page.cursor is not None,
                "returned_matches": page.returned_matches,
                "collected_matches": page.collected_matches,
                "matched_files": page.matched_files,
                "incomplete": page.incomplete,
                "cold_index": page.cold_index,
                "duration_ms": page.duration_ms,
            },
        }

    def _run_search(self, command: SearchCommand) -> dict[str, Any]:
        backend = self.search_backend
        if backend is None:
            return {
                "status": "error",
                "model_text": self.search_unavailable_reason or "FFF search is unavailable",
                "ui_details": {
                    "virtual_operation": f"search.{command.operation}",
                    "backend": "unavailable",
                },
            }
        try:
            if command.operation == "health":
                health = self.redactor.redact(dict(backend.health()))
                return {
                    "status": "ok",
                    "model_text": json.dumps(health, sort_keys=True),
                    "ui_details": {
                        "virtual_operation": "search.health",
                        "backend": health.get("backend", "fff"),
                    },
                }
            if command.operation == "grep":
                page = backend.grep(
                    pattern=command.pattern,
                    path=command.path,
                    mode=command.mode,
                    case_sensitive=command.case_sensitive,
                    context=command.context,
                    limit=command.limit,
                    cursor=command.cursor,
                )
            else:
                page = backend.find(
                    pattern=command.pattern,
                    path=command.path,
                    limit=command.limit,
                    cursor=command.cursor,
                )
        except (SearchError, ValueError) as exc:
            return {
                "status": "error",
                "model_text": self.redactor.redact_text(str(exc)),
                "ui_details": {
                    "virtual_operation": f"search.{command.operation}",
                    "backend": "fff-search/0.10.5",
                },
            }
        except Exception as exc:
            LOGGER.exception("FFF virtual search failed unexpectedly")
            return {
                "status": "error",
                "model_text": "FFF search failed unexpectedly; use a bounded rg query",
                "ui_details": {
                    "virtual_operation": f"search.{command.operation}",
                    "backend": "fff-search/0.10.5",
                    "error": type(exc).__name__,
                },
            }
        return self._search_result(page)

    def bash(
        self,
        command: str,
        timeout_seconds: int = 120,
        *,
        task_scope: str | None = None,
        invocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= timeout_seconds <= self.bash_max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds must be between 1 and {self.bash_max_timeout_seconds}"
            )
        try:
            search_command = parse_search_command(
                command,
                default_limit=self.search_default_page_size,
                max_limit=self.search_max_page_size,
            )
        except SearchCommandParseError as exc:
            return {
                "status": "error",
                "model_text": f"Invalid virtual search command: {exc}",
                "ui_details": {
                    "virtual_operation": "search.invalid",
                    "backend": "not-dispatched",
                },
            }
        if search_command is not None:
            return self._run_search(search_command)
        fingerprint = _canonical_hash("bash", {"command": command})
        active_scope = task_scope or self.task_scope
        persisted = self.approvals.for_fingerprint(active_scope, fingerprint)
        policy = self.policy
        if persisted and persisted.status == "approved":
            # Never cache task-scoped or expiring decisions in the shared policy.
            policy = replace(
                policy, approved_fingerprints=policy.approved_fingerprints | {fingerprint}
            )
        elif persisted and persisted.status in {"denied", "expired"}:
            return {
                "status": "blocked",
                "model_text": self.redactor.redact_text(
                    f"Command not executed: approval {persisted.status} by "
                    f"{persisted.decided_by or 'reviewer'}."
                ),
                "approval_required": False,
                "approval_request_id": persisted.request_id,
                "risk": persisted.risk,
            }
        decision = policy.decide(
            command,
            fingerprint=fingerprint,
            workspace=self.workspace,
        )
        if decision.action != ApprovalAction.ALLOW:
            request_id: str | None = None
            if decision.action == ApprovalAction.REQUIRE_APPROVAL:
                request = self.approvals.request(
                    task_id=active_scope,
                    fingerprint=fingerprint,
                    operation=self.redactor.redact_text(command),
                    risk=decision.risk.value,
                    reason=decision.reason,
                )
                request_id = request.request_id
            return {
                "status": "blocked",
                "model_text": (
                    f"Command not executed ({decision.risk.value}): {decision.reason}. "
                    f"Approval fingerprint: {fingerprint}"
                ),
                "approval_required": decision.action == ApprovalAction.REQUIRE_APPROVAL,
                "approval_request_id": request_id,
                "risk": decision.risk.value,
            }
        def execute() -> dict[str, Any]:
            return self._redact(self.sandbox.execute(
                SandboxRequest(
                    command=command,
                    timeout_seconds=timeout_seconds,
                )
            ).to_tool_result())

        if operation_id is None:
            return execute()
        return self._mutate(
            "bash", {"command": command, "timeout_seconds": timeout_seconds}, execute,
            task_scope=task_scope, invocation_id=invocation_id, operation_id=operation_id,
        )

    def _mutate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        operation: Callable[[], Any],
        *,
        task_scope: str | None = None,
        invocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        active_scope = task_scope or self.task_scope
        arguments_hash = _canonical_hash(tool_name, arguments)
        tool_call_id = (
            hashlib.sha256(f"{invocation_id}\0{operation_id}".encode()).hexdigest()
            if operation_id is not None else arguments_hash[:32]
        )
        try:
            receipt = self.receipts.begin(
                task_id=active_scope,
                invocation_id=invocation_id or "content-addressed",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                side_effect_key=tool_call_id if operation_id is not None else arguments_hash,
                claim=operation_id is not None,
                workspace_before=(
                    LocalRepositoryRuntime(self.workspace).fingerprint()
                    if operation_id is not None else None
                ),
            )
        except RuntimeError as error:
            return {"status": "blocked", "model_text": str(error),
                    "reconciliation_required": True, "receipt_id": tool_call_id}
        if receipt.status == "completed":
            if receipt.result_json is not None:
                return {**json.loads(receipt.result_json), "replayed": True,
                        "receipt_id": tool_call_id}
            return {
                "status": "ok",
                "model_text": f"{tool_name} already completed for this exact content",
                "replayed": True,
                "result_hash": receipt.result_hash,
                "artifact_uri": receipt.artifact_uri,
            }
        try:
            result = self._redact(operation())
        except Exception as exc:
            self.receipts.finish(
                task_id=active_scope,
                tool_call_id=tool_call_id,
                status="failed",
                error=self.redactor.redact_text(str(exc)),
            )
            raise
        result_hash = hashlib.sha256(
            json.dumps(result, sort_keys=True, default=str).encode()
        ).hexdigest()
        self.receipts.finish(
            task_id=active_scope,
            tool_call_id=tool_call_id,
            status="completed" if result.get("status") == "ok" else "failed",
            result_hash=result_hash,
            artifact_uri=result.get("artifact_uri"),
            result_json=json.dumps(result, sort_keys=True, default=str),
            workspace_after=(
                LocalRepositoryRuntime(self.workspace).fingerprint()
                if operation_id is not None else None
            ),
        )
        result["receipt_id"] = tool_call_id
        if result.get("status") == "ok" and self.search_backend is not None:
            try:
                self.search_backend.refresh()
            except Exception:
                LOGGER.exception("FFF refresh failed after a verified mutation")
        return result

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
        *,
        task_scope: str | None = None,
        invocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        arguments = {
            "path": path,
            "old_text_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
            "new_text_sha256": hashlib.sha256(new_text.encode()).hexdigest(),
            "expected_sha256": expected_sha256,
        }
        return self._mutate(
            "edit",
            arguments,
            lambda: self._with_post_write_diagnostic(
                path,
                self._file_tool(
                    execute_edit,
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                    expected_sha256=expected_sha256,
                ),
            ),
            task_scope=task_scope,
            invocation_id=invocation_id,
            operation_id=operation_id,
        )

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
        *,
        task_scope: str | None = None,
        invocation_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        arguments = {
            "path": path,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "expected_sha256": expected_sha256,
            "expected_absent": expected_absent,
        }
        return self._mutate(
            "write",
            arguments,
            lambda: self._with_post_write_diagnostic(
                path,
                self._file_tool(
                    execute_write,
                    path=path,
                    content=content,
                    expected_sha256=expected_sha256,
                    expected_absent=expected_absent,
                ),
                content=content,
            ),
            task_scope=task_scope,
            invocation_id=invocation_id,
            operation_id=operation_id,
        )


def create_adk_tools(
    workspace: Path,
    *,
    environment: WorkspaceEnvironment | None = None,
    state_root: Path | None = None,
    sandbox: CommandSandbox | None = None,
    search_backend: SearchBackend | None = None,
    search_mode: str | None = None,
    policy: ApprovalPolicy | None = None,
    known_secrets: Sequence[str] | None = None,
    task_scope: str | None = None,
    bash_max_timeout_seconds: int = 600,
    search_default_page_size: int = 20,
    search_max_page_size: int = 50,
    receipt_sink: Callable[[ToolReceipt], object] | None = None,
    approval_sink: Callable[[ApprovalRequest], object] | None = None,
) -> AdkCodingTools:
    managed = _ManagedTools(
        workspace,
        environment=environment,
        state_root=state_root,
        sandbox=sandbox,
        search_backend=search_backend,
        search_mode=search_mode,
        policy=policy,
        known_secrets=known_secrets,
        task_scope=task_scope,
        bash_max_timeout_seconds=bash_max_timeout_seconds,
        search_default_page_size=search_default_page_size,
        search_max_page_size=search_max_page_size,
        receipt_sink=receipt_sink,
        approval_sink=approval_sink,
    )
    return AdkCodingTools(
        read=managed.read,
        bash=managed.bash,
        edit=managed.edit,
        write=managed.write,
    )


__all__ = ["AdkCodingTools", "create_adk_tools", "discover_known_secrets"]
