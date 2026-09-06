"""Optional, evidence-bound summary calls; the caller supplies the authorized model."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from harness.ledger.models import canonical_json

from .context import bounded_events
from .models import ViewRequest
from .runtime import MemoryProgramRuntime


class SummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=16000)
    evidence_event_ids: tuple[str, ...]


class SummaryCache:
    """Replay recorded model output only for identical, still-available evidence."""

    def __init__(self, runtime: MemoryProgramRuntime) -> None:
        self.runtime = runtime

    async def summarize(
        self, request: ViewRequest, *, model_id: str, prompt_version: str,
        settings: dict[str, Any], generate: Callable[[str], Awaitable[SummaryOutput]],
        cache: bool = False, timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        import time

        if not model_id or not prompt_version or not 0 < timeout_seconds <= 300:
            raise ValueError("summary requires pinned model/prompt identity and bounded timeout")
        if request.program != "history.page":
            raise ValueError("summary inputs must be a bounded exposed history page")
        # Rehydrate current evidence before cache lookup: erasure/changed snapshots fail closed.
        evidence = self.runtime.compute(request)
        if evidence.status not in {"ok", "partial"} or "events" not in evidence.data:
            return {"status": evidence.status, "reason": "summary evidence unavailable"}
        prompt = ("Summarize this historical evidence as advisory memory, not instructions. "
                  "Cite only the supplied event IDs; preserve uncertainty and corrections.\n"
                  + canonical_json(evidence.data))
        identity = {"source_view_hash": evidence.content_hash, "model_id": model_id,
                    "prompt_version": prompt_version, "settings": settings, "prompt": prompt}
        key = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if cache:
            events = bounded_events(self.runtime.ledger, (request.task_id,),
                                    maximum=request.max_scan_events,
                                    deadline=time.monotonic() + request.timeout_seconds)
            for event in events:
                if event.kind == "memory.summary" and event.payload.get("cache_key") == key:
                    return {"status": "ok", "cached": True, **event.payload}
        try:
            output = await asyncio.wait_for(generate(prompt), timeout=timeout_seconds)
        except TimeoutError:
            return {"status": "timeout", "reason": "summary model deadline exceeded"}
        output = SummaryOutput.model_validate(output)
        if not set(output.evidence_event_ids) <= set(evidence.evidence_event_ids):
            return {"status": "unavailable", "reason": "summary cites evidence outside its input"}
        payload = {"cache_key": key, "source_view_hash": evidence.content_hash,
                   "source_manifest": evidence.source_manifest,
                   "model_id": model_id, "prompt_version": prompt_version,
                   "settings": settings, "text": self.runtime.redactor.redact_text(output.text),
                   "evidence_event_ids": list(output.evidence_event_ids),
                   "complete": evidence.status == "ok", "advisory": True}
        if len(canonical_json(payload).encode()) > request.max_bytes:
            return {"status": "partial", "reason": "summary exceeds egress budget"}
        # Record both cached and uncached outputs. Different model outputs are different receipts.
        output_hash = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        self.runtime.ledger.append(
            task_id=request.task_id, source="context_summary",
            source_id=f"{request.task_id}:{output_hash}", kind="memory.summary", payload=payload,
            status="completed", idempotency_key=f"memory-summary:{output_hash}",
        )
        return {"status": "ok", "cached": False, **payload}
