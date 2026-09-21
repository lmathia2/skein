"""Pure orchestration decisions kept independent of Google ADK runtime objects."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from enum import StrEnum

from harness.core.context import estimate_tokens, truncate_to_tokens
from harness.core.context.compiler import ContextBudgetExceeded
from harness.core.models.agent_step import AgentStep
from harness.core.models.ledger import TaskLedger
from harness.core.models.task import CriterionRow, TaskRequest, criterion_id
from harness.evidence.state.progress import ProgressRoute, route_for_progress


class HarnessRoute(StrEnum):
    CONTINUE = "continue"
    REPLAN = "replan"
    BLOCKED = "blocked"
    VERIFY = "verify"


def create_initial_ledger(
    request: TaskRequest,
    *,
    task_id: str,
    base_revision: str,
    workspace_id: str,
    branch_id: str,
) -> TaskLedger:
    return TaskLedger(
        task_id=task_id,
        goal=request.goal,
        mode=request.mode,
        acceptance_criteria=request.acceptance_criteria,
        criteria_inferred=request.criteria_inferred,
        constraints=request.constraints,
        non_goals=request.non_goals,
        permitted_paths=request.permitted_paths,
        forbidden_paths=request.forbidden_paths,
        verification_requirements=request.verification_requirements,
        verification_level=request.verification_level,
        max_input_tokens=request.max_input_tokens,
        base_revision=base_revision,
        workspace_id=workspace_id,
        branch_id=branch_id,
        next_action=(
            "Respond directly to conversation or explanation requests. For requested "
            "code changes, inspect relevant code, implement and verify the change."
            if request.mode == "auto" else
            "Inspect the repository and identify the smallest coherent change"
        ),
    )


def reduce_agent_step(ledger: TaskLedger, step: AgentStep) -> TaskLedger:
    """Project one model work batch into the durable ledger schema."""

    data = ledger.model_dump(mode="python")
    data["iteration"] = int(data.get("iteration", 0)) + 1
    if step.next_action:
        data["next_action"] = step.next_action
    data["progress"] = list(
        dict.fromkeys([*data.get("progress", []), *step.progress])
    )
    decisions = list(data.get("decisions", []))
    known_decisions = {str(decision.get("summary", "")) for decision in decisions}
    decisions.extend(
        {"summary": decision}
        for decision in dict.fromkeys(step.decisions)
        if decision not in known_decisions
    )
    data["decisions"] = decisions
    if step.criterion_proposals:
        if (
            not ledger.criteria_inferred
            or ledger.phase != "review"
            or any(row.parent_id is not None for row in ledger.criterion_rows)
        ):
            raise ValueError("criterion decomposition is allowed only at the first inferred review")
        rows = list(ledger.criterion_rows)
        known = {row.criterion_id for row in rows}
        proposals = []
        for proposal in step.criterion_proposals:
            if proposal.parent_id not in known:
                raise ValueError(f"unknown criterion parent: {proposal.parent_id}")
            row = CriterionRow(
                criterion_id=criterion_id(proposal.text, proposal.parent_id),
                text=proposal.text,
                parent_id=proposal.parent_id,
                probe=proposal.probe,
            )
            if row.criterion_id not in known:
                proposals.append(row)
                known.add(row.criterion_id)
        for parent_id in {row.parent_id for row in proposals if row.probe != "general"}:
            probes = {row.probe for row in proposals if row.parent_id == parent_id}
            if not {"positive", "negative", "precondition_unmet"}.issubset(probes):
                raise ValueError("transition criterion proposals require all three probe rows")
        rows.extend(proposals)
        data["criterion_rows"] = [row.model_dump(mode="python") for row in rows]
        data["acceptance_criteria"] = [row.text for row in rows]
    if "constraints" in data:
        data["constraints"] = list(
            dict.fromkeys([*data.get("constraints", []), *step.discovered_constraints])
        )
    if "open_questions" in data:
        data["open_questions"] = list(
            dict.fromkeys([*data.get("open_questions", []), *step.questions])
        )

    if step.status == "blocked":
        data["phase"] = "blocked"
        data["status"] = "needs_input"
        blockers = list(data.get("blockers", []))
        blockers.extend(step.questions or [step.message or step.next_action or "Coding agent is blocked"])
        data["blockers"] = list(dict.fromkeys(blockers))
    elif step.status in {"verify", "done"}:
        data["phase"] = "verify"
        data["status"] = "verifying"
    else:
        data["phase"] = "implement"
        data["status"] = "active"

    return TaskLedger.model_validate(data)


def decide_route(
    ledger: TaskLedger,
    step: AgentStep,
    *,
    pending_steering: bool = False,
    replan_after_no_progress: int = 2,
    block_after_no_progress: int = 4,
) -> HarnessRoute:
    if pending_steering:
        return HarnessRoute.CONTINUE
    if step.status == "blocked":
        return HarnessRoute.BLOCKED
    if step.status in {"verify", "done"}:
        return HarnessRoute.VERIFY
    progress_route = route_for_progress(
        ledger,
        replan_threshold=replan_after_no_progress,
        human_threshold=block_after_no_progress,
    )
    if progress_route == ProgressRoute.NEEDS_INPUT:
        return HarnessRoute.BLOCKED
    if progress_route == ProgressRoute.REPLAN:
        return HarnessRoute.REPLAN
    return HarnessRoute.CONTINUE


def resume_for_steering(ledger: TaskLedger) -> TaskLedger:
    """Return a terminal-bound ledger to an active safe-point steering state."""

    data = ledger.model_dump(mode="python")
    data["phase"] = "implement"
    data["status"] = "active"
    data["next_action"] = "Apply the newest user steering before continuing"
    return TaskLedger.model_validate(data)


def build_work_packet(
    ledger: TaskLedger,
    *,
    selected_skills: str = "",
    conversation: str = "",
    repository_manifest: str = "",
    compaction_summary: str = "",
    evidence_navigation: str = "",
    recent_events: Iterable[str] = (),
    steering_messages: Iterable[str] = (),
    max_tokens: int = 20_000,
    section_token_limits: dict[str, int] | None = None,
) -> str:
    """Reserve complete control first; section targets bound optional detail.

    Required sections may exceed their preferred allocation, never the packet
    ceiling. No JSON or active instruction is head/tail-spliced to make it fit.
    """

    limits = {
        "TASK": 2_000,
        "CONVERSATION": 2_000,
        "SELECTED SKILLS": 6_000,
        "REPOSITORY MANIFEST": 800,
        "COMPACTED HISTORY": 3_000,
        "RECENT EVENTS": 3_500,
        "USER STEERING": 1_000,
    }
    limits.update(section_token_limits or {})
    projection = ledger.compact_projection()
    optional_fields = ("latest_validation", "files_in_focus", "files_modified",
                       "completed_step_ids", "recent_progress", "open_questions")
    task = {key: value for key, value in projection.items() if key not in optional_fields}
    task.update(packet_version="work_packet@3", permitted_paths=ledger.permitted_paths,
                forbidden_paths=ledger.forbidden_paths,
                verification_requirements=ledger.verification_requirements,
                verification_level=ledger.verification_level,
                omitted_fields=list(optional_fields))

    def task_text(value: dict[str, object]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    sections: list[tuple[str, str]] = [
        ("TASK", task_text(task)),
        ("CONVERSATION", conversation),
        ("SELECTED SKILLS", selected_skills.strip()),
        ("REPOSITORY MANIFEST", repository_manifest.strip()),
        ("COMPACTED HISTORY", compaction_summary.strip()),
        ("EVIDENCE NAVIGATION", evidence_navigation.strip()),
        ("RECENT EVENTS", "\n".join(recent_events).strip()),
        ("USER STEERING", "\n".join(steering_messages).strip()),
    ]
    required = {"TASK", "SELECTED SKILLS", "COMPACTED HISTORY", "EVIDENCE NAVIGATION", "USER STEERING"}
    rendered = {title: f"## {title}\n{body}" for title, body in sections if body and title in required}

    def packet(values: dict[str, str]) -> str:
        return "\n\n".join(values[title] for title, _ in sections if title in values)

    required_tokens = estimate_tokens(packet(rendered))
    if required_tokens > max_tokens:
        raise ContextBudgetExceeded(required_tokens, max_tokens)
    for field in optional_fields:
        candidate = {**task, field: projection[field]}
        candidate["omitted_fields"] = [key for key in optional_fields if key not in candidate]
        body = task_text(candidate)
        proposed = {**rendered, "TASK": f"## TASK\n{body}"}
        if estimate_tokens(body) <= limits["TASK"] and estimate_tokens(packet(proposed)) <= max_tokens:
            task, rendered = candidate, proposed
    for title, body in sections:
        if not body or title in required:
            continue
        header = f"## {title}\n"
        available = (max_tokens * 4 - len(packet(rendered)) - len(header) - 2) // 4
        limit = max(0, min(limits[title], available))
        if limit <= 0:
            continue
        bounded, truncated = truncate_to_tokens(body, limit)
        if truncated:
            marker = f"\n[{title.lower()} truncated to configured budget]"
            if estimate_tokens(marker) >= limit:
                continue
            bounded, _ = truncate_to_tokens(body, limit - estimate_tokens(marker))
            bounded += marker
        rendered[title] = header + bounded
    return packet(rendered)


def build_thin_packet(
    ledger: TaskLedger,
    *,
    selected_skills: str = "",
    conversation: str = "",
    compaction_summary: str = "",
    steering_messages: Iterable[str] = (),
    max_tokens: int = 20_000,
) -> str:
    """Render the durable ledger as a small model-facing continuation."""

    criteria = "\n".join(f"- {row.text}" for row in ledger.criterion_rows) or "- Complete the requested outcome."
    constraints = "\n".join(f"- {item}" for item in ledger.constraints) or "- None."
    changed = "\n".join(f"- `{path}`" for path in ledger.files_modified) or "- None yet."
    latest_validation = ledger.compact_projection().get("latest_validation")
    if isinstance(latest_validation, dict):
        mark = "passed" if latest_validation.get("passed") else "failed"
        validation = f"**{mark.upper()}** — `{latest_validation.get('command', 'verification')}`"
        if latest_validation.get("summary"):
            validation += f"\n\n{latest_validation['summary']}"
        if latest_validation.get("artifact_uri"):
            validation += f"\n\nDetails: `{latest_validation['artifact_uri']}`"
    else:
        validation = "No independent verification result yet."
    sections = [
        ("Goal", ledger.goal),
        ("Acceptance criteria", criteria),
        ("Constraints", constraints),
        ("Workspace changes", changed),
        ("Latest verification", validation),
        ("Next action", ledger.next_action or "Continue the task."),
        ("Selected skills", selected_skills.strip()),
        ("Earlier context", compaction_summary.strip()),
        ("Recent conversation", conversation.strip()),
        ("User steering", "\n".join(steering_messages).strip()),
    ]
    text = "\n\n".join(f"## {title}\n\n{body}" for title, body in sections if body)
    bounded, truncated = truncate_to_tokens(text, max_tokens)
    return bounded + ("\n\n[projection truncated to configured budget]" if truncated else "")


def work_packet_sections(packet: str) -> dict[str, str]:
    """Recover complete, ordered work-packet sections for append-only updates."""
    sections: dict[str, str] = {}
    titles = {"TASK", "CONVERSATION", "SELECTED SKILLS", "REPOSITORY MANIFEST",
              "COMPACTED HISTORY", "EVIDENCE NAVIGATION", "RECENT EVENTS", "USER STEERING"}
    boundary = r"\n\n(?=## (?:" + "|".join(sorted(titles)) + r")\n)"
    for block in re.split(boundary, packet):
        block = block.removeprefix("## ")
        title, separator, body = block.partition("\n")
        if not separator or title not in titles or title in sections:
            raise ValueError("invalid work-packet section")
        sections[title] = body
    return sections


def _field_patch(previous: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    """A field-level merge patch; null explicitly removes an old field."""
    return {key: current.get(key) for key in sorted(previous.keys() | current.keys())
            if previous.get(key, object()) != current.get(key, object())}


def build_work_packet_update(
    full_packet: str,
    previous_sections: dict[str, str] | None,
    *,
    previous_recent_sequence: int = 0,
) -> tuple[str, dict[str, str], int]:
    """Append only changed sections while retaining a full replayable snapshot."""
    sections = work_packet_sections(full_packet)
    if previous_sections is None:
        recent = sections.get("RECENT EVENTS", "")
        sequence = max((int(line.split(".", 1)[0]) for line in recent.splitlines()
                        if line.split(".", 1)[0].isdigit()), default=0)
        return full_packet, sections, sequence
    updates: list[str] = []
    recent_sequence = previous_recent_sequence
    for title, body in sections.items():
        old = previous_sections.get(title)
        if title == "RECENT EVENTS":
            fresh = []
            for line in body.splitlines():
                prefix = line.split(".", 1)[0]
                if prefix.isdigit():
                    sequence = int(prefix)
                    recent_sequence = max(recent_sequence, sequence)
                    if sequence > previous_recent_sequence:
                        fresh.append(line)
            if fresh:
                updates.append("## RECENT EVENTS\n" + "\n".join(fresh))
            continue
        if body == old:
            continue
        if title == "TASK" and old is not None:
            patch = _field_patch(json.loads(old), json.loads(body))
            if patch:
                updates.append("## TASK UPDATE\n" + json.dumps(
                    {"program": "work_packet_task_update@1", "fields": patch},
                    sort_keys=True, separators=(",", ":")))
            continue
        if title == "USER STEERING" and old is not None:
            # The projection's source watermark changes on unrelated events.
            # Only new delivered messages are a new model instruction.
            try:
                old_messages = json.loads(old.partition("\n")[2]).get("messages", [])
                new_messages = json.loads(body.partition("\n")[2]).get("messages", [])
            except (ValueError, TypeError, AttributeError):
                old_messages, new_messages = [], []
            seen = {item.get("message_id") for item in old_messages}
            fresh = [item for item in new_messages if item.get("message_id") not in seen]
            if not fresh:
                continue
            updates.append("## USER STEERING\n" + json.dumps(
                {"program": "delivered_steering_update@1", "messages": fresh},
                sort_keys=True, separators=(",", ":")))
            continue
        if old is not None and not body:
            updates.append(f"## {title} UPDATE\nUnavailable (previous section withdrawn).")
        elif body:
            updates.append(f"## {title}\n{body}")
    for title in previous_sections.keys() - sections.keys():
        if title != "RECENT EVENTS":
            updates.append(f"## {title} UPDATE\nUnavailable (previous section withdrawn).")
    return "\n\n".join(updates) or "## TASK UPDATE\nNo change to previously supplied work packet.", sections, recent_sequence


def replan_ledger(ledger: TaskLedger) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["phase"] = "plan"
    data["status"] = "active"
    data["next_action"] = (
        "Reassess the current evidence and choose a materially different approach"
    )
    return TaskLedger.model_validate(data)
