import json
import os

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.heldout_continuity import HELDOUT_CASES
from evals.learned_continuity import LearnedContinuation
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ("no_recall", "findings"))
@pytest.mark.parametrize("case,pending_answer", [(case, False) for case in HELDOUT_CASES] + [("heldout_rollout", True)])
async def test_real_worker_loss_keeps_authorized_recovery_and_completed_evidence_gate(
    tmp_path, monkeypatch, caplog, arm, case, pending_answer,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0
    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        visible = "\n".join(p.text or "" for c in request.contents for p in c.parts or [])
        if index == 0:
            code = ""
            if arm == "findings":
                code += (
                    "contract = agent.shell.run('memory note schema')\nassert contract['status'] == 'ok'\n"
                    "assert contract['result_kind'] == 'managed' and 'exit_code' not in contract\n"
                    "assert 'stdout' not in contract['data']\n"
                    "assert contract['data']['input_schema']['$defs']['MemoryFinding']['properties']['id']['pattern'] == '^[A-Za-z0-9_-]{1,96}$'\n"
                )
            code += f"sources = {{p: agent.fs.read(p) for p in {sorted(trial.fixture['files'])!r}}}\n"
            code += "assert all(r['status'] == 'ok' for r in sources.values())\nprint('ACQUISITION_ONLY_SENTINEL')"
        elif index == 1:
            assert trial.fixture["followup"] not in visible
            code = "import json, shlex\nstored = {p: r['data']['text'] for p, r in sources.items()}\n"
            if arm == "findings":
                code += (
                    "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': text, "
                    "'related_paths': [p], 'evidence_refs': [sources[p]['read_reference']['artifact_uri']]} "
                    "for i, (p, text) in enumerate(stored.items())]\n"
                    "note = agent.shell.run('memory note write --text checkpoint --expected-version 0 "
                    "--operation-id heldout-note --entries ' + shlex.quote(json.dumps(entries)))\n"
                    "assert note['status'] == 'ok' and note['result_kind'] == 'managed'\n"
                    "assert note['data']['receipt_version'] == 1 and note['data']['version'] == 1\n"
                    "assert 'entries' not in note['data'] and 'text' not in note['data']\n"
                    "assert note['data']['entry_count'] == len(entries)\n"
                )
            else:
                code += "saved = agent.artifacts.publish(stored, 'Source_checkpoint')\nassert saved['status'] == 'ok'\n"
            code += "print('LEARNING_COMPLETE')"
        elif index == 2:
            assert "CHECKPOINT_READY" in visible
            assert trial.fixture["followup"] not in visible
            code = "print('CHECKPOINT_READY')"
        elif index == 3:
            assert trial.fixture["followup"] in visible
            assert '"live": false' in visible
            assert "ACQUISITION_ONLY_SENTINEL" not in visible
            if arm == "no_recall":
                assert "Memory commands are disabled" in visible
                assert "agent.artifacts.load" in visible
            code = "assert 'sources' not in [d['name'] for d in agent.state.list()]\nimport json, csv, tomllib\n"
            if arm == "findings":
                code += (
                    "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                    "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in note['data']['entries']}\n"
                )
            else:
                code += (
                    "available = agent.artifacts.list()['data']['artifacts']\n"
                    "uri = next(a['uri'] for a in available if a.get('published'))\n"
                    "stored = json.loads(agent.artifacts.load(uri)['data']['text'])\n"
                )
            if case == "heldout_settlements":
                code += (
                    "rows = [r for p, text in stored.items() if p.endswith('.csv') for r in csv.DictReader(text.splitlines()) "
                    "if r['account'] == 'K42' and r['status'] == 'settled']\n"
                    "answer = {'settled_cents': sum(int(r['amount_cents']) for r in rows), 'settled_rows': len(rows)}\n"
                )
            elif case == "heldout_rollout":
                code += (
                    "routes = tomllib.loads(stored['routing.toml'])\n"
                    "service = next(k for k, v in routes.items() if v['alias'] == 'forest-api' and v['enabled'])\n"
                    "deployments = json.loads(stored['deployments.json'])\n"
                    f"rows = [r for r in deployments if r['service'] == service and r['status'] == {'queued' if pending_answer else 'completed'!r}]\n"
                    "live = max(rows, key=lambda r: r['sequence'])\nanswer = {'build': live['build'], 'region': live['region']}\n"
                )
            else:
                code += (
                    "units = tomllib.loads(stored['units.toml'])\n"
                    "factors = {'ms': 1, 'us': 1 / units['microseconds_per_ms'], 's': units['milliseconds_per_s']}\n"
                    "rows = [json.loads(text) for p, text in stored.items() if p.endswith('.json')]\n"
                    "eligible = [(r['value'] * factors[r['unit']], r['sensor']) for r in rows if r['state'] == 'complete']\n"
                    "peak, sensor = max(eligible)\nanswer = {'sensor': sensor, 'peak_ms': int(peak)}\n"
                )
            code += "written = agent.fs.write('answer.json', json.dumps(answer))\nassert written['status'] == 'ok'"
        else:
            code = None
        part = types.Part(function_call=types.FunctionCall(name="code", id=f"step-{index}", args={"code": code})) if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert result["accepted"] is not pending_answer, result
    assert result["terminal"] == ("workflow_blocked" if pending_answer else "verified_completion"), result
    assert result["first_verification_passed"] is not pending_answer
    assert result["checkpoint_exercised"]
    checkpoint = result["learning_checkpoints"][0]
    assert checkpoint["worker_loss"]["before"]["live"]
    assert checkpoint["worker_loss"]["after"] == {"live": False, "kernel_epoch": None}
    assert checkpoint["learning_model_calls"] == 3 and trial.seed_cells == 0
    assert result["measurement"]["answer_evidence"]["first_answer"] == "available"
    assert result["measurement"]["reads"]["counts"].get("post_cut_reads", 0) == 0
    events = trial.ledger.read(trial.task_id)
    stopped = next(e for e in events if e.kind == "evaluation.worker_stopped")
    requested = next(e for e in events if e.kind == "evaluation.worker_stop_requested")
    assert requested.sequence < stopped.sequence < trial.cut_sequence
    assert sum(e.kind == "compaction.created" for e in events) == 1
    assert any(e.kind == "memory.note" for e in events) is (arm == "findings")
    epochs = {e.payload["kernel_epoch"] for e in events if e.kind == "repl.cell_completed"}
    assert len(epochs) == 2
    assert "trace observation failed" not in caplog.text


def test_no_recall_changes_memory_services_not_ptc_or_evidence_access(tmp_path):
    control = LearnedContinuation(tmp_path / "control", HELDOUT_CASES[0], "no_recall")
    treatment = LearnedContinuation(tmp_path / "treatment", HELDOUT_CASES[0], "findings")
    a, b = (t.composition.model_dump(mode="json")["harness"]["config"] for t in (control, treatment))
    assert a["memory"]["context_programs"]["mode"] == "off"
    assert not a["memory"]["working_notes"] and not a["memory"]["prior_runs"]
    assert a["memory"]["enabled"]  # Canonical trace remains host authority in both arms.
    a.pop("memory")
    b.pop("memory")
    assert a == b
    assert control.fixture == treatment.fixture
