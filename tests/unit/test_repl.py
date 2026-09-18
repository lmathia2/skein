from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from harness.ptc.repl import PersistentPythonWorker
from harness.ptc.repl.worker import _agent_help


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


def test_committed_plain_values_survive_a_failed_cell_without_repeating_a_read() -> None:
    broker = _Broker()
    with PersistentPythonWorker(capture_committed=True) as worker:
        learned = worker.execute(
            "read = agent.fs.read('input.txt')\npage = {'path': 'input.txt', 'text': read['model_text']}",
            broker, 5, cell_id="learn",
        )
        assert learned.status == "ok" and learned.checkpoint_values is not None
        failed = worker.execute("scratch = {'partial': True}\nmissing_name", broker, 5)
        assert failed.status == "error" and failed.checkpoint_values is None
        worker.reset()
        restored = worker.restore_plain(learned.checkpoint_values, "learn")
        assert any(item["name"] == "page" for item in restored)
        reused = worker.execute("print(page['text'])", broker, 5)
    assert reused.status == "ok" and reused.stdout == "hello\n"
    assert [name for name, _ in broker.calls] == ["read"]


def test_committed_plain_checkpoint_reports_omitted_values() -> None:
    with PersistentPythonWorker(capture_committed=True, snapshot_max_bytes=1024) as worker:
        learned = worker.execute("import datetime\nhuge = 'x' * 5000\nsaved = {'answer': 42}", _Broker(), 5)
        assert learned.status == "ok" and learned.checkpoint_values is not None
        assert {"datetime", "huge"} <= set(learned.checkpoint_omitted_names)
        worker.reset()
        worker.restore_plain(learned.checkpoint_values, "learn")
        assert worker.execute("print(saved['answer'])", _Broker(), 5).stdout == "42\n"
        missing = worker.execute("print(huge)", _Broker(), 5)
        assert missing.status == "error" and missing.error_type == "NameError"


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


def test_capability_help_degrades_to_signatures_then_targeted_pointer() -> None:
    detailed = {
        f"mcp.tool_{index:03d}": {
            "signature": f"agent.mcp.call('tool_{index:03d}', arguments)",
            "description": "x" * 1000,
        }
        for index in range(100)
    }

    degraded = _agent_help(detailed, details=True)
    pointer = _agent_help(
        {
            f"mcp.{'x' * 400}_{index:03d}": {"signature": "y" * 400}
            for index in range(100)
        }
    )

    assert "exceeded 16000 bytes" in str(degraded["_notice"])
    assert isinstance(degraded["mcp.tool_000"], str)
    assert set(pointer) == {"_notice", "matches"}
    assert len(json.dumps(degraded, sort_keys=True).encode()) <= 16_000
    assert len(json.dumps(pointer, sort_keys=True).encode()) <= 16_000


def test_kernel_help_and_static_prompt_share_the_actual_execution_boundary():
    from app.agent.config import NOTEBOOK_PTC_INSTRUCTION
    from harness.ptc.repl.worker import WORKSPACE_EXECUTION_GUIDANCE, default_help_catalog

    catalog = default_help_catalog()
    assert catalog["kernel"]["workspace_execution"] == WORKSPACE_EXECUTION_GUIDANCE
    assert NOTEBOOK_PTC_INSTRUCTION.endswith(WORKSPACE_EXECUTION_GUIDANCE)
    with PersistentPythonWorker() as worker:
        result = worker.execute("agent.help('kernel', details=True)", _Broker(), 5)
        assert result.status == "ok"
        assert len((result.value_repr or "").encode()) < 16_000
        for name in catalog["kernel"]["blocked_direct_calls"]:
            rejected = worker.execute(f"{name}('pass')", _Broker(), 5)
            assert rejected.status == "error" and rejected.failure_stage == "source_validation"
        for name in catalog["kernel"]["blocked_direct_modules"]:
            rejected = worker.execute(f"import {name}", _Broker(), 5)
            assert rejected.status == "error" and rejected.failure_stage == "source_validation"


def test_prompt_source_mapping_survives_separate_answer_reads_without_refetching():
    import hashlib

    from app.agent.config import NOTEBOOK_PTC_INSTRUCTION

    class ReadBroker(_Broker):
        def read(self, path, offset=1, limit=400):
            self.calls.append(("read", (path, offset, limit)))
            text = self.files[path]
            return {"status": "ok", "data": {"path": path, "text": text, "offset": offset,
                    "returned_lines": 1, "complete": False, "next_offset": offset + 1},
                    "read_reference": {"artifact_uri": "artifact://sha256/" + hashlib.sha256(path.encode()).hexdigest(),
                        "path": path, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "offset": offset, "returned_lines": 1}}

        def parallel(self, operations):
            return [self.read(**item["arguments"]) for item in operations]

    broker = ReadBroker()
    broker.files.update({"config.json": '{"price": 7}', "answer.json": '{"cost": 21}'})
    # Execute the actual shipped prompt example, not a separately scripted analogue.
    recipe = "source_pages = agent.parallel" + NOTEBOOK_PTC_INSTRUCTION.split(
        "source_pages = agent.parallel", 1)[1].split("\nchanged =", 1)[0]
    with PersistentPythonWorker() as worker:
        captured = worker.execute("known_paths = ['config.json']\n" + recipe, broker, 5)
        assert captured.status == "ok"
        reused = worker.execute(
            "answer_reads = {'answer.json': agent.fs.read('answer.json')}\n"
            "check_results = json.loads(source_reads['config.json']['data']['text'])['price'] * 3\n"
            "check_results == json.loads(answer_reads['answer.json']['data']['text'])['cost']", broker, 5)
        assert reused.status == "ok" and reused.value_repr == "True"
        descriptor = worker.execute(
            "json.dumps(agent.state.describe('source_reads', selector=('config.json',)))", broker, 5)
        assert descriptor.status == "ok"
        # The source still has its original partial-range receipt; no full-file claim.
        entry = next(row for row in reused.state_manifest if row.get("availability") == "live_retained_read"
                     and row["read_reference"]["path"] == "config.json")
        assert entry["read_reference"]["path"] == "config.json"
        assert entry["read_reference"]["returned_lines"] == 1
        retained = worker.execute(
            f"agent.state.reuse({entry['read_reference']['artifact_uri']!r})['data']['text']", broker, 5)
        assert retained.value_repr == repr('{"price": 7}')
        assert retained.retained_read_uses == ("read:1",)
        rejected = worker.execute("not valid python !", broker, 5)
        assert rejected.status == "error" and rejected.retained_read_uses == ()
        assert worker.execute("source_reads['config.json']['data']['complete']", broker, 5).value_repr == "False"
    assert broker.calls == [("read", ("config.json", 1, 400)), ("read", ("answer.json", 1, 400))]


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


@pytest.mark.parametrize("code,stage,line", [
    ("import json\nvalue = 7\njson.loads('not JSON')", "execution", 3),
    ("def parse_data():\n    return json.loads('not JSON')\nparse_data()", "execution", 2),
    ("value = 7\nif True:\n    missing = (", "parse", 3),
    ("value = 7\nif False:\n    open('file')", "source_validation", 3),
])
def test_worker_error_location_is_in_submitted_source_not_parsed_data(code, stage, line):
    with PersistentPythonWorker() as worker:
        failed = worker.execute(code, _Broker(), 5)
    assert failed.status == "error" and failed.failure_stage == stage
    assert failed.error_line == line
    assert failed.error_source == code.splitlines()[line - 1].strip()


def test_prior_cell_function_failure_points_to_the_current_call_site():
    with PersistentPythonWorker() as worker:
        defined = worker.execute("def fail_later():\n    return 1 / 0", _Broker(), 5)
        assert defined.status == "ok"
        failed = worker.execute("marker = 7\nmarker += 1\n\nfail_later()", _Broker(), 5)
    assert failed.status == "error" and failed.failure_stage == "execution"
    assert failed.error_line == 4 and failed.error_source == "fail_later()"


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


def test_direct_tool_aliases_survive_snapshot_rollback() -> None:
    with PersistentPythonWorker(state_recovery="snapshot") as worker:
        direct = worker.execute("saved = read('input.txt')\nsaved['model_text']", _Broker(), 5)
        worker.execute("temporary = 1\n1 / 0", _Broker(), 5)
        restored = worker.execute("read('input.txt')['model_text'], saved['model_text']", _Broker(), 5)

    assert direct.value_repr == "'hello'"
    assert restored.value_repr == "('hello', 'hello')"


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


def test_kernel_status_does_not_start_worker() -> None:
    with PersistentPythonWorker() as worker:
        assert worker.kernel_status() == {"live": False, "kernel_epoch": None}
        epoch = worker.kernel_epoch
        assert worker.kernel_status() == {"live": True, "kernel_epoch": epoch}
