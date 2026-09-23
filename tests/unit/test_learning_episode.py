from harness.evidence.learning import verified_learning_episode
from harness.evidence.state import EventKind, HarnessEvent


def _event(sequence, kind, payload):
    return HarnessEvent(task_id="task", sequence=sequence, kind=kind, payload=payload)


def test_learning_episode_requires_clean_independent_verification() -> None:
    events = [
        _event(1, EventKind.TASK_CREATED, {"ledger": {"goal": "Fix it"}}),
        _event(2, EventKind.CAPABILITY_COMPLETED, {"effect": "changed"}),
        _event(3, EventKind.VERIFICATION_COMPLETED, {
            "report": {"passed": True, "changed_paths": ["b.py", "a.py"]}
        }),
        _event(4, EventKind.TASK_FINISHED, {}),
    ]

    episode = verified_learning_episode(events, candidate_version="candidate@7")

    assert episode is not None
    assert episode.changed_paths == ("a.py", "b.py")
    assert episode.candidate_version == "candidate@7"
    assert verified_learning_episode(events[:-1], candidate_version="candidate@7") is None
    uncertain = [*events[:2], _event(3, EventKind.CAPABILITY_FAILED, {"effect": "unknown"}),
                 *[event.model_copy(update={"sequence": event.sequence + 1}) for event in events[2:]]]
    assert verified_learning_episode(uncertain, candidate_version="candidate@7") is None
