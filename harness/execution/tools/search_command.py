"""Strict grammar for the in-process repository search virtual command."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal, cast

from harness.execution.repo import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_CONTEXT,
    MAX_SEARCH_LIMIT,
    SearchMode,
)

SearchCommandOperation = Literal["grep", "find", "health"]

_RESERVED = re.compile(r"^\s*search(?:\s|$)")
_CURSOR = re.compile(r"^fff_[0-9a-f]{64}_[0-9]{1,6}$")
_SHELL_PUNCTUATION = frozenset(";&|<>")
_MAX_COMMAND_CHARS = 4_096


class SearchCommandParseError(ValueError):
    """Raised when a reserved search command is malformed."""


@dataclass(frozen=True, slots=True)
class SearchCommand:
    """Validated arguments for a virtual search operation."""

    operation: SearchCommandOperation
    pattern: str | None = None
    path: str | None = None
    mode: SearchMode = "literal"
    case_sensitive: bool = False
    context: int = 0
    limit: int = DEFAULT_SEARCH_LIMIT
    cursor: str | None = None


def _integer(name: str, value: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SearchCommandParseError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise SearchCommandParseError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return parsed


def _tokens(command: str) -> list[str] | None:
    if not _RESERVED.match(command):
        return None
    if len(command) > _MAX_COMMAND_CHARS or any(char in command for char in "\x00\r\n"):
        raise SearchCommandParseError("search command is too long or contains an invalid character")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise SearchCommandParseError(f"cannot parse search command: {exc}") from exc
    if any(token and set(token) <= _SHELL_PUNCTUATION for token in tokens):
        raise SearchCommandParseError("shell control operators are not allowed in search commands")
    return tokens


def parse_search_command(
    command: str,
    *,
    default_limit: int = DEFAULT_SEARCH_LIMIT,
    max_limit: int = MAX_SEARCH_LIMIT,
) -> SearchCommand | None:
    """Return a validated virtual command, or ``None`` for ordinary shell input."""

    if not 1 <= default_limit <= max_limit <= MAX_SEARCH_LIMIT:
        raise ValueError("search page limits must be ordered within the backend maximum")

    tokens = _tokens(command)
    if tokens is None:
        return None
    if len(tokens) < 2 or tokens[0] != "search":
        raise SearchCommandParseError("usage: search grep|find|health ...")
    operation = tokens[1]
    if operation == "health":
        if len(tokens) != 2:
            raise SearchCommandParseError("usage: search health")
        return SearchCommand(operation="health")
    if operation not in {"grep", "find"}:
        raise SearchCommandParseError("search operation must be grep, find, or health")
    parsed_operation = cast(Literal["grep", "find"], operation)

    values: dict[str, str] = {}
    case_sensitive = False
    index = 2
    while index < len(tokens):
        flag = tokens[index]
        if flag == "--case-sensitive":
            if operation != "grep":
                raise SearchCommandParseError("--case-sensitive is only valid for search grep")
            if case_sensitive:
                raise SearchCommandParseError("duplicate option: --case-sensitive")
            case_sensitive = True
            index += 1
            continue
        allowed = {"--pattern", "--path", "--limit", "--cursor"}
        if operation == "grep":
            allowed.update({"--mode", "--context"})
        if flag not in allowed:
            raise SearchCommandParseError(f"unknown search option: {flag}")
        if flag in values:
            raise SearchCommandParseError(f"duplicate option: {flag}")
        if index + 1 >= len(tokens):
            raise SearchCommandParseError(f"missing value for {flag}")
        values[flag] = tokens[index + 1]
        index += 2

    cursor = values.get("--cursor")
    if cursor is not None:
        if len(values) != 1 or case_sensitive:
            raise SearchCommandParseError("cursor continuation cannot change search options")
        if _CURSOR.fullmatch(cursor) is None:
            raise SearchCommandParseError("search cursor is malformed")
        return SearchCommand(operation=parsed_operation, cursor=cursor)

    pattern = values.get("--pattern")
    if pattern is None:
        raise SearchCommandParseError("--pattern is required")
    mode = values.get("--mode", "literal")
    if mode not in {"literal", "regex"}:
        raise SearchCommandParseError("--mode must be literal or regex")
    parsed_mode = cast(SearchMode, mode)
    limit = _integer(
        "--limit",
        values.get("--limit", str(default_limit)),
        minimum=1,
        maximum=max_limit,
    )
    context = _integer(
        "--context",
        values.get("--context", "0"),
        minimum=0,
        maximum=MAX_SEARCH_CONTEXT,
    )
    return SearchCommand(
        operation=parsed_operation,
        pattern=pattern,
        path=values.get("--path"),
        mode=parsed_mode,
        case_sensitive=case_sensitive,
        context=context,
        limit=limit,
    )


__all__ = ["SearchCommand", "SearchCommandParseError", "parse_search_command"]
