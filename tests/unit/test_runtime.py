from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.core.orchestration.runtime import changed_paths, parse_agent_step


def _run(root: Path, *args: str) -> None:
    subprocess.run(args, cwd=root, check=True, capture_output=True)


def _repository(root: Path) -> Path:
    root.mkdir()
    _run(root, "git", "init", "-q")
    _run(root, "git", "config", "user.email", "test@example.com")
    _run(root, "git", "config", "user.name", "Test")
    (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    (root / "renamed.py").write_text("name = 'old'\n", encoding="utf-8")
    _run(root, "git", "add", ".")
    _run(root, "git", "commit", "-qm", "initial")
    return root


def test_parse_agent_step_accepts_fenced_or_prefixed_json() -> None:
    payload = (
        '{"status":"continue","progress":["inspected"],'
        '"next_action":"edit","decisions":[],"questions":[],'
        '"discovered_constraints":[],"files_in_focus":["a.py"],'
        '"completion_claims":[]}'
    )
    assert parse_agent_step(f"```json\n{payload}\n```").next_action == "edit"
    assert parse_agent_step(f"Work batch complete.\n{payload}").files_in_focus == [
        "a.py"
    ]


def test_parse_agent_step_rejects_non_structured_final_text() -> None:
    with pytest.raises(ValueError, match="valid AgentStep"):
        parse_agent_step("I am done")


def test_parse_agent_step_normalizes_scalar_claim_evidence_from_local_models() -> None:
    step = parse_agent_step(
        '{"status":"done","completion_claims":['
        '{"criterion":"It works","evidence":"python test.py passed"}]}'
    )

    assert step.completion_claims[0].evidence == ["python test.py passed"]


def test_changed_paths_include_staged_renamed_and_untracked(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repository")
    base = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (root / "tracked.py").write_text("value = 2\n", encoding="utf-8")
    _run(root, "git", "add", "tracked.py")
    _run(root, "git", "mv", "renamed.py", "moved.py")
    (root / "new.py").write_text("new = True\n", encoding="utf-8")

    paths = changed_paths(root, base)
    assert "tracked.py" in paths
    assert "moved.py" in paths
    assert "new.py" in paths
