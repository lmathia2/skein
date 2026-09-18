import hashlib
import json
import os

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.continuity_oracle import AnswerSpec
from evals.learned_continuity import LearnedContinuation
from evals.transfer_continuity import TRANSFER_CASES, transfer_fixture
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm


def derive(case, question):
    """Scripted contract check, deriving values from captured bytes, not expected answers."""
    if case == "qualification_ordered_rules":
        method, path = [("GET", "/api/admin/logs"), ("POST", "/private/jobs"), ("POST", "/jobs/new")][question]
        return (
            "rules = json.loads(stored['routing.json'])\n"
            f"selected = next(r for r in rules if r['enabled'] and r['method'] in ('*', {method!r}) and {path!r}.startswith(r['prefix']))\n"
            "answer = {'rule_id': selected['id'], 'action': selected['action']}\n")
    if case == "qualification_sql_eligibility":
        where = ["WHERE owner = 'iris'", "", "WHERE owner IS NULL"][question]
        return (
            "import sqlite3\ndb = sqlite3.connect(':memory:')\n"
            "for p in ('schema.sql', 'data.sql', 'eligible.sql'):\n    db.executescript(stored[p])\n"
            f"rows = db.execute({'SELECT job, cents FROM eligible ' + where + ' ORDER BY job'!r}).fetchall()\n"
            "answer = {'jobs': [r[0] for r in rows], 'total_cents': sum(r[1] for r in rows)}\ndb.close()\n")
    return (
        "manifest = json.loads(stored['manifest.json'])\n"
        "nodes = {p[6:-5]: json.loads(t) for p, t in stored.items() if p.startswith('nodes/')}\n"
        "reachable = {}\n"
        "for target, root in manifest['targets'].items():\n"
        "    found, pending = set(), [root]\n"
        "    while pending:\n"
        "        name = pending.pop()\n"
        "        if name in found:\n            continue\n"
        "        found.add(name)\n"
        "        pending.extend(n for n, flag in nodes[name]['deps'] if flag is None or manifest['flags'][flag])\n"
        "    reachable[target] = found\n"
        + ("selected = reachable['api'] & reachable['worker']\n" if question == 1 else "selected = reachable['api']\n")
        + "answer = {'modules': sorted(selected), 'total_cost': sum(nodes[n]['cost'] for n in selected)}\n")


@pytest.mark.parametrize("case", TRANSFER_CASES)
def test_fresh_transfer_sources_independently_reproduce_frozen_answers_and_contracts(tmp_path, case):
    fixture = transfer_fixture(case)
    assert fixture == transfer_fixture(case)
    assert len(fixture['answers']) == len(fixture['stages']) == 3
    assert fixture['max_model_calls'] == 24 and fixture['input_budget'] == 350_000
    for question, raw in enumerate(fixture['answers']):
        spec = AnswerSpec.model_validate(raw)
        contents = fixture['files'] | (fixture['revision_files'] if question == 2 else {})
        namespace = {'stored': contents, 'json': json}
        exec(derive(case, question), namespace)
        assert namespace['answer'] == spec.expected
        assert spec.checkpoint == question
        assert {r.path for r in spec.required} == set(contents)
        assert all(r.sha256 == hashlib.sha256(contents[r.path].encode()).hexdigest()
                   and r.offset == 1 and r.returned_lines == len(contents[r.path].splitlines()) for r in spec.required)
    a, b = [LearnedContinuation(tmp_path / arm, case, arm) for arm in ('no_recall', 'findings')]
    assert a.fixture == b.fixture == fixture and a.seed_cells == b.seed_cells == 0
    configs = [t.composition.model_dump(mode='json')['harness']['config'] for t in (a, b)]
    assert configs[0]['notebook_ptc'] == configs[1]['notebook_ptc']
    assert configs[0]['context'] == configs[1]['context']
    assert not configs[0]['memory']['working_notes'] and configs[1]['memory']['working_notes']
    for config in configs:
        for field in ('working_notes', 'prior_runs', 'context_programs'):
            config['memory'].pop(field)
    assert configs[0] == configs[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("case,arm,bad", [(c, a, None) for c in TRANSFER_CASES for a in ('no_recall', 'findings')] + [
    ('qualification_ordered_rules', 'findings', 'early_answer'),
    ('qualification_ordered_rules', 'findings', 'scope_change'),
    ('qualification_sql_eligibility', 'findings', 'partial_preparation'),
    ('qualification_build_graph', 'findings', 'missing_revision_read'),
    ('qualification_build_graph', 'findings', 'stale_write_guard'),
    ('qualification_build_graph', 'findings', 'stale_final'),
])
async def test_transfer_root_workflow_uses_recovered_sources_and_rejects_bad_evidence(tmp_path, monkeypatch, case, arm, bad):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-fixture-key')
    trial = LearnedContinuation(tmp_path / 'trial', case, arm)
    trial.command_image = os.getenv('SKEIN_EVAL_DOCKER_IMAGE')
    calls = 0

    def checkpoint(revised=False):
        if arm == 'no_recall':
            return f"assert agent.artifacts.publish({{'stored': stored, 'refs': refs}}, {'Revised' if revised else 'Initial'!r})['status'] == 'ok'\n"
        return (
            "entries = [{'id': 'source_' + str(i), 'kind': 'observation', 'text': t, 'related_paths': [p], "
            "'evidence_refs': [refs[p]]} for i, (p, t) in enumerate(stored.items())]\n"
            f"note = agent.shell.run('memory note write --text checkpoint --expected-version {int(revised)} "
            f"--operation-id transfer-{int(revised)} --entries ' + shlex.quote(json.dumps(entries)))\n"
            "assert note['status'] == 'ok', note\n")

    def recover(question):
        code = "import json, shlex, hashlib\n"
        if arm == 'findings':
            return code + (
                "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in note['data']['entries']}\n"
                "refs = {e['finding']['related_paths'][0]: e['finding']['evidence_refs'][0] for e in note['data']['entries']}\n")
        name = 'Revised' if trial.fixture['revision_files'] and question == 2 else 'Initial'
        return code + (
            "items = agent.artifacts.list()['data']['artifacts']\n"
            f"uri = next(a['uri'] for a in items if a.get('name') == {name!r})\n"
            "page = agent.artifacts.load(uri)\nassert page['status'] == 'ok' and page['data']['offset'] == 0 and page['data']['complete']\n"
            "saved = json.loads(page['data']['text'])\nstored, refs = saved['stored'], saved['refs']\n")

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        code = None
        if index == 0:
            paths = sorted(trial.fixture['files'])
            if bad == 'partial_preparation':
                paths.remove('eligible.sql')
            code = ("import json, shlex\n"
                    f"sources = {{p: agent.fs.read(p) for p in {paths!r}}}\n"
                    "assert all(r['status'] == 'ok' for r in sources.values())\n"
                    "stored = {p: r['data']['text'] for p, r in sources.items()}\n"
                    "refs = {p: r['read_reference']['artifact_uri'] for p, r in sources.items()}\n") + checkpoint()
            if bad == 'early_answer':
                code += derive(case, 0) + "agent.fs.write('answers/use_1.json', json.dumps(answer))\n"
            code += "print('LEARNING_COMPLETE')"
        elif bad == 'partial_preparation':
            if index == 1:
                # Correct guess with a missing decisive source must not count.
                code = f"agent.fs.write('answer.json', json.dumps({trial.fixture['expected']!r}))"
        elif index <= 5 and index % 2:
            code = f"print('CHECKPOINT_READY_{(index + 1) // 2}')"
        elif index <= 6 and index % 2 == 0:
            question = index // 2 - 1
            code = recover(question)
            if bad == 'stale_final' and question == 2:
                code += f"stored['manifest.json'] = {trial.fixture['files']['manifest.json']!r}\n"
            code += derive(case, question)
            if not (bad == 'early_answer' and question == 0):
                code += f"assert agent.fs.write({trial.fixture['answers'][question]['path']!r}, json.dumps(answer))['status'] == 'ok'\n"
            if question == 1 and trial.fixture['revision_files']:
                for path, replacement in trial.fixture['revision_files'].items():
                    guard = repr('0' * 64) if bad == 'stale_write_guard' else f"hashlib.sha256(stored[{path!r}].encode()).hexdigest()"
                    code += (f"written = agent.fs.write({path!r}, {replacement!r}, expected_sha256={guard})\n"
                             + ("assert written['status'] != 'ok'\n" if bad == 'stale_write_guard' else "assert written['status'] == 'ok'\n"))
                    if bad not in ('missing_revision_read', 'stale_write_guard'):
                        code += (f"fresh = agent.fs.read({path!r})\nassert fresh['status'] == 'ok'\n"
                                 f"stored[{path!r}], refs[{path!r}] = fresh['data']['text'], fresh['read_reference']['artifact_uri']\n")
                code += checkpoint(revised=True)
            if question < 2:
                code += f"print('USE_{question + 1}_COMPLETE')"
            if bad == 'scope_change' and question == 2:
                code += "agent.fs.write('contract.md', stored['contract.md'] + 'unauthorized change\\n')\n"
        part = (types.Part(function_call=types.FunctionCall(name='execute_code', id=f'step-{index}', args={'code': code}))
                if code else types.Part(text=json.dumps({'status': 'verify', 'message': 'Verify.'})))
        yield LlmResponse(content=types.Content(role='model', parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={'provider_cost_usd': .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, 'generate_content_async', model)
    result = await run_verified_case(trial)
    assert result['accepted'] is (bad is None), result
    assert result['first_verification_passed'] is (bad is None), result
    assert not result['unresolved_execution'], result
    report = result['measurement']['answer_contracts']
    if bad is None:
        assert report['all_submissions_supported']
        assert len(result['learning_checkpoints']) == 3
        assert all(c['worker_loss']['before']['live'] and not c['worker_loss']['after']['live'] for c in result['learning_checkpoints'])
        assert all(i['reads']['counts'].get('all_earlier_overlap_lines', 0) == 0 for i in result['measurement']['checkpoint_intervals'])
        assert result['provider_cost_usd'] == pytest.approx(calls * .001)
        assert trial.seed_cells == 0
    elif bad in ('partial_preparation', 'early_answer', 'missing_revision_read', 'stale_write_guard'):
        assert not report['all_submissions_supported']
        assert len(result['learning_checkpoints']) == {'partial_preparation': 0, 'early_answer': 1, 'missing_revision_read': 2, 'stale_write_guard': 2}[bad]
        if bad == 'stale_write_guard':
            assert (trial.workspace / 'manifest.json').read_text() == trial.fixture['files']['manifest.json']
    elif bad == 'stale_final':
        assert report['all_submissions_supported']  # Completed sources alone do not prove a correct calculation.
        assert not result['passed']
