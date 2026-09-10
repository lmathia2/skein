"""Repository discovery and bounded lexical search."""

from .discovery import (
    BuildCommand,
    RepositoryManifest,
    build_repository_manifest,
    collect_project_instructions,
    discover_build_commands,
    discover_instruction_files,
    repository_manifest_from_snapshot,
)
from .fff_search import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_CONTEXT,
    MAX_SEARCH_LIMIT,
    FffSearchService,
    SearchBackend,
    SearchCursorError,
    SearchError,
    SearchMode,
    SearchPage,
    SearchUnavailableError,
)

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_CONTEXT",
    "MAX_SEARCH_LIMIT",
    "BuildCommand",
    "FffSearchService",
    "RepositoryManifest",
    "SearchBackend",
    "SearchCursorError",
    "SearchError",
    "SearchMode",
    "SearchPage",
    "SearchUnavailableError",
    "build_repository_manifest",
    "collect_project_instructions",
    "discover_build_commands",
    "discover_instruction_files",
    "repository_manifest_from_snapshot",
]
