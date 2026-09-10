"""Runtime helpers shared by the ADK workflow and deterministic tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from harness.core.models.agent_step import AgentStep
from harness.core.models.task import TaskRequest

from .reply import parse_reply


def parse_task_request(value: str | dict[str, Any] | TaskRequest) -> TaskRequest:
    if isinstance(value, TaskRequest):
        return value
    if isinstance(value, dict):
        return TaskRequest.model_validate(value)
    text = value.strip()
    if text.startswith("{"):
        try:
            return TaskRequest.model_validate_json(text)
        except ValueError:
            pass
    return TaskRequest(goal=text, mode="auto")


def can_answer_directly(
    request: TaskRequest,
    step: AgentStep,
    *,
    verification_required: bool,
    workspace_unchanged: bool,
) -> bool:
    """An answer is not verified task completion; explicit work obligations win."""
    return (
        request.mode == "auto"
        and step.status == "answer"
        and bool(step.message.strip())
        and not request.acceptance_criteria
        and not request.verification_requirements
        and request.verification_level == "auto"
        and not step.completion_claims
        and not verification_required
        and workspace_unchanged
    )


def parse_agent_step(value: str | dict[str, Any] | AgentStep) -> AgentStep:
    if isinstance(value, AgentStep):
        return value
    if isinstance(value, dict):
        return AgentStep.model_validate(value)

    if not isinstance(value, str):
        raise ValueError("model response did not contain a valid AgentStep")
    reply = parse_reply(value)
    if reply is not None:
        return reply
    text = value.strip()
    decoder = json.JSONDecoder()
    last_error: ValueError | None = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text, index)
            return AgentStep.model_validate(payload)
        except ValueError as error:
            last_error = error

    raise ValueError(
        "model response did not contain a valid AgentStep JSON object"
    ) from last_error


def task_id_for(request: TaskRequest, session_id: str | None = None) -> str:
    canonical = json.dumps(
        {
            "session_id": session_id or "",
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def changed_paths(root: Path, base_revision: str | None) -> list[str]:
    command = ["git", "diff", "--name-only", "-z"]
    if base_revision and base_revision != "unknown":
        command.append(base_revision)
    else:
        command.append("HEAD")
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    tracked = completed.stdout.split("\0") if completed.returncode == 0 else []

    untracked_result = subprocess.run(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    untracked = (
        untracked_result.stdout.split("\0")
        if untracked_result.returncode == 0
        else []
    )
    return sorted(set(path for path in [*tracked, *untracked] if path))
