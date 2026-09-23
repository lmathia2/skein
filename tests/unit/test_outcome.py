import pytest
from pydantic import ValidationError

from harness.core.models import HarnessOutcome, parse_outcome_status


def test_terminal_outcomes_require_host_evidence_or_a_public_reason() -> None:
    assert HarnessOutcome(
        status="complete", verification={"passed": True}
    ).verification.passed
    assert parse_outcome_status("cancelled") == "cancelled"

    with pytest.raises(ValidationError, match="passing deterministic verification"):
        HarnessOutcome(status="complete")
    with pytest.raises(ValidationError, match="blocked requires"):
        HarnessOutcome(status="blocked")
