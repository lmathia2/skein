"""A small persistent CPython worker.

The child owns only the Python namespace. All intended workspace effects cross the
pipe and are performed by the parent-owned broker. The source guard is deliberately
defense-in-depth, not a security sandbox. The supported boundary is a trusted local
workspace; OS isolation is an optional outer deployment concern.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import io
import multiprocessing
import pickle
import platform
import queue
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from types import SimpleNamespace
from typing import Any, Literal, Protocol
from uuid import uuid4


class ReplBroker(Protocol):
    """Parent-side operations exposed to programs through ``agent``."""

    def read(self, path: str, offset: int = 1, limit: int = 400) -> Any: ...

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> Any: ...

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> Any: ...

    def bash(self, command: str, timeout_seconds: int = 120) -> Any: ...

    def call(self, capability: str, arguments: dict[str, Any]) -> Any: ...

    def parallel(self, operations: list[dict[str, Any]]) -> Any: ...


@dataclass(frozen=True, slots=True)
class PythonExecutionResult:
    status: Literal["ok", "error", "timeout"]
    stdout: str = ""
    stderr: str = ""
    value_repr: str | None = None
    display_data: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    failure_stage: Literal["parse", "source_validation", "execution", "transport"] | None = None
    error_line: int | None = None
    error_source: str | None = None
    traceback: tuple[str, ...] = ()
    duration_ms: int = 0
    effect_unknown: bool = False
    output_truncated: bool = False
    state_count: int = 0
    state_delta: tuple[str, ...] = ()
    state_manifest: tuple[dict[str, Any], ...] = ()
    state_preserved: bool = False


class _BoundedText(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        remaining = self._limit - self._size
        if remaining > 0:
            kept = text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            self._parts.append(kept)
            self._size += len(kept.encode("utf-8"))
        if len(text.encode("utf-8")) > remaining:
            self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._parts)


_BLOCKED_MODULES = frozenset(
    {
        "asyncio",
        "builtins",
        "ctypes",
        "ftplib",
        "http",
        "importlib",
        "io",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "signal",
        "socket",
        "subprocess",
        "sys",
        "telnetlib",
        "urllib",
    }
)
_BLOCKED_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)


def _validate_source(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            error = PermissionError("dunder namespace access is blocked")
            error.lineno = node.lineno  # type: ignore[attr-defined]
            raise error
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            error = PermissionError("dunder attribute access is blocked")
            error.lineno = node.lineno  # type: ignore[attr-defined]
            raise error
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                if name.split(".", 1)[0] in _BLOCKED_MODULES:
                    error = PermissionError(
                        f"direct import of {name!r} is blocked; use agent capabilities"
                    )
                    error.lineno = node.lineno  # type: ignore[attr-defined]
                    raise error
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _BLOCKED_CALLS
        ):
            error = PermissionError(
                f"direct call to {node.func.id!r} is blocked; use agent capabilities"
            )
            error.lineno = node.lineno  # type: ignore[attr-defined]
            raise error


def _safe_builtins() -> dict[str, Any]:
    allowed = dict(vars(builtins))
    for name in _BLOCKED_CALLS:
        allowed.pop(name, None)

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".", 1)[0] in _BLOCKED_MODULES:
            raise PermissionError(f"direct import of {name!r} is blocked; use agent capabilities")
        return original_import(name, *args, **kwargs)

    allowed["__import__"] = guarded_import
    return allowed


class _RemoteOperation:
    def __init__(self, connection: Connection, operation: str) -> None:
        self._connection = connection
        self._operation = operation

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        request_id = uuid4().hex
        self._connection.send(
            {
                "type": "broker_call",
                "id": request_id,
                "operation": self._operation,
                "args": args,
                "kwargs": kwargs,
            }
        )
        response = self._connection.recv()
        if response.get("type") != "broker_result" or response.get("id") != request_id:
            raise RuntimeError("invalid broker response")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "broker call failed")))
        return response.get("result")


_PRELOADED_MODULES = ("json", "math", "re")
_RESERVED_NAMES = frozenset({"agent", *_PRELOADED_MODULES})
_AGENT_HELP = {
    "fs.read": "agent.fs.read(path, offset=1, limit=400)",
    "fs.write": ("agent.fs.write(path, content, expected_sha256=None, expected_absent=False)"),
    "fs.edit": "agent.fs.edit(path, old_text, new_text, expected_sha256=None)",
    "shell.run": "agent.shell.run(command, timeout_seconds=120)",
    "mcp.call": "agent.mcp.call(capability, arguments)",
    "parallel": "agent.parallel([{'operation': 'fs.read', 'arguments': {...}}, ...])",
    "state.list": "agent.state.list()",
    "state.describe": "agent.state.describe(name)",
}

_AGENT_RESULTS: dict[str, dict[str, object]] = {
    "fs.read": {
        "status": "ok|error|blocked",
        "data": {
            "path": "str",
            "text": "str (exact redacted selected range)",
            "offset": "int",
            "returned_lines": "int",
            "total_lines": "int",
            "complete": "bool",
            "next_offset": "int|null",
            "sha256": "str (original content identity)",
        },
    },
    "shell.run": {
        "status": "ok|error|blocked|timeout",
        "exit_code": "int|null",
        "data": {"stdout": "str", "stderr": "str"},
        "truncated": "bool",
        "artifact_uri": "str|null",
    },
    "fs.write": {"status": "ok|error|blocked", "changed_paths": "list[str]"},
    "fs.edit": {"status": "ok|error|blocked", "changed_paths": "list[str]"},
    "mcp.call": {"status": "capability-defined result mapping"},
    "parallel": {"status": "list[result] in input order", "allowed": ["fs.read"]},
}


def _agent_help(
    catalog: Mapping[str, Mapping[str, object]],
    prefix: str | None = None,
    *,
    details: bool = False,
) -> dict[str, str] | dict[str, dict[str, object]]:
    """Return bounded, deterministic capability signatures."""

    if prefix is not None and not isinstance(prefix, str):
        raise TypeError("help prefix must be a string or None")
    selected = {
        name: item
        for name, item in sorted(catalog.items())
        if prefix is None or name.startswith(prefix)
    }
    if not details:
        return {name: str(item.get("signature", item.get("description", ""))) for name, item in selected.items()}
    return {name: dict(item) for name, item in selected.items()}


def _binding_description(
    name: str,
    value: Any,
    metadata: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    value_type = type(value)
    item: dict[str, Any] = {
        "name": name,
        "type": value_type.__name__,
        "module": value_type.__module__,
        "cell_id": metadata.get(name, {}).get("cell_id", "unknown"),
        "replay": metadata.get(name, {}).get("replay", "transient"),
    }
    if value_type in {str, bytes, list, tuple, dict, set, frozenset}:
        item["size"] = len(value)
    return item


def _state_manifest(
    namespace: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    return [
        _binding_description(name, namespace[name], metadata)
        for name in sorted(namespace)
        if not name.startswith("__") and name not in _RESERVED_NAMES
    ]


def _snapshot_value(value: Any, seen: set[int], depth: int = 0) -> bool:
    if type(value) in {type(None), bool, int, float, str, bytes}:
        return True
    if depth >= 20 or type(value) not in {list, tuple, dict, set, frozenset}:
        return False
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    values = value.items() if type(value) is dict else value
    if type(value) is dict:
        valid = all(
            type(key) is str and _snapshot_value(item, seen, depth + 1)
            for key, item in values
        )
    else:
        valid = all(_snapshot_value(item, seen, depth + 1) for item in values)
    seen.remove(identity)
    return valid


def _snapshot_namespace(namespace: Mapping[str, Any], max_bytes: int) -> bytes:
    selected: dict[str, Any] = {}
    for name in sorted(namespace):
        if name.startswith("__") or name in _RESERVED_NAMES:
            continue
        value = namespace[name]
        if not _snapshot_value(value, set()):
            continue
        candidate = pickle.dumps({**selected, name: value}, protocol=5)
        if len(candidate) <= max_bytes:
            selected[name] = value
    return pickle.dumps(selected, protocol=5)


def _restore_snapshot(namespace: dict[str, Any], snapshot: bytes) -> None:
    for name in tuple(namespace):
        if not name.startswith("__") and name not in _RESERVED_NAMES:
            del namespace[name]
    namespace.update(pickle.loads(snapshot))


class _StateProxy:
    def __init__(
        self,
        namespace: Mapping[str, Any],
        metadata: Mapping[str, Mapping[str, str]],
    ) -> None:
        self._namespace = namespace
        self._metadata = metadata

    def list(self) -> list[dict[str, Any]]:
        """Return metadata for live bindings without exposing their values."""

        return _state_manifest(self._namespace, self._metadata)

    def describe(self, name: str) -> dict[str, Any]:
        """Describe one live binding without exposing its value."""

        if not isinstance(name, str) or name.startswith("__") or name in _RESERVED_NAMES:
            raise KeyError("unknown state binding")
        try:
            value = self._namespace[name]
        except KeyError as error:
            raise KeyError(f"unknown state binding: {name}") from error
        return _binding_description(name, value, self._metadata)


def _agent_proxy(
    connection: Connection,
    namespace: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, str]],
    help_catalog: Mapping[str, Mapping[str, object]],
) -> SimpleNamespace:
    return SimpleNamespace(
        help=lambda prefix=None, *, details=False: _agent_help(
            help_catalog, prefix, details=details
        ),
        parallel=_RemoteOperation(connection, "parallel"),
        fs=SimpleNamespace(
            read=_RemoteOperation(connection, "fs.read"),
            write=_RemoteOperation(connection, "fs.write"),
            edit=_RemoteOperation(connection, "fs.edit"),
        ),
        shell=SimpleNamespace(run=_RemoteOperation(connection, "shell.run")),
        mcp=SimpleNamespace(call=_RemoteOperation(connection, "mcp.call")),
        state=_StateProxy(namespace, metadata),
    )


def _bound_names(tree: ast.AST) -> set[str]:
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
    return names


def _execute_cell(
    code: str,
    namespace: dict[str, Any],
    *,
    max_output_bytes: int,
    cell_id: str = "unknown",
    replay_policy: str = "transient",
    state_metadata: dict[str, dict[str, str]] | None = None,
    state_recovery: str = "replay_safe",
    snapshot_max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    stdout = _BoundedText(max_output_bytes)
    stderr = _BoundedText(max_output_bytes)
    failure_stage: Literal["parse", "source_validation", "execution"] = "parse"
    snapshot = (
        _snapshot_namespace(namespace, snapshot_max_bytes)
        if state_recovery == "snapshot"
        else None
    )
    try:
        tree = ast.parse(code, filename="<agent-cell>", mode="exec")
        failure_stage = "source_validation"
        _validate_source(tree)
        failure_stage = "execution"
        touched_names = _bound_names(tree)
        final_expression: ast.expr | None = None
        if tree.body:
            last_statement = tree.body[-1]
            if isinstance(last_statement, ast.Expr):
                final_expression = last_statement.value
                tree.body.pop()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            if tree.body:
                exec(compile(tree, "<agent-cell>", "exec"), namespace)
            value = (
                eval(compile(ast.Expression(final_expression), "<agent-cell>", "eval"), namespace)
                if final_expression is not None
                else None
            )
        display_data = (
            value
            if isinstance(value, dict)
            and value
            and all(isinstance(key, str) and "/" in key for key in value)
            else None
        )
        value_repr = None if value is None or display_data is not None else repr(value)
        if value_repr is not None and len(value_repr.encode()) > max_output_bytes:
            value_repr = value_repr.encode()[:max_output_bytes].decode(errors="ignore")
            stdout.truncated = True
        metadata = state_metadata if state_metadata is not None else {}
        for name in touched_names:
            if name in namespace and not name.startswith("__") and name not in _RESERVED_NAMES:
                metadata[name] = {"cell_id": cell_id, "replay": replay_policy}
            else:
                metadata.pop(name, None)
        manifest = _state_manifest(namespace, metadata)
        return {
            "status": "ok",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "value_repr": value_repr,
            "display_data": display_data,
            "output_truncated": stdout.truncated or stderr.truncated,
            "state_count": len(manifest),
            "state_delta": sorted(touched_names),
            "state_manifest": manifest[:64],
        }
    except BaseException as error:
        state_preserved = snapshot is not None and failure_stage == "execution"
        if state_preserved:
            assert snapshot is not None
            _restore_snapshot(namespace, snapshot)
        error_line = getattr(error, "lineno", None) or next(
            (
                frame.lineno
                for frame in reversed(traceback.extract_tb(error.__traceback__))
                if frame.filename == "<agent-cell>"
            ),
            None,
        )
        lines = code.splitlines()
        return {
            "status": "error",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "failure_stage": failure_stage,
            "error_line": error_line,
            "error_source": (
                lines[error_line - 1].strip()
                if error_line is not None and 0 < error_line <= len(lines)
                else None
            ),
            "traceback": tuple(traceback.format_exception_only(error)),
            "state_preserved": state_preserved,
            "output_truncated": stdout.truncated or stderr.truncated,
        }


def _worker_main(
    connection: Connection,
    max_output_bytes: int,
    help_catalog: Mapping[str, Mapping[str, object]],
    state_recovery: str,
    snapshot_max_bytes: int,
) -> None:
    namespace: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "__agent_repl__",
    }
    state_metadata: dict[str, dict[str, str]] = {}
    for name in _PRELOADED_MODULES:
        namespace[name] = importlib.import_module(name)
    namespace["agent"] = _agent_proxy(connection, namespace, state_metadata, help_catalog)
    while True:
        try:
            request = connection.recv()
        except EOFError:
            return
        if request.get("type") == "close":
            return
        if request.get("type") != "execute":
            continue
        result = _execute_cell(
            str(request.get("code", "")),
            namespace,
            max_output_bytes=max_output_bytes,
            cell_id=str(request.get("cell_id", "unknown")),
            replay_policy=str(request.get("replay_policy", "transient")),
            state_metadata=state_metadata,
            state_recovery=state_recovery,
            snapshot_max_bytes=snapshot_max_bytes,
        )
        connection.send({"type": "execution_result", "id": request.get("id"), **result})


def default_help_catalog() -> dict[str, dict[str, object]]:
    """Return the deterministic built-in capability and kernel catalog."""

    catalog = {
        name: {
            "signature": signature,
            "result": _AGENT_RESULTS.get(name, {}),
        }
        for name, signature in _AGENT_HELP.items()
    }
    catalog["kernel"] = {
        "description": "Persistent CPython computation environment",
        "python": platform.python_version(),
        "preloaded_modules": list(_PRELOADED_MODULES),
    }
    return catalog


class PersistentPythonWorker:
    """Own one restartable CPython subprocess and its durable-in-process namespace."""

    def __init__(
        self,
        *,
        max_output_bytes: int = 64_000,
        help_catalog: Mapping[str, Mapping[str, object]] | None = None,
        state_recovery: str = "replay_safe",
        snapshot_max_bytes: int = 1_000_000,
    ) -> None:
        if max_output_bytes < 1_024:
            raise ValueError("max_output_bytes must be at least 1024")
        self.max_output_bytes = max_output_bytes
        self.help_catalog = {
            name: dict(item)
            for name, item in sorted((help_catalog or default_help_catalog()).items())
        }
        if state_recovery not in {"replay_safe", "snapshot"}:
            raise ValueError("state_recovery must be replay_safe or snapshot")
        self.state_recovery = state_recovery
        self.snapshot_max_bytes = snapshot_max_bytes
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None
        self._kernel_epoch: str | None = None
        self._lock = threading.Lock()

    def _start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._discard()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_worker_main,
            args=(
                child,
                self.max_output_bytes,
                self.help_catalog,
                self.state_recovery,
                self.snapshot_max_bytes,
            ),
            daemon=True,
            name="agent-cpython-worker",
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process
        self._kernel_epoch = uuid4().hex

    def _discard(self) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        self._kernel_epoch = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=1)

    @property
    def kernel_epoch(self) -> str:
        """Return the current worker lifetime, starting the worker when needed."""

        with self._lock:
            self._start()
            assert self._kernel_epoch is not None
            return self._kernel_epoch

    @staticmethod
    def _broker_operation(broker: ReplBroker, operation: str) -> Callable[..., Any]:
        names: Mapping[str, str] = {
            "fs.read": "read",
            "fs.write": "write",
            "fs.edit": "edit",
            "shell.run": "bash",
            "mcp.call": "call",
            "parallel": "parallel",
        }
        name = names.get(operation)
        if name is None:
            raise ValueError(f"unsupported broker operation: {operation}")
        candidate = getattr(broker, name, None)
        if not callable(candidate):
            raise TypeError(f"broker does not implement {name}")
        return candidate

    def execute(
        self,
        code: str,
        broker: ReplBroker,
        timeout_seconds: float = 120,
        *,
        cell_id: str = "unknown",
        replay_policy: str = "transient",
    ) -> PythonExecutionResult:
        if not isinstance(code, str):
            raise TypeError("code must be a string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        started = time.monotonic()
        deadline = started + timeout_seconds
        with self._lock:
            self._start()
            assert self._connection is not None
            request_id = uuid4().hex
            try:
                self._connection.send(
                    {
                        "type": "execute",
                        "id": request_id,
                        "code": code,
                        "cell_id": cell_id,
                        "replay_policy": replay_policy,
                    }
                )
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._connection.poll(min(remaining, 0.05)):
                        if remaining > 0:
                            continue
                        self._discard()
                        return PythonExecutionResult(
                            status="timeout",
                            error_type="TimeoutError",
                            error_message="Python execution timed out; worker state was discarded",
                            duration_ms=int((time.monotonic() - started) * 1_000),
                            effect_unknown=True,
                            failure_stage="execution",
                        )
                    response = self._connection.recv()
                    if response.get("type") == "broker_call":
                        replies: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

                        def invoke_broker(
                            request: dict[str, Any] = response,
                            output: queue.Queue[dict[str, Any]] = replies,
                        ) -> None:
                            try:
                                operation = self._broker_operation(
                                    broker, str(request.get("operation", ""))
                                )
                                result = operation(
                                    *tuple(request.get("args", ())),
                                    **dict(request.get("kwargs", {})),
                                )
                                output.put(
                                    {
                                        "type": "broker_result",
                                        "id": request.get("id"),
                                        "ok": True,
                                        "result": result,
                                    }
                                )
                            except BaseException as error:
                                output.put(
                                    {
                                        "type": "broker_result",
                                        "id": request.get("id"),
                                        "ok": False,
                                        "error": f"{type(error).__name__}: {error}",
                                    }
                                )

                        threading.Thread(
                            target=invoke_broker,
                            daemon=True,
                            name="agent-broker-call",
                        ).start()
                        try:
                            reply = replies.get(timeout=max(deadline - time.monotonic(), 0))
                        except queue.Empty:
                            self._discard()
                            return PythonExecutionResult(
                                status="timeout",
                                error_type="TimeoutError",
                                error_message=(
                                    "Python execution timed out during a broker call; "
                                    "worker state was discarded and reconciliation is required"
                                ),
                                duration_ms=int((time.monotonic() - started) * 1_000),
                                effect_unknown=True,
                                failure_stage="execution",
                            )
                        self._connection.send(reply)
                        continue
                    if (
                        response.get("type") != "execution_result"
                        or response.get("id") != request_id
                    ):
                        raise RuntimeError("invalid worker response")
                    return PythonExecutionResult(
                        status=response["status"],
                        stdout=response.get("stdout", ""),
                        stderr=response.get("stderr", ""),
                        value_repr=response.get("value_repr"),
                        display_data=response.get("display_data"),
                        error_type=response.get("error_type"),
                        error_message=response.get("error_message"),
                        failure_stage=response.get("failure_stage"),
                        error_line=response.get("error_line"),
                        error_source=response.get("error_source"),
                        traceback=tuple(response.get("traceback", ())),
                        duration_ms=int((time.monotonic() - started) * 1_000),
                        output_truncated=bool(response.get("output_truncated", False)),
                        state_count=int(response.get("state_count", 0)),
                        state_delta=tuple(str(name) for name in response.get("state_delta", ())),
                        state_manifest=tuple(response.get("state_manifest", ())),
                        state_preserved=bool(response.get("state_preserved", False)),
                    )
            except (EOFError, BrokenPipeError, OSError) as error:
                self._discard()
                return PythonExecutionResult(
                    status="error",
                    error_type=type(error).__name__,
                    error_message="Python worker exited unexpectedly; worker state was discarded",
                    duration_ms=int((time.monotonic() - started) * 1_000),
                    effect_unknown=True,
                    failure_stage="transport",
                )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None and self._process is not None:
                try:
                    self._connection.send({"type": "close"})
                    self._process.join(timeout=1)
                except (BrokenPipeError, OSError):
                    pass
            self._discard()

    def reset(self) -> None:
        """Discard the current namespace after an uncommitted cell failure."""

        with self._lock:
            self._discard()

    def __enter__(self) -> PersistentPythonWorker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = [
    "PersistentPythonWorker",
    "PythonExecutionResult",
    "ReplBroker",
    "default_help_catalog",
]
