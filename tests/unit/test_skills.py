from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from harness.core.skills import (
    DuplicateSkillError,
    SkillPathError,
    SkillRegistry,
    SkillRoot,
    SkillValidationError,
    UntrustedSkillRootError,
)


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str,
    body: str = "Follow these deterministic instructions.",
    extra_frontmatter: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    manifest = directory / "SKILL.md"
    manifest.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra_frontmatter}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return manifest


def test_registry_discovers_multiple_roots_in_deterministic_precedence_order(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    beta = _write_skill(primary, "beta-skill", description="Work with beta records")
    _write_skill(primary, "alpha-skill", description="Work with alpha records")
    _write_skill(secondary, "first-skill", description="Work with first records")

    registry = SkillRegistry(
        [
            SkillRoot(secondary, origin="system", precedence=10),
            SkillRoot(primary, origin="project", precedence=20),
        ]
    )

    assert [skill.name for skill in registry.skills] == [
        "first-skill",
        "alpha-skill",
        "beta-skill",
    ]
    assert registry.get("BETA-SKILL") is not None
    assert registry.get("beta-skill").content_hash == hashlib.sha256(beta.read_bytes()).hexdigest()  # type: ignore[union-attr]
    catalog = registry.build_catalog()
    assert catalog.included_names == ("first-skill", "alpha-skill", "beta-skill")
    assert "origin=system" in catalog.text
    assert "sha256=" in catalog.text


@pytest.mark.parametrize(
    "contents",
    [
        "no frontmatter",
        "---\nname: Bad_Name\ndescription: useful\n---\nbody\n",
        "---\nname: sample\n---\nbody\n",
        "---\nname: sample\ndescription: useful\nallowed-tools: Bash\n---\nbody\n",
        "---\nname: sample\ndescription: useful\nmetadata:\n  nested:\n    bad: true\n---\nbody\n",
    ],
)
def test_registry_rejects_malformed_or_unsupported_frontmatter(
    tmp_path: Path,
    contents: str,
) -> None:
    root = tmp_path / "skills"
    directory = root / "sample"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(contents, encoding="utf-8")

    with pytest.raises(SkillValidationError):
        SkillRegistry([SkillRoot(root, origin="project")])


def test_registry_rejects_untrusted_roots_before_reading_skills(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()

    with pytest.raises(UntrustedSkillRootError, match="not trusted"):
        SkillRegistry([SkillRoot(root, origin="download", trusted=False)])


def test_duplicate_skill_names_are_conflicts_even_across_lifecycles(tmp_path: Path) -> None:
    active = tmp_path / "active"
    candidate = tmp_path / "candidate"
    active.mkdir()
    candidate.mkdir()
    _write_skill(active, "review", description="Review a change")
    _write_skill(candidate, "review", description="A proposed review replacement")

    with pytest.raises(DuplicateSkillError, match="duplicate skill 'review'"):
        SkillRegistry(
            [
                SkillRoot(active, origin="learned:active", lifecycle="enabled"),
                SkillRoot(candidate, origin="learned:candidate", lifecycle="candidate"),
            ]
        )


def test_explicit_mentions_outrank_lexical_matches_and_unknowns_are_reported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root, "python-tests", description="Run Python pytest unit tests")
    _write_skill(root, "database-migration", description="Plan database schema migrations")

    selection = SkillRegistry([SkillRoot(root, origin="project")]).select(
        goal="Plan a database migration but use $python-tests and $missing-skill",
        next_action="Update the schema",
        top_n=2,
    )

    assert [skill.name for skill in selection.skills] == [
        "python-tests",
        "database-migration",
    ]
    assert selection.skills[0].explicit is True
    assert selection.skills[1].explicit is False
    assert selection.unmatched_explicit_names == ("missing-skill",)


def test_references_are_disclosed_only_for_explicit_selection(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    manifest = _write_skill(
        root,
        "release-notes",
        description="Prepare release notes and changelogs",
        body="Read the [release checklist](references/checklist.md).",
    )
    references = manifest.parent / "references"
    references.mkdir()
    (references / "checklist.md").write_text("SECRET CHECKLIST CONTENT", encoding="utf-8")

    registry = SkillRegistry([SkillRoot(root, origin="project")])
    lexical = registry.select(goal="Prepare the release changelog")
    explicit = registry.select(goal="Use $release-notes")

    assert "SECRET CHECKLIST CONTENT" not in lexical.text
    assert lexical.skills[0].included_references == ()
    assert "SECRET CHECKLIST CONTENT" in explicit.text
    assert explicit.skills[0].included_references == ("references/checklist.md",)


def test_generated_skill_metadata_accepts_scalar_lists(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(
        root,
        "learned-skill",
        description="A generated workflow",
        extra_frontmatter=(
            "metadata:\n"
            "  status: candidate\n"
            "  version: 1\n"
            "  source_trace_ids: [trace-b, trace-a]\n"
        ),
    )

    skill = SkillRegistry([SkillRoot(root, origin="learned")]).skills[0]

    assert skill.metadata == {
        "source_trace_ids": ("trace-b", "trace-a"),
        "status": "candidate",
        "version": "1",
    }


def test_reference_cannot_escape_skill_directory(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "outside.md").write_text("outside", encoding="utf-8")
    _write_skill(
        root,
        "unsafe-skill",
        description="Try unsafe paths",
        body="Read [outside](../outside.md).",
    )

    with pytest.raises(SkillPathError, match="escapes configured directory"):
        SkillRegistry([SkillRoot(root, origin="project")])


def test_symlinked_skill_directory_and_reference_are_rejected(tmp_path: Path) -> None:
    external_root = tmp_path / "external"
    external_root.mkdir()
    external_skill = _write_skill(external_root, "linked-skill", description="Linked content")
    root = tmp_path / "skills"
    root.mkdir()
    (root / "linked-skill").symlink_to(external_skill.parent, target_is_directory=True)

    with pytest.raises(SkillPathError, match="directory may not be a symlink"):
        SkillRegistry([SkillRoot(root, origin="project")])

    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    manifest = _write_skill(
        safe_root,
        "reference-skill",
        description="Linked reference",
        body="Read [linked](linked.md).",
    )
    (manifest.parent / "linked.md").symlink_to(tmp_path / "external-file.md")
    (tmp_path / "external-file.md").write_text("external", encoding="utf-8")

    with pytest.raises(SkillPathError, match="reference may not be a symlink"):
        SkillRegistry([SkillRoot(safe_root, origin="project")])


def test_reference_is_revalidated_when_disclosed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    manifest = _write_skill(
        root,
        "mutable-reference",
        description="Read mutable reference",
        body="Read [data](data.md).",
    )
    reference = manifest.parent / "data.md"
    reference.write_text("safe", encoding="utf-8")
    registry = SkillRegistry([SkillRoot(root, origin="project")])
    reference.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("unsafe", encoding="utf-8")
    reference.symlink_to(outside)

    with pytest.raises(SkillPathError, match="reference may not be a symlink"):
        registry.select(goal="$mutable-reference")


def test_catalog_and_selection_respect_byte_and_token_bounds(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(
        root,
        "bounded-skill",
        description="Handle bounded payloads",
        body="payload " * 1_000,
    )
    registry = SkillRegistry([SkillRoot(root, origin="project")])

    catalog = registry.build_catalog(max_bytes=80, max_tokens=20)
    selection = registry.select(
        goal="$bounded-skill",
        max_bytes=100,
        max_tokens=25,
    )

    assert catalog.byte_count <= 80
    assert catalog.estimated_tokens <= 20
    assert catalog.truncated is True
    assert selection.byte_count <= 100
    assert selection.estimated_tokens <= 25
    assert selection.truncated is True
    assert selection.skills[0].content.endswith("[skill content truncated]\n")


def test_lexical_ties_are_deterministic_by_precedence_then_name(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_skill(first, "zebra-migration", description="Database migration")
    _write_skill(second, "alpha-migration", description="Database migration")
    registry = SkillRegistry(
        [
            SkillRoot(first, origin="late", precedence=20),
            SkillRoot(second, origin="early", precedence=10),
        ]
    )

    one = registry.select(goal="database migration", top_n=2)
    two = registry.select(goal="database migration", top_n=2)

    assert [skill.name for skill in one.skills] == ["alpha-migration", "zebra-migration"]
    assert one == two
