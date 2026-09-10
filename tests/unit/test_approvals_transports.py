from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.execution.approvals import (
    ApprovalDecision,
    ApprovalStore,
    ApprovalSubmission,
)


@dataclass
class _Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _clock() -> _Clock:
    return _Clock(datetime(2026, 1, 1, tzinfo=UTC))


def _submission(*, expires_at: str | None = None) -> ApprovalSubmission:
    return ApprovalSubmission(
        task_id="task",
        fingerprint="exact-command-fingerprint",
        operation="deploy --dry-run",
        risk="publishing",
        reason="human authorization required",
        expires_at=expires_at,
    )


def test_request_and_decision_submissions_replay_idempotently(
    tmp_path: Path,
) -> None:
    store = ApprovalStore(tmp_path / "approvals.db", clock=_clock())
    first = store.submit(_submission())
    replay = store.submit(_submission())
    assert replay.request_id == first.request_id
    with pytest.raises(ValueError, match="different request content"):
        store.submit(_submission().model_copy(update={"operation": "different command"}))

    decision = ApprovalDecision(
        request_id=first.request_id,
        decision="approved",
        actor="operator",
        note="reviewed",
    )
    approved = store.submit_decision(decision)
    assert store.submit_decision(decision) == approved
    with pytest.raises(ValueError, match="already decided as approved"):
        store.submit_decision(
            ApprovalDecision(
                request_id=first.request_id,
                decision="denied",
                actor="other-operator",
            )
        )


def test_expired_request_cannot_be_decided_or_leased(tmp_path: Path) -> None:
    clock = _clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    request = store.submit(
        _submission(expires_at=(clock.current + timedelta(seconds=5)).isoformat())
    )

    clock.advance(seconds=6)

    assert store.get(request.request_id).status == "expired"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="status expired"):
        store.decide(
            request.request_id,
            decision="approved",
            actor="late-operator",
        )
    assert store.list(status="pending") == []


def test_approved_authorization_expires(tmp_path: Path) -> None:
    clock = _clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    request = store.submit(
        _submission(expires_at=(clock.current + timedelta(seconds=5)).isoformat())
    )
    store.decide(
        request.request_id,
        decision="approved",
        actor="operator",
    )
    assert store.is_approved(request.task_id, request.fingerprint)

    clock.advance(seconds=6)

    assert not store.is_approved(request.task_id, request.fingerprint)
    assert store.get(request.request_id).status == "expired"  # type: ignore[union-attr]
