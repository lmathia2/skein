import asyncio
import hashlib
import json
import os
import subprocess
import threading

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agent.factory import default_harness_registry
from evals.continuity_oracle import (
    ORACLE_COMMAND,
    AnswerSpec,
    HostOracleSandbox,
    audit_answer_contracts,
)
from evals.learned_continuity import LearnedContinuation
from evals.qualification_continuity import QUALIFICATION_CASES
from evals.validation_continuity import VALIDATION_CASES
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm
from harness.evidence.memory.findings import source_freshness, source_observations
from harness.execution.environment.local import LocalWorkspaceEnvironment
from harness.execution.environment.runtime import ExecutionRuntime, LocalRepositoryRuntime
from harness.execution.sandbox.docker import DockerSandbox
from harness.execution.sandbox.factory import create_configured_command_sandbox

COMMAND = "python -B -m unittest check_evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_actual_ptc_validation_pending_and_failed_cannot_support_an_answer(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", "reuse_config_1", "findings")
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def runtime(settings, config, known_secrets):
        image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
        delegate = (DockerSandbox(settings.workspace, settings.state_root / "artifacts" / "commands", image=image,
                                  known_secrets=known_secrets) if image else
                    create_configured_command_sandbox(settings.workspace, settings.state_root, config.sandbox,
                                                      max_output_bytes=config.tools.output.max_bytes, known_secrets=known_secrets))
        class PendingPublication:
            workspace = settings.workspace
            def execute(self, request):
                nonlocal calls
                result = delegate.execute(request)
                if request.command == COMMAND:
                    calls += 1
                    entered.set()  # A real subprocess returned; no broker terminal exists yet.
                    if not release.wait(15):
                        raise TimeoutError("test did not release pending result publication")
                return result
        return ExecutionRuntime(files=LocalWorkspaceEnvironment(settings.workspace),
                                commands=PendingPublication(), repository=LocalRepositoryRuntime(settings.workspace))

    trial.registry = default_harness_registry(execution_runtime_factory=runtime)
    await trial.prepare()
    (trial.workspace / "check_evidence.py").write_text(
        "import unittest\nclass Check(unittest.TestCase):\n def test_arithmetic(self):\n"
        f"  self.assertEqual(2 + 3, {6 if failure else 5})\n")
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
        subprocess.run(["git", "-C", str(trial.workspace), *args], check=True, capture_output=True)
    task = None
    try:
        # No model or invented validation event: all capabilities execute through PTC.
        sources = sorted(trial.fixture["files"])
        assert (await trial.cell(f"sources = {{p: agent.fs.read(p) for p in {sources!r}}}"))["status"] == "ok"
        assert (await trial.cell("print('CUT_READY')"))["status"] == "ok"
        trial.plugin.config = trial.plugin.config.model_copy(update={"compaction_threshold_ratio": .000001})
        await trial.request()
        cuts = [e.sequence for e in trial.ledger.read(trial.task_id) if e.kind == "compaction.created"]
        assert len(cuts) == 1
        spec = AnswerSpec.model_validate({**trial.fixture["answers"][0], "validations": [COMMAND]})
        write = f"agent.fs.write('answer.json', {json.dumps(spec.expected)!r})"
        assert (await trial.cell(write))["status"] == "ok"
        task = asyncio.create_task(trial.cell(f"validation = agent.shell.run({COMMAND!r})"))
        assert await asyncio.to_thread(entered.wait, 10)
        events = trial.ledger.read(trial.task_id)
        command_hash = hashlib.sha256(COMMAND.encode()).hexdigest()
        requests = [e for e in events if e.kind == "capability.requested" and e.payload.get("command_sha256") == command_hash]
        assert len(requests) == 1
        identity = requests[0].payload["operation_id"]
        assert not any(e.kind != "capability.requested" and e.payload.get("operation_id") == identity for e in events)
        assert not audit_answer_contracts(events, [spec], cuts)["all_latest_supported"]
        release.set()
        assert (await asyncio.wait_for(task, 15))["status"] == "ok"
        events = trial.ledger.read(trial.task_id)
        terminal = [e for e in events if e.kind in {"capability.completed", "capability.failed"} and e.payload.get("operation_id") == identity]
        assert len(terminal) == 1
        assert terminal[0].kind == ("capability.failed" if failure else "capability.completed")
        assert terminal[0].payload["command_sha256"] == command_hash
        assert not audit_answer_contracts(events, [spec], cuts)["all_latest_supported"]  # Completion was later than the answer.
        assert (await trial.cell(write))["status"] == "ok"
        events = trial.ledger.read(trial.task_id)
        audit = audit_answer_contracts(events, [spec], cuts)
        assert audit["all_latest_supported"] is not failure
        assert not audit["all_submissions_supported"]
        assert calls == 1  # No replay/re-execution to obtain a validation receipt.
        if failure:
            assert terminal[0].payload["effect"] == "observed"  # The failed check is known, not passing evidence.
            assert not any(e.kind == "execution.validation_observed" and e.payload.get("command") == COMMAND for e in events)
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.wait_for(task, 15)
        await trial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", VALIDATION_CASES)
@pytest.mark.parametrize("arm", ["no_recall", "findings"])
@pytest.mark.parametrize("guess", [False, True])
async def test_validation_controls_through_actual_root_workflow(tmp_path, monkeypatch, case, arm, guess):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        code = None
        if index == 0:
            code = (
                "import json, shlex\n"
                "sources = {p: agent.fs.read(p) for p in ['rules.md', 'check_evidence.py']}\n"
                "assert all(r['status'] == 'ok' for r in sources.values())\n"
            )
            if arm == "findings":
                code += (
                    "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': r['data']['text'], "
                    "'related_paths': [p], 'evidence_refs': [r['read_reference']['artifact_uri']]} for i, (p, r) in enumerate(sources.items())]\n"
                    "saved = agent.shell.run('memory note write --text checkpoint --expected-version 0 --operation-id checkpoint --entries ' + shlex.quote(json.dumps(entries)))\n"
                    "assert saved['status'] == 'ok', saved\n"
                )
            else:
                code += "assert agent.artifacts.publish(sources, 'Initial')['status'] == 'ok'\n"
            code += "print('LEARNING_COMPLETE')"
        elif index == 1:
            code = "print('CHECKPOINT_READY_1')"
        elif index == 2:
            code = "agent.fs.read('input.json')\nagent.fs.write('answer.json', '{\"value\":43}')" if guess else (
                "import json\n"
                "source = agent.fs.read('input.json')\n"
                "if source['status'] == 'ok':\n"
                f"    checked = agent.shell.run({COMMAND!r})\n"
                "    if checked['status'] == 'ok' and checked['exit_code'] == 0:\n"
                "        value = json.loads(source['data']['text'])['value']\n"
                "        assert agent.fs.write('answer.json', json.dumps({'value': value}))['status'] == 'ok'\n"
            )
        proposal = {"status": "verify" if guess or case == "validation_complete" else "blocked", "message": "Check the recorded evidence."}
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{index}", args={"code": code}))
                if code else types.Part(text=json.dumps(proposal)))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert "measurement_error" not in result, result
    assert result["checkpoint_exercised"], result
    if guess:
        assert not result["accepted"] and not result["first_verification_passed"], result
        assert not result["measurement"]["answer_contracts"]["all_latest_supported"]
        assert result["validation_outcomes"] == []
        return
    if case == "validation_complete":
        assert result["accepted"] and result["first_verification_passed"], result
        assert result["measurement"]["answer_contracts"]["all_submissions_supported"]
        assert result["validation_outcomes"][0]["exit_code"] == 0
    else:
        assert not result["accepted"] and not result["passed"] and result["answer_withheld"], result
        assert result["correct_abstention"]  # A completed failed check permits safe abstention, not acceptance.
        if case == "validation_failed":
            assert result["validation_outcomes"][0]["exit_code"] == 1
            assert result["validation_outcomes"][0]["effect"] == "observed"
        else:
            assert result["validation_outcomes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["no_recall", "findings"])
@pytest.mark.parametrize("case,bad", [(c, None) for c in QUALIFICATION_CASES if "validation" in c] + [
    ("qualification_validation_1", bad) for bad in ("skip", "wrong_command", "late", "wrong_version", "bypass", "repeat")
] + [("qualification_validation_2", "force")])
async def test_fresh_validation_reuse_requires_the_actual_completed_check(tmp_path, monkeypatch, arm, case, bad):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", case, arm)
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    failed = trial.fixture["validation_control"] == "failed"
    command = trial.fixture["validation_command"]
    if bad == "wrong_version":
        trial.fixture["permitted_paths"].append("records.csv")
    if bad == "bypass":
        original = HostOracleSandbox.execute
        def bypass_evidence(self, request):
            check = self._evidence_check
            if request.command == ORACLE_COMMAND:
                self._evidence_check = None
            try:
                return original(self, request)
            finally:
                self._evidence_check = check
        monkeypatch.setattr(HostOracleSandbox, "execute", bypass_evidence)
    calls = 0

    async def model(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        code = None
        if index == 0:
            code = (
                "import json, shlex\n"
                "sources = {p: agent.fs.read(p) for p in ['rules.md', 'check_batch.py']}\n"
                "assert all(r['status'] == 'ok' for r in sources.values())\n")
            if arm == "findings":
                code += (
                    "entries = [{'id': 'source-' + str(i), 'kind': 'observation', 'text': r['data']['text'], "
                    "'related_paths': [p], 'evidence_refs': [r['read_reference']['artifact_uri']]} for i, (p, r) in enumerate(sources.items())]\n"
                    "saved = agent.shell.run('memory note write --text checkpoint --expected-version 0 --operation-id prepare --entries ' + shlex.quote(json.dumps(entries)))\n"
                    "assert saved['status'] == 'ok', saved\n")
            else:
                code += "assert agent.artifacts.publish(sources, 'Initial')['status'] == 'ok'\n"
            code += "print('LEARNING_COMPLETE')"
        elif index == 1 or (index == 3 and not failed):
            code = f"print('CHECKPOINT_READY_{1 if index == 1 else 2}')"
        elif index == 2:
            code = (
                "import csv, json, shlex\n"
                "source = agent.fs.read('records.csv')\nassert source['status'] == 'ok'\n"
                "rows = list(csv.DictReader(source['data']['text'].splitlines()))\n"
                "answer = {'net_cents': sum(int(r['units']) * int(r['cents_per_unit']) for r in rows), 'rows': len(rows)}\n")
            if bad == "late":
                code += "assert agent.fs.write('answer.json', json.dumps(answer))['status'] == 'ok'\n"
            if bad == "wrong_version":
                # This real passing check sees a different, valid input version.
                # Restoring the requested source must not retarget that receipt.
                code += (
                    "rows[0]['units'] = str(int(rows[0]['units']) + 1)\n"
                    "rows[0]['checksum'] = str(int(rows[0]['units']) * int(rows[0]['cents_per_unit']))\n"
                    "alternate = ','.join(rows[0]) + '\\n' + ''.join(','.join(r.values()) + '\\n' for r in rows)\n"
                    "assert agent.fs.write('records.csv', alternate, expected_sha256=source['read_reference']['sha256'])['status'] == 'ok'\n")
            if bad not in {"skip", "bypass"}:
                invoked = command + ".Check.test_receipt_integrity" if bad == "wrong_command" else command
                code += f"checked = agent.shell.run({invoked!r})\n"
                if bad == "repeat":
                    code += f"checked = agent.shell.run({command!r})\n"
            else:
                code += "checked = {'status': 'unavailable', 'exit_code': None}\n"
            if bad == "wrong_version":
                code += (
                    "assert agent.fs.write('records.csv', source['data']['text'])['status'] == 'ok'\n"
                    "source = agent.fs.read('records.csv')\nassert source['status'] == 'ok'\n")
            if failed:
                if bad == "force":
                    code += "assert agent.fs.write('answer.json', json.dumps(answer))['status'] == 'ok'\n"
            else:
                code += "summary = {'answer': answer, 'validation_status': checked['status'], 'exit_code': checked.get('exit_code')}\n"
                if arm == "findings":
                    code += (
                        "history = agent.shell.run('memory history --kinds execution.validation_observed --limit 8')\n"
                        "assert history['status'] == 'ok', history\n"
                        "refs = [e['event_id'] for e in history['data']['data']['events']]\n"
                        "refs.append(source['read_reference']['artifact_uri'])\n"
                        "entry = {'id': 'batch', 'kind': 'observation', 'text': json.dumps(summary), "
                        "'related_paths': ['records.csv', 'check_batch.py'], 'evidence_refs': refs}\n"
                        "saved = agent.shell.run('memory note write --text checkpoint --expected-version 1 --operation-id checked --entries ' + shlex.quote(json.dumps([entry])))\n"
                        "assert saved['status'] == 'ok', saved\n")
                else:
                    code += "assert agent.artifacts.publish(summary, 'Checked')['status'] == 'ok'\n"
                code += "print('VALIDATION_COMPLETE')"
        elif index == 4 and not failed:
            code = "import json\n"
            if arm == "findings":
                code += (
                    "note = agent.shell.run('memory note read')\nassert note['status'] == 'ok'\n"
                    "summary = json.loads(next(e['finding']['text'] for e in note['data']['entries'] if e['finding']['id'] == 'batch'))\n")
            else:
                code += (
                    "items = agent.artifacts.list()['data']['artifacts']\n"
                    "uri = next(a['uri'] for a in items if a.get('name') == 'Checked')\n"
                    "summary = json.loads(agent.artifacts.load(uri)['data']['text'])\n")
            code += "assert agent.fs.write('answer.json', json.dumps(summary['answer']))['status'] == 'ok'\n"
        proposal = {"status": "blocked" if failed and bad != "force" else "verify", "message": "Check recorded evidence."}
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{index}", args={"code": code}))
                if code else types.Part(text=json.dumps(proposal)))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", model)
    result = await run_verified_case(trial)
    assert "measurement_error" not in result, result
    assert result["checkpoint_exercised"], result
    assert result["measurement"]["initial_capture"]["respected"]
    success = not failed and bad not in {"skip", "wrong_command", "wrong_version", "bypass"}
    assert result["accepted"] is (success or bad == "bypass"), result
    assert (result["terminal"] == "false_acceptance") is (bad == "bypass"), result
    audit = result["measurement"]["answer_contracts"]
    assert audit["all_latest_supported"] is success
    assert audit["all_submissions_supported"] is (success and bad != "late")
    outcomes = result["validation_outcomes"]
    if bad in {"skip", "wrong_command", "bypass"}:
        assert not outcomes and not result["unresolved_execution"]
    else:
        assert len(outcomes) == (2 if bad == "repeat" else 1)
        assert all(o["exit_code"] == int(failed) for o in outcomes)
        assert not result["unresolved_execution"]  # Known failed validation is not an unresolved tool effect.
        if bad == "repeat":
            assert len(audit["artifacts"]["answer.json"]["answers"][-1]["validations"][command]) == 2
    if failed:
        assert result["answer_withheld"] is (bad != "force")
        assert result["correct_abstention"] is (bad != "force")
    else:
        checkpoints = result["learning_checkpoints"]
        assert len(checkpoints) == 2
        assert all(c['worker_loss']['before']['live'] and not c['worker_loss']['after']['live'] for c in checkpoints)
        if bad != "wrong_version":
            assert all(i['reads']['counts'].get('all_earlier_overlap_lines', 0) == 0 for i in result['measurement']['checkpoint_intervals'])
        if outcomes:
            events = trial.ledger.read(trial.task_id)
            observed = next(e for e in events if e.kind == 'execution.validation_observed' and e.payload['command'] == command)
            assert checkpoints[0]['cut_sequence'] < observed.sequence < checkpoints[1]['cut_sequence']
            if arm == "findings":
                note = [e for e in events if e.kind == 'memory.note'][-1]
                finding = next(e['finding'] for e in note.payload['entries'] if e['finding']['id'] == 'batch')
                assert observed.event_id in finding['evidence_refs']
            if bad is None:
                before_cut = [{"task_id": e.task_id, "sequence": e.sequence, "kind": e.kind, "payload": e.payload}
                              for e in events if e.sequence < checkpoints[1]['cut_sequence'] and e.kind in {
                                  'capability.completed', 'capability.failed', 'read.observed', 'execution.validation_observed'}]
                observations = source_observations(before_cut)
                assert all(source_freshness(r, observations, trial.task_id)['status'] == 'historical_snapshot'
                           for r in trial.fixture['source_requirements'])
