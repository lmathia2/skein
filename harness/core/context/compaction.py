"""Coding-aware compaction policy and structured handoff snapshots."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from harness.core.models import CompactionSnapshot, TaskLedger
from harness.execution.safety import SecretRedactor

from .compiler import estimate_tokens, truncate_to_tokens

_ARTIFACT_BLOCK_PATTERN = re.compile(r"<artifacts>\s*(.*?)\s*</artifacts>", re.DOTALL)
_CONTENT_ADDRESS_PATTERN = re.compile(r"[0-9a-f]{64}\.[A-Za-z0-9]{1,12}")
_FILE_ARTIFACT_PATTERN = re.compile(r"command-[0-9a-f]{64}\.log")
_MAX_ARTIFACT_SCAN_DEPTH = 8
_MAX_ARTIFACT_SCAN_NODES = 512
_MAX_ARTIFACT_SUMMARY_CHARS = 64_000
_MODEL_CONTEXT_REDACTOR = SecretRedactor()


class CompactionPolicy(BaseModel):
    """Bounds for deterministic legacy compaction snapshots."""

    model_config = ConfigDict(extra="forbid")

    retain_recent_events: int = Field(default=20, ge=1)
    max_event_chars: int = Field(default=2_000, ge=200)
    max_summary_tokens: int = Field(default=4_000, ge=512, le=32_000)
    max_previous_summary_tokens: int = Field(default=1_000, ge=128, le=8_000)
    max_summarized_event_tokens: int = Field(default=1_600, ge=128, le=16_000)
    max_artifact_references: int = Field(default=12, ge=0, le=64)
    max_artifact_uri_chars: int = Field(default=512, ge=64, le=2_048)

def _bullets(items: Sequence[str], *, empty: str = "- None") -> str:
    values = [item.strip() for item in items if item.strip()]
    return "\n".join(f"- {item}" for item in values) if values else empty


def _event_text(event: Any, *, max_artifact_uri_chars: int) -> str:
    if isinstance(event, str):
        return _MODEL_CONTEXT_REDACTOR.redact_text(event)
    kind = getattr(event, "kind", None)
    payload = getattr(event, "payload", None)
    sequence = getattr(event, "sequence", None)
    if kind is not None and isinstance(payload, dict) and sequence is not None:
        return json.dumps(
            {
                "sequence": int(sequence),
                "kind": str(kind),
                "payload": _redact_unsafe_artifact_fields(
                    _MODEL_CONTEXT_REDACTOR.redact(payload),
                    max_chars=max_artifact_uri_chars,
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    canonical_json = getattr(event, "canonical_json", None)
    if callable(canonical_json):
        return _MODEL_CONTEXT_REDACTOR.redact_text(str(canonical_json()))
    model_dump_json = getattr(event, "model_dump_json", None)
    if callable(model_dump_json):
        return _MODEL_CONTEXT_REDACTOR.redact_text(str(model_dump_json()))
    return _MODEL_CONTEXT_REDACTOR.redact_text(str(event))


def _event_id(event: Any) -> str | None:
    value = getattr(event, "event_id", None)
    return str(value) if value else None


def safe_artifact_uri(value: Any, *, max_chars: int = 512) -> str | None:
    """Accept only opaque, content-addressed artifact references emitted by the harness."""

    if not isinstance(value, str) or not value or len(value) > max_chars:
        return None
    if value != value.strip() or any(ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None

    decoded_path = unquote(parsed.path)
    if any(ord(character) < 32 for character in decoded_path):
        return None
    if any(part in {"", ".", ".."} for part in decoded_path.split("/")[1:-1]):
        return None
    filename = decoded_path.rsplit("/", 1)[-1]
    if parsed.scheme == "artifact":
        if not parsed.netloc or not decoded_path.startswith("/"):
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parsed.netloc):
            return None
        return value if _CONTENT_ADDRESS_PATTERN.fullmatch(filename) else None
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"} or not decoded_path.startswith("/"):
            return None
        return value if _FILE_ARTIFACT_PATTERN.fullmatch(filename) else None
    return None


def _redact_unsafe_artifact_fields(
    value: Any,
    *,
    max_chars: int,
    depth: int = 0,
) -> Any:
    """Remove untrusted artifact references from the model-facing event rendering."""

    if depth > _MAX_ARTIFACT_SCAN_DEPTH:
        return "<depth-limited>"
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, nested in value.items():
            if key == "artifact_uri":
                sanitized[key] = (
                    safe_artifact_uri(nested, max_chars=max_chars)
                    or "<unsafe-artifact-reference-omitted>"
                )
            elif (
                key == "artifact_uris"
                and isinstance(nested, Sequence)
                and not isinstance(nested, (str, bytes))
            ):
                sanitized[key] = [
                    artifact
                    for item in nested
                    if (artifact := safe_artifact_uri(item, max_chars=max_chars)) is not None
                ]
            else:
                sanitized[key] = _redact_unsafe_artifact_fields(
                    nested,
                    max_chars=max_chars,
                    depth=depth + 1,
                )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _redact_unsafe_artifact_fields(
                item,
                max_chars=max_chars,
                depth=depth + 1,
            )
            for item in value
        ]
    return value


def _structured_artifact_values(
    value: Any,
    *,
    max_chars: int,
    remaining_nodes: list[int],
    depth: int = 0,
) -> list[str]:
    """Find artifact fields without treating arbitrary event text as trusted references."""

    if depth > _MAX_ARTIFACT_SCAN_DEPTH or remaining_nodes[0] <= 0:
        return []
    remaining_nodes[0] -= 1
    found: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            if remaining_nodes[0] <= 0:
                break
            nested = value[key]
            if key == "artifact_uri":
                remaining_nodes[0] -= 1
                artifact = safe_artifact_uri(nested, max_chars=max_chars)
                if artifact is not None:
                    found.append(artifact)
                continue
            if (
                key == "artifact_uris"
                and isinstance(nested, Sequence)
                and not isinstance(nested, (str, bytes))
            ):
                for item in nested:
                    if remaining_nodes[0] <= 0:
                        break
                    remaining_nodes[0] -= 1
                    artifact = safe_artifact_uri(item, max_chars=max_chars)
                    if artifact is not None:
                        found.append(artifact)
                continue
            found.extend(
                _structured_artifact_values(
                    nested,
                    max_chars=max_chars,
                    remaining_nodes=remaining_nodes,
                    depth=depth + 1,
                )
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if remaining_nodes[0] <= 0:
                break
            found.extend(
                _structured_artifact_values(
                    item,
                    max_chars=max_chars,
                    remaining_nodes=remaining_nodes,
                    depth=depth + 1,
                )
            )
    return found


def _event_artifacts(
    event: Any,
    *,
    max_chars: int,
    remaining_nodes: list[int],
) -> list[str]:
    payload = getattr(event, "payload", event)
    return _structured_artifact_values(
        payload,
        max_chars=max_chars,
        remaining_nodes=remaining_nodes,
    )


def _summary_artifacts(summary: str, *, max_chars: int) -> list[str]:
    found: list[str] = []
    for block in _ARTIFACT_BLOCK_PATTERN.findall(summary[:_MAX_ARTIFACT_SUMMARY_CHARS]):
        for line in block.splitlines():
            artifact = safe_artifact_uri(line.strip(), max_chars=max_chars)
            if artifact is not None:
                found.append(artifact)
    return found


def _collect_artifacts(
    *,
    previous_summary: CompactionSnapshot | str | None,
    events_to_summarize: Sequence[Any],
    retained_events: Sequence[Any],
    policy: CompactionPolicy,
) -> list[str]:
    """Prefer newest event artifacts, then carry forward prior snapshot references."""

    if policy.max_artifact_references == 0:
        return []
    candidates: list[str] = []
    remaining_nodes = [_MAX_ARTIFACT_SCAN_NODES]
    for event in reversed(retained_events):
        if remaining_nodes[0] <= 0:
            break
        candidates.extend(
            _event_artifacts(
                event,
                max_chars=policy.max_artifact_uri_chars,
                remaining_nodes=remaining_nodes,
            )
        )
    for event in reversed(events_to_summarize):
        if remaining_nodes[0] <= 0:
            break
        candidates.extend(
            _event_artifacts(
                event,
                max_chars=policy.max_artifact_uri_chars,
                remaining_nodes=remaining_nodes,
            )
        )
    if isinstance(previous_summary, CompactionSnapshot):
        candidates.extend(previous_summary.artifact_uris[:_MAX_ARTIFACT_SCAN_NODES])
        candidates.extend(
            _summary_artifacts(
                previous_summary.summary_markdown,
                max_chars=policy.max_artifact_uri_chars,
            )
        )
    elif isinstance(previous_summary, str):
        candidates.extend(
            _summary_artifacts(previous_summary, max_chars=policy.max_artifact_uri_chars)
        )

    selected: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        safe_candidate = safe_artifact_uri(
            candidate,
            max_chars=policy.max_artifact_uri_chars,
        )
        if safe_candidate is None or safe_candidate in seen:
            continue
        seen.add(safe_candidate)
        selected.append(safe_candidate)
        if len(selected) == policy.max_artifact_references:
            break
    return sorted(selected)


def _render_events(
    events: Sequence[Any],
    *,
    max_chars: int,
    max_artifact_uri_chars: int,
) -> str:
    blocks: list[str] = []
    for event in events:
        text = _event_text(event, max_artifact_uri_chars=max_artifact_uri_chars)
        bounded, truncated = truncate_to_tokens(text, max(1, max_chars // 4))
        suffix = " [truncated]" if truncated else ""
        blocks.append(f"- {bounded}{suffix}")
    return "\n".join(blocks) if blocks else "- None"


def build_compaction_snapshot(
    *,
    ledger: TaskLedger,
    previous_summary: CompactionSnapshot | str | None = None,
    events_to_summarize: Sequence[Any] = (),
    retained_events: Sequence[Any] = (),
    tokens_before: int = 0,
    policy: CompactionPolicy | None = None,
) -> CompactionSnapshot:
    """Build a deterministic fallback summary with Pi-compatible structure.

    A production deployment may replace this body with an LLM-generated summary,
    but the section contract and file/validation bookkeeping remain stable.
    """

    active_policy = policy or CompactionPolicy()
    previous_text_raw = (
        previous_summary.summary_markdown
        if isinstance(previous_summary, CompactionSnapshot)
        else previous_summary or "None"
    )
    previous_text, _ = truncate_to_tokens(
        _MODEL_CONTEXT_REDACTOR.redact_text(previous_text_raw),
        active_policy.max_previous_summary_tokens,
    )
    latest_validation = (
        ledger.validations[-1].summary if ledger.validations else "No validation run yet."
    )
    decisions = [
        f"**{decision.summary}**: {decision.rationale or 'No rationale recorded.'}"
        for decision in ledger.decisions[-12:]
    ]
    plan_completed = [step.description for step in ledger.plan if step.status.value == "complete"]
    plan_in_progress = [step.description for step in ledger.plan if step.status.value == "active"]
    artifact_uris = _collect_artifacts(
        previous_summary=previous_summary,
        events_to_summarize=events_to_summarize,
        retained_events=retained_events,
        policy=active_policy,
    )
    summarized_events, _ = truncate_to_tokens(
        _render_events(
            events_to_summarize,
            max_chars=active_policy.max_event_chars,
            max_artifact_uri_chars=active_policy.max_artifact_uri_chars,
        ),
        active_policy.max_summarized_event_tokens,
    )

    summary_unbounded = f"""## Goal
{ledger.goal}

## Acceptance Criteria
{_bullets(ledger.acceptance_criteria)}

## Constraints & Non-Goals
{_bullets([*ledger.constraints, *(f"Non-goal: {item}" for item in ledger.non_goals)])}

## Progress
### Done
{_bullets([*plan_completed, *ledger.progress[-12:]])}

### In Progress
{_bullets(plan_in_progress or ([ledger.next_action] if ledger.next_action else []))}

### Blocked
{_bullets(ledger.blockers)}

## Key Decisions
{_bullets(decisions)}

## Current Code State
- Base revision: {ledger.base_revision}
- Workspace: {ledger.workspace_id}
- Branch: {ledger.branch_id}
- Files modified: {", ".join(sorted(set(ledger.files_modified))) or "None"}

## Validation
- Latest result: {latest_validation}
- Commands run: {", ".join(result.command for result in ledger.validations[-10:]) or "None"}

## Next Action
{
        ledger.next_action
        or "Reconstruct the next action from the goal and latest verification evidence."
    }

## Critical Context
### Previous Summary
{previous_text}

### Newly Summarized Events
{summarized_events}

<read-files>
{chr(10).join(sorted(set(ledger.files_read))) or "(none)"}
</read-files>

<modified-files>
{chr(10).join(sorted(set(ledger.files_modified))) or "(none)"}
</modified-files>

### Recoverable Artifacts
Complete outputs remain outside the compacted context under these identifiers.
<artifacts>
{chr(10).join(artifact_uris) or "(none)"}
</artifacts>
""".strip()
    summary, _ = truncate_to_tokens(
        summary_unbounded,
        active_policy.max_summary_tokens,
    )

    retained_text = _render_events(
        retained_events,
        max_chars=active_policy.max_event_chars,
        max_artifact_uri_chars=active_policy.max_artifact_uri_chars,
    )
    estimated_after = estimate_tokens(summary) + estimate_tokens(retained_text)
    previous_hash = (
        previous_summary.content_hash()
        if isinstance(previous_summary, CompactionSnapshot)
        else None
    )
    return CompactionSnapshot(
        summary_markdown=summary,
        previous_summary_hash=previous_hash,
        first_retained_event_id=(_event_id(retained_events[0]) if retained_events else None),
        last_summarized_event_id=(
            _event_id(events_to_summarize[-1]) if events_to_summarize else None
        ),
        tokens_before=tokens_before,
        estimated_tokens_after=estimated_after,
        files_read=sorted(set(ledger.files_read)),
        files_modified=sorted(set(ledger.files_modified)),
        artifact_uris=artifact_uris,
    )
