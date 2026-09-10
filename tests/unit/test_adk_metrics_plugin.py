from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Any

import pytest

pytest.importorskip("google.adk")

from google.adk.sessions.state import State

from harness.evidence.telemetry.adk_plugin import (
    HarnessMetricsPlugin,
    ModelPricing,
    estimate_cost,
    usage_counts,
)


@dataclass
class _Usage:
    prompt_token_count: int = 1_000
    candidates_token_count: int = 200
    cached_content_token_count: int = 800
    thoughts_token_count: int = 50


@dataclass
class _Response:
    usage_metadata: _Usage
    partial: bool | None = None
    model_version: str | None = None
    custom_metadata: dict[str, Any] | None = None


@dataclass
class _Request:
    model: str = "test-model"


@dataclass
class _Tool:
    name: str


@dataclass
class _Context:
    invocation_id: str = "invocation-1"
    state: Any = None
    session: Any = None


@dataclass
class _Session:
    id: str


def test_usage_extraction_and_optional_pricing() -> None:
    counts = usage_counts(_Response(usage_metadata=_Usage()))
    assert counts == {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "cache_read_tokens": 800,
        "cache_write_tokens": 0,
        "reasoning_tokens": 50,
    }
    cost = estimate_cost(
        counts,
        ModelPricing(input=1.0, output=2.0, cache_read=0.1, reasoning=3.0),
    )
    assert cost == pytest.approx((200 + 400 + 80 + 150) / 1_000_000)


def test_plugin_records_one_model_call(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
        pricing={"test-model": ModelPricing(input=1.0)},
    )
    context = _Context(
        state={
            "task_id": "task-1",
            "dynamic_context_tokens_estimate": 250,
        }
    )

    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )

    summary = plugin.store.task_summary("task-1")
    assert summary["model_calls"] == 1
    assert summary["input_tokens"] == 1_000
    assert summary["cache_read_tokens"] == 800
    assert summary["prefix_versions"] == 1


def test_plugin_does_not_count_host_work_batch_yield_as_a_model_call(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    context = _Context(state={"task_id": "task-1"})
    asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=_Request()))
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(
                usage_metadata=_Usage(),
                custom_metadata={"skein_work_batch_yield": True},
            ),
        )
    )

    assert plugin.store.task_summary("task-1")["model_calls"] == 0


def test_plugin_enforces_actual_input_budget_before_each_inner_model_call(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    context = _Context(state={"task_id": "task-1", "task_input_token_limit": 1_000})
    asyncio.run(plugin.before_model_callback(callback_context=context, llm_request=_Request()))
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage(prompt_token_count=1_000)),
        )
    )

    with pytest.raises(RuntimeError, match="input-token budget exhausted"):
        asyncio.run(
            plugin.before_model_callback(callback_context=context, llm_request=_Request())
        )


def test_plugin_prefers_provider_reported_cost_and_concrete_model(tmp_path) -> None:
    database = tmp_path / "metrics.db"
    plugin = HarnessMetricsPlugin(
        database=database,
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="openrouter/pareto-code",
        default_task_id="task-1",
        pricing={"routed/model": ModelPricing(input=99.0)},
    )
    context = _Context()
    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(model="openrouter/pareto-code"),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(
                usage_metadata=_Usage(),
                model_version="routed/model",
                custom_metadata={"provider_cost_usd": 0.0123},
            ),
        )
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT model, cost_usd FROM model_usage").fetchone()
    assert row == ("routed/model", 0.0123)


def test_plugin_captures_per_call_prefix_before_state_changes(tmp_path) -> None:
    database = tmp_path / "metrics.db"
    plugin = HarnessMetricsPlugin(
        database=database,
        static_prefix_hash="default-prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    state = {
        "stable_instruction_sha256": "review-prefix",
        "static_prefix_tokens_estimate": 125,
        "dynamic_context_tokens_estimate": 250,
    }
    context = _Context(state=state)
    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(),
        )
    )
    state.update(
        {
            "stable_instruction_sha256": "changed-after-start",
            "static_prefix_tokens_estimate": 999,
            "dynamic_context_tokens_estimate": 999,
        }
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT static_prefix_hash, static_prefix_tokens, dynamic_suffix_tokens "
            "FROM model_usage"
        ).fetchone()
    assert row == ("review-prefix", 125, 250)


def test_plugin_reads_real_adk_state_and_session_fallback(tmp_path) -> None:
    database = tmp_path / "metrics.db"
    plugin = HarnessMetricsPlugin(
        database=database,
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
    )
    context = _Context(
        state=State(
            value={
                "task_id": "task-from-state",
                "dynamic_context_tokens_estimate": 321,
            },
            delta={},
        ),
        session=_Session(id="session-fallback"),
    )

    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT task_id, dynamic_suffix_tokens FROM model_usage"
        ).fetchone()
    assert row == ("task-from-state", 321)

    fallback_context = _Context(
        invocation_id="invocation-2",
        state=State(value={}, delta={}),
        session=_Session(id="session-fallback"),
    )
    asyncio.run(
        plugin.before_model_callback(
            callback_context=fallback_context,
            llm_request=_Request(),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=fallback_context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )
    assert plugin.store.task_summary("session-fallback")["model_calls"] == 1


def test_streaming_records_only_the_final_model_response(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    context = _Context()

    asyncio.run(
        plugin.before_model_callback(
            callback_context=context,
            llm_request=_Request(),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage(), partial=True),
        )
    )
    assert plugin.store.task_summary("task-1")["model_calls"] == 0

    asyncio.run(
        plugin.after_model_callback(
            callback_context=context,
            llm_response=_Response(usage_metadata=_Usage(), partial=False),
        )
    )
    assert plugin.store.task_summary("task-1")["model_calls"] == 1


def test_model_error_discards_pending_call_metadata(tmp_path) -> None:
    database = tmp_path / "metrics.db"
    plugin = HarnessMetricsPlugin(
        database=database,
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="fallback-model",
    )
    failed_context = _Context(
        state=State(value={"task_id": "failed-task"}, delta={})
    )
    successful_context = _Context(
        state=State(value={"task_id": "successful-task"}, delta={})
    )

    asyncio.run(
        plugin.before_model_callback(
            callback_context=failed_context,
            llm_request=_Request(model="failed-model"),
        )
    )
    asyncio.run(
        plugin.on_model_error_callback(
            callback_context=failed_context,
            llm_request=_Request(model="failed-model"),
            error=RuntimeError("provider failed"),
        )
    )
    asyncio.run(
        plugin.before_model_callback(
            callback_context=successful_context,
            llm_request=_Request(model="successful-model"),
        )
    )
    asyncio.run(
        plugin.after_model_callback(
            callback_context=successful_context,
            llm_response=_Response(usage_metadata=_Usage()),
        )
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT task_id, model FROM model_usage"
        ).fetchone()
    assert row == ("successful-task", "successful-model")


def test_plugin_records_tool_usage_and_monotonic_action_fingerprints(tmp_path) -> None:
    plugin = HarnessMetricsPlugin(
        database=tmp_path / "metrics.db",
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    context = _Context(
        invocation_id="invocation-tools",
        state={"task_id": "task-1"},
    )
    tool = _Tool(name="read")
    arguments = {"path": "src/parser.py", "offset": 1, "limit": 20}
    result = {
        "status": "ok",
        "model_text": "bounded output",
        "omitted_bytes": 32,
        "replayed": True,
        "result_hash": "b" * 64,
    }

    asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
            result=result,
        )
    )
    asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
            result=result,
        )
    )

    summary = plugin.store.task_summary("task-1")
    assert summary["tool_calls"] == 2
    assert summary["omitted_bytes"] == 64
    assert summary["replayed_calls"] == 2
    actions = context.state["tool_action_fingerprints"]
    assert [item["sequence"] for item in actions] == [1, 2]
    assert actions[0]["fingerprint"] == actions[1]["fingerprint"]
    assert "src/parser.py" not in str(actions)


def test_tool_error_is_counted_without_persisting_error_content(tmp_path) -> None:
    database = tmp_path / "metrics.db"
    plugin = HarnessMetricsPlugin(
        database=database,
        static_prefix_hash="prefix",
        static_prefix_tokens=500,
        default_model="test-model",
        default_task_id="task-1",
    )
    context = _Context(state={"task_id": "task-1"})
    tool = _Tool(name="bash")
    arguments = {"command": "secret command"}

    asyncio.run(
        plugin.before_tool_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
        )
    )
    asyncio.run(
        plugin.on_tool_error_callback(
            tool=tool,
            tool_args=arguments,
            tool_context=context,
            error=RuntimeError("secret provider detail"),
        )
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, model_visible_bytes, arguments_hash, result_hash "
            "FROM tool_usage"
        ).fetchone()
    assert row[0:2] == ("error", 2)
    assert len(row[2]) == 64
    assert len(row[3]) == 64
    assert "secret" not in str(row)
