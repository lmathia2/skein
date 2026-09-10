"""Stable model instruction and prompt-prefix fingerprinting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

DEFAULT_TOOL_NAMES = ("read", "bash", "edit", "write")


def build_static_prefix(
    *,
    model_name: str,
    tool_names: Iterable[str] = DEFAULT_TOOL_NAMES,
    instruction: str,
) -> str:
    """Build a deterministic behavior identity for the provider's stable prefix."""

    normalized_tools = sorted(set(tool_names))
    metadata = json.dumps(
        {"model": model_name, "tools": normalized_tools, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{instruction.strip()}\n\n<harness-interface>{metadata}</harness-interface>"


def prefix_hash(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()
