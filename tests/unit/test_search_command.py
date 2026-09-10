from __future__ import annotations

import pytest

from harness.execution.tools.search_command import SearchCommandParseError, parse_search_command


def test_search_parser_accepts_bounded_grep_find_health_and_cursor() -> None:
    grep = parse_search_command(
        'search grep --pattern "TODO fix" --path src --mode regex '
        "--case-sensitive --context 2 --limit 17"
    )
    find = parse_search_command('search find --pattern "app service" --limit 3')
    health = parse_search_command("search health")
    cursor = parse_search_command("search grep --cursor fff_" + "a" * 64 + "_2")

    assert grep is not None
    assert grep.pattern == "TODO fix"
    assert grep.path == "src"
    assert grep.mode == "regex"
    assert grep.case_sensitive is True
    assert grep.context == 2
    assert grep.limit == 17
    assert find is not None and find.operation == "find" and find.limit == 3
    assert health is not None and health.operation == "health"
    assert cursor is not None and cursor.cursor == "fff_" + "a" * 64 + "_2"
    assert parse_search_command("rg TODO") is None


@pytest.mark.parametrize(
    "command",
    [
        "search grep",
        "search grep --pattern TODO --limit 51",
        "search grep --pattern TODO --pattern FIXME",
        "search grep --cursor fff_bad_1",
        "search grep --cursor fff_" + "a" * 64 + "_1 --limit 2",
        "search find --pattern app --case-sensitive",
        "search health --limit 1",
        "search grep --pattern TODO | sh",
        "search grep --pattern TODO\nwhoami",
    ],
)
def test_reserved_search_commands_fail_closed(command: str) -> None:
    with pytest.raises(SearchCommandParseError):
        parse_search_command(command)


def test_quoted_shell_metacharacters_are_safe_literal_pattern_text() -> None:
    command = parse_search_command("search grep --pattern 'left | right; still text'")

    assert command is not None
    assert command.pattern == "left | right; still text"


def test_search_parser_applies_configured_default_and_maximum() -> None:
    command = parse_search_command(
        "search find --pattern app",
        default_limit=7,
        max_limit=9,
    )

    assert command is not None and command.limit == 7
    with pytest.raises(SearchCommandParseError, match="between 1 and 9"):
        parse_search_command(
            "search find --pattern app --limit 10",
            default_limit=7,
            max_limit=9,
        )
