from __future__ import annotations

from collections.abc import Mapping

from harness.ledger import LedgerStore
from harness.safety.redaction import SecretRedactor

from .context import ArtifactReader, compute_context
from .lance import LanceMemorySearch
from .models import ViewRequest, ViewResult
from .programs import PROGRAM_REGISTRY


class MemoryProgramRuntime:
    """Execute only programs present in the closed code-owned registry."""

    def __init__(
        self, ledger: LedgerStore, *, semantic_search: LanceMemorySearch | None = None,
        authorized_tasks: tuple[str, ...] = (), redactor: SecretRedactor | None = None,
        reuse: bool = False, artifact_reader: ArtifactReader | None = None,
        source_ledgers: Mapping[str, LedgerStore] | None = None,
    ) -> None:
        self.ledger = ledger
        self.semantic_search = semantic_search
        self.authorized_tasks = authorized_tasks
        self.redactor = redactor or SecretRedactor()
        self.reuse = reuse
        self.artifact_reader = artifact_reader
        self.source_ledgers = source_ledgers
        self._known_context_sources: set[str] = set()

    def compute(self, request: ViewRequest) -> ViewResult:
        if (request.program, request.version) not in PROGRAM_REGISTRY:
            raise KeyError(f"unknown memory program: {request.program}@{request.version}")
        return compute_context(
            self.ledger,
            request,
            authorized_tasks=self.authorized_tasks,
            redactor=self.redactor,
            reuse=self.reuse,
            artifact_reader=self.artifact_reader,
            source_ledgers=self.source_ledgers,
            semantic_search=self.semantic_search,
            known_sources=self._known_context_sources,
        )
