from __future__ import annotations

from pathlib import Path

import pytest

from harness.environment import (
    FileConflictError,
    LocalWorkspaceEnvironment,
    WorkspaceViolationError,
)
from harness.models import ToolStatus
from harness.tools import (
    bound_output,
    compact_tool_result,
    execute_edit,
    execute_read,
    execute_write,
)


def _environment(tmp_path: Path) -> LocalWorkspaceEnvironment:
    (tmp_path / "src").mkdir()
    return LocalWorkspaceEnvironment(tmp_path)


def test_environment_blocks_path_escape(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with pytest.raises(WorkspaceViolationError):
        environment.resolve("../outside.txt")


def test_read_is_line_numbered_and_bounded(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    (tmp_path / "src" / "example.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = execute_read(environment, "src/example.py", offset=2, limit=2)
    assert result.status is ToolStatus.OK
    assert "2 | two" in result.model_text
    assert "3 | three" in result.model_text
    assert "more available" in result.model_text
    assert result.data == {
        "path": "src/example.py",
        "text": "two\nthree\n",
        "offset": 2,
        "returned_lines": 2,
        "total_lines": 4,
        "complete": False,
        "next_offset": 4,
        "sha256": result.content_hashes["src/example.py"],
    }


def test_edit_requires_a_unique_preimage_and_is_idempotent(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    path = tmp_path / "src" / "example.py"
    path.write_text("value = 1\n", encoding="utf-8")
    first = execute_edit(environment, "src/example.py", "value = 1", "value = 2")
    second = execute_edit(environment, "src/example.py", "value = 1", "value = 2")
    assert first.changed_paths == ["src/example.py"]
    assert second.status is ToolStatus.OK
    assert "already applied" in second.model_text
    assert path.read_text(encoding="utf-8") == "value = 2\n"


def test_environment_rejects_ambiguous_edit(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    path = tmp_path / "src" / "example.py"
    path.write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(FileConflictError):
        environment.replace_text("src/example.py", "x", "y")


def test_write_supports_expected_absence_and_hash_conflict(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    created = execute_write(environment, "src/new.py", "answer = 42\n", expected_absent=True)
    repeated = execute_write(environment, "src/new.py", "answer = 42\n", expected_absent=True)
    conflict = execute_write(environment, "src/new.py", "answer = 43\n", expected_sha256="bad")
    assert created.changed_paths == ["src/new.py"]
    assert repeated.status is ToolStatus.OK
    assert conflict.status is ToolStatus.ERROR


def test_output_bounding_preserves_head_and_tail() -> None:
    bounded = bound_output("HEAD\n" + "x\n" * 1_000 + "TAIL\n", max_chars=200, max_lines=20)
    assert bounded.truncated is True
    assert bounded.text.startswith("HEAD")
    assert bounded.text.endswith("TAIL")


def test_compact_tool_result_has_one_bounded_stable_envelope() -> None:
    raw = {
        "status": "ok",
        "stdout": "HEAD\n" + "x\n" * 1_000 + "TAIL",
        "artifact_uris": ["artifact://sha256/b", "artifact://sha256/a"],
        "effect": "changed",
        "attempt_id": "cell-1",
        "notebook_path": "/private/state/notebook.ipynb",
        "runtime_epoch": "large-runtime-detail",
    }
    result = compact_tool_result(
        raw,
        max_chars=200,
        max_lines=20,
    )

    assert set(result) == {
        "status", "model_text", "truncated", "omitted_bytes", "artifact_uris",
        "result_hash", "effect", "attempt_id",
    }
    assert result["artifact_uris"] == ["artifact://sha256/a", "artifact://sha256/b"]
    assert result["model_text"].startswith("HEAD") and result["model_text"].endswith("TAIL")
    assert "notebook_path" not in result and "runtime_epoch" not in result
    replay = compact_tool_result(
        {**raw, "attempt_id": "cell-2", "runtime_epoch": "new", "replayed": True},
        max_chars=200,
        max_lines=20,
    )
    assert replay["result_hash"] == result["result_hash"]
