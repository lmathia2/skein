from __future__ import annotations

import pytest

from harness.core.context import (
    CompactionPolicy,
    build_compaction_snapshot,
    truncate_to_tokens,
)
from harness.core.models import (
    Decision,
    TaskLedger,
    TaskRequest,
    ValidationResult,
)
from harness.evidence.state.events import HarnessEvent


def _ledger() -> TaskLedger:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Fix parser", acceptance_criteria=["Parser tests pass"]),
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    ledger.progress = ["Located the parser"]
    ledger.files_read = ["src/parser.py"]
    ledger.files_modified = ["src/parser.py"]
    ledger.decisions = [Decision(summary="Keep API stable", rationale="Avoid regressions")]
    ledger.validations = [
        ValidationResult(command="pytest tests/test_parser.py", passed=False, summary="1 failed")
    ]
    ledger.next_action = "Repair quoted-token handling"
    return ledger


def test_truncation_preserves_head_and_tail() -> None:
    text = "HEAD" + "x" * 1_000 + "TAIL"
    bounded, truncated = truncate_to_tokens(text, 50)
    assert truncated is True
    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")


@pytest.mark.parametrize("limit", [0, 1, 4, 12, 50, 500])
def test_truncation_respects_tiny_and_full_budgets(limit: int) -> None:
    text = "x" * 1_000
    bounded, truncated = truncate_to_tokens(text, limit)
    assert len(bounded) <= limit * 4
    assert truncated == (len(text) > limit * 4)


def test_compaction_snapshot_preserves_goal_files_and_validation() -> None:
    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=["read parser", "edited parser"],
        retained_events=["run tests"],
        tokens_before=50_000,
    )
    assert "## Goal\nFix parser" in snapshot.summary_markdown
    assert "src/parser.py" in snapshot.summary_markdown
    assert "1 failed" in snapshot.summary_markdown
    assert snapshot.tokens_before == 50_000
    assert snapshot.estimated_tokens_after > 0


def test_compaction_snapshot_accepts_state_events_without_volatile_metadata() -> None:
    summarized = HarnessEvent(
        event_id="random-event-id",
        task_id="task-1",
        sequence=3,
        kind="action.recorded",
        payload={"path": "src/parser.py"},
    )
    retained = summarized.model_copy(update={"event_id": "another-random-id", "sequence": 4})

    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=[summarized],
        retained_events=[retained],
        tokens_before=20_000,
    )

    assert snapshot.last_summarized_event_id == "random-event-id"
    assert snapshot.first_retained_event_id == "another-random-id"
    assert '"sequence":3' in snapshot.summary_markdown
    assert "random-event-id" not in snapshot.summary_markdown


def test_compaction_snapshot_preserves_only_safe_structured_artifact_references() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    summarized = HarnessEvent(
        task_id="task-1",
        sequence=3,
        kind="tool.completed",
        payload={
            "nested": {
                "artifact_uri": f"artifact://tool-output/{digest_a}.txt",
                "model_text": f"artifact://tool-output/{'c' * 64}.txt",
            },
            "unsafe": {
                "artifact_uri": "https://user:token@example.test/private.log",
            },
        },
    )
    retained = HarnessEvent(
        task_id="task-1",
        sequence=4,
        kind="verification.completed",
        payload={
            "commands": [
                {"artifact_uri": f"file:///tmp/artifacts/command-{digest_b}.log"},
                {"artifact_uri": f"artifact://../{digest_b}.txt"},
            ]
        },
    )

    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=[summarized],
        retained_events=[retained],
    )

    assert snapshot.artifact_uris == [
        f"artifact://tool-output/{digest_a}.txt",
        f"file:///tmp/artifacts/command-{digest_b}.log",
    ]
    assert snapshot.summary_markdown.count(f"artifact://tool-output/{digest_a}.txt") == 2
    assert all(not uri.startswith("https://") for uri in snapshot.artifact_uris)
    assert "user:token" not in snapshot.summary_markdown
    assert "<unsafe-artifact-reference-omitted>" in snapshot.summary_markdown
    assert f"artifact://tool-output/{'c' * 64}.txt" not in snapshot.artifact_uris


def test_compaction_snapshot_carries_forward_artifacts_with_a_deterministic_cap() -> None:
    previous_uris = [f"artifact://tool-output/{character * 64}.txt" for character in ("a", "b")]
    previous = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=[
            {"artifact_uri": previous_uris[0]},
            {"artifact_uri": previous_uris[1]},
        ],
    )
    newest = f"artifact://tool-output/{'c' * 64}.txt"
    duplicate = {"result": {"artifact_uri": previous_uris[1]}}

    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        previous_summary=previous,
        events_to_summarize=[duplicate],
        retained_events=[{"artifact_uri": newest}],
        policy=CompactionPolicy(max_artifact_references=2),
    )
    repeated = build_compaction_snapshot(
        ledger=_ledger(),
        previous_summary=previous,
        events_to_summarize=[duplicate],
        retained_events=[{"artifact_uri": newest}],
        policy=CompactionPolicy(max_artifact_references=2),
    )

    assert snapshot.artifact_uris == sorted([previous_uris[1], newest])
    assert snapshot.model_dump() == repeated.model_dump()
    assert snapshot.previous_summary_hash == previous.content_hash()


def test_compaction_snapshot_recovers_artifacts_from_legacy_summary_block() -> None:
    artifact_uri = f"artifact://tool-output/{'d' * 64}.txt"
    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        previous_summary=f"legacy handoff\n<artifacts>\n{artifact_uri}\n</artifacts>",
    )

    assert snapshot.artifact_uris == [artifact_uri]


def test_compaction_snapshot_redacts_event_secrets_without_mutating_raw_event() -> None:
    secret = "authorization: Bearer top-secret-value-123456789"
    event = HarnessEvent(
        task_id="task-1",
        sequence=7,
        kind="steering.received",
        payload={"content": secret, "nested": {"password": "plain-secret-value"}},
    )
    original = event.model_dump(mode="json")

    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=[event],
    )

    assert "top-secret-value" not in snapshot.summary_markdown
    assert "plain-secret-value" not in snapshot.summary_markdown
    assert snapshot.summary_markdown.count("<redacted>") >= 2
    assert event.model_dump(mode="json") == original


def test_repeated_compaction_summaries_remain_within_the_configured_bound() -> None:
    policy = CompactionPolicy(
        max_summary_tokens=512,
        max_previous_summary_tokens=128,
        max_summarized_event_tokens=256,
    )
    previous = None
    for index in range(25):
        previous = build_compaction_snapshot(
            ledger=_ledger(),
            previous_summary=previous,
            events_to_summarize=[f"event-{index}:" + "x" * 4_000],
            policy=policy,
        )
        assert len(previous.summary_markdown) <= policy.max_summary_tokens * 4

    assert previous is not None
    assert previous.previous_summary_hash is not None
    assert previous.summary_markdown.endswith("</artifacts>")
