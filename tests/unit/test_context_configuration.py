from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.config import RuntimeBindings, SkeinConfig, load_harness_composition
from harness.config.models import ContextProgramConfig, MemoryConfig, NotebookPtcConfig


@pytest.mark.parametrize("payload", [
    {"context_programs": {"mode": "active"}},
    {"enabled": True, "working_notes": True},
    {"enabled": True, "prior_runs": True},
    {"enabled": True, "context_programs": {"mode": "shadow", "reuse": True}},
])
def test_invalid_memory_dependencies_fail_closed(payload):
    with pytest.raises(ValidationError):
        MemoryConfig.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("max_result_bytes", 0), ("max_result_bytes", 1_000_001),
    ("max_scan_events", 0), ("max_scan_events", 1_000_001),
    ("timeout_seconds", 0), ("timeout_seconds", 61),
])
def test_context_program_budgets_have_code_owned_ceilings(field, value):
    with pytest.raises(ValidationError):
        ContextProgramConfig.model_validate({field: value})


def test_recovery_requires_durable_substrate_and_fresh_requires_notes():
    base = load_harness_composition()
    config = base.harness.config.model_dump()
    config["context"]["reconstruction"] = "fresh"
    with pytest.raises(ValidationError, match="working notes"):
        SkeinConfig.model_validate(config)
    config["memory"].update(enabled=True, working_notes=True)
    config["memory"]["context_programs"]["mode"] = "active"
    config["adk"]["recovery"] = "safe_auto"
    candidate = SkeinConfig.model_validate(config)
    from harness.config.models import HarnessComposition, PersistenceConfig
    with pytest.raises(ValidationError, match="SQLite sessions"):
        HarnessComposition(
            app=base.app, harness=base.harness.model_copy(update={"config": candidate}),
            persistence=PersistenceConfig(),
        )


def test_notebook_and_history_identity_must_be_explicit(tmp_path):
    with pytest.raises(ValidationError, match="requires notebook"):
        NotebookPtcConfig(continuity="conversation")
    with pytest.raises(ValidationError, match="paired"):
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path, prior_task_ids=("other",))
    with pytest.raises(ValidationError, match="owned conversation"):
        RuntimeBindings(workspace=tmp_path, state_root=tmp_path,
                        prior_task_ids=("other",), prior_state_roots=(tmp_path,))
