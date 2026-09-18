import json
import os

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.recovery_messages import audit_probe, make_trial
from evals.runner import _atomic_write
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import (
    OpenRouterResponsesLlm,
    build_openrouter_request_body,
)


def test_recovery_conditions_differ_only_in_state_message_emission(tmp_path):
    off, on = [make_trial(tmp_path / str(enabled), "complete", enabled) for enabled in (False, True)]
    assert off.fixture == on.fixture
    a, b = [trial.composition.model_dump(mode="json") for trial in (off, on)]
    assert not a["harness"]["config"]["notebook_ptc"].pop("emit_state_updates")
    assert b["harness"]["config"]["notebook_ptc"].pop("emit_state_updates")
    assert a == b


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["accounting", "audit"])
async def test_recovery_campaign_stops_unstarted_trials_and_retains_settled_usage(tmp_path, monkeypatch, fault):
    from evals import recovery_messages

    calls = []
    async def run(trial):
        calls.append(trial.root)
        return {"terminal": "verified_completion", "accepted": True, "model_calls": 3,
                "provider_cost_usd": .003, "cost_missing_calls": int(fault == "accounting")}
    def audit(trial):
        if fault == "audit":
            raise ValueError("corrupt measurement fixture")
        return {"exercised": True}
    monkeypatch.setattr(recovery_messages, "run_verified_case", run)
    monkeypatch.setattr(recovery_messages, "audit_probe", audit)
    rows = await recovery_messages.campaign(tmp_path / "campaign", "unused", concurrency=1)
    assert len(calls) == 1 and len(rows) == 4
    assert rows[0]["provider_cost_usd"] == .003 and rows[0]["model_calls"] == 3
    assert rows[0]["terminal"] == "verified_completion"  # Raw acceptance is not rewritten by the audit.
    assert sum(r["terminal"] == "not_started_infrastructure_gate" for r in rows) == 3
    assert ("measurement_error" in rows[0]) is (fault == "audit")


@pytest.mark.asyncio
@pytest.mark.parametrize("capture,updates,defect", [(c, u, None) for c in ("complete", "partial") for u in (False, True)]
                         + [("complete", True, "skip_probe"), ("partial", True, "unsupported_answer")])
async def test_recovery_probe_through_actual_workflow(tmp_path, monkeypatch, capture, updates, defect):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = make_trial(tmp_path / "trial", capture, updates)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0

    async def model(self, request, stream=False):
        nonlocal calls
        _atomic_write(trial.root / "wire" / f"{calls:03}.json", json.dumps(
            build_openrouter_request_body(request, model=self.model, reasoning_effort="max")))
        calls += 1
        if calls == 1:
            code = trial.fixture["probe_code"]
            if defect == "skip_probe":
                code = code.splitlines()[0]
        elif calls == 2:
            code = "import json, tomllib\n"
            if defect == "unsupported_answer":
                code += "answer = {'port': 8400, 'protocol': 'https'}\n"
            else:
                if defect == "skip_probe":
                    code += "source = tomllib.loads(captured['data']['text'])['service']\n"
                elif capture == "complete" and updates:
                    response = next(p.function_response.response for c in reversed(request.contents) for p in c.parts or []
                                    if p.function_response and p.function_response.response.get("status") == "error")
                    notice, _ = json.JSONDecoder().raw_decode(response["model_text"].split(
                        "State updates (advisory; sources historical):\n", 1)[1])
                    handle = next(row for row in notice["entries"] if row.get("historical_read", {}).get("returned_lines") == 13)
                    code += (f"page = {handle['recover_expression']}\nassert page['status'] == 'ok' and page['data']['complete']\n"
                             "saved = json.loads(page['data']['text'])\nassert saved['status'] == 'ok'\n"
                             "source = tomllib.loads(saved['data']['text'])['service']\n")
                elif capture == "partial" and updates:
                    code += ("fresh = agent.fs.read('config/route_00.toml', offset=12, limit=2)\n"
                             "assert fresh['status'] == 'ok'\nsource = tomllib.loads(fresh['data']['text'])\n")
                else:
                    code += ("fresh = agent.fs.read('config/route_00.toml')\nassert fresh['status'] == 'ok'\n"
                             "source = tomllib.loads(fresh['data']['text'])['service']\n")
                code += "answer = {'port': source['active_port'], 'protocol': source['protocol']}\n"
            code += "agent.fs.write('answer.json', json.dumps(answer))"
        else:
            code = None
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{calls}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify the answer."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    report = audit_probe(trial)
    assert result["accepted"] is (defect != "unsupported_answer"), result
    assert report["exercised"] is (defect != "skip_probe"), report
    assert not any(result.get(k, 0) for k in ("cost_missing_calls", "unaccounted_model_calls", "extra_wire_attempts"))
    if defect == "skip_probe":
        return  # Correct source-backed output does not prove the failure intervention ran.
    assert report["direct_handle_exposed"] is updates
    if defect == "unsupported_answer":
        assert result["first_verification_passed"] is False
        assert not result["measurement"]["answer_evidence"]["all_answers_source_available"]
        return
    assert result["first_verification_passed"] and result["measurement"]["answer_evidence"]["all_answers_source_available"]
    assert bool(report["probe_artifact_load_events"]) is (capture == "complete" and updates)
    reads = report["post_failure_reads"]["counts"]
    assert reads.get("pre_cut_overlap_lines", 0) == (0 if updates else 13 if capture == "complete" else 3)
