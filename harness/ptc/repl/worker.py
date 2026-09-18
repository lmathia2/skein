"""A small persistent CPython worker.

The child owns only the Python namespace. All intended workspace effects cross the
pipe and are performed by the parent-owned broker. The source guard is deliberately
defense-in-depth, not a security sandbox. The supported boundary is a trusted local
workspace; OS isolation is an optional outer deployment concern.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib
import io
import json
import multiprocessing
import pickle
import platform
import queue
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from itertools import islice
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

    def artifacts_load(self, uri: str, offset: int = 0, limit: int = 16_000) -> Any: ...

    def artifacts_list(self) -> Any: ...

    def artifacts_publish(
        self, value: Any, name: str, description: str | None = None
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PythonExecutionResult:
    status: Literal["ok", "error", "timeout"]
    stdout: str = ""
    stderr: str = ""
    full_stdout: str | None = None
    full_stderr: str | None = None
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
        self._full_parts: list[str] = []
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        self._full_parts.append(text)
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

    def full_value(self) -> str | None:
        return "".join(self._full_parts) if self.truncated else None


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
    def __init__(self, connection: Connection, operation: str, state: _StateProxy | None = None) -> None:
        self._connection = connection
        self._operation = operation
        self._state = state

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
        result = response.get("result")
        if self._state is not None:
            for item in result if self._operation == "parallel" and type(result) is list else [result]:
                self._state.register_read(item)
        return result


_PRELOADED_MODULES = ("json", "math", "re")
_RESERVED_NAMES = frozenset({"agent", *_PRELOADED_MODULES})
_MAX_HELP_BYTES = 16_000

# JSON is preloaded. This example decodes only one complete UTF-8 artifact page;
# it does not turn a partially captured source into a whole-file observation.
READ_RESULT_RECIPE = """saved_result = source_text = None
if page.get('status') == 'ok':
    data = page['data']
    if data['offset'] == 0 and data['complete'] and data['encoding'] == 'utf-8':
        saved = json.loads(data['text'])
        if isinstance(saved, dict) and saved.get('status') == 'ok':
            saved_result, source_text = saved, saved['data']['text']"""
_AGENT_HELP = {
    "fs.read": "agent.fs.read(path, offset=1, limit=400)",
    "fs.write": ("agent.fs.write(path, content, expected_sha256=None, expected_absent=False)"),
    "fs.edit": "agent.fs.edit(path, old_text, new_text, expected_sha256=None)",
    "shell.run": "agent.shell.run(command, timeout_seconds=120)",
    "mcp.call": "agent.mcp.call(capability, arguments)",
    "parallel": "agent.parallel([{'operation': 'fs.read', 'arguments': {...}}, ...])",
    "state.list": "agent.state.list()",
    "state.describe": "agent.state.describe(name, selector=(), preview=False)",
    "state.annotate": "agent.state.annotate(name, description, selector=())",
    "artifacts.load": "agent.artifacts.load(uri, offset=0, limit=16000)",
    "artifacts.list": "agent.artifacts.list()",
    "artifacts.publish": "agent.artifacts.publish(value, name, description=None)",
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
        "read_reference": {
            "artifact_uri": "str (completed read receipt; cite this exact value, not data.sha256)",
            "task_id": "str", "operation_id": "str", "path": "str", "sha256": "str",
            "offset": "int", "returned_lines": "int",
            "source_coverage": {"whole_file": "bool|null", "total_lines": "int|null",
                                "next_unread_offset": "int|null"},
        },
    },
    "shell.run": {
        "result_kind": "process|managed (route, not proof of execution or success)",
        "status": "ok|error|blocked|timeout",
        "exit_code": "int|null (process result only; absent for managed CLI views)",
        "data": {"stdout": "str (process only)", "stderr": "str (process only)"},
        "truncated": "bool",
        "artifact_uri": "str|null",
        "managed_cli": {
            "data": "native managed-command payload, not process stdout/stderr",
            "model_text": "bounded rendering of the native payload",
            "memory_query": "result['data'] is the view envelope; result['data']['data'] is its body",
            "memory_note": "Check result['status'] and result['data']['status']. Writes return a compact receipt in data (event_id, committed version, payload_hash, entry_count, recovery), not text/entries. note read returns full latest text/entries in data. No exit_code or stdout, no json.loads needed; a write receipt is not verification of findings.",
            "read_recover": "body['text'], body['read_evidence'], body['source_coverage']; historical capture only",
        },
    },
    "state.describe": {
        "shape": "descriptor mapping directly, without a status/data envelope",
        "name": "str", "type": "str", "preview": "optional bounded display, not full content",
        "read_reference": "optional broker-established historical source reference",
        "unavailable": "raises KeyError for an unavailable binding/selector; inspect state.list or catch KeyError",
    },
    "fs.write": {"status": "ok|error|blocked", "changed_paths": "list[str]"},
    "fs.edit": {"status": "ok|error|blocked", "changed_paths": "list[str]"},
    "artifacts.load": {
        "status": "ok|error|blocked",
        "data": {"uri": "str", "encoding": "utf-8|base64",
                 "text": "str for exact UTF-8, null otherwise",
                 "base64": "present only for non-UTF-8 byte pages; decode with base64.b64decode",
                 "offset": "zero-based byte offset", "returned_bytes": "int", "total_bytes": "int",
                 "complete": "bool (end of artifact, not whole coverage if offset > 0)",
                 "next_offset": "next byte offset or null"},
        "recovery": "Combine exact page bytes before UTF-8/JSON parsing. A completed fs.read artifact is a saved result envelope: check its status, then data.text and source metadata. Historical only.",
        "completed_read_recipe": READ_RESULT_RECIPE,
        "recipe_contract": "Assign page = agent.artifacts.load(uri). page.data.text contains JSON bytes, NOT source text. Run the recipe in the same cell; select only task-relevant source fields. source_text=None means decoding was not established: handle non-ok status first; otherwise finish exact byte paging, including base64 decoding where needed. saved_result retains original source coverage; decoding never establishes current freshness.",
        "rejection": "Invalid arguments or denied access return error/blocked with effect=none. Recover exact authorized URIs via agent.artifacts.list(); never guess hashes. Corruption or unknown effects still require fail-closed handling.",
    },
    "artifacts.list": {"data": {"artifacts": "list of task-authorized uri entries; published entries also carry name/description"}},
    "mcp.call": {"status": "capability-defined result mapping"},
    "parallel": {"status": "list[result] in input order", "allowed": ["fs.read"]},
}


def _agent_help(
    catalog: Mapping[str, Mapping[str, object]],
    prefix: str | None = None,
    *,
    details: bool = False,
) -> dict[str, object]:
    """Return bounded, deterministic capability signatures."""

    if prefix is not None and not isinstance(prefix, str):
        raise TypeError("help prefix must be a string or None")
    selected = {
        name: item
        for name, item in sorted(catalog.items())
        if prefix is None or name.startswith(prefix)
    }
    compact: dict[str, object] = {
        name: str(item.get("signature", item.get("description", "")))
        for name, item in selected.items()
    }
    candidate: dict[str, object] = (
        {name: dict(item) for name, item in selected.items()} if details else compact
    )
    if len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()) <= _MAX_HELP_BYTES:
        return candidate
    notice = (
        f"Help exceeded {_MAX_HELP_BYTES} bytes; "
        "call agent.help('exact.name', details=True)."
    )
    degraded: dict[str, object] = {"_notice": notice, **compact}
    if len(json.dumps(degraded, sort_keys=True, separators=(",", ":")).encode()) <= _MAX_HELP_BYTES:
        return degraded
    names: list[str] = []
    pointer: dict[str, object] = {"_notice": notice, "matches": names}
    for name in selected:
        names.append(name)
        if len(json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode()) > _MAX_HELP_BYTES:
            names.pop()
            break
    return pointer


def _binding_description(
    name: str,
    value: Any,
    metadata: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    value_type = type(value)
    normal_type = type(value_type) is type
    item: dict[str, Any] = {
        "name": name,
        "type": value_type.__name__ if normal_type else "object",
        "module": value_type.__module__ if normal_type and type(value_type.__module__) is str else "unknown",
        "cell_id": metadata.get(name, {}).get("cell_id", "unknown"),
        "replay": metadata.get(name, {}).get("replay", "transient"),
    }
    if any(value_type is known for known in (str, bytes, list, tuple, dict, set, frozenset)):
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


def _value_fingerprint(value: Any) -> str | None:
    """Validate bounded plain data before hashing; never invoke object hooks."""
    nodes = 0
    size = 0
    seen: set[int] = set()

    def supported(item: Any, depth: int = 0) -> bool:
        nonlocal nodes, size
        nodes += 1
        if nodes > 1024 or depth > 12:
            return False
        kind = type(item)
        if kind is str:
            if len(item) > 65536 - size:
                return False
            size += len(item.encode("utf-8"))
        elif any(kind is known for known in (type(None), bool, float)):
            size += 16
        elif kind is int:
            if item.bit_length() > 256:
                return False
            size += 80
        elif any(kind is known for known in (dict, list, tuple)):
            if id(item) in seen or len(item) > 1024:
                return False
            seen.add(id(item))
            if kind is dict:
                valid = all(type(key) is str and supported(key, depth + 1) and
                            supported(child, depth + 1) for key, child in item.items())
            else:
                valid = all(supported(child, depth + 1) for child in item)
            seen.remove(id(item))
            return valid
        else:
            return False
        return size <= 65536

    try:
        if not supported(value):
            return None
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeError):
        return None
    return hashlib.sha256(encoded).hexdigest()


class _StateProxy:
    def __init__(
        self,
        namespace: Mapping[str, Any],
        metadata: Mapping[str, Mapping[str, str]],
    ) -> None:
        self._namespace = namespace
        self._metadata = metadata
        self._sources: dict[int, tuple[Any, str, dict[str, Any]]] = {}
        self._annotations: dict[tuple[str, tuple[str | int, ...]], tuple[Any, str, str]] = {}

    def register_read(self, result: Any) -> None:
        """Called only on a host broker response, before returning it to code."""
        if type(result) is not dict or result.get("status") != "ok":
            return
        reference = result.get("read_reference")
        data = result.get("data")
        if type(reference) is not dict or type(data) is not dict:
            return
        # Detach the attestation from all model-mutable result containers.
        reference = json.loads(json.dumps(reference))
        for value in (result, data, data.get("text")):
            digest = _value_fingerprint(value)
            if digest is None:
                continue
            self._sources[id(value)] = (value, digest, reference)
            # ponytail: bounded live-value index, not whole-heap lineage tracking.
            while len(self._sources) > 128:
                self._sources.pop(next(iter(self._sources)))

    def begin_cell(self, assigned_names: set[str]) -> None:
        for key in tuple(self._annotations):
            if key[0] in assigned_names:
                self._annotations.pop(key, None)

    def annotate(self, name: str, description: str, selector: tuple[str | int, ...] = ()) -> dict[str, Any]:
        """Describe one current value; the annotation is advisory, not a fact."""
        try:
            value = self._resolve(name, selector)
        except KeyError:
            return {"status": "unavailable", "reason": "unknown live binding"}
        if type(description) is not str or not description.strip() or len(description.encode(errors="replace")) > 500:
            return {"status": "unavailable", "reason": "description must be nonempty and at most 500 UTF-8 bytes"}
        description = description.encode(errors="replace").decode()
        key = (name, tuple(selector))
        digest = _value_fingerprint(value)
        if digest is None:
            return {"status": "unavailable", "reason": "value exceeds the supported fingerprint budget"}
        if key not in self._annotations and len(self._annotations) >= 64:
            return {"status": "unavailable", "reason": "at most 64 live annotations are supported"}
        self._annotations[key] = (value, digest, description)
        return {"status": "ok", **self.describe(name, selector)}

    def list(self) -> list[dict[str, Any]]:
        """Return metadata for live bindings without exposing their values."""

        names = sorted(name for name in self._namespace
                       if not name.startswith("__") and name not in _RESERVED_NAMES)
        reads: set[tuple[str, tuple[str | int, ...]]] = set()
        pending: deque[tuple[str, tuple[str | int, ...], Any]] = deque(
            (name, (), self._namespace[name]) for name in names[:128])
        seen: set[int] = set()
        # ponytail: scan 128 roots/512 values, 64 children and eight levels;
        # use targeted state.describe or rebind nearer values beyond discovery.
        for _ in range(512):
            if not pending:
                break
            name, selector, value = pending.popleft()
            if id(value) in seen:
                continue
            if id(value) in self._sources:
                try:
                    item = self.describe(name, selector)
                except KeyError:
                    continue
                if item.get("read_reference"):
                    reads.add((name, selector))
                    seen.add(id(value))
                    continue  # Do not repeat the same capture's data/text children.
            if len(selector) >= 8:
                continue
            if type(value) is dict:
                if len(value) > 1024 or any(type(key) is not str and type(key) is not int for key in value):
                    continue
                children = islice(value.items(), 64)
            elif type(value) is list or type(value) is tuple:
                children = enumerate(value[:64])
            else:
                continue
            seen.add(id(value))
            for key, child in children:
                if len(pending) >= 512:
                    break
                if ((type(key) is str and len(key.encode(errors="replace")) > 128)
                        or (type(key) is int and key.bit_length() > 256)):
                    continue
                pending.append((name, (*selector, key), child))
        keys = sorted(set((name, ()) for name in names) | set(self._annotations) | reads,
                      key=lambda key: (key not in self._annotations, key not in reads, key[0], json.dumps(key[1])))
        result = []
        for name, selector in keys[:64]:
            try:
                result.append(self.describe(name, selector))
            except KeyError:
                self._annotations.pop((name, selector), None)
                result.append({"name": name, "selector": list(selector), "availability": "unavailable"})
        return result

    def _resolve(self, name: str, selector: tuple[str | int, ...]) -> Any:
        if type(name) is not str or not name.isidentifier() or name.startswith("__") or name in _RESERVED_NAMES:
            raise KeyError("unknown state binding")
        if (type(selector) is not list and type(selector) is not tuple) or len(selector) > 8 or any(
            type(key) is not str and type(key) is not int for key in selector
        ):
            raise KeyError("selector must be at most eight string/integer keys")
        if any((type(key) is str and len(key.encode(errors="replace")) > 4096) or
               (type(key) is int and key.bit_length() > 256) for key in selector):
            raise KeyError("selector key exceeds its bounded representation")
        try:
            value = self._namespace[name]
            for key in selector:
                if type(value) is dict and (len(value) > 1024 or any(type(existing) is not str and type(existing) is not int for existing in value)):
                    raise KeyError("selector requires bounded plain keys")
                if type(value) is dict:
                    value = value[key]
                elif (type(value) is list or type(value) is tuple) and type(key) is int and key >= 0:
                    value = value[int(key)]
                else:
                    raise KeyError("selector requires a plain container")
        except (KeyError, IndexError) as error:
            raise KeyError(f"unknown state binding: {name}") from error
        return value

    def describe(self, name: str, selector: tuple[str | int, ...] = (), *, preview: bool = False) -> dict[str, Any]:
        """Describe one live value using only plain-container selectors."""
        value = self._resolve(name, selector)
        key = (name, tuple(selector))
        item = _binding_description(name, value, self._metadata)
        if preview:
            if type(value) is str:
                item["preview"] = value[:256].encode(errors="replace").decode()
                item["preview_truncated"] = len(value) > 256
            elif type(value) is dict:
                item["preview"] = {"keys": [key[:80] for key in islice(value, 8) if type(key) is str]}
            elif type(value) is list or type(value) is tuple:
                item["preview"] = {"item_types": [
                    type(child).__name__ if type(type(child)) is type else "object"
                    for child in value[:8]
                ]}
        if selector:
            item["selector"] = list(selector)
            item["binding_type"] = _binding_description(name, self._namespace[name], self._metadata)["type"]
            item["access_expression"] = name + "".join(f"[{part!r}]" for part in selector)
            item["inspect_expression"] = f"agent.state.describe({name!r}, selector={tuple(selector)!r}, preview=True)"
        source = self._sources.get(id(value))
        annotation = self._annotations.get(key)
        digest = _value_fingerprint(value) if source or annotation else None
        if source:
            if source[0] is value and digest is not None and source[1] == digest:
                item["read_reference"] = json.loads(json.dumps(source[2]))
                item["freshness"] = "historical_snapshot"
            else:
                self._sources.pop(id(value), None)
                item["provenance"] = "invalidated"
        if annotation:
            if annotation[0] is value and digest is not None and annotation[1] == digest:
                item["description"] = annotation[2]
                item["description_authority"] = "advisory"
            else:
                self._annotations.pop(key, None)
                item["description_status"] = "invalidated"
        if source or annotation:
            item["value_fingerprint"] = digest
        return item


def _agent_proxy(
    connection: Connection,
    namespace: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, str]],
    help_catalog: Mapping[str, Mapping[str, object]],
    state: _StateProxy,
) -> SimpleNamespace:
    return SimpleNamespace(
        help=lambda prefix=None, *, details=False: _agent_help(
            help_catalog, prefix, details=details
        ),
        parallel=_RemoteOperation(connection, "parallel", state).__call__,
        fs=SimpleNamespace(
            read=_RemoteOperation(connection, "fs.read", state).__call__,
            write=_RemoteOperation(connection, "fs.write"),
            edit=_RemoteOperation(connection, "fs.edit"),
        ),
        shell=SimpleNamespace(run=_RemoteOperation(connection, "shell.run")),
        mcp=SimpleNamespace(call=_RemoteOperation(connection, "mcp.call")),
        artifacts=SimpleNamespace(
            load=_RemoteOperation(connection, "artifacts.load"),
            list=_RemoteOperation(connection, "artifacts.list"),
            publish=_RemoteOperation(connection, "artifacts.publish"),
        ),
        state=SimpleNamespace(list=state.list, describe=state.describe, annotate=state.annotate),
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
    state_catalog: _StateProxy | None = None,
) -> dict[str, Any]:
    stdout = _BoundedText(max_output_bytes)
    stderr = _BoundedText(max_output_bytes)
    failure_stage: Literal["parse", "source_validation", "execution"] = "parse"
    annotation_checkpoint = dict(state_catalog._annotations) if state_catalog is not None else None
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
        if state_catalog is not None:
            state_catalog.begin_cell(touched_names)
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
        manifest = state_catalog.list() if state_catalog is not None else _state_manifest(namespace, metadata)
        return {
            "status": "ok",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "full_stdout": stdout.full_value(),
            "full_stderr": stderr.full_value(),
            "value_repr": value_repr,
            "display_data": display_data,
            "output_truncated": stdout.truncated or stderr.truncated,
            "state_count": sum(not name.startswith("__") and name not in _RESERVED_NAMES for name in namespace),
            "state_delta": sorted(touched_names),
            "state_manifest": manifest[:64],
        }
    except BaseException as error:
        if state_catalog is not None and annotation_checkpoint is not None:
            state_catalog._annotations = annotation_checkpoint
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
            "full_stdout": stdout.full_value(),
            "full_stderr": stderr.full_value(),
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
    state_catalog = _StateProxy(namespace, state_metadata)
    namespace["agent"] = _agent_proxy(connection, namespace, state_metadata, help_catalog, state_catalog)
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
            state_catalog=state_catalog,
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

    def kernel_status(self) -> dict[str, Any]:
        """Describe the current worker without starting or resetting it."""
        with self._lock:
            live = self._process is not None and self._process.is_alive()
            return {"live": live, "kernel_epoch": self._kernel_epoch if live else None}

    @staticmethod
    def _broker_operation(broker: ReplBroker, operation: str) -> Callable[..., Any]:
        names: Mapping[str, str] = {
            "fs.read": "read",
            "fs.write": "write",
            "fs.edit": "edit",
            "shell.run": "bash",
            "mcp.call": "call",
            "parallel": "parallel",
            "artifacts.load": "artifacts_load",
            "artifacts.list": "artifacts_list",
            "artifacts.publish": "artifacts_publish",
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
                        full_stdout=response.get("full_stdout"),
                        full_stderr=response.get("full_stderr"),
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
