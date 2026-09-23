import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.learned_continuity import LearnedContinuation
from evals.staged_continuity import STAGED_CASES
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["write_before_cut", "write_failed", "read_before_write", "read_pending"])
async def test_revision_checkpoint_requires_completed_change_then_new_read(tmp_path, monkeypatch, invalid):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", "staged_dispatch", "findings")
    await trial.prepare()
    controller = trial.assembly.app.plugins[0]
    stage = trial.fixture["stages"][1]
    policy = next(r for r in stage["learning_requirements"] if r["path"] == "policy.toml")
    def append(kind, **payload):
        return trial.ledger.append(task_id=trial.task_id, source="test", source_id=str(len(trial.ledger.read(trial.task_id))), kind=kind, payload=payload)
    def change(kind="capability.completed"):
        return append(kind, status="ok", operation="fs.write", changed_paths=["policy.toml"], content_hashes=stage["required_changes"])
    def read(raw, kind="capability.completed"):
        return append(kind, status="ok", operation="fs.read", read_evidence=raw)
    def checkpoint():
        append("memory.note", text="revised checkpoint")
        append("repl.cell_completed", stdout="REVISION_COMPLETE\n")
    try:
        if invalid == "write_before_cut":
            change()
        controller.stage = 1
        controller.stage_floor = append("test.boundary").sequence
        for raw in stage["learning_requirements"]:
            if raw["path"] != "policy.toml":
                read(raw)
        if invalid == "read_before_write":
            read(policy)
        if invalid != "write_before_cut":
            change("capability.failed" if invalid == "write_failed" else "capability.completed")
        if invalid != "read_before_write":
            read(policy, "capability.requested" if invalid == "read_pending" else "capability.completed")
        checkpoint()
        context = SimpleNamespace(agent_name="coding_worker", state={"steering_owner": "owner"})
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is None and controller.ack_message is None
        change()
        read(policy)
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is None  # Later evidence cannot justify the earlier checkpoint.
        queue = controller._steering.queue
        own = queue.enqueue(trial.task_id, "Completed coordinator revision", idempotency_key="owned-revision")
        unrelated = queue.enqueue(trial.task_id, "Unrelated user request", idempotency_key="unrelated")
        queue.lease(trial.task_id, "owner", limit=2, lease_seconds=60)
        controller.followup_message = own
        checkpoint()
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.note is not None and controller.ack_message is not None
        statuses = {m.message_id: m.status for m in queue.list_messages(trial.task_id)}
        assert statuses[own.message_id] == "acked" and statuses[unrelated.message_id] == "leased"
        append("repl.cell_completed", stdout="CHECKPOINT_READY_1\n")
        append("repl.cell_failed", stdout="CHECKPOINT_READY_2\n")
        await controller.before_model_callback(callback_context=context, llm_request=None)
        assert controller.pending is None  # Neither an old token nor a failed cell can advance.
    finally:
        await trial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["no_recall", "findings"])
@pytest.mark.parametrize("case,bad,multi", [(case, None, False) for case in STAGED_CASES] + [
    ("staged_dispatch", bad, False) for bad in ("stale", "early", "reverted")] + [
    ("staged_allocation", bad, True) for bad in (None, "missing_first", "unsupported_first", "corrupt_ack", "repair_ack")])
async def test_two_worker_losses_require_revised_evidence_and_current_sources(tmp_path, monkeypatch, arm, case, bad, multi):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    if multi:
        trial.fixture["permitted_paths"].append("answers/policy.json")
        trial.fixture["answers"] = [
            {"path": "answers/policy.json", "expected": {"reserve": 17}, "checkpoint": 0,
             "required": [r for r in trial.fixture["source_requirements"] if r["path"] == "policy.toml"]},
            {"path": "answer.json", "expected": trial.fixture["expected"], "checkpoint": 1,
             "required": trial.fixture["source_requirements"]},
        ]
        trial.fixture["stages"][0]["followup"] = (
            "Revision replaces the acknowledgement request. Use agent.fs with a current-version guard to replace "
            f"policy.toml with these exact bytes as a JSON string: {json.dumps(trial.fixture['revision_text'])}. "
            "Read the completed revised file. Write answers/policy.json with exactly reserve (integer) from that read; "
            "keep it intact for final verification. Preserve the revised checkpoint, retain unchanged evidence, "
            "then print REVISION_COMPLETE on its own line in a successful cell. Do not write answer.json yet. "
            "A new acknowledgement and worker-loss cut precede the final question.")
    calls = 0

    def checkpoint(stage):
        if arm == "findings":
            return (
                "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': text, "
                "'related_paths': [p], 'evidence_refs': [refs[p]]} for i, (p, text) in enumerate(stored.items())]\n"
                f"note = agent.shell.run('memory note write --text checkpoint --expected-version {stage} "
                f"--operation-id stage-{stage} --entries ' + shlex.quote(json.dumps(entries)))\n"
                "assert note['status'] == 'ok', note\n"
            )
        return f"saved = agent.artifacts.publish({{'stored': stored, 'refs': refs}}, 'Checkpoint_{stage}')\nassert saved['status'] == 'ok'\n"

    def recover(stage):
        prefix = "import json, shlex, hashlib, csv, tomllib\n"
        if arm == "findings":
            return prefix + (
                "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                "stored = {e['finding']['related_paths'][0]: e['finding']['text'] for e in note['data']['entries']}\n"
                "refs = {e['finding']['related_paths'][0]: e['finding']['evidence_refs'][0] for e in note['data']['entries']}\n"
            )
        return prefix + (
            "artifacts = agent.artifacts.list()['data']['artifacts']\n"
            f"uri = next(a['uri'] for a in artifacts if a.get('name') == 'Checkpoint_{stage}')\n"
            "loaded = agent.artifacts.load(uri)\nassert loaded['status'] == 'ok'\n"
            "payload = json.loads(loaded['data']['text'])\nstored, refs = payload['stored'], payload['refs']\n"
        )

    def answer():
        code = "policy = tomllib.loads(stored['policy.toml'])\n"
        if bad == "stale":
            code += "policy['active_region'] = 'north'\n"
        if case == "staged_dispatch":
            code += (
                "rows = json.loads(stored['deployments.json'])\n"
                "live = max((r for r in rows if r['region'] == policy['active_region'] and r['state'] == 'completed'), key=lambda r: r['sequence'])\n"
                "result = {k: live[k] for k in ['build', 'region', 'sequence']}\n"
            )
        else:
            code += (
                "rows = list(csv.DictReader(stored['stock.csv'].splitlines()))\n"
                "eligible = [(int(r['units']) - policy['reserve_units'], r['warehouse']) for r in rows if r['state'] == 'completed']\n"
                "units, warehouse = max(eligible)\nresult = {'warehouse': warehouse, 'available': units}\n"
            )
        return code + "written = agent.fs.write('answer.json', json.dumps(result))\nassert written['status'] == 'ok'"

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        visible = "\n".join(p.text or "" for c in request.contents for p in c.parts or [])
        code = None
        if index == 0:
            code = (
                "import json, shlex\n"
                f"sources = {{p: agent.fs.read(p) for p in {sorted(trial.fixture['files'])!r}}}\n"
                "assert all(r['status'] == 'ok' for r in sources.values())\n"
                "stored = {p: r['data']['text'] for p, r in sources.items()}\n"
                "refs = {p: r['read_reference']['artifact_uri'] for p, r in sources.items()}\n"
            ) + checkpoint(0) + "print('LEARNING_COMPLETE')"
        elif index == 1 or (index == 3 and bad != "early"):
            token = f"CHECKPOINT_READY_{1 if index == 1 else 2}"
            if multi and index == 3 and bad in {"missing_first", "unsupported_first"}:
                assert "Answer checkpoint not advanced" in visible
            if not (multi and index == 3 and bad in {"missing_first", "unsupported_first"}):
                assert token in visible
                code = f"print({token!r})"
                if index == 3 and bad in {"corrupt_ack", "repair_ack"}:
                    code = "assert agent.fs.write('answers/policy.json', '{}')['status'] == 'ok'\n" + code
        elif index == 2:
            revision = trial.fixture["stages"][0]["followup"]
            assert revision in visible or json.dumps(revision)[1:-1] in visible
            assert trial.fixture["followup"] not in visible
            assert '"live": false' in visible
            code = recover(0) + (
                f"changed = agent.fs.write('policy.toml', {trial.fixture['revision_text']!r}, expected_sha256=hashlib.sha256(stored['policy.toml'].encode()).hexdigest())\n"
                "assert changed['status'] == 'ok', changed\n"
            )
            if bad == "unsupported_first":
                code += "assert agent.fs.write('answers/policy.json', json.dumps({'reserve': 17}))['status'] == 'ok'\n"
            code += (
                "fresh = agent.fs.read('policy.toml')\nassert fresh['status'] == 'ok'\n"
                "stored['policy.toml'], refs['policy.toml'] = fresh['data']['text'], fresh['read_reference']['artifact_uri']\n"
            )
            if multi and bad not in {"missing_first", "unsupported_first"}:
                code += "assert agent.fs.write('answers/policy.json', json.dumps({'reserve': tomllib.loads(stored['policy.toml'])['reserve_units']}))['status'] == 'ok'\n"
            code += answer() if bad == "early" else checkpoint(1) + "print('REVISION_COMPLETE')"
        elif index == 4 and bad == "repair_ack":
            assert "Answer checkpoint not advanced" in visible
            code = (
                "assert agent.fs.write('answers/policy.json', json.dumps({'reserve': tomllib.loads(stored['policy.toml'])['reserve_units']}))['status'] == 'ok'\n"
            ) + checkpoint(2) + "print('REVISION_COMPLETE')"
        elif index == 5 and bad == "repair_ack":
            assert "CHECKPOINT_READY_2" in visible
            code = "print('CHECKPOINT_READY_2')"
        elif (index == 4 and bad != "early" and not (multi and bad)) or (index == 6 and bad == "repair_ack"):
            assert trial.fixture["followup"] in visible
            assert '"live": false' in visible
            code = recover(2 if bad == "repair_ack" else 1) + answer()
            if bad == "reverted":
                code += f"\nassert agent.fs.write('policy.toml', {trial.fixture['files']['policy.toml']!r})['status'] == 'ok'"
        part = (types.Part(function_call=types.FunctionCall(name="code", id=f"step-{index}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    success = bad in {None, "repair_ack"}
    assert result["accepted"] is success, result
    assert result["first_verification_passed"] is success, result
    closes_second = bad != "early" and (not multi or success)
    assert result["checkpoint_exercised"] is closes_second, result
    checkpoints = result["learning_checkpoints"]
    assert len(checkpoints) == (2 if closes_second else 1)
    assert [c["stage"] for c in checkpoints] == list(range(len(checkpoints)))
    assert [c["ack_token"] for c in checkpoints] == [f"CHECKPOINT_READY_{i+1}" for i in range(len(checkpoints))]
    assert all(c["worker_loss"]["before"]["live"] and c["worker_loss"]["after"] == {"live": False, "kernel_epoch": None} for c in checkpoints)
    assert len({c["worker_loss"]["before"]["kernel_epoch"] for c in checkpoints}) == len(checkpoints)
    if multi:
        audit = result["measurement"]["answer_contracts"]
        assert audit["all_latest_supported"] is success
        assert audit["all_submissions_supported"] is success
        if bad == "unsupported_first":
            # Recovery can leave opaque lineage; neither unknown nor missing
            # is completed evidence for the revised-policy answer.
            assert audit["artifacts"]["answers/policy.json"]["answers"][0]["status"] != "available"
    else:
        assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    intervals = result["measurement"]["checkpoint_intervals"]
    assert len(intervals) == len(checkpoints)
    assert all(i["reads"]["counts"].get("pre_cut_overlap_lines", 0) == 0 for i in intervals)
    policy_reads = [e for e in trial.ledger.read(trial.task_id) if e.kind == "capability.completed" and e.payload.get("read_evidence", {}).get("path") == "policy.toml"]
    assert len(policy_reads) == 2
    assert policy_reads[-1].payload["read_evidence"]["sha256"] == hashlib.sha256(trial.fixture["revision_text"].encode()).hexdigest()
    if bad == "reverted":
        assert result["passed"]  # Correct answer and earlier source capture cannot hide a later revert.
