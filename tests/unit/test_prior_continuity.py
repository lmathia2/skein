import json
import os

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.prior_continuity import COST_FIELDS, run_owned_prior
from evals.qualification_continuity import qualification_fixture
from evals.verified_continuity import campaign
from harness.adapters.providers.openrouter_responses import (
    OpenRouterResponsesLlm,
    build_openrouter_request_body,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case,fault", [("qualification_prior_1", None), ("qualification_prior_2", None),
                                       ("qualification_prior_1", "producer_failed"), ("qualification_prior_1", "guess"),
                                       ("qualification_prior_1", "identity_only"),
                                       ("qualification_prior_2", "stale")])
@pytest.mark.parametrize("arm", ["no_recall", "findings"])
async def test_owned_prior_tasks_use_completed_applicable_evidence_with_full_cost_accounting(tmp_path, monkeypatch, case, arm, fault):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    fixture = qualification_fixture(case)
    calls = {}
    applicability = []
    reviews = []

    async def model(self, request, stream=False):
        producer = not calls or id(self) == next(iter(calls))
        index = calls.get(id(self), 0)
        calls[id(self)] = index + 1
        body = build_openrouter_request_body(request, model=self.model, reasoning_effort="max")
        for item in body["input"]:
            for content in item.get("content", []):
                text = content.get("text", "")
                if not text.startswith("## TASK\n"):
                    continue
                task, _ = json.JSONDecoder().raw_decode(text.removeprefix("## TASK\n"))
                if task["phase"] != "review":
                    continue
                episode = fixture["producer"] if producer else fixture
                assert task["goal"] == episode["goal"]
                assert task["acceptance_criteria"][0] == episode["goal"]
                assert "## USER STEERING\n" in text
                history = json.loads(text.split("## USER STEERING\n", 1)[1].split("\n", 1)[1])
                assert history["program"] == "delivered_steering@1"
                assert history["messages"][-1]["content"] == episode["followup"]
                assert history["omitted_count"] == 0
                assert "Audit completed prerequisite actions from their execution evidence" in task["next_action"]
                reviews.append((producer, history["content_hash"]))
        if not producer:
            for content in request.contents:
                for part in content.parts or ():
                    if part.function_response and part.function_response.response.get("prior_applicability"):
                        applicability.append(part.function_response.response["prior_applicability"])
        code = None
        if index == 0:
            paths = sorted(fixture["producer"]["files"]) if producer else ["shipment.toml"]
            code = ("import json, shlex, tomllib\n"
                    f"sources = {{p: agent.fs.read(p) for p in {paths!r}}}\n"
                    "assert all(r['status'] == 'ok' for r in sources.values())\n"
                    "stored = {p: json.dumps(tomllib.loads(r['data']['text'])) if p.endswith('.toml') else r['data']['text'] for p, r in sources.items()}\n")
            if arm == "findings":
                code += ("entries = [{'id': 'source_' + str(i), 'kind': 'observation', 'text': text, 'related_paths': [p], "
                         "'evidence_refs': [sources[p]['read_reference']['artifact_uri']]} for i, (p, text) in enumerate(stored.items())]\n"
                         "saved = agent.shell.run('memory note write --text checkpoint --expected-version 0 --operation-id initial '"
                         "'--entries ' + shlex.quote(json.dumps(entries)))\nassert saved['status'] == 'ok', saved\n")
            else:
                code += "assert agent.artifacts.publish(stored, 'Initial')['status'] == 'ok'\n"
            code += "print('LEARNING_COMPLETE')"
        elif index == 1:
            code = "print('CHECKPOINT_READY_1')"
        elif index == 2:
            code = "import json, tomllib, ast\n"
            if arm == "findings":
                code += ("note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                         "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in note['data']['entries']}\n")
            else:
                code += ("items = agent.artifacts.list()['data']['artifacts']\n"
                         "uri = next(a['uri'] for a in items if a.get('published'))\n"
                         "stored = json.loads(agent.artifacts.load(uri)['data']['text'])\n")
            if producer:
                code += ("policies = [json.loads(text) for p, text in stored.items() if p.endswith('.toml')]\n"
                         "answer = {'regions': len(policies), 'max_unit_cents': max(p['unit_cents'] for p in policies)}\n")
                if fault == "producer_failed":
                    code += "answer['regions'] += 1\n"
            else:
                code += "shipment = json.loads(stored['shipment.toml'])\n"
                if arm == "findings":
                    code += (
                        "prior = agent.shell.run('memory query --program working_set --tasks fixture-prior --focus ' + shipment['policy'] + ',pricing.py')\n"
                        "assert prior['status'] == 'ok', prior\n"
                        "entries = {e['finding']['related_paths'][0]: e for e in prior['data']['data']['findings']}\n"
                        "for path in [shipment['policy'], 'pricing.py']:\n"
                        "    identity = agent.fs.read(path, limit=1)\n"
                        f"    if {fault == 'stale'!r} or identity['data']['sha256'] == entries[path]['source_dependencies'][0]['sha256']:\n"
                        "        stored[path] = entries[path]['finding']['text']\n"
                        "    else:\n"
                        "        current = agent.fs.read(path, offset=2, limit=3 if path.endswith('.toml') else 2)\n"
                        "        text = identity['data']['text'] + current['data']['text']\n"
                        "        stored[path] = json.dumps(tomllib.loads(text)) if path.endswith('.toml') else text\n")
                else:
                    code += ("for path in [shipment['policy'], 'pricing.py']:\n"
                             "    current = agent.fs.read(path, limit=4 if path.endswith('.toml') else 3)\n"
                             "    stored[path] = json.dumps(tomllib.loads(current['data']['text'])) if path.endswith('.toml') else current['data']['text']\n")
                code += (
                    "policy = json.loads(stored[shipment['policy']])\n"
                    "function = ast.parse(stored['pricing.py']).body[0]\n"
                    "assert isinstance(function.body[0].value.op, ast.Add) and isinstance(function.body[0].value.right.op, ast.Mult)\n"
                    "assert function.body[1].value.func.id == 'min'\n"
                    "answer = {'charge_cents': min(policy['base_cents'] + shipment['units'] * policy['unit_cents'], policy['cap_cents']), 'zone': policy['name']}\n")
            if not producer and fault == "guess":
                code = f"import json\nanswer = {fixture['expected']!r}\n"
            if not producer and fault == "identity_only":
                code = ("import tomllib\nshipment = tomllib.loads(agent.fs.read('shipment.toml')['data']['text'])\n"
                        "identities = [agent.fs.read(p, limit=1) for p in [shipment['policy'], 'pricing.py']]\n"
                        "assert all(r['status'] == 'ok' for r in identities)\n")
            else:
                code += "assert agent.fs.write('answer.json', json.dumps(answer))['status'] == 'ok'\n"
        elif index == 3 and not producer and fault == "identity_only":
            code = f"import json\nagent.fs.write('answer.json', json.dumps({fixture['expected']!r}))\n"
        blocked = (fault == "producer_failed" and producer) or (fault == "guess" and not producer and index > 4)
        blocked |= fault == "stale" and arm == "findings" and not producer and index > 4
        blocked |= fault == "identity_only" and not producer and index > 5
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{index}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "blocked" if blocked else "verify", "message": "Report completed evidence."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    root = tmp_path / "pair"
    result = await run_owned_prior(root, case, arm, os.getenv("SKEIN_EVAL_DOCKER_IMAGE"))
    if fault == "producer_failed":
        assert not result["producer_qualified"] and result["terminal"] == "producer_not_qualified", result
        assert len(calls) == 1 and not (root / "consumer").exists()
        assert result["model_calls"] == sum(calls.values())
        assert result["provider_cost_usd"] == result["episodes"]["producer"]["provider_cost_usd"] > 0
        return
    if fault in {"guess", "identity_only"} or (fault == "stale" and arm == "findings"):
        assert result["producer_qualified"] and not result["accepted"], result
        consumer = result["episodes"]["consumer"]
        assert consumer["first_verification_passed"] is False
        assert not consumer["measurement"]["answer_contracts"]["all_latest_supported"]
        assert result["model_calls"] == sum(calls.values())
        if fault == "identity_only" and arm == "findings":
            assert applicability  # Automatic metadata reached the model before its unsupported answer.
            assert any(e["consumer_versions"]["status"] == "matching_observations"
                       for update in applicability for e in update["entries"])
        return
    assert result["producer_qualified"] and result["terminal"] == "verified_completion", result
    assert {producer for producer, _ in reviews} == {True, False}
    assert result["model_calls"] == sum(calls.values()) and len(calls) == 2
    first, second = result["episodes"]["producer"], result["episodes"]["consumer"]
    assert first["first_verification_passed"] and second["first_verification_passed"]
    assert first["checkpoint_exercised"] and second["checkpoint_exercised"]
    assert first["measurement"]["answer_contracts"]["all_submissions_supported"]
    assert second["measurement"]["answer_contracts"]["all_submissions_supported"]
    assert second["measurement"]["initial_capture"]["respected"]
    if arm == "findings":
        assert applicability, "completed consumer version observations must reach the next provider request"
        assert any(e["consumer_versions"]["status"] == "matching_observations"
                   for update in applicability for e in update["entries"])
        assert all("finding" not in e and "text" not in e for update in applicability for e in update["entries"])
    else:
        assert not applicability
    for field in COST_FIELDS:
        assert result[field] == first.get(field, 0) + second.get(field, 0)
    assert (root / "producer" / "completed-answer.json").is_file()
    frozen = json.loads((root / "consumer" / "prior-evidence.json").read_text())
    assert frozen["current"]["workspace"] == frozen["sources"][0]["bindings"]["workspace"]
    assert fixture["expected"] != fixture["producer"]["expected"]
    counts = result["cross_run_reads"]["counts"]
    expected_lines = (2 if case.endswith("_1") else 1) if arm == "findings" else (7 if case.endswith("_1") else 3)
    assert counts["same_version_refetched_lines"] == expected_lines


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True, "missing_cost"])
async def test_repeated_owned_pairs_use_fresh_roots_and_share_the_stop_gate(tmp_path, monkeypatch, failure):
    roots = []
    async def pair(root, case, arm, sandbox_image):
        assert not root.exists()
        roots.append(root)
        return {"family": case, "arm": arm, "accepted": failure is not True, "passed": failure is not True,
                "terminal": "producer_not_qualified" if failure is True else "verified_completion",
                "model_calls": 10, "provider_cost_usd": .01, "cost_missing_calls": int(failure == "missing_cost")}
    monkeypatch.setattr("evals.prior_continuity.run_owned_prior", pair)
    result = await campaign(tmp_path, ["qualification_prior_1"], ["no_recall", "findings"], 1, repetitions=3)
    assert len(result) == 6 and {r["repetition"] for r in result} == {1, 2, 3}
    assert len(roots) == len(set(roots)) == (1 if failure else 6)
    assert sum(r.get("model_calls", 0) for r in result) == (10 if failure else 60)
    if failure:
        assert all(r["terminal"] == "not_started_infrastructure_gate" for r in result[1:])
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert sum(r["planned"] for r in summary["arms"].values()) == 6
