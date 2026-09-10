from __future__ import annotations

from pathlib import Path

import pytest

from evals.experiments import (
    TrialMetrics,
    TrialRecord,
    analyze_trials,
    append_trial_record,
    build_matrix_from_file,
    harbor_command,
    harbor_task_path,
    next_assignment,
    trial_record_from_harbor_result,
)

SPEC = Path("tests/eval/experiments/phase4-ablation-v1.json")


def test_phase4_matrix_is_deterministic_and_fail_closed() -> None:
    first = build_matrix_from_file(SPEC)
    second = build_matrix_from_file(SPEC)

    assert first == second
    assert len(first.assignments) == 36
    assert len({assignment.trial_key for assignment in first.assignments}) == 36
    assert not first.live_ready
    assert "subscription authorization is pending" in first.blockers
    assert all(
        left.block == right.block
        for left, right in zip(first.assignments[::2], first.assignments[1::2], strict=True)
    )


def test_next_trial_command_is_single_task_digest_pinned_and_host_authenticated() -> None:
    matrix = build_matrix_from_file(SPEC)
    assignment = next_assignment(matrix, ())

    assert assignment is not None
    command = harbor_command(assignment, matrix.model)
    assert command[:2] == ("harbor", "run")
    task_path = harbor_task_path(assignment.harbor_task, assignment.task_artifact_sha256)
    assert command[command.index("--path") + 1] == str(task_path)
    assert task_path.name == assignment.task_artifact_sha256
    assert command.count("--n-concurrent") == 1
    assert "--agent-env" not in command
    assert "--yes" in command


def test_paired_analysis_uses_tasks_not_attempts() -> None:
    matrix = build_matrix_from_file(SPEC)
    metrics = TrialMetrics(
        active_wall_time_seconds=10,
        end_to_end_wall_time_seconds=12,
        api_equivalent_cost_usd=0.1,
        uncached_input_tokens=100,
        cache_read_tokens=50,
        cache_write_tokens=5,
        output_tokens=10,
        reasoning_tokens=4,
        model_turns=2,
        steps=2,
        tool_calls=2,
        shell_calls=1,
        verification_runs=1,
        changed_files=1,
        compactions=0,
        duplicate_actions=0,
        operator_interventions=0,
        subscription_limit_interruptions=0,
    )
    records = tuple(
        TrialRecord(
            trial_key=assignment.trial_key,
            benchmark=assignment.benchmark,
            task_id=assignment.task_id,
            attempt=assignment.attempt,
            candidate_id=assignment.candidate_id,
            status=("pass" if assignment.candidate_id == "skein-four-tool" else "task_failure"),
            official_reward=(1 if assignment.candidate_id == "skein-four-tool" else 0),
            metrics=metrics,
        )
        for assignment in matrix.assignments
    )

    analysis = analyze_trials(matrix, records, bootstrap_samples=200)

    assert [summary.capability_composite for summary in analysis.candidates] == [1.0, 0.0]
    comparison = analysis.comparisons[0]
    assert (comparison.wins, comparison.losses, comparison.ties) == (18, 0, 0)
    assert comparison.bootstrap_95 == (1.0, 1.0)
    assert comparison.mcnemar_p < 0.001


def test_analysis_rejects_missing_or_mismatched_trials() -> None:
    matrix = build_matrix_from_file(SPEC)

    with pytest.raises(ValueError, match="incomplete"):
        analyze_trials(matrix, (), bootstrap_samples=100)

    assignment = matrix.assignments[0]
    record = TrialRecord(
        trial_key=assignment.trial_key,
        benchmark=assignment.benchmark,
        task_id="wrong-task",
        attempt=assignment.attempt,
        candidate_id=assignment.candidate_id,
        status="pass",
        official_reward=1,
    )
    with pytest.raises(ValueError, match=r"incomplete|identity mismatch"):
        analyze_trials(matrix, (record,), bootstrap_samples=100)


def test_harbor_import_uses_verifier_reward_and_appends_idempotently(tmp_path: Path) -> None:
    matrix = build_matrix_from_file(SPEC)
    assignment = matrix.assignments[0]
    payload = {
        "task_name": assignment.task_id,
        "started_at": "2026-09-02T12:00:00Z",
        "finished_at": "2026-09-02T12:01:00Z",
        "agent_execution": {
            "started_at": "2026-09-02T12:00:05Z",
            "finished_at": "2026-09-02T12:00:55Z",
        },
        "agent_result": {
            "n_input_tokens": 120,
            "n_cache_tokens": 20,
            "n_output_tokens": 30,
            "cost_usd": 0.2,
            "metadata": {
                "official_reward": 0,
                "skein": {
                    "api_equivalent_cost_usd": 0.2,
                    "changed_paths": ["src/a.py"],
                    "metrics": {
                        "input_tokens": 120,
                        "cache_read_tokens": 20,
                        "cache_write_tokens": 5,
                        "output_tokens": 30,
                        "reasoning_tokens": 10,
                        "model_calls": 3,
                        "tool_calls": 4,
                        "outcome_iterations": 3,
                        "outcome_compactions": 1,
                        "outcome_user_interventions": 0,
                    },
                    "subscription_limit": {"interrupted": False},
                    "verification": {"passed": True},
                },
            },
        },
        "verifier_result": {"rewards": {"reward": 1}},
    }

    record = trial_record_from_harbor_result(assignment, payload)

    assert record.status == "pass"
    assert record.official_reward == 1
    assert record.metrics is not None
    assert record.metrics.uncached_input_tokens == 100
    assert record.metrics.active_wall_time_seconds == 50
    ledger = tmp_path / "results.jsonl"
    assert append_trial_record(ledger, record)
    assert not append_trial_record(ledger, record)
    with pytest.raises(ValueError, match="different result"):
        append_trial_record(ledger, record.model_copy(update={"official_reward": 0}))
