import asyncio
from pathlib import Path

import pytest

from harness.ledger import JsonlLedgerStore
from harness.memory import MemoryProgramRuntime, ViewRequest
from harness.memory.summary import SummaryCache, SummaryOutput


@pytest.mark.asyncio
async def test_summary_replay_identity_erasure_and_citation_scope(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "ledger")
    event = store.append(task_id="task", source="context", source_id="1", kind="context.history",
                         payload={"role": "user", "parts": [{"text": "correct fact"}]})
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    summary = SummaryCache(runtime)
    request = ViewRequest(task_id="task", program="history.page")
    calls = []
    async def generate(prompt):
        calls.append(prompt)
        return SummaryOutput(text="A fact", evidence_event_ids=(event.event_id,))
    kwargs = {"model_id": "pinned-v1", "prompt_version": "v1", "settings": {}, "generate": generate, "cache": True}
    first = await summary.summarize(request, **kwargs)
    replay = await summary.summarize(request, **kwargs)
    assert first["cached"] is False and replay["cached"] is True and len(calls) == 1
    await summary.summarize(request, **{**kwargs, "model_id": "pinned-v2"})
    assert len(calls) == 2
    async def bad(prompt):
        return SummaryOutput(text="invented citation", evidence_event_ids=("outside",))
    denied = await summary.summarize(request, **{**kwargs, "generate": bad, "cache": False})
    assert denied["status"] == "unavailable"
    store.erase_task("task")
    after = await summary.summarize(request, **kwargs)
    assert after["status"] == "unavailable" and len(calls) == 2


@pytest.mark.asyncio
async def test_summary_async_call_is_cancelled_on_deadline(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "ledger")
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",))
    cancelled = []
    async def slow(prompt):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return SummaryOutput(text="late", evidence_event_ids=())
    result = await SummaryCache(runtime).summarize(
        ViewRequest(task_id="task", program="history.page"), model_id="test", prompt_version="v1",
        settings={}, generate=slow, timeout_seconds=0.01,
    )
    assert result["status"] == "timeout" and cancelled == [True]
    assert store.read("task") == []
