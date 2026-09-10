"""Workspace-confined, snapshot-paginated access to the native FFF index."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from fff import FFFException, FileFinder, GrepCursor

LOGGER = logging.getLogger(__name__)

SearchOperation = Literal["grep", "find"]
SearchMode = Literal["literal", "regex"]

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50
MAX_SEARCH_CONTEXT = 2
MAX_PATTERN_CHARS = 1_000
MAX_SCOPE_CHARS = 500
MAX_GREP_FILE_BYTES = 10 * 1024 * 1024
MAX_MATCHES_PER_FILE = 500
MAX_SNAPSHOT_MATCHES = 5_000
NATIVE_BATCH_TARGET = 200
MAX_NATIVE_BATCHES = 25
PER_FILE_PAGE_LIMIT = 5
MAX_RENDERED_LINE_CHARS = 300
_CURSOR = re.compile(r"^fff_(?P<snapshot>[0-9a-f]{64})_(?P<page>[0-9]{1,6})$")
_INTERNAL_PARTS = {".artifacts", ".git"}


class SearchError(RuntimeError):
    """Base error for deterministic virtual search operations."""


class SearchUnavailableError(SearchError):
    """Raised when the native index cannot answer safely."""


class SearchCursorError(SearchError):
    """Raised for malformed, missing, or stale search cursors."""


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One bounded logical page before the outer tool-output byte cap."""

    operation: SearchOperation
    text: str
    cursor: str | None
    returned_matches: int
    collected_matches: int
    matched_files: int
    has_more: bool
    incomplete: bool
    query_hash: str
    duration_ms: int
    cold_index: bool


class SearchBackend(Protocol):
    """Injectable backend contract used by the managed bash adapter."""

    def grep(
        self,
        *,
        pattern: str | None = None,
        path: str | None = None,
        mode: SearchMode = "literal",
        case_sensitive: bool = False,
        context: int = 0,
        limit: int = DEFAULT_SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> SearchPage: ...

    def find(
        self,
        *,
        pattern: str | None = None,
        path: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> SearchPage: ...

    def health(self) -> Mapping[str, object]: ...

    def refresh(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _MatchRecord:
    path: str
    line: int
    column: int
    git_status: str
    definition: bool
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _FindRecord:
    path: str
    git_status: str
    score: int


@dataclass(frozen=True, slots=True)
class _Snapshot:
    operation: SearchOperation
    backend_version: str
    workspace_hash: str
    query_hash: str
    page_size: int
    context: int
    incomplete: bool
    omitted_matches: int
    matches: tuple[_MatchRecord, ...] = ()
    files: tuple[_FindRecord, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "backend_version": self.backend_version,
            "context": self.context,
            "files": [asdict(item) for item in self.files],
            "incomplete": self.incomplete,
            "matches": [asdict(item) for item in self.matches],
            "omitted_matches": self.omitted_matches,
            "operation": self.operation,
            "page_size": self.page_size,
            "query_hash": self.query_hash,
            "workspace_hash": self.workspace_hash,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> _Snapshot:
        return cls(
            operation=payload["operation"],
            backend_version=str(payload["backend_version"]),
            workspace_hash=str(payload["workspace_hash"]),
            query_hash=str(payload["query_hash"]),
            page_size=int(payload["page_size"]),
            context=int(payload["context"]),
            incomplete=bool(payload["incomplete"]),
            omitted_matches=int(payload["omitted_matches"]),
            matches=tuple(_MatchRecord(**item) for item in payload.get("matches", [])),
            files=tuple(_FindRecord(**item) for item in payload.get("files", [])),
        )


class _SnapshotStore:
    """Content-addressed cursor snapshots without raw query or source bodies."""

    def __init__(self, database: Path, *, retained: int = 200) -> None:
        self.database = database
        self.retained = max(retained, 10)
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fff_search_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_ns INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=30)

    def put(self, snapshot: _Snapshot) -> str:
        payload_json = json.dumps(
            snapshot.payload(), sort_keys=True, separators=(",", ":")
        )
        snapshot_id = hashlib.sha256(payload_json.encode()).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO fff_search_snapshots
                (snapshot_id, payload_json, created_ns) VALUES (?, ?, ?)
                """,
                (snapshot_id, payload_json, time.time_ns()),
            )
            connection.execute(
                """
                DELETE FROM fff_search_snapshots
                WHERE snapshot_id IN (
                    SELECT snapshot_id FROM fff_search_snapshots
                    ORDER BY created_ns DESC, snapshot_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.retained,),
            )
        return snapshot_id

    def get(self, snapshot_id: str) -> _Snapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM fff_search_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise SearchCursorError("search cursor is missing or expired; rerun the search")
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise SearchCursorError("search cursor snapshot is invalid")
        return _Snapshot.from_payload(payload)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
    return limit


def _validate_context(context: int) -> int:
    if isinstance(context, bool) or not 0 <= context <= MAX_SEARCH_CONTEXT:
        raise ValueError(f"context must be between 0 and {MAX_SEARCH_CONTEXT}")
    return context


def _validate_pattern(pattern: str | None) -> str:
    if pattern is None or not pattern.strip():
        raise ValueError("pattern is required when cursor is not supplied")
    if len(pattern) > MAX_PATTERN_CHARS or any(char in pattern for char in "\x00\r\n"):
        raise ValueError("pattern is too long or contains an invalid character")
    return pattern


class FffSearchService:
    """Own FFF lifecycle, confinement, pagination snapshots, and rendering."""

    def __init__(
        self,
        workspace: Path,
        state_root: Path,
        *,
        index_timeout_ms: int = 5_000,
        finder_factory: Callable[..., FileFinder] = FileFinder,
    ) -> None:
        self.workspace = workspace.resolve()
        self.state_root = state_root.resolve()
        self.index_timeout_ms = max(100, min(index_timeout_ms, 30_000))
        self.finder_factory = finder_factory
        self._workspace_hash = _hash_text(self.workspace.as_posix())
        self._finder: FileFinder | None = None
        self._initialization_error: str | None = None
        self._lock = threading.RLock()
        self._snapshots = _SnapshotStore(state_root / "fff" / "search.db")
        if self.workspace == Path(self.workspace.anchor) or self.workspace == Path.home().resolve():
            raise ValueError("FFF search refuses filesystem-root and home-directory workspaces")

    def _new_finder(self) -> FileFinder:
        data_root = self.state_root / "fff"
        frecency = data_root / "frecency"
        history = data_root / "history"
        frecency.mkdir(parents=True, exist_ok=True)
        history.mkdir(parents=True, exist_ok=True)
        options = {
            "ai_mode": True,
            "enable_fs_root_scanning": False,
            "enable_home_dir_scanning": False,
            "follow_symlinks": False,
            "watch": True,
        }
        try:
            return self.finder_factory(
                self.workspace,
                frecency_db_path=frecency,
                history_db_path=history,
                **options,
            )
        except (FFFException, OSError):
            LOGGER.warning(
                "FFF persistence unavailable; continuing with an ephemeral index",
                exc_info=True,
            )
            return self.finder_factory(self.workspace, **options)

    def _ready_finder(self) -> tuple[FileFinder, bool]:
        with self._lock:
            cold = self._finder is None
            if self._finder is None:
                try:
                    self._finder = self._new_finder()
                except Exception as exc:
                    self._initialization_error = f"{type(exc).__name__}: {exc}"
                    raise SearchUnavailableError(
                        "FFF index initialization failed; use a bounded rg query"
                    ) from exc
            finder = self._finder
            try:
                ready = finder.wait_for_scan_blocking(self.index_timeout_ms)
            except Exception as exc:
                raise SearchUnavailableError(
                    "FFF index readiness check failed; use a bounded rg query"
                ) from exc
            if not ready and finder.scan_progress.scanned_files_count == 0:
                raise SearchUnavailableError(
                    "FFF index is still cold; retry or use a bounded rg query"
                )
            return finder, cold

    def _scope(self, value: str | None) -> str | None:
        if value is None or value in {"", "."}:
            return None
        if len(value) > MAX_SCOPE_CHARS or any(char in value for char in "\x00\r\n"):
            raise ValueError("path scope is too long or contains an invalid character")
        candidate = Path(value)
        if candidate.is_absolute() or value.startswith("~") or ".." in candidate.parts:
            raise ValueError("path scope must remain inside the workspace")
        target = (self.workspace / candidate).resolve(strict=True)
        try:
            relative = target.relative_to(self.workspace).as_posix()
        except ValueError as exc:
            raise ValueError("path scope leaves the workspace") from exc
        if any(part in _INTERNAL_PARTS for part in Path(relative).parts):
            raise ValueError("path scope targets harness-internal data")
        if not target.is_file() and not target.is_dir():
            raise ValueError("path scope must identify a file or directory")
        return relative

    def _result_path(self, relative: str, scope: str | None) -> tuple[str, Path]:
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise SearchUnavailableError("FFF returned an invalid repository path")
        try:
            target = (self.workspace / relative).resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise SearchUnavailableError("FFF returned a missing repository path") from exc
        try:
            canonical = target.relative_to(self.workspace).as_posix()
        except ValueError as exc:
            raise SearchUnavailableError("FFF returned a path outside the workspace") from exc
        if not target.is_file() or any(
            part in _INTERNAL_PARTS for part in Path(canonical).parts
        ):
            raise SearchUnavailableError("FFF returned a non-source or internal path")
        if scope is not None and canonical != scope and not canonical.startswith(scope + "/"):
            raise SearchUnavailableError("FFF returned a result outside the requested scope")
        return canonical, target

    @staticmethod
    def _query(pattern: str, scope: str | None) -> str:
        return f"{scope} {pattern}" if scope else pattern

    def _cursor(self, snapshot_id: str, page: int) -> str:
        return f"fff_{snapshot_id}_{page}"

    def _load_cursor(self, cursor: str, operation: SearchOperation) -> tuple[_Snapshot, int]:
        match = _CURSOR.fullmatch(cursor)
        if match is None:
            raise SearchCursorError("search cursor is malformed")
        snapshot = self._snapshots.get(match.group("snapshot"))
        if snapshot.workspace_hash != self._workspace_hash:
            raise SearchCursorError("search cursor belongs to a different workspace")
        if snapshot.operation != operation:
            raise SearchCursorError("search cursor belongs to a different operation")
        return snapshot, int(match.group("page"))

    @staticmethod
    def _grep_pages(snapshot: _Snapshot) -> list[list[_MatchRecord]]:
        queues: dict[str, list[_MatchRecord]] = defaultdict(list)
        order: list[str] = []
        for record in snapshot.matches:
            if record.path not in queues:
                order.append(record.path)
            queues[record.path].append(record)
        offsets = {path: 0 for path in order}
        pages: list[list[_MatchRecord]] = []
        while any(offsets[path] < len(queues[path]) for path in order):
            page: list[_MatchRecord] = []
            for path in order:
                available = queues[path]
                start = offsets[path]
                if start >= len(available):
                    continue
                count = min(
                    PER_FILE_PAGE_LIMIT,
                    len(available) - start,
                    snapshot.page_size - len(page),
                )
                page.extend(available[start : start + count])
                offsets[path] += count
                if len(page) >= snapshot.page_size:
                    break
            if not page:
                break
            pages.append(page)
        return pages

    def _render_grep_page(self, snapshot: _Snapshot, page_index: int) -> SearchPage:
        started = time.monotonic()
        pages = self._grep_pages(snapshot)
        if page_index >= len(pages):
            raise SearchCursorError("search cursor page is out of range")
        records = pages[page_index]
        by_path: dict[str, list[_MatchRecord]] = defaultdict(list)
        path_order: list[str] = []
        for record in records:
            if record.path not in by_path:
                path_order.append(record.path)
            by_path[record.path].append(record)
        lines = [
            f"FFF grep page {page_index + 1}: {len(records)} of "
            f"{len(snapshot.matches)} collected matches"
        ]
        for relative in path_order:
            target = (self.workspace / relative).resolve(strict=True)
            try:
                target.relative_to(self.workspace)
            except ValueError as exc:
                raise SearchCursorError("search snapshot path left the workspace") from exc
            if not target.is_file() or _file_sha256(target) != by_path[relative][0].content_sha256:
                raise SearchCursorError("search cursor is stale because a matched file changed")
            source_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            status = by_path[relative][0].git_status
            annotation = f" [git:{status}]" if status not in {"", "clean", "unknown"} else ""
            lines.append(f"\n{relative}{annotation}")
            emitted: set[int] = set()
            match_lines = {record.line for record in by_path[relative]}
            for record in sorted(by_path[relative], key=lambda item: (item.line, item.column)):
                start = max(1, record.line - snapshot.context)
                end = min(len(source_lines), record.line + snapshot.context)
                for number in range(start, end + 1):
                    if number in emitted:
                        continue
                    emitted.add(number)
                    marker = ":" if number in match_lines else "-"
                    body = source_lines[number - 1].strip()
                    if len(body) > MAX_RENDERED_LINE_CHARS:
                        body = body[:MAX_RENDERED_LINE_CHARS] + "..."
                    lines.append(f"  {number}{marker} {body}")
        has_more = page_index + 1 < len(pages)
        snapshot_id = self._snapshots.put(snapshot)
        cursor = self._cursor(snapshot_id, page_index + 1) if has_more else None
        if cursor:
            lines.append(f'\n[Continue with cursor="{cursor}"]')
        if snapshot.incomplete:
            lines.append(
                "\n[Results are incomplete because the bounded index snapshot reached "
                "a safety cap; narrow the pattern or path.]"
            )
        return SearchPage(
            operation="grep",
            text="\n".join(lines),
            cursor=cursor,
            returned_matches=len(records),
            collected_matches=len(snapshot.matches),
            matched_files=len({record.path for record in snapshot.matches}),
            has_more=has_more,
            incomplete=snapshot.incomplete,
            query_hash=snapshot.query_hash,
            duration_ms=int((time.monotonic() - started) * 1_000),
            cold_index=False,
        )

    def grep(
        self,
        *,
        pattern: str | None = None,
        path: str | None = None,
        mode: SearchMode = "literal",
        case_sensitive: bool = False,
        context: int = 0,
        limit: int = DEFAULT_SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> SearchPage:
        started = time.monotonic()
        if cursor is not None:
            if pattern is not None or path is not None:
                raise ValueError("cursor continuation cannot change pattern or path")
            snapshot, page_index = self._load_cursor(cursor, "grep")
            page = self._render_grep_page(snapshot, page_index)
            return replace(
                page,
                duration_ms=int((time.monotonic() - started) * 1_000),
            )
        pattern = _validate_pattern(pattern)
        scope = self._scope(path)
        limit = _validate_limit(limit)
        context = _validate_context(context)
        if mode not in {"literal", "regex"}:
            raise ValueError("mode must be literal or regex")
        query = self._query(pattern, scope)
        query_hash = _hash_text(
            json.dumps(
                {
                    "case_sensitive": case_sensitive,
                    "context": context,
                    "mode": mode,
                    "pattern": pattern,
                    "scope": scope,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        finder, cold = self._ready_finder()
        records: list[_MatchRecord] = []
        next_cursor: GrepCursor | None = None
        incomplete = False
        batches = 0
        with self._lock:
            while batches < MAX_NATIVE_BATCHES and len(records) < MAX_SNAPSHOT_MATCHES:
                result = finder.grep(
                    query,
                    mode="plain" if mode == "literal" else "regex",
                    max_file_size=MAX_GREP_FILE_BYTES,
                    max_matches_per_file=MAX_MATCHES_PER_FILE,
                    smart_case=not case_sensitive,
                    cursor=next_cursor,
                    page_limit=NATIVE_BATCH_TARGET,
                    time_budget_ms=0,
                    before_context=0,
                    after_context=0,
                    classify_definitions=True,
                )
                if result.regex_fallback_error:
                    raise ValueError(f"invalid regex: {result.regex_fallback_error}")
                per_file: dict[str, int] = defaultdict(int)
                for item in result.items:
                    relative, target = self._result_path(item.relative_path, scope)
                    per_file[relative] += 1
                    records.append(
                        _MatchRecord(
                            path=relative,
                            line=item.line_number,
                            column=item.col,
                            git_status=item.git_status,
                            definition=item.is_definition,
                            content_sha256=_file_sha256(target),
                        )
                    )
                    if len(records) >= MAX_SNAPSHOT_MATCHES:
                        incomplete = True
                        break
                if any(count >= MAX_MATCHES_PER_FILE for count in per_file.values()):
                    incomplete = True
                batches += 1
                next_cursor = result.next_cursor()
                if next_cursor is None or len(records) >= MAX_SNAPSHOT_MATCHES:
                    break
            if next_cursor is not None or batches >= MAX_NATIVE_BATCHES:
                incomplete = True
        snapshot = _Snapshot(
            operation="grep",
            backend_version="fff-search/0.10.5",
            workspace_hash=self._workspace_hash,
            query_hash=query_hash,
            page_size=limit,
            context=context,
            incomplete=incomplete,
            omitted_matches=0,
            matches=tuple(records),
        )
        self._snapshots.put(snapshot)
        if not records:
            return SearchPage(
                operation="grep",
                text="FFF grep: no matches found",
                cursor=None,
                returned_matches=0,
                collected_matches=0,
                matched_files=0,
                has_more=False,
                incomplete=incomplete,
                query_hash=query_hash,
                duration_ms=int((time.monotonic() - started) * 1_000),
                cold_index=cold,
            )
        page = self._render_grep_page(snapshot, 0)
        return replace(
            page,
            duration_ms=int((time.monotonic() - started) * 1_000),
            cold_index=cold,
        )

    def _render_find_page(self, snapshot: _Snapshot, page_index: int) -> SearchPage:
        started = time.monotonic()
        start = page_index * snapshot.page_size
        records = snapshot.files[start : start + snapshot.page_size]
        if not records:
            raise SearchCursorError("search cursor page is out of range")
        lines = [
            f"FFF find page {page_index + 1}: {len(records)} of "
            f"{len(snapshot.files)} collected paths"
        ]
        for record in records:
            target = (self.workspace / record.path).resolve(strict=True)
            try:
                target.relative_to(self.workspace)
            except ValueError as exc:
                raise SearchCursorError("search snapshot path left the workspace") from exc
            if not target.is_file():
                raise SearchCursorError("search cursor is stale because a matched file changed")
            annotation = (
                f" [git:{record.git_status}]"
                if record.git_status not in {"", "clean", "unknown"}
                else ""
            )
            lines.append(f"{record.path}{annotation}")
        has_more = start + len(records) < len(snapshot.files)
        snapshot_id = self._snapshots.put(snapshot)
        cursor = self._cursor(snapshot_id, page_index + 1) if has_more else None
        if cursor:
            lines.append(f'\n[Continue with cursor="{cursor}"]')
        if snapshot.incomplete:
            lines.append("\n[Path results were capped; narrow the pattern or path.]")
        return SearchPage(
            operation="find",
            text="\n".join(lines),
            cursor=cursor,
            returned_matches=len(records),
            collected_matches=len(snapshot.files),
            matched_files=len(snapshot.files),
            has_more=has_more,
            incomplete=snapshot.incomplete,
            query_hash=snapshot.query_hash,
            duration_ms=int((time.monotonic() - started) * 1_000),
            cold_index=False,
        )

    def find(
        self,
        *,
        pattern: str | None = None,
        path: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        cursor: str | None = None,
    ) -> SearchPage:
        started = time.monotonic()
        if cursor is not None:
            if pattern is not None or path is not None:
                raise ValueError("cursor continuation cannot change pattern or path")
            snapshot, page_index = self._load_cursor(cursor, "find")
            page = self._render_find_page(snapshot, page_index)
            return replace(
                page,
                duration_ms=int((time.monotonic() - started) * 1_000),
            )
        pattern = _validate_pattern(pattern)
        scope = self._scope(path)
        limit = _validate_limit(limit)
        query = self._query(pattern, scope)
        query_hash = _hash_text(
            json.dumps(
                {"operation": "find", "pattern": pattern, "scope": scope},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        finder, cold = self._ready_finder()
        with self._lock:
            result = finder.search(query, page_index=0, page_size=MAX_SNAPSHOT_MATCHES)
        records: list[_FindRecord] = []
        for item, score in zip(result.items, result.scores, strict=True):
            relative, _ = self._result_path(item.relative_path, scope)
            records.append(
                _FindRecord(path=relative, git_status=item.git_status, score=score.total)
            )
        incomplete = result.total_matched > len(records)
        snapshot = _Snapshot(
            operation="find",
            backend_version="fff-search/0.10.5",
            workspace_hash=self._workspace_hash,
            query_hash=query_hash,
            page_size=limit,
            context=0,
            incomplete=incomplete,
            omitted_matches=max(0, result.total_matched - len(records)),
            files=tuple(records),
        )
        self._snapshots.put(snapshot)
        if not records:
            return SearchPage(
                operation="find",
                text="FFF find: no paths found",
                cursor=None,
                returned_matches=0,
                collected_matches=0,
                matched_files=0,
                has_more=False,
                incomplete=incomplete,
                query_hash=query_hash,
                duration_ms=int((time.monotonic() - started) * 1_000),
                cold_index=cold,
            )
        page = self._render_find_page(snapshot, 0)
        return replace(
            page,
            duration_ms=int((time.monotonic() - started) * 1_000),
            cold_index=cold,
        )

    def health(self) -> Mapping[str, object]:
        with self._lock:
            if self._finder is None:
                return {
                    "backend": "fff-search/0.10.5",
                    "state": "cold",
                    "initialization_error": self._initialization_error,
                }
            try:
                health = self._finder.health_check()
                progress = self._finder.scan_progress
            except Exception as exc:
                return {
                    "backend": "fff-search/0.10.5",
                    "state": "degraded",
                    "error": type(exc).__name__,
                }
        picker = health.get("file_picker", {})
        return {
            "backend": "fff-search/0.10.5",
            "state": "warming" if progress.is_scanning else "ready",
            "indexed_files": int(picker.get("indexed_files", progress.scanned_files_count)),
            "git_available": bool(health.get("git", {}).get("available", False)),
            "persistent_frecency": bool(health.get("frecency", {}).get("initialized", False)),
        }

    def refresh(self) -> None:
        with self._lock:
            if self._finder is None:
                return
            try:
                self._finder.scan_files()
                self._finder.wait_for_scan_blocking(self.index_timeout_ms)
                self._finder.refresh_git_status()
            except Exception:
                LOGGER.exception("FFF refresh failed; the next query may use a stale index")

    def close(self) -> None:
        with self._lock:
            if self._finder is not None:
                self._finder.close()
                self._finder = None


__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_CONTEXT",
    "MAX_SEARCH_LIMIT",
    "FffSearchService",
    "SearchBackend",
    "SearchCursorError",
    "SearchError",
    "SearchMode",
    "SearchPage",
    "SearchUnavailableError",
]
