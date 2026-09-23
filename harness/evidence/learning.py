"""Offline-only learning projection over independently verified task traces."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from harness.evidence.state import EventKind, HarnessEvent


class LearningEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "verified-trace-episode@1"
    task_id: str
    goal: str
    source_watermark: int
    verification_event_id: str
    changed_paths: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    candidate_version: str


def verified_learning_episode(
    events: list[HarnessEvent], *, candidate_version: str
) -> LearningEpisode | None:
    """Return a reproducible episode only for a clean, host-verified trace."""

    ordered = sorted(events, key=lambda event: event.sequence)
    if not ordered or any(event.task_id != ordered[0].task_id for event in ordered):
        return None
    verification = next(
        (
            event
            for event in reversed(ordered)
            if event.kind == EventKind.VERIFICATION_COMPLETED
            and event.payload.get("report", {}).get("passed") is True
        ),
        None,
    )
    finished = next(
        (event for event in reversed(ordered) if event.kind == EventKind.TASK_FINISHED),
        None,
    )
    if verification is None or finished is None or finished.sequence <= verification.sequence:
        return None
    if any(
        event.sequence <= finished.sequence
        and event.payload.get("effect") in {"unknown", "native_untracked"}
        for event in ordered
    ):
        return None
    created = next((event for event in ordered if event.kind == EventKind.TASK_CREATED), None)
    if created is None:
        return None
    report = verification.payload["report"]
    return LearningEpisode(
        task_id=created.task_id,
        goal=str(created.payload.get("ledger", {}).get("goal", "")),
        source_watermark=finished.sequence,
        verification_event_id=verification.event_id,
        changed_paths=tuple(sorted(str(path) for path in report.get("changed_paths", ()))),
        evidence_event_ids=tuple(
            event.event_id for event in ordered if event.sequence <= finished.sequence
        ),
        candidate_version=candidate_version,
    )
