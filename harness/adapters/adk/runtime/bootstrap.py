"""Composition-driven assembly for one-shot evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent.factory import default_harness_registry
from harness.adapters.adk.persistence import build_service_bundle, settings_from_composition
from harness.core.agent import HarnessRegistry
from harness.core.config import (
    DEFAULT_COMPOSITION_PATH,
    HarnessComposition,
    RuntimeBindings,
    load_harness_composition,
)
from harness.evidence.ledger import LedgerStore, open_ledger
from harness.evidence.ledger.importers import import_public_event, import_run, import_session_record
from harness.execution.safety import SecretRedactor
from harness.execution.tools.adk_adapter import discover_known_secrets

from .registry import RunEventBroker, SqliteRunEventStore
from .runtime import AdkRunExecutionFactory, RunCoordinator, RunLivenessPolicy


@dataclass(frozen=True, slots=True)
class ServerAssembly:
    """Resolved server components, exposed for embedding and deterministic tests."""

    composition: HarnessComposition
    coordinator: RunCoordinator
    workspace: Path
    state_root: Path


def build_server_assembly(
    *,
    workspace: Path,
    state_root: Path,
    auth_state_root: Path | None = None,
    config_path: Path = DEFAULT_COMPOSITION_PATH,
    production: bool = False,
    trust_project: bool = False,
    registry: HarnessRegistry | None = None,
) -> ServerAssembly:
    """Build the configured harness and internal run coordinator."""

    resolved_workspace = workspace.expanduser().resolve()
    resolved_state_root = state_root.expanduser().resolve()
    resolved_auth_state_root = (
        auth_state_root.expanduser().resolve()
        if auth_state_root is not None
        else resolved_state_root
    )
    resolved_config = config_path.expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved_workspace}")
    registry = registry or default_harness_registry()
    composition = load_harness_composition(
        resolved_config,
        config_models=registry.config_models(),
    )
    sandbox = getattr(composition.harness.config, "sandbox", None)
    sandbox_kind = str(getattr(sandbox, "kind", "unknown"))
    if production and sandbox_kind == "local":
        raise ValueError(
            "production mode requires the docker sandbox; "
            "the local adapter is not an OS security boundary"
        )
    resolved_state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    bindings = RuntimeBindings(
        workspace=resolved_workspace,
        state_root=resolved_state_root,
        auth_state_root=resolved_auth_state_root,
        configuration_root=resolved_config.parent,
        source_repository=resolved_workspace,
        project_trusted=trust_project,
    )
    memory = getattr(composition.harness.config, "memory", None)
    canonical_ledger: LedgerStore | None = (
        open_ledger(resolved_state_root, memory.ledger)
        if memory is not None and memory.enabled
        else None
    )
    known_secrets = discover_known_secrets()
    session_redactor = SecretRedactor(
        known_secrets=known_secrets, redact_high_entropy_values=True
    )
    public_redactor = SecretRedactor(known_secrets=known_secrets)
    services = build_service_bundle(
        settings_from_composition(
            composition.persistence,
            state_root=resolved_state_root,
        ),
        session_sink=(
            lambda session_id, kind, payload: import_session_record(
                canonical_ledger,
                session_id,
                kind,
                session_redactor.redact(payload),
            )
        )
        if canonical_ledger is not None
        else None,
    )
    coordinator = RunCoordinator(
        provider_controls=None,
        store=SqliteRunEventStore(
            resolved_state_root / "server" / "runs.db",
            run_sink=(
                (lambda run: import_run(canonical_ledger, run))
                if canonical_ledger is not None
                else None
            ),
            event_sink=(
                (lambda event: import_public_event(canonical_ledger, event))
                if canonical_ledger is not None
                else None
            ),
        ),
        broker=RunEventBroker(
            queue_capacity=composition.server.outbound_queue_size,
        ),
        execution_factory=AdkRunExecutionFactory(
            composition=composition,
            bindings=bindings,
            registry=registry,
            services=services,
            startup_coding_model_status=None,
        ),
        redactor=public_redactor,
        liveness=RunLivenessPolicy(
            first_event_timeout=composition.server.first_event_timeout_seconds,
            idle_timeout=composition.server.idle_timeout_seconds,
            total_timeout=composition.server.total_timeout_seconds,
            first_event_retries=composition.server.first_event_retries,
            close_timeout=composition.server.close_timeout_seconds,
        ),
    )
    return ServerAssembly(
        composition=composition,
        coordinator=coordinator,
        workspace=resolved_workspace,
        state_root=resolved_state_root,
    )


__all__ = [
    "ServerAssembly",
    "build_server_assembly",
]
