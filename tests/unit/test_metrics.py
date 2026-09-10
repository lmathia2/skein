from __future__ import annotations

from pathlib import Path

from harness.evidence.telemetry import (
    MetricsStore,
    ModelUsageSample,
    TaskOutcomeSample,
    ToolUsageSample,
)


def test_metrics_separate_cached_and_uncached_context(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics.db")
    store.record_model_usage(
        ModelUsageSample(
            task_id="task",
            invocation_id="inv-1",
            model="model",
            static_prefix_hash="prefix-a",
            static_prefix_tokens=1_000,
            dynamic_suffix_tokens=500,
            input_tokens=1_500,
            output_tokens=100,
            cache_read_tokens=900,
            cache_write_tokens=100,
            cost_usd=0.10,
            latency_ms=500,
        )
    )
    store.record_model_usage(
        ModelUsageSample(
            task_id="task",
            invocation_id="inv-2",
            model="model",
            static_prefix_hash="prefix-a",
            static_prefix_tokens=1_000,
            dynamic_suffix_tokens=600,
            input_tokens=1_600,
            output_tokens=120,
            cache_read_tokens=1_000,
            cost_usd=0.12,
            latency_ms=600,
        )
    )
    store.record_tool_usage(
        ToolUsageSample(
            task_id="task",
            invocation_id="inv-2",
            tool_name="read",
            status="ok",
            arguments_hash="args",
            result_hash="result",
            model_visible_bytes=2_000,
            omitted_bytes=10_000,
        )
    )
    store.record_outcome(
        TaskOutcomeSample(
            task_id="task",
            status="complete",
            passed=True,
            iterations=2,
            tests_passed=4,
        )
    )

    summary = store.task_summary("task")
    assert summary["input_tokens"] == 3_100
    assert summary["cache_read_tokens"] == 1_900
    assert summary["uncached_input_tokens"] == 1_200
    assert summary["prefix_versions"] == 1
    assert summary["peak_context_tokens"] == 1_600
    assert summary["tool_calls"] == 1
    assert summary["omitted_bytes"] == 10_000
    assert summary["cost_per_passed_task"] == 0.22


def test_failed_outcome_does_not_report_cost_per_pass(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics.db")
    store.record_outcome(
        TaskOutcomeSample(
            task_id="failed",
            status="failed",
            passed=False,
            iterations=3,
        )
    )
    assert store.task_summary("failed")["cost_per_passed_task"] is None
