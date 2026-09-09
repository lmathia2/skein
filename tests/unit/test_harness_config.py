from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from harness.config import (
    FOUR_CODING_TOOLS,
    RuntimeBindings,
    SkeinConfig,
    load_harness_composition,
    parse_harness_composition,
)


def _composition_payload() -> dict[str, Any]:
    harness_config = {
        "models": {"coding": {"provider": "google_adk", "name": "coding-model"}},
        "agents": {"coding_worker": {"model": "coding", "instruction": "test worker"}},
        "workflow": {"max_iterations": 40},
    }
    return {
        "schema_version": 1,
        "app": {"name": "coding_harness"},
        "harness": {
            "implementation": "skein_v1",
            "api_version": 1,
            "config": harness_config,
        },
        "persistence": {
            "session_backend": "sqlite",
        },
    }


def test_default_composition_is_strict_and_uses_the_four_tool_surface() -> None:
    composition = load_harness_composition()
    config = composition.harness.config
    assert isinstance(config, SkeinConfig)

    assert composition.schema_version == 1
    assert FOUR_CODING_TOOLS == ("read", "bash", "edit", "write")
    assert composition.server.protocol == "ag_ui_websocket_v1"
    assert composition.server.first_event_timeout_seconds == 120
    assert composition.server.idle_timeout_seconds == 180
    assert composition.server.total_timeout_seconds == 1_800
    assert composition.server.first_event_retries == 1
    assert config.context.work_packet_tokens == 20_000
    assert config.context.max_task_input_tokens == 200_000
    assert config.context.project_instruction_bytes == 16_000
    assert config.workflow.progress.replan_after_no_progress == 2
    assert config.workflow.progress.block_after_no_progress == 4
    assert config.agents["coding_worker"].generation.temperature is None
    assert config.notebook_ptc.enabled is False
    assert config.notebook_ptc.implementation == "skein_notebook"
    assert config.notebook_ptc.default_timeout_seconds == 120
    assert config.notebook_ptc.max_timeout_seconds == 600
    assert config.notebook_ptc.max_output_bytes == 16_000
    assert config.memory.enabled is False
    assert config.memory.implementation == "trace_native"
    assert config.memory.ledger == "jsonl"
    assert config.memory.retrieval == "lexical"
    assert "tui" not in type(composition.server).model_fields
    assert config.models["coding"].provider == "google_adk"
    assert "never parse notebook JSON" in config.agents["coding_worker"].instruction


@pytest.mark.parametrize(
    ("filename", "ptc_enabled", "memory_enabled", "ledger"),
    [
        ("four-tool.yaml", False, False, "jsonl"),
        ("notebook-ptc-jsonl.yaml", True, True, "jsonl"),
        ("notebook-ptc-duckdb.yaml", True, True, "duckdb"),
    ],
)
def test_annotated_standard_profiles_are_complete_and_strict(
    filename: str,
    ptc_enabled: bool,
    memory_enabled: bool,
    ledger: str,
) -> None:
    path = Path(__file__).parents[2] / "harness" / "config" / "profiles" / filename
    composition = load_harness_composition(path)
    config = composition.harness.config

    assert isinstance(config, SkeinConfig)
    assert config.notebook_ptc.enabled is ptc_enabled
    assert config.memory.enabled is memory_enabled
    assert config.memory.ledger == ledger
    assert "never parse notebook JSON" in config.agents["coding_worker"].instruction
    annotations = path.read_text(encoding="utf-8")
    assert "Primary learnable" in annotations
    assert "optimizer-owned" in annotations
    assert "verification" in annotations


@pytest.mark.parametrize("removed_field", ["inbound_queue_size", "heartbeat_seconds"])
def test_server_rejects_settings_without_runtime_semantics(removed_field: str) -> None:
    payload = _composition_payload()
    payload["server"] = {removed_field: 20}

    with pytest.raises(ValidationError, match=removed_field):
        parse_harness_composition(payload)


def test_server_total_deadline_cannot_be_shorter_than_first_event_deadline() -> None:
    payload = _composition_payload()
    payload["server"] = {
        "first_event_timeout_seconds": 60,
        "total_timeout_seconds": 30,
    }

    with pytest.raises(ValidationError, match="total_timeout_seconds"):
        parse_harness_composition(payload)


def test_server_idle_deadline_can_match_a_long_total_deadline() -> None:
    payload = _composition_payload()
    payload["server"] = {
        "idle_timeout_seconds": 5_400,
        "total_timeout_seconds": 5_400,
    }

    composition = parse_harness_composition(payload)

    assert composition.server.idle_timeout_seconds == 5_400


def test_task_input_budget_cannot_be_smaller_than_one_work_packet() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["context"] = {
        "work_packet_tokens": 10_000,
        "max_task_input_tokens": 8_000,
    }

    with pytest.raises(ValidationError, match="max_task_input_tokens"):
        parse_harness_composition(payload)


def test_adk_code_mode_rejects_conversation_continuity() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["notebook_ptc"] = {
        "enabled": True,
        "implementation": "adk_code_mode",
        "continuity": "conversation",
    }

    with pytest.raises(NotImplementedError, match="run-scoped continuity only"):
        parse_harness_composition(payload)


@pytest.mark.parametrize(
    ("implementation", "serialization", "state", "supported"),
    [
        ("skein_notebook", "native", "native", True),
        ("skein_notebook", "notebook", "replay_safe", True),
        ("skein_notebook", "jsonl", "native", False),
        ("skein_notebook", "native", "snapshot", False),
        ("adk_code_mode", "native", "none", True),
        ("adk_code_mode", "notebook", "none", False),
        ("adk_code_mode", "jsonl", "none", False),
        ("adk_code_mode", "native", "replay_safe", False),
        ("prime_repl", "jsonl", "snapshot", False),
    ],
)
def test_ptc_native_configuration_support_matrix(
    implementation: str, serialization: str, state: str, supported: bool,
) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["notebook_ptc"] = {
        "enabled": True, "implementation": implementation,
        "serialization": serialization, "state": state,
        "adk_code_mode_image": "local-test-image",
    }
    if supported:
        config = parse_harness_composition(payload).harness.config
        assert config.notebook_ptc.implementation == implementation
        assert config.notebook_ptc.serialization == serialization
        assert config.notebook_ptc.state == state
    else:
        with pytest.raises(NotImplementedError):
            parse_harness_composition(payload)


def test_pi_memory_rejects_trace_native_programs() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["memory"] = {
        "enabled": True,
        "implementation": "pi",
        "context_programs": {"mode": "active"},
    }

    with pytest.raises(ValidationError, match="simple ADK transcript/compaction"):
        parse_harness_composition(payload)


def test_progress_block_threshold_must_exceed_replan_threshold() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["workflow"]["progress"] = {
        "replan_after_no_progress": 4,
        "block_after_no_progress": 4,
    }

    with pytest.raises(ValidationError, match="block threshold"):
        parse_harness_composition(payload)


def test_generation_settings_change_behavior_hash() -> None:
    payload = _composition_payload()
    baseline = parse_harness_composition(payload)
    payload["harness"]["config"]["agents"]["coding_worker"]["generation"] = {
        "temperature": 0.2,
        "max_output_tokens": 8_192,
    }

    assert parse_harness_composition(payload).behavior_sha256 != baseline.behavior_sha256


def test_canonical_composition_hash_is_stable_across_mapping_order() -> None:
    payload = _composition_payload()
    reordered = dict(reversed(payload.items()))

    first = parse_harness_composition(payload)
    second = parse_harness_composition(reordered)

    assert first.canonical_json() == second.canonical_json()
    assert first.composition_sha256 == second.composition_sha256


def test_loader_preserves_portable_resource_paths(tmp_path: Path) -> None:
    payload = _composition_payload()
    config = payload["harness"]["config"]
    config["skills"] = {"additional_roots": ["skills"]}
    config_path = tmp_path / "config" / "harness.yaml"
    config_path.parent.mkdir()
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    composition = load_harness_composition(config_path)
    config = composition.harness.config
    assert isinstance(config, SkeinConfig)
    assert config.agents["coding_worker"].instruction == "test worker"
    assert config.skills.additional_roots == (Path("skills"),)
    assert str(tmp_path) not in composition.canonical_json()


def test_behavior_hash_tracks_prompt_content() -> None:
    payload = _composition_payload()
    composition = parse_harness_composition(payload)
    payload["harness"]["config"]["agents"]["coding_worker"]["instruction"] = "changed"

    assert parse_harness_composition(payload).behavior_sha256 != composition.behavior_sha256


def test_behavior_hash_excludes_deployment_configuration() -> None:
    composition = parse_harness_composition(_composition_payload())
    moved = composition.model_copy(
        update={"server": composition.server.model_copy(update={"host": "0.0.0.0", "port": 9999})}
    )

    assert moved.composition_sha256 != composition.composition_sha256
    assert moved.behavior_sha256 == composition.behavior_sha256


def test_capability_order_does_not_change_behavior_hash() -> None:
    payload = _composition_payload()
    payload["harness"]["required_capabilities"] = ["streaming", "steering", "cancel"]
    reordered = _composition_payload()
    reordered["harness"]["required_capabilities"] = ["cancel", "streaming", "steering"]

    first = parse_harness_composition(payload)
    second = parse_harness_composition(reordered)

    assert first.behavior_sha256 == second.behavior_sha256


def test_unknown_configuration_fields_are_rejected() -> None:
    payload = _composition_payload()
    payload["surprise"] = True

    with pytest.raises(ValidationError, match="surprise"):
        parse_harness_composition(payload)


@pytest.mark.parametrize("field", ["entry", "nodes"])
def test_removed_graph_dsl_is_rejected(field: str) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["workflow"][field] = {}
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_harness_composition(payload)


def test_skein_config_rejects_unused_model_entries() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["models"]["unused"] = {
        "provider": "google_adk",
        "name": "unused-model",
    }

    with pytest.raises(ValidationError, match="must be referenced"):
        parse_harness_composition(payload)


def test_tool_surface_is_a_code_invariant_not_configuration() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["tools"] = {"visible": ["read"]}

    with pytest.raises(ValidationError, match="visible"):
        parse_harness_composition(payload)


@pytest.mark.parametrize("feature", ["learning", "reviewer"])
def test_removed_automatic_layers_fail_closed(feature: str) -> None:
    payload = _composition_payload()
    payload["harness"]["config"][feature] = {"enabled": True}
    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_harness_composition(payload)


def test_runtime_bindings_are_not_part_of_declarative_behavior(tmp_path: Path) -> None:
    composition = parse_harness_composition(_composition_payload())
    bindings = RuntimeBindings(
        workspace=tmp_path / "workspace",
        state_root=tmp_path / "state",
        source_repository=tmp_path / "source",
        task_id="task-123",
    )

    assert bindings.task_id == "task-123"
    serialized = composition.canonical_json()
    assert "task-123" not in serialized
    assert str(tmp_path) not in serialized


def test_search_default_page_size_cannot_exceed_maximum() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["tools"] = {
        "search": {"default_page_size": 40, "max_page_size": 20},
    }

    with pytest.raises(ValidationError, match="default search page size"):
        parse_harness_composition(payload)


def test_notebook_ptc_default_timeout_cannot_exceed_maximum() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["notebook_ptc"] = {
        "default_timeout_seconds": 61,
        "max_timeout_seconds": 60,
    }

    with pytest.raises(ValidationError, match="default notebook PTC timeout"):
        parse_harness_composition(payload)


def test_notebook_ptc_configuration_changes_behavior_hash() -> None:
    payload = _composition_payload()
    disabled = parse_harness_composition(payload)
    payload["harness"]["config"]["notebook_ptc"] = {"enabled": True}

    assert parse_harness_composition(payload).behavior_sha256 != disabled.behavior_sha256


def test_notebook_ptc_batching_instruction_changes_behavior_hash() -> None:
    payload = _composition_payload()
    baseline = parse_harness_composition(payload)
    payload["harness"]["config"]["notebook_ptc"] = {
        "enabled": True,
        "batching_instruction": "Batch independent reads only.",
    }

    configured = parse_harness_composition(payload)
    assert configured.behavior_sha256 != baseline.behavior_sha256


def test_lance_retrieval_requires_duckdb() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["memory"] = {
        "enabled": True,
        "ledger": "jsonl",
        "retrieval": "lance",
    }

    with pytest.raises(ValidationError, match="requires the DuckDB ledger"):
        parse_harness_composition(payload)


def test_disabled_memory_rejects_ignored_backend_selection() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["memory"] = {
        "enabled": False,
        "ledger": "duckdb",
        "retrieval": "lexical",
    }

    with pytest.raises(ValidationError, match="disabled memory"):
        parse_harness_composition(payload)


def test_removed_builtin_prompt_selector_is_rejected() -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["agents"]["coding_worker"]["source"] = "builtin"

    with pytest.raises(ValidationError, match="Extra inputs"):
        parse_harness_composition(payload)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "harness.yaml"
    config_path.write_text(
        "schema_version: 1\nschema_version: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_harness_composition(config_path)


@pytest.mark.parametrize("kind", ["kubernetes", "remote"])
def test_removed_remote_sandbox_cannot_silently_fall_back_to_host(kind: str) -> None:
    payload = _composition_payload()
    payload["harness"]["config"]["sandbox"] = {"kind": kind}
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        parse_harness_composition(payload)
