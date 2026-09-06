"""Real SQLite ADK restart at a published checkpoint; no provider calls."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from app.agent.factory import default_harness_registry
from harness.ai import ClosedAdkModelProviderRegistry
from harness.approvals import ApprovalStore
from harness.config import load_harness_composition
from harness.ledger import LedgerBackedEventStore, open_ledger
from harness.ledger.importers import import_tool_receipt
from harness.server.bootstrap import build_server_assembly
from harness.server.protocol import CancelTaskMessage, StartTaskMessage
from harness.state import CheckpointStore, JsonlEventStore, ToolReceiptStore


class ResumeModel(BaseLlm):
    _write: bool = PrivateAttr(default=False)

    async def generate_content_async(self, llm_request, stream=False):
        del llm_request, stream
        if self._write:
            self._write = False
            part = types.Part(function_call=types.FunctionCall(
                id="write-once", name="write", args={"path": "result.txt", "content": "done"},
            ))
        else:
            part = types.Part(text=json.dumps({
                "status": "verify", "message": "Verify result.",
                "completion_claims": [{"criterion": "result is done", "evidence": ["result.txt"]}],
            }))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class ResumeProvider:
    provider_id = "resume_fixture"

    def __init__(self, model):
        self.model = model

    def build_model(self, config, *, secrets, bindings=None):
        return self.model


async def _child(root: Path, phase: str) -> None:
    model = ResumeModel(model="resume-fixture")
    model._write = phase == "fault"
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((ResumeProvider(model),)))
    assembly = build_server_assembly(workspace=root / "workspace", state_root=root / "state",
                                     config_path=root / "config.json", registry=registry)
    coordinator = assembly.coordinator
    if phase == "fault":
        service = coordinator.execution_factory.services.session_service
        original = service.append_event

        async def append_event(session, event):
            result = await original(session=session, event=event)
            if event.actions.state_delta.get("checkpoint_id"):
                os._exit(86)
            return result

        service.append_event = append_event
        record, _ = await coordinator.start(StartTaskMessage(
            type="task.start", request_id="run", idempotency_key="run", thread_id="conversation",
            input=json.dumps({"goal": "Write result.txt as done and verify", "mode": "coding",
                              "acceptance_criteria": ["result is done"],
                              "verification_requirements": [f"{sys.executable} -m unittest test_check"]}),
        ), user_id="owner")
        await coordinator.wait(record.run_id)
        raise AssertionError("fault boundary was not reached")
    record = coordinator.conversations.store.run_for_key("owner", "run")
    assert record is not None
    run_root = root / "state" / "runs" / record.run_id
    canonical = open_ledger(run_root, "jsonl")
    event_store = LedgerBackedEventStore(JsonlEventStore(run_root / "events"), canonical)
    if phase == "expired_budget":
        event_store.append(record.run_id, "execution.model_budget_reserved", {
            "estimated_task_input_tokens": 200_000, "task_input_token_limit": 200_000,
        })
    elif phase == "expired_approval":
        ApprovalStore(run_root / "approvals.db").request(
            task_id=record.run_id, fingerprint="expired", operation="git push", risk="high",
            reason="operator approval required", expires_at="2000-01-01T00:00:00+00:00",
        )
    elif phase == "missing_receipt":
        receipts = ToolReceiptStore(run_root / "managed-tools.db", on_save=lambda receipt: import_tool_receipt(canonical, receipt))
        receipts.begin(task_id=record.run_id, invocation_id=record.invocation_id, tool_call_id="lost-intent",
                       tool_name="bash", arguments_hash="unchanged-workspace-external-effect")
        with receipts._connect() as connection:
            connection.execute("DELETE FROM tool_receipts WHERE tool_call_id='lost-intent'")
    elif phase == "unpublished_checkpoint":
        checkpoints = CheckpointStore(run_root / "state.db")
        checkpoint = checkpoints.latest(record.run_id)
        assert checkpoint is not None
        candidate = checkpoint.model_copy(update={"checkpoint_id": "not-adk-published", "parent_checkpoint_id": checkpoint.checkpoint_id})
        event_store.append(record.run_id, "checkpoint.created", {"checkpoint_id": candidate.checkpoint_id})
        checkpoints.save(candidate)
    coordinator.recover_interrupted_runs()
    if phase == "cancel":
        await coordinator.cancel(CancelTaskMessage(type="task.cancel", run_id=record.run_id, idempotency_key="cancel"), user_id="owner")
    resumed = await coordinator.wait(record.run_id)
    events = coordinator.store.replay(record.run_id)
    session = await coordinator.execution_factory.services.session_service.get_session(
        app_name="skein", user_id="owner", session_id=record.session_id,
    )
    assert session is not None
    user_events = [event for event in session.events if event.author == "user"]
    output = {"status": resumed.status, "error": resumed.error,
              "user_messages": len(user_events), "invocations": sorted({event.invocation_id for event in user_events}),
              "invocation": record.invocation_id,
              "final": events[-1].event.model_dump(mode="json")}
    print(json.dumps(output))
    await coordinator.aclose()


@pytest.mark.parametrize("phase,expected", [
    ("recover", "completed"), ("unpublished_checkpoint", "completed"),
    ("expired_budget", "failed"), ("expired_approval", "failed"),
    ("missing_receipt", "failed"), ("cancel", "cancelled"),
])
def test_real_process_resume_same_invocation_to_verified_completion(tmp_path: Path, phase: str, expected: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_check.py").write_text(
        "import unittest\nfrom pathlib import Path\nclass Check(unittest.TestCase):\n"
        " def test_result(self): self.assertEqual(Path('result.txt').read_text(), 'done')\n"
    )
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "test_check.py"], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    profile = Path(__file__).resolve().parents[2] / "harness/config/profiles/four-tool.yaml"
    payload = load_harness_composition(profile).model_dump(mode="json")
    payload["harness"]["config"]["memory"]["enabled"] = True
    payload["harness"]["config"]["adk"]["recovery"] = "safe_auto"
    payload["harness"]["config"]["models"]["coding"]["provider"] = "resume_fixture"
    payload["harness"]["config"]["models"]["coding"]["name"] = "resume-fixture"
    payload["harness"]["config"]["tools"]["search"]["backend"] = "disabled"
    (tmp_path / "config.json").write_text(json.dumps(payload))
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path)]
    fault = subprocess.run([*command, "fault"], capture_output=True, text=True, timeout=40)
    assert fault.returncode == 86, fault.stdout + fault.stderr
    assert (workspace / "result.txt").read_text() == "done"
    resumed = subprocess.run([*command, phase], capture_output=True, text=True, timeout=40)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    output = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert output["status"] == expected, output
    if expected == "completed":
        assert output["final"]["result"]["status"] == "complete", output
    elif expected == "failed":
        assert output["final"]["code"] == "recovery_blocked", output
    assert output["user_messages"] == 1
    assert output["invocations"] == [output["invocation"]]


if __name__ == "__main__":
    asyncio.run(_child(Path(sys.argv[1]), sys.argv[2]))
