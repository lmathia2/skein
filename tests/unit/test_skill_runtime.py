from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.config import settings_from_composition
from app.agent.skills import build_skill_context
from harness.core.config import RuntimeBindings, load_harness_composition


@pytest.mark.parametrize("budget", [0, 32, 512, 4096])
def test_directory_skill_context_is_bounded_and_does_not_mutate_state(
    tmp_path: Path, budget: int,
) -> None:
    root = tmp_path / ".agents" / "skills" / "python-tests"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: python-tests\ndescription: Test Python changes\n---\n"
        + "Check edge cases.\n" * 100,
    )
    settings = settings_from_composition(
        load_harness_composition(),
        RuntimeBindings(
            workspace=tmp_path, state_root=tmp_path / "state", project_trusted=True,
        ),
    )
    settings = replace(settings, skill_context_bytes=budget)
    prefix = settings.static_prefix
    first = build_skill_context(goal="$python-tests", next_action="", settings=settings)
    assert first == build_skill_context(goal="$python-tests", next_action="", settings=settings)
    assert len(first.text.encode()) <= budget
    assert settings.static_prefix == prefix
    assert not settings.state_root.exists()
    if budget == 4096:
        assert first.selected_names == ("python-tests",)
        assert len(first.selected_hashes[0]) == 64
    if budget == 0:
        assert not first.text
