"""Optional LanceDB physical projection for hybrid memory retrieval."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from harness.evidence.ledger import LedgerEvent
from harness.evidence.ledger.models import canonical_json

Vectorizer = Callable[[str], Sequence[float]]


class LanceMemorySearch:
    """Build immutable Lance snapshots while DuckDB remains authoritative."""

    def __init__(self, root: Path, *, vectorizer: Vectorizer, embedding_version: str) -> None:
        if not embedding_version:
            raise ValueError("embedding_version must not be empty")
        self.root = root.resolve()
        self.vectorizer = vectorizer
        self.embedding_version = embedding_version

    @staticmethod
    def _text(event: LedgerEvent) -> str:
        return canonical_json({"kind": event.kind, "payload": event.payload})

    def _rows(self, events: Sequence[LedgerEvent]) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        dimension = 0
        for event in events:
            text = self._text(event)
            vector = [float(value) for value in self.vectorizer(text)]
            if not vector or not all(math.isfinite(value) for value in vector):
                raise ValueError("vectorizer must return a non-empty finite vector")
            if dimension and len(vector) != dimension:
                raise ValueError("vectorizer returned inconsistent dimensions")
            dimension = len(vector)
            rows.append(
                {
                    "event_id": event.event_id,
                    "task_id": event.task_id,
                    "sequence": event.sequence,
                    "kind": event.kind,
                    "status": event.status,
                    "observed_at": event.observed_at.isoformat(),
                    "text": text,
                    "vector": vector,
                }
            )
        return rows, dimension

    def _projection_id(self, events: Sequence[LedgerEvent]) -> str:
        body = {
            "embedding_version": self.embedding_version,
            "events": [event.model_dump(mode="json") for event in events],
        }
        return hashlib.sha256(canonical_json(body).encode()).hexdigest()

    def search(
        self, events: Sequence[LedgerEvent], query: str, *, limit: int = 32,
        mode: Literal["semantic", "hybrid"] = "hybrid",
    ) -> tuple[str, ...]:
        if not events or limit <= 0:
            return ()
        try:
            import lancedb  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - optional dependency boundary
            raise RuntimeError("install skein[memory-search] to use Lance search") from exc
        task_ids = {event.task_id for event in events}
        if len(task_ids) != 1:
            raise ValueError("one Lance projection cannot mix tasks")
        projection_id = self._projection_id(events)
        task_digest = hashlib.sha256(events[0].task_id.encode()).hexdigest()
        directory = self.root / task_digest / projection_id
        if not directory.exists():
            rows, dimension = self._rows(events)
            self._build(directory, rows, dimension)

        table = lancedb.connect(directory).open_table("events")
        query_vector = [float(value) for value in self.vectorizer(query)]
        vector_type = table.schema.field("vector").type
        dimension = int(vector_type.list_size)
        if len(query_vector) != dimension or not all(math.isfinite(value) for value in query_vector):
            raise ValueError("query vector does not match the event-vector schema")
        search = (table.search(query_type="hybrid").vector(query_vector).text(query)
                  if mode == "hybrid" else table.search(query_vector))
        result = search.limit(min(limit * 4, len(events))).to_arrow()
        score = "_relevance_score" if mode == "hybrid" else "_distance"
        ranked = sorted(
            result.select(["event_id", "sequence", score]).to_pylist(),
            key=lambda row: (float(row[score]) * (-1 if mode == "hybrid" else 1),
                             -int(row["sequence"]), row["event_id"]),
        )
        return tuple(str(row["event_id"]) for row in ranked[:limit])

    def _build(self, destination: Path, rows: list[dict[str, Any]], dimension: int) -> None:
        try:
            import lancedb  # pyright: ignore[reportMissingImports]
            import pyarrow as pa  # pyright: ignore[reportMissingImports]
            from lancedb.index import FTS  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - exercised without the optional extra
            raise RuntimeError("install skein[memory-search] to use Lance search") from exc

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".lance-", dir=destination.parent))
        try:
            schema = pa.schema(
                [
                    ("event_id", pa.string()),
                    ("task_id", pa.string()),
                    ("sequence", pa.int64()),
                    ("kind", pa.string()),
                    ("status", pa.string()),
                    ("observed_at", pa.string()),
                    ("text", pa.string()),
                    ("vector", pa.list_(pa.float32(), dimension)),
                ]
            )
            table = lancedb.connect(temporary).create_table(
                "events", data=pa.Table.from_pylist(rows, schema=schema)
            )
            table.create_index("text", config=FTS())
            with suppress(FileExistsError):
                os.replace(temporary, destination)
            # ponytail: immutable snapshots accumulate; add task-level GC when projection
            # bytes exceed the configured retention budget.
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
