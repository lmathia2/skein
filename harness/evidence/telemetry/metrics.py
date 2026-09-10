"""Durable, provider-neutral harness metrics.

The store separates cached and uncached input so context-economy regressions are
visible even when total input tokens appear stable.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ModelUsageSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    invocation_id: str
    model: str
    static_prefix_hash: str
    static_prefix_tokens: int = Field(ge=0)
    dynamic_suffix_tokens: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    provider_request_bytes: int = Field(default=0, ge=0)
    provider_request_sha256: str | None = None
    provider_request_regions_json: str = "{}"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cache_read_tokens, 0)


class ToolUsageSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    invocation_id: str
    tool_name: str
    status: Literal["ok", "error", "blocked", "timeout"]
    arguments_hash: str
    result_hash: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    model_visible_bytes: int = Field(default=0, ge=0)
    omitted_bytes: int = Field(default=0, ge=0)
    replayed: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class TaskOutcomeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["complete", "answered", "blocked", "failed"]
    passed: bool
    iterations: int = Field(ge=0)
    compactions: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)
    user_interventions: int = Field(default=0, ge=0)
    changed_files: int = Field(default=0, ge=0)
    tests_passed: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)
    completed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class MetricsStore:
    def __init__(
        self,
        database: Path,
        *,
        on_record: Callable[
            [ModelUsageSample | ToolUsageSample | TaskOutcomeSample], object
        ]
        | None = None,
    ) -> None:
        self.database = database
        self._on_record = on_record
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_usage (
                    sample_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    static_prefix_hash TEXT NOT NULL,
                    static_prefix_tokens INTEGER NOT NULL,
                    dynamic_suffix_tokens INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    provider_request_bytes INTEGER NOT NULL DEFAULT 0,
                    provider_request_sha256 TEXT,
                    provider_request_regions_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_model_usage_task
                ON model_usage(task_id, created_at);

                CREATE TABLE IF NOT EXISTS tool_usage (
                    sample_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    result_hash TEXT,
                    duration_ms INTEGER NOT NULL,
                    model_visible_bytes INTEGER NOT NULL,
                    omitted_bytes INTEGER NOT NULL,
                    replayed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_tool_usage_task
                ON tool_usage(task_id, created_at);

                CREATE TABLE IF NOT EXISTS task_outcomes (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    iterations INTEGER NOT NULL,
                    compactions INTEGER NOT NULL,
                    replans INTEGER NOT NULL,
                    user_interventions INTEGER NOT NULL,
                    changed_files INTEGER NOT NULL,
                    tests_passed INTEGER NOT NULL,
                    tests_failed INTEGER NOT NULL,
                    wall_time_ms INTEGER NOT NULL,
                    completed_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(model_usage)")
            }
            for statement in (
                "ALTER TABLE model_usage ADD COLUMN provider_request_bytes INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE model_usage ADD COLUMN provider_request_sha256 TEXT",
                "ALTER TABLE model_usage ADD COLUMN provider_request_regions_json TEXT NOT NULL DEFAULT '{}'",
            ):
                name = statement.split()[5]
                if name not in columns:
                    connection.execute(statement)

    def record_model_usage(self, sample: ModelUsageSample) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO model_usage (
                    sample_id, task_id, invocation_id, model,
                    static_prefix_hash, static_prefix_tokens,
                    dynamic_suffix_tokens, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens, reasoning_tokens,
                    cost_usd, latency_ms, provider_request_bytes,
                    provider_request_sha256, provider_request_regions_json, created_at
                ) VALUES (
                    :sample_id, :task_id, :invocation_id, :model,
                    :static_prefix_hash, :static_prefix_tokens,
                    :dynamic_suffix_tokens, :input_tokens, :output_tokens,
                    :cache_read_tokens, :cache_write_tokens, :reasoning_tokens,
                    :cost_usd, :latency_ms, :provider_request_bytes,
                    :provider_request_sha256, :provider_request_regions_json, :created_at
                )
                """,
                sample.model_dump(mode="python"),
            )
        if self._on_record is not None:
            self._on_record(sample)

    def record_tool_usage(self, sample: ToolUsageSample) -> None:
        data = sample.model_dump(mode="python")
        data["replayed"] = int(sample.replayed)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tool_usage VALUES (
                    :sample_id, :task_id, :invocation_id, :tool_name, :status,
                    :arguments_hash, :result_hash, :duration_ms,
                    :model_visible_bytes, :omitted_bytes, :replayed, :created_at
                )
                """,
                data,
            )
        if self._on_record is not None:
            self._on_record(sample)

    def record_outcome(self, sample: TaskOutcomeSample) -> None:
        data = sample.model_dump(mode="python")
        data["passed"] = int(sample.passed)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_outcomes VALUES (
                    :task_id, :status, :passed, :iterations, :compactions,
                    :replans, :user_interventions, :changed_files,
                    :tests_passed, :tests_failed, :wall_time_ms, :completed_at
                )
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    passed=excluded.passed,
                    iterations=excluded.iterations,
                    compactions=excluded.compactions,
                    replans=excluded.replans,
                    user_interventions=excluded.user_interventions,
                    changed_files=excluded.changed_files,
                    tests_passed=excluded.tests_passed,
                    tests_failed=excluded.tests_failed,
                    wall_time_ms=excluded.wall_time_ms,
                    completed_at=excluded.completed_at
                """,
                data,
            )
        if self._on_record is not None:
            self._on_record(sample)

    def task_summary(self, task_id: str) -> dict[str, float | int | str | None]:
        with self._connect() as connection:
            usage = connection.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                    COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                    COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                    COALESCE(SUM(latency_ms), 0) AS model_latency_ms,
                    COALESCE(SUM(provider_request_bytes), 0) AS provider_request_bytes,
                    COALESCE(MAX(input_tokens), 0) AS peak_context_tokens,
                    COUNT(*) AS model_calls,
                    COUNT(DISTINCT static_prefix_hash) AS prefix_versions
                FROM model_usage WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
            tools = connection.execute(
                """
                SELECT
                    COUNT(*) AS tool_calls,
                    COALESCE(SUM(model_visible_bytes), 0) AS model_visible_bytes,
                    COALESCE(SUM(omitted_bytes), 0) AS omitted_bytes,
                    COALESCE(SUM(replayed), 0) AS replayed_calls
                FROM tool_usage WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
            outcome = connection.execute(
                "SELECT * FROM task_outcomes WHERE task_id=?",
                (task_id,),
            ).fetchone()

        data: dict[str, float | int | str | None] = {
            **dict(usage),
            **dict(tools),
        }
        input_tokens = int(data["input_tokens"] or 0)
        cache_read = int(data["cache_read_tokens"] or 0)
        data["uncached_input_tokens"] = max(input_tokens - cache_read, 0)
        data["cache_read_ratio"] = cache_read / input_tokens if input_tokens else 0.0
        if outcome:
            for key, value in dict(outcome).items():
                data[f"outcome_{key}"] = value
            data["cost_per_passed_task"] = (
                float(data["cost_usd"] or 0.0) if outcome["passed"] else None
            )
        else:
            data["cost_per_passed_task"] = None
        return data
