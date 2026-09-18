import json
import os
import re

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.learned_continuity import LearnedContinuation
from evals.qualification_continuity import QUALIFICATION_CASES
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ("no_recall", "findings"))
@pytest.mark.parametrize("case,guess,overshoot", [(c, False, False) for c in QUALIFICATION_CASES if any(f in c for f in ("routes", "partial"))]
                         + [("qualification_partial_1", True, False), ("qualification_partial_2", False, True)])
async def test_fresh_breadth_and_missing_ranges_use_real_checkpoint_workflow(tmp_path, monkeypatch, arm, case, guess, overshoot):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        if index == 0:
            ranges = {r["path"]: (r["offset"], r["returned_lines"]) for r in trial.fixture["stages"][0]["learning_requirements"]}
            if overshoot:
                ranges["settings.toml"] = (1, len(trial.fixture["files"]["settings.toml"].splitlines()))
            code = ("import json, shlex, ast\n"
                    f"sources = {{p: agent.fs.read(p, offset=span[0], limit=span[1]) for p, span in {ranges!r}.items()}}\n"
                    "assert all(r['status'] == 'ok' for r in sources.values())\n"
                    "knowledge = {}\nentries = []\n")
            if "routes" in case:
                code += (
                    "for branch in range(len(sources) // 3):\n"
                    "    paths = ['pipeline/branch_' + str(branch) + '_' + str(d) + '.py' for d in range(3)]\n"
                    "    trees = [ast.parse(sources[p]['data']['text']) for p in paths]\n"
                    "    expressions = [next(n for n in tree.body if isinstance(n, ast.FunctionDef)).body[0].value for tree in trees]\n"
                    "    factor = expressions[2].right.value\n"
                    "    increment = sum(e.args[0].right.value for e in expressions[:2])\n"
                    "    fact = {'slope': factor, 'intercept': increment * factor}\n"
                    "    knowledge[str(branch)] = fact\n"
                    "    entries.append({'id': 'branch_' + str(branch), 'kind': 'observation', 'text': json.dumps(fact), "
                    "'related_paths': paths, 'evidence_refs': [sources[p]['read_reference']['artifact_uri'] for p in paths]})\n")
            else:
                code += (
                    "knowledge = {'dispatch': sources['dispatch.toml']['data']['text'], "
                    "'settings_lines': sources['settings.toml']['read_reference']['source_coverage']['total_lines']}\n"
                    "entries = [{'id': 'partial', 'kind': 'observation', 'text': json.dumps(knowledge), "
                    "'related_paths': list(sources), 'evidence_refs': [r['read_reference']['artifact_uri'] for r in sources.values()]}]\n")
            if arm == "findings":
                code += ("note = agent.shell.run('memory note write --text Learned_checkpoint --expected-version 0 "
                         "--operation-id qualification-note --entries ' + shlex.quote(json.dumps(entries)))\n"
                         "assert note['status'] == 'ok'\n")
            else:
                code += "assert agent.artifacts.publish(knowledge, 'Checkpoint')['status'] == 'ok'\n"
            code += "print('LEARNING_COMPLETE')"
        elif index % 2 == 1 and index <= 2 * len(trial.fixture["stages"]) - 1:
            code = f"print('CHECKPOINT_READY_{(index + 1) // 2}')"
        elif index % 2 == 0 and index <= 2 * len(trial.fixture["stages"]):
            stage = index // 2 - 1
            spec = trial.fixture["answers"][stage]
            code = "import json, tomllib\n"
            if arm == "findings":
                code += ("note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                         "knowledge = {e['finding']['id'].removeprefix('branch_'): json.loads(e['finding']['text']) for e in note['data']['entries']}\n")
                if "partial" in case:
                    code += "knowledge = knowledge['partial']\n"
            else:
                code += ("items = agent.artifacts.list()['data']['artifacts']\n"
                         "uri = next(a['uri'] for a in items if a.get('published'))\n"
                         "knowledge = json.loads(agent.artifacts.load(uri)['data']['text'])\n")
            if "routes" in case:
                match = re.search(r"branch_(\d+)_0.apply\((\d+)\)", trial.fixture["stages"][stage]["followup"])
                assert match
                branch, argument = match.groups()
                code += f"fact = knowledge[{branch!r}]\nanswer = {{'result': fact['slope'] * {int(argument)} + fact['intercept']}}\n"
            elif guess:
                # Deliberately correct but unavailable: this must not be accepted.
                code += f"answer = {spec['expected']!r}\n"
            else:
                code += ("r = agent.fs.read('settings.toml', offset=knowledge['settings_lines'] - 2, limit=3)\n"
                         "assert r['status'] == 'ok'\nservice = tomllib.loads(knowledge['dispatch'])['service']\n"
                         "profile = tomllib.loads(r['data']['text'])[service['profile']]\n"
                         "answer = {'total_quota': service['workers'] * profile['quota'], 'enabled': profile['enabled']}\n")
            code += f"assert agent.fs.write({spec['path']!r}, json.dumps(answer))['status'] == 'ok'\n"
            if stage < len(trial.fixture["stages"]) - 1:
                code += f"print('USE_{stage + 1}_COMPLETE')"
        else:
            code = None
        part = types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{index}", args={"code": code})) if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert result["accepted"] is not guess, result
    assert result["first_verification_passed"] is not guess, result
    assert not result["unresolved_execution"]
    assert len(result["learning_checkpoints"]) == len(trial.fixture["stages"])
    assert result["measurement"]["answer_contracts"]["all_submissions_supported"] is not guess
    if not overshoot:
        assert all(i['reads']['counts'].get('all_earlier_overlap_lines', 0) == 0 for i in result['measurement']['checkpoint_intervals'])
    if "partial" in case:
        assert result["measurement"]["initial_capture"]["respected"] is not overshoot
    if "partial" in case and not guess:
        reads = result["measurement"]["reads"]["reads"]
        assert len(reads) == 1 and reads[0]["returned_lines"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ("no_recall", "findings"))
@pytest.mark.parametrize("case,bad", [(c, None) for c in QUALIFICATION_CASES if any(f in c for f in ("changed", "conflict"))]
                         + [("qualification_changed_1", "stale"), ("qualification_conflict_2", "stale"),
                            ("qualification_conflict_1", "early"), ("qualification_conflict_2", "overshoot")])
async def test_fresh_revisions_and_conflicts_recover_from_completed_evidence(tmp_path, monkeypatch, arm, case, bad):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    conflict = "conflict" in case
    calls = 0
    repaired = False

    def checkpoint(stage):
        if arm == "no_recall":
            return f"assert agent.artifacts.publish({{'stored': stored, 'refs': refs}}, 'Checkpoint_{stage}')['status'] == 'ok'\n"
        code = (
            "entries = [{'id': p.replace('/', '_').replace('.', '_'), 'kind': 'observation', 'text': t, "
            "'related_paths': [p], 'evidence_refs': [refs[p]]} for p, t in stored.items()]\n")
        if conflict:
            if stage == 0:
                code += "next(e for e in entries if e['id'] == 'claims_violet_json')['conflicts_with'] = ['claims_amber_json']\n"
            else:
                code += (
                    "entries = [e for e in entries if not e['id'].startswith('claims_')]\n"
                    "entries.append({'id': 'resolved', 'kind': 'observation', 'text': json.dumps(answer), "
                    "'evidence_refs': list(refs.values()), 'related_paths': list(stored), "
                    "'supersedes': ['claims_amber_json', 'claims_violet_json']})\n")
        return code + (
            f"note = agent.shell.run('memory note write --text checkpoint --expected-version {stage} "
            f"--operation-id qualification-{stage} --entries ' + shlex.quote(json.dumps(entries)))\n"
            "assert note['status'] == 'ok', note\n")

    def recover(stage):
        code = "import json, shlex, hashlib, csv, tomllib\n"
        if arm == "no_recall":
            return code + (
                "items = agent.artifacts.list()['data']['artifacts']\n"
                f"uri = next(a['uri'] for a in items if a.get('name') == 'Checkpoint_{stage}')\n"
                "loaded = json.loads(agent.artifacts.load(uri)['data']['text'])\n"
                "stored, refs = loaded['stored'], loaded['refs']\n")
        return code + (
            "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
            "entries = [e['finding'] for e in note['data']['entries']]\n"
            "stored = {e['related_paths'][0]: e['text'] for e in entries if e['id'] != 'resolved'}\n"
            "refs = {e['related_paths'][0]: e['evidence_refs'][0] for e in entries if e['id'] != 'resolved'}\n")

    def derive():
        if conflict:
            return (
                "rows = json.loads(stored['activation.json'])\n"
                "eligible = [r for r in rows if r['state'] == 'completed' and "
                "hashlib.sha256(stored['claims/' + r['claim'] + '.json'].encode()).hexdigest() == r['sha256']]\n"
                "selected = max(eligible, key=lambda r: r['sequence'])\n"
                "answer = dict(json.loads(stored['claims/' + selected['claim'] + '.json']), sequence=selected['sequence'])\n")
        return (
            "shipment = tomllib.loads(stored['shipment.toml'])\npricing = tomllib.loads(stored['pricing.toml'])\n"
            "rates = {r['zone']: int(r['cents_per_unit']) for r in csv.DictReader(stored['rates.csv'].splitlines())}\n"
            "charge = shipment['units'] * rates[shipment['zone']] + (0 if shipment['waive_base'] else pricing['base_cents']) - shipment['credit_cents']\n"
            "answer = {'charge_cents': charge, 'zone': shipment['zone']}\n")

    async def model(self, request, stream=False):
        nonlocal calls, repaired
        index, calls = calls, calls + 1
        code = None
        if index == 0:
            paths = [r['path'] for r in trial.fixture['stages'][0]['learning_requirements']]
            if bad == "overshoot":
                paths.append("activation.json")
            code = (
                "import json, shlex, hashlib, csv, tomllib\n"
                f"sources = {{p: agent.fs.read(p) for p in {paths!r}}}\n"
                "assert all(r['status'] == 'ok' for r in sources.values())\n"
                "stored = {p: r['data']['text'] for p, r in sources.items()}\n"
                "refs = {p: r['read_reference']['artifact_uri'] for p, r in sources.items()}\n")
            code += checkpoint(0) + "print('LEARNING_COMPLETE')"
        elif index in (1, 3):
            code = f"print('CHECKPOINT_READY_{1 if index == 1 else 2}')"
        elif index == 2:
            code = recover(0)
            if bad == "early":
                # A correct guess before the decisive receipt and answer window
                # must stay unsupported, even after a subsequent valid rewrite.
                code += f"assert agent.fs.write('answer.json', json.dumps({trial.fixture['expected']!r}))['status'] == 'ok'\n"
            if conflict:
                path = "activation.json"
            else:
                path = "shipment.toml"
                code += (
                    f"assert agent.fs.write({path!r}, {trial.fixture['revision_files'][path]!r}, "
                    f"expected_sha256=hashlib.sha256(stored[{path!r}].encode()).hexdigest())['status'] == 'ok'\n")
            code += (
                f"r = agent.fs.read({path!r})\nassert r['status'] == 'ok'\n"
                f"stored[{path!r}], refs[{path!r}] = r['data']['text'], r['read_reference']['artifact_uri']\n")
            code += derive() + checkpoint(1) + "print('REVISION_COMPLETE')"
        elif index == 4 or (bad == "stale" and not repaired and any(
                e.kind == "verification.completed" and not e.payload['report']['passed']
                for e in trial.ledger.read(trial.task_id))):
            repaired = index != 4
            code = recover(1)
            if conflict and arm == "findings":
                code += "answer = json.loads(next(e['text'] for e in entries if e['id'] == 'resolved'))\n"
            else:
                code += derive()
            if index == 4 and bad == "stale":
                if conflict:
                    # Choose the newer failed activation, not the completed one.
                    code += "answer = {'limit': 45, 'enabled': False, 'sequence': 72}\n"
                else:
                    code += f"stored['shipment.toml'] = {trial.fixture['files']['shipment.toml']!r}\n" + derive()
            code += "assert agent.fs.write('answer.json', json.dumps(answer))['status'] == 'ok'\n"
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{index}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert result["accepted"], result
    assert result["first_verification_passed"] is (bad != "stale"), result
    assert result["checkpoint_exercised"] and not result["unresolved_execution"]
    assert len(result["learning_checkpoints"]) == 2
    assert all(c["worker_loss"]["before"]["live"] and not c["worker_loss"]["after"]["live"] for c in result["learning_checkpoints"])
    audit = result["measurement"]["answer_contracts"]
    assert audit["all_latest_supported"]
    assert audit["all_submissions_supported"] is (bad != "early")
    if bad != "overshoot":
        assert all(i['reads']['counts'].get('all_earlier_overlap_lines', 0) == 0 for i in result['measurement']['checkpoint_intervals'])
    if conflict:
        assert result["measurement"]["initial_capture"]["respected"] is (bad != "overshoot")
        if arm == "findings":
            notes = [e for e in trial.ledger.read(trial.task_id) if e.kind == "memory.note"]
            assert len(notes) == 2
            initial = [e['finding'] for e in notes[0].payload['entries'] if e['finding']['id'].startswith('claims_')]
            assert len(initial) == 2 and {e['status'] for e in initial} == {'disputed'}
            resolved = next(e['finding'] for e in notes[-1].payload['entries'] if e['finding']['id'] == 'resolved')
            assert set(resolved['supersedes']) == {'claims_amber_json', 'claims_violet_json'}
