from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.execution.repo import FffSearchService, SearchCursorError


def _service(tmp_path: Path) -> FffSearchService:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return FffSearchService(workspace, tmp_path / "state")


def test_fff_grep_owns_strict_grouped_cursor_pagination(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace = service.workspace
    (workspace / "src").mkdir()
    (workspace / "noise.ts").write_text(
        "".join(f"// TODO noise {index}\n" for index in range(30)),
        encoding="utf-8",
    )
    (workspace / "src" / "app.ts").write_text("// TODO app\n", encoding="utf-8")
    (workspace / "src" / "utils.ts").write_text("// TODO utils\n", encoding="utf-8")
    (workspace / "README.md").write_text("TODO docs\n", encoding="utf-8")

    pages = []
    page = service.grep(pattern="TODO", limit=20)
    pages.append(page)
    while page.cursor:
        page = service.grep(cursor=page.cursor)
        pages.append(page)

    assert sum(item.returned_matches for item in pages) == 33
    assert all(item.returned_matches <= 20 for item in pages)
    assert pages[0].returned_matches == 8
    assert "noise.ts" in pages[0].text
    assert "src/app.ts" in pages[0].text
    assert "src/utils.ts" in pages[0].text
    assert "README.md" in pages[0].text
    rendered_matches = re.findall(r"^\s+\d+: ", "\n".join(item.text for item in pages), re.MULTILINE)
    assert len(rendered_matches) == 33
    service.close()


def test_fff_cursor_is_replayable_tamper_evident_and_stale_on_file_change(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    target = service.workspace / "many.py"
    target.write_text("".join(f"# HIT {index}\n" for index in range(8)), encoding="utf-8")

    first = service.grep(pattern="HIT", limit=5)
    assert first.cursor is not None
    replay_one = service.grep(cursor=first.cursor)
    replay_two = service.grep(cursor=first.cursor)
    assert replay_one == replay_two

    snapshot_start = len("fff_")
    replacement = "0" if first.cursor[snapshot_start] != "0" else "1"
    tampered = first.cursor[:snapshot_start] + replacement + first.cursor[snapshot_start + 1 :]
    with pytest.raises(SearchCursorError, match=r"malformed|missing"):
        service.grep(cursor=tampered)

    target.write_text(target.read_text(encoding="utf-8") + "# HIT changed\n", encoding="utf-8")
    with pytest.raises(SearchCursorError, match="stale"):
        service.grep(cursor=first.cursor)
    service.close()


def test_fff_find_is_fuzzy_bounded_and_scoped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    (service.workspace / "src").mkdir()
    (service.workspace / "src" / "application_service.py").write_text("pass\n")
    (service.workspace / "README.md").write_text("docs\n")

    page = service.find(pattern="app service", path="src", limit=1)

    assert page.returned_matches == 1
    assert "src/application_service.py" in page.text
    assert "README.md" not in page.text
    service.close()


def test_fff_rejects_scope_escape_and_cross_operation_cursor(tmp_path: Path) -> None:
    service = _service(tmp_path)
    (service.workspace / "a.py").write_text("MARK\nMARK\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the workspace"):
        service.grep(pattern="MARK", path="../outside")

    page = service.grep(pattern="MARK", limit=1)
    assert page.cursor is not None
    with pytest.raises(SearchCursorError, match="different operation"):
        service.find(cursor=page.cursor)
    service.close()


def test_fff_rejects_cursor_from_another_workspace(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    (first_workspace / "a.py").write_text("MARK\nMARK\n", encoding="utf-8")
    (second_workspace / "a.py").write_text("MARK\nMARK\n", encoding="utf-8")
    first_service = FffSearchService(first_workspace, state)
    first = first_service.grep(pattern="MARK", limit=1)
    first_service.close()
    assert first.cursor is not None

    second_service = FffSearchService(second_workspace, state)
    with pytest.raises(SearchCursorError, match="different workspace"):
        second_service.grep(cursor=first.cursor)
    second_service.close()


def test_fff_health_is_sanitized_and_lazy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    cold = service.health()
    assert cold == {
        "backend": "fff-search/0.10.5",
        "state": "cold",
        "initialization_error": None,
    }
    (service.workspace / "a.py").write_text("value = 1\n")
    service.find(pattern="a")
    health = service.health()
    assert health["state"] == "ready"
    assert "base_path" not in health
    assert "workdir" not in health
    service.close()
