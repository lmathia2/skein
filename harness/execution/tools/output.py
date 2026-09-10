"""Model-facing output normalization, truncation, and spill metadata."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from harness.core.models import ToolEnvelope, ToolStatus

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass(frozen=True, slots=True)
class BoundedOutput:
    text: str
    truncated: bool
    omitted_bytes: int


def normalize_output(text: str) -> str:
    text = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        run_end = index + 1
        while run_end < len(lines) and lines[run_end] == line:
            run_end += 1
        count = run_end - index
        collapsed.append(line)
        if count > 3:
            collapsed.append(f"... repeated {count - 1} additional times ...")
        elif count > 1:
            collapsed.extend([line] * (count - 1))
        index = run_end
    return "\n".join(collapsed)


def bound_output(text: str, *, max_chars: int = 16_000, max_lines: int = 400) -> BoundedOutput:
    canonical = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalize_output(text)
    summarized_repetition = normalized != canonical.rstrip("\n")
    lines = normalized.splitlines()
    over_lines = len(lines) > max_lines
    over_chars = len(normalized) > max_chars
    if not over_lines and not over_chars:
        omitted = max(0, len(canonical.encode("utf-8")) - len(normalized.encode("utf-8")))
        return BoundedOutput(normalized, summarized_repetition, omitted)

    line_limited = lines
    if over_lines:
        head_count = max_lines * 2 // 3
        tail_count = max_lines - head_count
        line_limited = [
            *lines[:head_count],
            "... [output lines omitted] ...",
            *lines[-tail_count:],
        ]
    candidate = "\n".join(line_limited)
    if len(candidate) > max_chars:
        marker = "\n... [output characters omitted] ...\n"
        available = max(0, max_chars - len(marker))
        head = available * 2 // 3
        tail = available - head
        candidate = candidate[:head] + marker + (candidate[-tail:] if tail else "")
    omitted = max(0, len(normalized.encode("utf-8")) - len(candidate.encode("utf-8")))
    return BoundedOutput(candidate, True, omitted)


def compact_tool_result(
    result: Mapping[str, Any], *, max_chars: int, max_lines: int = 400
) -> dict[str, Any]:
    """Project one rich tool result into the stable, bounded model envelope."""
    raw = dict(result)
    status = str(raw.get("status", "ok")).lower()
    if status not in {item.value for item in ToolStatus}:
        status = ToolStatus.ERROR
    text = str(raw.get("model_text", ""))
    if not text:
        stdout, stderr = str(raw.get("stdout", "")), str(raw.get("stderr", ""))
        text = "\n".join(part for part in (stdout, stderr) if part)
    bounded = bound_output(text, max_chars=max_chars, max_lines=max_lines)
    refs: set[str] = set()
    for key in ("artifact_uri", "artifact_uris", "artifact_refs", "output_files"):
        values = raw.get(key, [])
        values = values if isinstance(values, (list, tuple, set)) else [values]
        refs.update(
            value for value in values
            if isinstance(value, str) and value.startswith(("artifact://", "file://"))
        )
    hash_payload = {
        "status": status,
        "model_text": text,
        "exit_code": raw.get("exit_code"),
        "artifact_uris": sorted(refs),
        "truncated": bool(raw.get("truncated")),
        "omitted_bytes": max(0, int(raw.get("omitted_bytes", 0))),
        "effect": raw.get("effect"),
        "reconciliation_required": bool(raw.get("reconciliation_required")),
    }
    result_hash = str(raw.get("result_hash") or hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest())
    envelope = ToolEnvelope(
        status=ToolStatus(status),
        model_text=bounded.text,
        exit_code=raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None,
        truncated=bool(raw.get("truncated")) or bounded.truncated,
        omitted_bytes=max(0, int(raw.get("omitted_bytes", 0))) + bounded.omitted_bytes,
        artifact_uris=sorted(refs),
        result_hash=result_hash,
        effect=raw.get("effect"),
        attempt_id=str(raw.get("attempt_id") or raw.get("cell_id") or "") or None,
        replayed=bool(raw.get("replayed")),
        reconciliation_required=bool(raw.get("reconciliation_required")),
    )
    return envelope.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
