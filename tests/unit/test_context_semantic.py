import time
from pathlib import Path

from harness.evidence.ledger import JsonlLedgerStore
from harness.evidence.memory import MemoryProgramRuntime, ViewRequest
from harness.evidence.memory.lance import LanceMemorySearch


def _vector(text: str) -> list[float]:
    return [1.0, 0.0]


class FakeSearch(LanceMemorySearch):
    def search(self, events, query, *, limit=32, mode="hybrid"):
        assert all(event.kind == "context.history" for event in events)
        assert all("private" not in event.payload for event in events)
        return tuple(event.event_id for event in reversed(events))


class SlowSearch(FakeSearch):
    def search(self, events, query, *, limit=32, mode="hybrid"):
        time.sleep(10)
        return ()


def test_semantic_is_filtered_hydrated_bounded_and_explicitly_partial(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "ledger")
    events = [store.append(task_id="task", source="context", source_id=str(i),
                           kind="context.history", payload={"role": "user", "parts": [{"text": f"evidence {i}"}], "private": "excluded"})
              for i in range(3)]
    search = FakeSearch(tmp_path / "index", vectorizer=_vector, embedding_version="fixture-v1")
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",), semantic_search=search)
    request = ViewRequest(task_id="task", program="history.page", query="paraphrase", retrieval="semantic", watermark=2)
    result = runtime.compute(request)
    assert result.status == "partial"
    assert result.data["coverage"] == "semantic_top_k"
    assert result.evidence_event_ids == (events[1].event_id, events[0].event_id)
    count = runtime.compute(request.model_copy(update={"program": "events.count"}))
    assert count.status == "partial" and count.data["count"] == 2 and not count.data["complete"]
    runtime.semantic_search = None
    assert runtime.compute(request).status == "unavailable"


def test_semantic_worker_deadline(tmp_path: Path):
    store = JsonlLedgerStore(tmp_path / "ledger")
    store.append(task_id="task", source="context", source_id="1", kind="context.history",
                 payload={"role": "user", "parts": [{"text": "x"}]})
    runtime = MemoryProgramRuntime(store, authorized_tasks=("task",),
                                   semantic_search=SlowSearch(tmp_path / "index", vectorizer=_vector, embedding_version="fixture-v1"))
    start = time.monotonic()
    result = runtime.compute(ViewRequest(task_id="task", program="history.page", query="x", retrieval="hybrid", timeout_seconds=0.1))
    assert result.status == "timeout" and time.monotonic() - start < 3
