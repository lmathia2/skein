"""Shared strict and deterministic model helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and serializes canonically."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def canonical_json(self, *, exclude: set[str] | None = None) -> str:
        payload: Any = self.model_dump(mode="json", exclude=exclude or set())
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def content_hash(self, *, exclude: set[str] | None = None) -> str:
        return hashlib.sha256(self.canonical_json(exclude=exclude).encode("utf-8")).hexdigest()
