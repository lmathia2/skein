import hashlib
import json
import os

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.learned_continuity import LearnedContinuation
from evals.repeated_continuity import REPEATED_CASES, repeated_fixture
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm
from harness.core.context import estimate_tokens


@pytest.mark.parametrize("family", ["repository", "config", "reconcile"])
def test_one_and_three_use_fixture_contracts_are_matched_and_bounded(family):
    short, repeated = (repeated_fixture(f"reuse_{family}_{n}") for n in (1, 3))
    assert short["files"] == repeated["files"]
    assert short["answers"][0]["expected"] == repeated["answers"][0]["expected"]
    assert short["answers"][0]["required"] == repeated["answers"][0]["required"]
    assert short["max_model_calls"] == repeated["max_model_calls"] == 24
    assert short["input_budget"] == repeated["input_budget"] == 350_000
    for fixture in (short, repeated):
        assert len(fixture["answers"]) == len(fixture["stages"]) == fixture["uses"]
        assert [a["checkpoint"] for a in fixture["answers"]] == list(range(fixture["uses"]))
        assert fixture["answers"][-1]["path"] == "answer.json"
        assert {a["path"] for a in fixture["answers"]} <= set(fixture["permitted_paths"])
        assert all(estimate_tokens(s["followup"]) <= 200 for s in fixture["stages"])
        for stage, answer in zip(fixture["stages"], fixture["answers"], strict=True):
            assert f"workspace-relative {answer['path']}" in stage["followup"]
            assert f"top-level keys: {json.dumps(list(answer['expected']))}." in stage["followup"]
        assert all(not s["reuse_checkpoint"] for s in fixture["stages"] if s["required_changes"])
        final_files = {**fixture["files"], **fixture["revision_files"]}
        assert fixture["final_source_hashes"] == {p: hashlib.sha256(t.encode()).hexdigest() for p, t in final_files.items()}
        assert repeated_fixture(fixture["family"]) == fixture


def solve_code(family, question):
    """Derive scripted test answers from recovered source bytes, not oracle values."""
    if family == "config":
        return (
            "defaults = tomllib.loads(stored['defaults.toml'])\n"
            "environments = tomllib.loads(stored['environments.toml'])\n"
            "services = tomllib.loads(stored['services.toml'])\n"
            f"service = services[{'importer' if question == 1 else 'api'!r}]\n"
            "effective = {**defaults, **environments[service['environment']], **service}\n"
            f"result = {{k: effective[k] for k in {(['cache', 'retries'] if question == 1 else ['timeout_ms', 'cache'] if question == 2 else ['timeout_ms', 'retries'])!r}}}\n"
        )
    if family == "reconcile":
        return (
            "aliases = json.loads(stored['accounts.json'])['display_to_account']\n"
            "rows = list(csv.DictReader(stored['batch_a.csv'].splitlines())) + list(csv.DictReader(stored['batch_b.csv'].splitlines()))\n"
            f"selected = list(aliases.values()) if {question} == 2 else [aliases[{'cedar' if question == 1 else 'birch'!r}]]\n"
            "settled = [r for r in rows if r['account'] in selected and r['state'] == 'settled']\n"
            "result = {'net_cents': sum(int(r['cents']) for r in settled)}\n"
            + ("result['settled_rows'] = len(settled)\n" if question == 2 else "result['row_ids'] = sorted(r['id'] for r in settled)\n")
        )
    code = (
        "graph, literals = {}, {}\n"
        "for path, text in stored.items():\n"
        "    module = path[:-3].replace('/', '.')\n"
        "    tree = ast.parse(text)\n"
        "    imports = {a.asname or a.name: n.module + '.' + a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}\n"
        "    for fn in tree.body:\n"
        "        if isinstance(fn, ast.FunctionDef):\n"
        "            name = module + '.' + fn.name\n"
        "            graph[name] = [imports[n.func.id] for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in imports]\n"
        "            if isinstance(fn.body[0], ast.Return) and isinstance(fn.body[0].value, ast.Constant):\n"
        "                literals[name] = fn.body[0].value.value\n"
    )
    if question == 0:
        return code + "result = {'callers': sorted(name for name, calls in graph.items() if 'domain.money.gross' in calls)}\n"
    if question == 1:
        return code + (
            "entry = graph['app.api.create_order'][0]\ncap = next(name for name in graph[entry] if name in literals)\n"
            "result = {'cap_module': cap.rsplit('.', 1)[0], 'cap_cents': literals[cap]}\n"
        )
    return code + (
        "path = ['app.jobs.audit']\n"
        "while graph[path[-1]]:\n    path.append(graph[path[-1]][0])\n"
        "result = {'calls_threshold': any(n.endswith('.threshold') for n in path), 'path': path}\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case,omit_marker", [(case, False) for case in REPEATED_CASES] + [("reuse_repository_1", True)])
@pytest.mark.parametrize("arm", ["no_recall", "findings"])
async def test_repeated_questions_use_real_evidence_and_worker_loss_without_forced_note_rewrites(tmp_path, monkeypatch, case, arm, omit_marker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    family, uses = case.split("_")[1], trial.fixture["uses"]
    calls = 0

    def checkpoint(revised=False):
        if arm == "findings":
            return (
                "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': text, 'related_paths': [p], "
                "'evidence_refs': [refs[p]]} for i, (p, text) in enumerate(stored.items())]\n"
                f"saved = agent.shell.run('memory note write --text checkpoint --expected-version {int(revised)} "
                f"--operation-id checkpoint-{int(revised)} --entries ' + shlex.quote(json.dumps(entries)))\n"
                "assert saved['status'] == 'ok', saved\n"
            )
        return f"assert agent.artifacts.publish({{'stored': stored, 'refs': refs}}, {'Revised' if revised else 'Initial'!r})['status'] == 'ok'\n"

    def recover(question):
        code = "import json, shlex, hashlib, csv, tomllib, ast\n"
        if arm == "findings":
            return code + (
                "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in note['data']['entries']}\n"
                "refs = {e['finding']['related_paths'][0]: e['finding']['evidence_refs'][0] for e in note['data']['entries']}\n"
            )
        name = "Revised" if family == "config" and question == 2 else "Initial"
        return code + (
            "artifacts = agent.artifacts.list()['data']['artifacts']\n"
            f"uri = next(a['uri'] for a in artifacts if a.get('name') == {name!r})\n"
            "loaded = agent.artifacts.load(uri)\nassert loaded['status'] == 'ok'\n"
            "payload = json.loads(loaded['data']['text'])\nstored, refs = payload['stored'], payload['refs']\n"
        )

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        raw_index = index
        if omit_marker and index >= 3:
            index -= 2
        visible = "\n".join(p.text or "" for c in request.contents for p in c.parts or [])
        code = None
        if index == 0:
            code = (
                "import json, shlex\n"
                f"sources = {{p: agent.fs.read(p) for p in {sorted(trial.fixture['files'])!r}}}\n"
                "assert all(r['status'] == 'ok' for r in sources.values())\n"
                "stored = {p: r['data']['text'] for p, r in sources.items()}\n"
                "refs = {p: r['read_reference']['artifact_uri'] for p, r in sources.items()}\n"
            ) + checkpoint() + ("" if omit_marker else "print('LEARNING_COMPLETE')")
        elif omit_marker and raw_index == 1:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps({
                "status": "answer", "message": "LEARNING_COMPLETE was printed; preparation is complete."}))]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                custom_metadata={"provider_cost_usd": .001})
            return
        elif omit_marker and raw_index == 2:
            assert "Checkpoint not recorded" in visible
            assert not any(e.kind == "compaction.created" for e in trial.ledger.read(trial.task_id))
            code = "print('LEARNING_COMPLETE')"
        elif index <= uses * 2 and index % 2:
            token = f"CHECKPOINT_READY_{index // 2 + 1}"
            assert token in visible
            code = f"print({token!r})"
        elif index <= uses * 2:
            question = index // 2 - 1
            assert f"Question {question + 1}/{uses}" in visible
            assert '"live": false' in visible
            code = recover(question) + solve_code(family, question)
            code += f"assert agent.fs.write({trial.fixture['answers'][question]['path']!r}, json.dumps(result))['status'] == 'ok'\n"
            if question < uses - 1:
                if family == "config" and question == 1:
                    for path, replacement in trial.fixture["revision_files"].items():
                        code += (
                            f"assert agent.fs.write({path!r}, {replacement!r}, expected_sha256=hashlib.sha256(stored[{path!r}].encode()).hexdigest())['status'] == 'ok'\n"
                            f"fresh = agent.fs.read({path!r})\nassert fresh['status'] == 'ok'\n"
                            f"stored[{path!r}], refs[{path!r}] = fresh['data']['text'], fresh['read_reference']['artifact_uri']\n"
                        )
                    code += checkpoint(revised=True)
                code += f"print('USE_{question + 1}_COMPLETE')"
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{raw_index}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert result["accepted"] and result["first_verification_passed"], result
    assert result["checkpoint_exercised"]
    assert len(result["learning_checkpoints"]) == uses
    assert result["measurement"]["answer_contracts"]["all_submissions_supported"]
    events = trial.ledger.read(trial.task_id)
    assert sum(e.kind == "evaluation.checkpoint_reminder" for e in events) == int(omit_marker)
    assert len(result["checkpoint_reminders"]) == int(omit_marker)
    notes = [e for e in events if e.kind == "memory.note"]
    assert len(notes) == (int(arm == "findings") * (2 if family == "config" and uses == 3 else 1))
    assert not any(e.kind in {"repl.cell_failed", "repl.cell_timeout"} for e in events)
    assert all(c["worker_loss"]["before"]["live"] and not c["worker_loss"]["after"]["live"] for c in result["learning_checkpoints"])
    assert all(i["reads"]["counts"].get("pre_cut_overlap_lines", 0) == 0 for i in result["measurement"]["checkpoint_intervals"])
    for checkpoint_event in result["learning_checkpoints"]:
        if checkpoint_event.get("checkpoint_policy"):
            assert bool(checkpoint_event["reused_note_event_id"]) is (arm == "findings")
    cuts = [e for e in events if e.kind == "compaction.created"]
    if arm == "findings" and uses == 3:
        assert cuts[1].payload["note_stale"]  # Historical reuse never silently claims a fresh note.
    assert result["provider_cost_usd"] == pytest.approx(calls * .001)
    assert result["max_model_calls"] == 24 and result["input_budget"] == 350_000
