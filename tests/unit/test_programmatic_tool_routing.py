from __future__ import annotations

from pathlib import Path

from harness.skills import SkillRegistry, SkillRoot

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / ".agents" / "skills"
PLAN_PATH = ROOT / "tests" / "eval" / "ablations" / "programmatic_tool_routing.json"


def test_programmatic_routing_skill_uses_progressive_disclosure() -> None:
    registry = SkillRegistry([SkillRoot(SKILLS_ROOT, origin="project")])

    definition = registry.get("programmatic-tool-routing")
    catalog = registry.build_catalog()
    unrelated = registry.select(goal="Fix a localized arithmetic regression")
    selected = registry.select(goal="Use $programmatic-tool-routing for these trace files")

    assert definition is not None
    assert "programmatic-tool-routing" in catalog.included_names
    assert "Do not read environment variables" not in catalog.text
    assert all(skill.name != "programmatic-tool-routing" for skill in unrelated.skills)
    assert selected.skills[0].name == "programmatic-tool-routing"
    assert selected.skills[0].explicit is True
    assert "Do not read environment variables" in selected.text
    assert selected.skills[0].included_references == ()
