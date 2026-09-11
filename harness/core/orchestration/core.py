"""Pure orchestration decisions kept independent of Google ADK runtime objects."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum

from harness.core.context import estimate_tokens, truncate_to_tokens
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
        blockers.extend(step.questions or [step.next_action or "Coding agent is blocked"])
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
    recent_events: Iterable[str] = (),
    steering_messages: Iterable[str] = (),
    max_tokens: int = 20_000,
    section_token_limits: dict[str, int] | None = None,
) -> str:
    """Build deterministic dynamic input after the cache-stable system prefix."""

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
    sections: list[tuple[str, str]] = [
        ("TASK", json.dumps(ledger.compact_projection(), sort_keys=True, indent=2)),
        ("CONVERSATION", conversation),
        ("SELECTED SKILLS", selected_skills.strip()),
        ("REPOSITORY MANIFEST", repository_manifest.strip()),
        ("COMPACTED HISTORY", compaction_summary.strip()),
        ("RECENT EVENTS", "\n".join(recent_events).strip()),
        ("USER STEERING", "\n".join(steering_messages).strip()),
    ]
    rendered: list[str] = []
    for title, body in sections:
        if not body:
            continue
        bounded, truncated = truncate_to_tokens(body, max(limits[title], 0))
        if truncated:
            bounded += f"\n[{title.lower()} truncated to configured budget]"
        rendered.append(f"## {title}\n{bounded}")
    packet = "\n\n".join(rendered)
    if estimate_tokens(packet) > max_tokens:
        packet, _ = truncate_to_tokens(packet, max_tokens)
    return packet


def replan_ledger(ledger: TaskLedger) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["phase"] = "plan"
    data["status"] = "active"
    data["next_action"] = (
        "Reassess the current evidence and choose a materially different approach"
    )
    return TaskLedger.model_validate(data)
