"""Exercise PTC proposals through real workflow verification, without a provider."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from app.agent.factory import default_harness_registry
from harness.adapters.adk.runtime.bootstrap import build_server_assembly
from harness.adapters.adk.runtime.protocol import StartTaskMessage
from harness.adapters.providers import ClosedAdkModelProviderRegistry
from harness.core.config import load_harness_composition
from harness.core.models.task import criterion_id
from harness.evidence.ledger import open_ledger


class _ProposalModel(BaseLlm):
    _calls: int = PrivateAttr(default=0)
    _repair: bool = PrivateAttr(default=False)

    async def generate_content_async(self, llm_request, stream=False):
        del llm_request, stream
        index, self._calls = self._calls, self._calls + 1
        if index == 0:
            code = (
                "import json\n"
                "print(agent.fs.write('answer.json', '{\"value\": 11}', expected_absent=True))\n"
                "self_check = agent.fs.read('answer.json')\n"
                "assert json.loads(self_check['data']['text']) == {'value': 11}\n"
                "print('self_check passed')"
            )
        elif index == 3 and self._repair:
            code = (
                "evidence = agent.fs.read('evidence.py', offset=18, limit=1)\n"
                "assert evidence['status'] == 'ok'\n"
                "value = int(evidence['data']['text'].split('=')[1])\n"
                "old_answer = agent.fs.read('answer.json')\n"
                "print(agent.fs.write('answer.json', json.dumps({'value': value}), "
                "expected_sha256=old_answer['data']['sha256']))"
            )
        else:
            code = None
        part = types.Part(function_call=types.FunctionCall(
            id=f"cell-{index}", name="execute_code", args={"code": code},
        )) if code else types.Part(text=json.dumps({
            "status": "verify", "message": "The answer is verified.",
            "completion_claims": [{"criterion_id": criterion_id("Answer uses SAFE_VALUE"),
                                   "evidence": ["answer.json"]}],
        }))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class _ProposalProvider:
    provider_id = "proposal_fixture"

    def __init__(self, model):
        self.model = model

    def build_model(self, config, *, secrets, bindings=None):
        return self.model


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", (False, True))
async def test_wrong_self_checked_answer_requires_independent_verification(tmp_path, repair):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "evidence.py").write_text("VALUE = 11\n" + "# unrelated\n" * 16 + "SAFE_VALUE = 73\n")
    oracle = (
        "import json, unittest\nfrom pathlib import Path\nclass Check(unittest.TestCase):\n"
        " def test_source_grounded_value(self):\n"
        "  self.assertEqual(json.loads(Path('answer.json').read_text()), {'value': 73})\n"
    )
    (workspace / "test_check.py").write_text(oracle)
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    profile = Path(__file__).resolve().parents[2] / "harness/core/config/profiles/notebook-ptc-jsonl.yaml"
    payload = load_harness_composition(profile).model_dump(mode="json")
    payload["harness"]["config"]["models"]["coding"].update(provider="proposal_fixture", name="proposal-fixture")
    payload["harness"]["config"]["tools"]["search"]["backend"] = "disabled"
    (tmp_path / "config.json").write_text(json.dumps(payload))
    model = _ProposalModel(model="proposal-fixture")
    model._repair = repair
    registry = default_harness_registry(model_providers=ClosedAdkModelProviderRegistry((_ProposalProvider(model),)))
    assembly = build_server_assembly(workspace=workspace, state_root=tmp_path / "state",
                                    config_path=tmp_path / "config.json", registry=registry)
    coordinator = assembly.coordinator
    try:
        record, _ = await coordinator.start(StartTaskMessage(
            type="task.start", request_id="run", idempotency_key="run", thread_id="conversation",
            input=json.dumps({
                "goal": "Write answer.json using SAFE_VALUE from evidence.py", "mode": "coding",
                "acceptance_criteria": ["Answer uses SAFE_VALUE"], "permitted_paths": ["answer.json"],
                "verification_requirements": [f"{sys.executable} -m unittest test_check"],
            }),
        ), user_id="owner")
        async with asyncio.timeout(60):
            await coordinator.wait(record.run_id)
        events = open_ledger(tmp_path / "state" / "runs" / record.run_id, "jsonl").read(record.run_id)
        reports = [e.payload["report"] for e in events if e.kind == "verification.completed"]
        assert [r["passed"] for r in reports] == [False, repair]
        finished = [e for e in events if e.kind == "task.finished"]
        assert len(finished) == int(repair)
        assert (workspace / "test_check.py").read_text() == oracle
        reads = [e.payload["read_evidence"] for e in events
                 if e.payload.get("read_evidence", {}).get("path") == "evidence.py"]
        assert [(r["offset"], r["returned_lines"]) for r in reads] == ([(18, 1)] if repair else [])
        assert json.loads((workspace / "answer.json").read_text()) == {"value": 73 if repair else 11}
    finally:
        await coordinator.aclose()
