import json
import os
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.learned_continuity import LEARNED_CASES, LearnedContinuation
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm


@pytest.mark.asyncio
@pytest.mark.parametrize("case,guess_missing", [(case, False) for case in LEARNED_CASES] + [("learned_unavailable", True)])
@pytest.mark.parametrize("arm", ("metadata", "findings"))
async def test_model_authored_checkpoint_cut_and_recovery_use_real_workflow(tmp_path, monkeypatch, caplog, case, arm, guess_missing):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0
    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        visible = "\n".join(part.text or "" for c in request.contents for part in c.parts or [])
        if index == 0:
            assert not any(e.kind == "memory.note" for e in trial.ledger.read(trial.task_id))
            assert trial.fixture["followup"] not in visible
            code = f"paths = {list(trial.fixture['files'])!r}\n"
            code += "sources = {p: agent.fs.read(p) for p in paths}\nassert all(r['status'] == 'ok' for r in sources.values())\nprint('ACQUISITION_ONLY_SENTINEL')"
        elif index == 1:
            code = (
                "import json, shlex\n"
                "invalid_note = agent.shell.run('memory note write --expected-version 0 --operation-id missing-text')\n"
                "assert invalid_note['status'] == 'error' and invalid_note['effect'] == 'none'\n"
                "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': r['data']['text'], "
                "'related_paths': [p], 'evidence_refs': [r['read_reference']['artifact_uri']]} for i, (p, r) in enumerate(sources.items())]\n"
                "bad_entries = [{**entries[0], 'evidence_refs': [entries[0]['evidence_refs'][0][:-1]]}]\n"
                "bad_citation = agent.shell.run('memory note write --text checkpoint --expected-version 0 --operation-id bad-citation --entries ' + shlex.quote(json.dumps(bad_entries)))\n"
                "assert bad_citation['status'] == 'error' and bad_citation['effect'] == 'none'\n"
                "assert bad_citation['data']['recovery']['issue'] == 'malformed_artifact_address'\n"
                "assert bad_citation['data']['recovery']['strategy'] == 'recover_exact_current_task_reference'\n"
                "note = agent.shell.run('memory note write --text checkpoint --expected-version 0 --operation-id learned-note --entries ' + shlex.quote(json.dumps(entries)))\n"
                "assert note['status'] == 'ok'\nprint({'version': note['data']['version']})"
            )
        elif index == 2:
            assert "CHECKPOINT_READY" in visible
            assert trial.fixture["followup"] not in visible
            code = "print('CHECKPOINT_READY')"
        elif index == 3:
            assert trial.fixture["followup"] in visible
            assert "ACQUISITION_ONLY_SENTINEL" not in visible
            assert len([e for e in trial.ledger.read(trial.task_id) if e.kind == "compaction.created"]) == 1
            if case == "learned_unavailable":
                code = "missing = agent.fs.read('shards/policy_missing.toml')\nassert missing['status'] != 'ok'\nprint(missing['status'])"
                if guess_missing:
                    code += "\nprint(agent.fs.write('answer.json', 'null'))"
            else:
                code = (
                    "import json, tomllib\n"
                    "recovered = agent.shell.run('memory note read')\nassert recovered['status'] == 'ok'\n"
                    "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in recovered['data']['entries']}\n"
                    f"shard = json.loads(stored[{trial.fixture['target']!r}])\n"
                    "capacity = shard['burst'] - shard['reserve']\n"
                )
                code += ("answer = {'window_ms': capacity * tomllib.loads(stored['shards/' + shard['policy']])['interval_ms']}\n"
                         if case == "learned_join" else "answer = {'capacity': capacity}\n")
                code += "print(agent.fs.write('answer.json', json.dumps(answer)))"
        else:
            code = None
        part = types.Part(function_call=types.FunctionCall(name="code", id=f"step-{index}", args={"code": code})) if code else types.Part(text=json.dumps({
            "status": "blocked" if case == "learned_unavailable" and not guess_missing else "verify",
            "message": "Policy unavailable." if case == "learned_unavailable" else "Verify."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert result["terminal"] == ("workflow_blocked" if guess_missing else "expected_abstention"
                                  if case == "learned_unavailable" else "verified_completion"), result
    assert trial.seed_cells == 0
    assert result["checkpoint_exercised"]
    assert result["learning_checkpoints"][0]["learning_model_calls"] == 3
    assert result["learning_checkpoints"][0]["seeded_cells"] == 0
    if case == "learned_unavailable":
        assert result["correct_abstention"] is not guess_missing
        assert not result["accepted"]
        assert (trial.workspace / "answer.json").exists() is guess_missing
        if guess_missing:
            assert result["first_verification_passed"] is False
    else:
        assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    assert result["measurement"]["reads"]["counts"].get("post_cut_reads", 0) == 0
    assert trial.plugin.config.compaction_threshold_ratio == .8
    assert result["provider_cost_usd"] == pytest.approx(calls * .001)
    assert "trace observation failed" not in caplog.text
    rejected = [e for e in trial.ledger.read(trial.task_id) if e.kind == "capability.failed"
                and e.payload.get("discovery_kind") == "memory"]
    assert len(rejected) == 2 and all(e.payload["effect"] == "none" for e in rejected)
    assert not any(e.kind in {"repl.cell_failed", "repl.cell_timeout"} for e in trial.ledger.read(trial.task_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ("failed", "pending", "wrong_version", "partial"))
async def test_checkpoint_requires_completed_ranges_before_note_and_a_later_ack(tmp_path, monkeypatch, invalid):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", "learned_join", "findings")
    await trial.prepare()
    controller = trial.assembly.app.plugins[0]
    context = SimpleNamespace(agent_name="coding_worker", state={"steering_owner": "owner"})
    def append(kind, **payload):
        return trial.ledger.append(task_id=trial.task_id, source="evaluation-test",
                                   source_id=str(len(trial.ledger.read(trial.task_id))), kind=kind, payload=payload)
    def reads(valid):
        for raw in trial.fixture["learning_requirements"]:
            evidence = dict(raw)
            if not valid and invalid == "wrong_version":
                evidence["sha256"] = "0" * 64
            if not valid and invalid == "partial":
                evidence["returned_lines"] = 0
            kind = "capability.failed" if not valid and invalid == "failed" else (
                "capability.requested" if not valid and invalid == "pending" else "capability.completed")
            append(kind, operation="fs.read", status="ok", read_evidence=evidence)
    try:
        reads(False)
        append("memory.note", text="Unsupported checkpoint", version=1)
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is None
        reads(True)
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is None  # Later reads cannot support the old checkpoint.
        append("memory.note", text="Completed evidence checkpoint", version=2)
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is not None
        assert controller.pending is None
        append("repl.cell_failed", cell_id="bad-ack")
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.pending is None
        assert not trial.cut_sequence
        controller.pending = {"checkpoint_event_id": "not-complete"}
        await controller.after_model_callback(callback_context=context, llm_response=LlmResponse(partial=True))
        assert not trial.cut_sequence
        assert not any(e.kind == "evaluation.learning_checkpoint" for e in trial.ledger.read(trial.task_id))
    finally:
        await trial.close()
