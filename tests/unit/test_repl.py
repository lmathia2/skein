from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from harness.ptc.repl import PersistentPythonWorker


class _Broker:
    def __init__(self) -> None:
        self.files: dict[str, str] = {"input.txt": "hello"}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
        self.calls.append(("read", (path, offset, limit)))
        return {"status": "ok", "model_text": self.files[path]}

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(("write", (path, content, expected_sha256, expected_absent)))
        self.files[path] = content
        return {"status": "ok", "changed_paths": [path]}

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("edit", (path, old_text, new_text, expected_sha256)))
        self.files[path] = self.files[path].replace(old_text, new_text)
        return {"status": "ok", "changed_paths": [path]}

    def bash(self, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        self.calls.append(("bash", (command, timeout_seconds)))
        return {"status": "ok", "model_text": "command output"}

    def call(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("call", (capability, arguments)))
        return {"status": "ok"}


def test_worker_preserves_namespace_and_captures_outputs() -> None:
    broker = _Broker()
    with PersistentPythonWorker() as worker:
        first = worker.execute("value = 40\nprint('saved')", broker, 5)
        second = worker.execute("print('again', value)\nvalue + 2", broker, 5)

    assert first.status == "ok"
    assert first.stdout == "saved\n"
    assert second.status == "ok"
    assert second.stdout == "again 40\n"
    assert second.value_repr == "42"


def test_worker_routes_capabilities_through_parent_broker() -> None:
    broker = _Broker()
    code = """
source = agent.fs.read("input.txt")
agent.fs.write("output.txt", source["model_text"] + " world", expected_absent=True)
agent.fs.edit("output.txt", "world", "agent")
result = agent.shell.run("git status --short", timeout_seconds=7)
(source["model_text"], result["model_text"])
"""
    with PersistentPythonWorker() as worker:
        result = worker.execute(code, broker, 5)

    assert result.status == "ok"
    assert result.value_repr == "('hello', 'command output')"
    assert broker.files["output.txt"] == "hello agent"
    assert [name for name, _ in broker.calls] == ["read", "write", "edit", "bash"]


def test_worker_exposes_bounded_capability_help() -> None:
    with PersistentPythonWorker() as worker:
        all_help = worker.execute("agent.help()", _Broker(), 5)
        read_help = worker.execute("agent.help('fs.read')", _Broker(), 5)
        detailed = worker.execute("agent.help('fs.read', details=True)", _Broker(), 5)

    assert all_help.status == "ok"
    assert "agent.fs.read(path, offset=1, limit=400)" in (all_help.value_repr or "")
    assert "agent.shell.run(command, timeout_seconds=120)" in (all_help.value_repr or "")
    assert read_help.value_repr == "{'fs.read': 'agent.fs.read(path, offset=1, limit=400)'}"
    assert "exact redacted selected range" in (detailed.value_repr or "")
    assert "shell.run" not in (detailed.value_repr or "")
    assert "preloaded_modules" in (
        worker.execute("agent.help('kernel', details=True)", _Broker(), 5).value_repr or ""
    )
    assert worker.execute("json.dumps({'ok': True})", _Broker(), 5).value_repr == "'{\"ok\": true}'"


def test_worker_returns_mime_bundle_as_rich_display() -> None:
    with PersistentPythonWorker() as worker:
        result = worker.execute('{"image/png": b"png-bytes", "text/plain": "plot"}', _Broker(), 5)

    assert result.status == "ok"
    assert result.value_repr is None
    assert result.display_data == {"image/png": b"png-bytes", "text/plain": "plot"}


def test_worker_exposes_bounded_state_metadata_without_values() -> None:
    marker = "raw-value-must-not-leak"
    with PersistentPythonWorker() as worker:
        assigned = worker.execute(
            f"payload = {marker!r} * 1000",
            _Broker(),
            5,
            cell_id="cell-1",
            replay_policy="requires_reconciliation",
        )
        catalog = worker.execute("agent.state.list()", _Broker(), 5)

    assert assigned.state_count == 1
    assert assigned.state_delta == ("payload",)
    assert catalog.status == "ok"
    assert "'name': 'payload'" in (catalog.value_repr or "")
    assert "'size': 23000" in (catalog.value_repr or "")
    assert "'cell_id': 'cell-1'" in (catalog.value_repr or "")
    assert marker not in (catalog.value_repr or "")


def test_worker_reports_errors_without_losing_prior_state() -> None:
    broker = _Broker()
    with PersistentPythonWorker() as worker:
        worker.execute("value = 9", broker, 5)
        failed = worker.execute("print('before')\n1 / 0", broker, 5)
        restored = worker.execute("value", broker, 5)

    assert failed.status == "error"
    assert failed.stdout == "before\n"
    assert failed.error_type == "ZeroDivisionError"
    assert "division by zero" in (failed.error_message or "")
    assert restored.value_repr == "9"


def test_worker_classifies_and_compacts_failures() -> None:
    with PersistentPythonWorker() as worker:
        syntax = worker.execute("value =", _Broker(), 5)
        policy = worker.execute("open('x')", _Broker(), 5)
        runtime = worker.execute("value = 1\n1 / 0", _Broker(), 5)

    assert (syntax.failure_stage, syntax.error_line) == ("parse", 1)
    assert (policy.failure_stage, policy.error_source) == (
        "source_validation",
        "open('x')",
    )
    assert (runtime.failure_stage, runtime.error_line, runtime.error_source) == (
        "execution",
        2,
        "1 / 0",
    )
    assert all("_execute_cell" not in line for line in runtime.traceback)


def test_timeout_discards_worker_state_and_marks_effect_unknown() -> None:
    broker = _Broker()
    with PersistentPythonWorker() as worker:
        worker.execute("value = 'old'", broker, 5)
        timed_out = worker.execute("while True:\n    pass", broker, 0.15)
        after_restart = worker.execute("value", broker, 5)

    assert timed_out.status == "timeout"
    assert timed_out.effect_unknown is True
    assert after_restart.status == "error"
    assert after_restart.error_type == "NameError"


def test_bounded_snapshot_restores_only_safe_values_after_runtime_failure() -> None:
    with PersistentPythonWorker(state_recovery="snapshot", snapshot_max_bytes=4096) as worker:
        worker.execute("kept = {'items': [1, 2]}", _Broker(), 5)
        failed = worker.execute("kept['items'].append(3)\nnew = 4\n1 / 0", _Broker(), 5)
        restored = worker.execute("kept, 'new' in dir()", _Broker(), 5)

    assert failed.state_preserved is True
    assert restored.value_repr == "({'items': [1, 2]}, False)"


def test_timeout_during_broker_call_returns_without_waiting_for_late_result() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBroker(_Broker):
        def read(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
            entered.set()
            release.wait(timeout=5)
            return {"status": "ok", "model_text": path}

    started = time.monotonic()
    with PersistentPythonWorker() as worker:
        result = worker.execute('agent.fs.read("slow")', BlockingBroker(), 0.15)
    elapsed = time.monotonic() - started
    release.set()

    assert entered.is_set()
    assert result.status == "timeout"
    assert result.effect_unknown is True
    assert elapsed < 1


def test_direct_effectful_imports_and_builtins_are_blocked() -> None:
    broker = _Broker()
    with PersistentPythonWorker() as worker:
        imported = worker.execute("import pathlib", broker, 5)
        opened = worker.execute("open('outside.txt', 'w')", broker, 5)

    assert imported.status == "error"
    assert imported.error_type == "PermissionError"
    assert opened.status == "error"
    assert opened.error_type == "PermissionError"


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import socket",
        "().__class__.__base__.__subclasses__()",
        "globals()['__builtins__']",
        "getattr(agent, '__class__')",
    ],
)
def test_common_host_capability_bypasses_are_blocked(code: str) -> None:
    with PersistentPythonWorker() as worker:
        result = worker.execute(code, _Broker(), 5)
    assert result.status == "error"
    assert result.error_type == "PermissionError"


def test_close_returns_promptly_after_normal_execution() -> None:
    broker = _Broker()
    worker = PersistentPythonWorker()
    worker.execute("1 + 1", broker, 5)
    started = time.monotonic()
    worker.close()
    assert time.monotonic() - started < 2
