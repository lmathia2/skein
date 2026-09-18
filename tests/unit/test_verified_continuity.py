import json
import os
import shlex

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.continuity import Continuation
from evals.continuity_cases import CASES, DevelopmentContinuation
from evals.continuity_oracle import ORACLE_COMMAND, HostOracleSandbox
from evals.verified_continuity import run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm
from harness.execution.sandbox import SandboxRequest, SandboxResult


def test_host_oracle_hides_expected_values_and_delegates_nonexact_commands(tmp_path):
    class Commands:
        workspace = tmp_path
        def __init__(self):
            self.calls = []
        def execute(self, request):
            self.calls.append(request)
            return SandboxResult("blocked", None, "", "not run", 0)
    commands = Commands()
    expected = {"limit": 12}
    oracle = HostOracleSandbox(commands, expected)
    expected["limit"] = 64  # The expected result is immutable after construction.
    check = SandboxRequest(ORACLE_COMMAND)
    assert oracle.execute(check).exit_code == 1
    for content, exit_code in (("{}", 1), ('{"limit":64}', 1), ('{"limit":12,"route":"x"}', 1),
                               ('{"limit":12}', 0), ('{"limit":12.0}', 1), ("NaN", 1), ("x" * 5000, 1)):
        (tmp_path / "answer.json").write_text(content)
        result = oracle.execute(check)
        assert result.exit_code == exit_code
        assert "12" not in result.stdout + result.stderr
    (tmp_path / "answer.json").unlink()
    (tmp_path / "answer.json").symlink_to(tmp_path / "missing")
    assert oracle.execute(check).exit_code == 1
    assert not (tmp_path / "__skein_host_oracle__").exists()
    modified = SandboxRequest(ORACLE_COMMAND + "; echo not-the-oracle")
    assert oracle.execute(modified).status == "blocked"
    assert commands.calls == [modified]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_shell,bypass_fence", ((False, False), (True, False), (True, True)))
async def test_correct_source_backed_answer_cannot_clear_unknown_shell_effect(tmp_path, monkeypatch, failed_shell, bypass_fence):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    if bypass_fence:
        # Reproduce the old missing production fence. The independent evaluator
        # must still reject this acceptance, rather than trust a passing report.
        monkeypatch.setattr("app.agent.workflow.unresolved_execution", lambda *_: [])
    calls = 0

    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            code = (
                "import tomllib\n"
                "r = agent.fs.read('config/route_00.toml')\n"
                "source = tomllib.loads(r['data']['text'])['service']\n"
                "assert agent.fs.write('answer.json', json.dumps({'port': source['active_port'], 'protocol': source['protocol']}))['status'] == 'ok'\n"
            )
            if failed_shell:
                # Real completed Python cell with a failed nested shell result,
                # matching the live printf failure after otherwise correct work.
                code += "print(agent.shell.run(\"printf '--STATUS--\\\\n'\"))\n"
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="solve", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify the source-backed answer."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["passed"]  # Artifact correctness is intentionally insufficient.
    assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    assert result["accepted"] is (not failed_shell or bypass_fence), result
    assert result["first_verification_passed"] is (not failed_shell or bypass_fence)
    if failed_shell:
        assert result["terminal"] == ("false_acceptance" if bypass_fence else "workflow_blocked")
        assert any(e.kind == "capability.failed" and e.payload.get("effect") == "unknown"
                   for e in trial.ledger.read(trial.task_id))
        assert {"capability", "cell"} <= {item["kind"] for item in result["unresolved_execution"]}
        from harness.evidence.state import rebuild_ledger

        handoff = trial.plugin.handoff(rebuild_ledger(trial.events.read(trial.task_id)))
        assert handoff["unresolved_effects"]["operations"] == result["unresolved_execution"]
        assert handoff["unresolved_effects"]["count"] == len(result["unresolved_execution"])
        if not bypass_fence:
            assert all(any("reconciliation" in d for d in report["unresolved_diagnostics"])
                       for report in result["verification_reports"])


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("SKEIN_EVAL_DOCKER_IMAGE"), reason="explicit cached Docker image required")
async def test_host_oracle_with_real_docker_command_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.environ["SKEIN_EVAL_DOCKER_IMAGE"]
    calls = 0
    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            command = 'test ! -e ' + shlex.quote(str(trial.root.resolve()))
            code = (
                "assert agent.shell.run('pwd')['data']['stdout'].strip() == '/workspace'\n"
                f"assert agent.shell.run({command!r})['exit_code'] == 0\n"
                "import tomllib\nr = agent.fs.read('config/route_00.toml')\n"
                "source = tomllib.loads(r['data']['text'])['service']\n"
                "assert agent.fs.write('answer.json', json.dumps({'port': source['active_port'], 'protocol': source['protocol']}))['status'] == 'ok'\n"
                f"assert agent.shell.run({ORACLE_COMMAND!r})['exit_code'] == 0\n"
            )
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="solve", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["terminal"] == "verified_completion", result
    assert result["tool_cells"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", (False, True))
async def test_seeded_memory_runs_through_real_verification_and_reentry(tmp_path, monkeypatch, repair):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    calls = 0
    async def scripted(self, request, stream=False):
        nonlocal calls
        index, calls = calls, calls + 1
        if index == 0:
            code = "print(agent.fs.write('answer.json', '{\"value\":11101}', expected_absent=True))"
        elif index == 3 and repair:
            code = (
                "tail = agent.fs.read('src/module_01.py', offset=18, limit=1)\n"
                "assert tail['status'] == 'ok'\n"
                "value = int(tail['data']['text'].split('=')[1].split('#')[0])\n"
                "old = agent.fs.read('answer.json')\n"
                "print(agent.fs.write('answer.json', json.dumps({'value': value}), expected_sha256=old['data']['sha256']))"
            )
        else:
            code = None
        part = types.Part(function_call=types.FunctionCall(
            name="execute_code", id=f"live-{index}", args={"code": code},
        )) if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify answer."}))
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part(text="private_fixture_reasoning", thought=True), part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(
                              prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", scripted)
    trial = Continuation(tmp_path / "trial", "partial", 1, "findings")
    result = await run_verified_case(trial)
    assert result["first_verification_passed"] is False, result
    assert result["accepted"] is repair, result
    assert result["passed"] is repair, result
    assert [r["passed"] for r in result["verification_reports"]] == [False, repair]
    assert result["terminal"] == ("verified_completion" if repair else "workflow_blocked")
    assert result["unaccounted_model_calls"] == 0
    assert result["provider_cost_usd"] == pytest.approx(calls * .001)
    assert len(list((trial.root / "responses").glob("*.json"))) == calls
    assert not (trial.root / "oracle").exists()
    assert result["oracle_mode"] == "host_owned_virtual_test"
    assert not (trial.workspace / "test_answer.py").exists()
    assert all("private_fixture_reasoning" not in p.read_text() for p in (trial.root / "responses").glob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES)
async def test_development_oracles_accept_only_current_source_derived_answers(tmp_path, monkeypatch, case):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", case, "findings")
    calls = 0
    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            path = trial.fixture["target"]
            visible = "\n".join(part.text or "" for content in request.contents for part in content.parts or [])
            assert visible.count("Required continuation metadata:") == 1
            reuse = case in {"routing", "correction", "join"}
            expression = f"sources[{path!r}]" if reuse else f"agent.fs.read({path!r})"
            code = f"r = {expression}\nassert r['status'] == 'ok'\nsrc = r['data']['text']\n"
            if case in {"routing", "missing"}:
                code += "import tomllib\ns = tomllib.loads(src)['service']\nanswer = {'port': s['active_port'], 'protocol': s['protocol']}\n"
            elif case in {"restart", "freshness"}:
                code += "s = json.loads(src)\nanswer = {'region': s['region'], 'generation': s['generation']}\n"
            elif case == "correction":
                code += "limit = int(next(line.split('=')[1] for line in src.splitlines() if line.startswith('SAFE_LIMIT')))\nanswer = {'limit': limit}\n"
            else:
                code += "import tomllib\ns = json.loads(src)\np = sources['config/' + s['policy']]\nanswer = {'wait_ms': s['attempts'] * tomllib.loads(p['data']['text'])['retry']['pause_ms']}\n"
            code += "print(agent.fs.write('answer.json', json.dumps(answer), expected_absent=True))"
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="solve", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify the source-derived answer."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["terminal"] == "verified_completion", result
    assert result["first_verification_passed"] is True
    assert result["oracle_mode"] == "host_owned_virtual_test"
    assert "measurement_error" not in result
    assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    if case in {"routing", "correction", "join"}:
        assert result["measurement"]["reads"]["counts"].get("post_cut_reads", 0) == 0
    cuts = [e for e in trial.events.read(trial.task_id) if e.kind == "compaction.created"]
    assert len(cuts) == 1, [(e.kind, e.payload.get("trigger")) for e in cuts]
    assert trial.plugin.config.compaction_timing == "phase_boundary"
    assert trial.plugin.config.work_packet_tokens == 20_000


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", (False, True))
async def test_correct_guess_requires_source_evidence_before_rewritten_answer(tmp_path, monkeypatch, repair):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    calls = 0
    async def guess(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            code = "print(agent.fs.write('answer.json', '{\"port\":8400,\"protocol\":\"https\"}'))"
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="guess", args={"code": code}))
        elif calls == 4 and repair:
            code = (
                "r = agent.fs.read('config/route_00.toml', offset=12, limit=2)\n"
                "assert r['status'] == 'ok'\n"
                "import tomllib\ns = tomllib.loads(r['data']['text'])\n"
                "print(agent.fs.write('answer.json', json.dumps({'port': s['active_port'], 'protocol': s['protocol']})))\n"
            )
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="repair", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", guess)
    result = await run_verified_case(DevelopmentContinuation(tmp_path / "trial", "missing", "findings"))
    assert result["accepted"] is repair, result
    assert result["passed"]  # Correct bytes alone cannot pass the source-gated oracle.
    assert result["first_verification_passed"] is False
    assert result["terminal"] == ("verified_completion" if repair else "workflow_blocked"), result
    audit = result["measurement"]["answer_evidence"]
    assert not audit["all_answers_source_available"]
    assert audit["first_answer"] == "unknown"  # The seeded memory route is conservatively unmapped.
    assert audit["answers"][0]["requirements"][1]["covered_lines"] == 0
    assert audit["last_answer"] == ("available" if repair else "unknown")


def test_oracle_binds_evidence_to_exact_answer_bytes_and_fails_closed(tmp_path):
    import hashlib

    class Commands:
        workspace = tmp_path
        def execute(self, request):
            raise AssertionError("unexpected delegation")
    body = b'{"limit":12}'
    (tmp_path / "answer.json").write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    oracle = HostOracleSandbox(Commands(), {"limit": 12}, lambda observed: observed == {"answer.json": digest})
    assert oracle.execute(SandboxRequest(ORACLE_COMMAND)).exit_code == 0
    (tmp_path / "answer.json").write_bytes(b'{"limit": 12}')  # Same value, no receipt for these bytes.
    assert oracle.execute(SandboxRequest(ORACLE_COMMAND)).exit_code == 1
    def corrupt_evidence(observed):
        raise ValueError("corrupt evidence")
    broken = HostOracleSandbox(Commands(), {"limit": 12}, corrupt_evidence)
    with pytest.raises(ValueError, match="corrupt evidence"):
        broken.execute(SandboxRequest(ORACLE_COMMAND))


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "unsupported_first", "corrupt_first", "missing_first", "repair_first"])
async def test_real_workflow_verifies_every_answer_and_reports_unsupported_earlier_writes(tmp_path, monkeypatch, defect):
    from evals.continuity_cases import decisive_sources

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    trial.fixture["permitted_paths"] = ["answer.json", "answers/first.json"]
    trial.fixture["answers"] = [{"path": path, "expected": trial.fixture["expected"], "checkpoint": 0,
                                "required": [read.model_dump() for read in decisive_sources(trial.fixture)]}
                               for path in trial.fixture["permitted_paths"]]
    calls = 0
    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            code = "import json, tomllib\n"
            if defect in {"unsupported_first", "repair_first"}:
                code += "assert agent.fs.write('answers/first.json', '{\"port\":8400,\"protocol\":\"https\"}')['status'] == 'ok'\n"
            code += (
                "r = agent.fs.read('config/route_00.toml', offset=12, limit=2)\nassert r['status'] == 'ok'\n"
                "s = tomllib.loads(r['data']['text'])\n"
                "answer = json.dumps({'port': s['active_port'], 'protocol': s['protocol']})\n"
            )
            if defect not in {"unsupported_first", "missing_first"}:
                code += "assert agent.fs.write('answers/first.json', answer)['status'] == 'ok'\n"
            code += "assert agent.fs.write('answer.json', answer)['status'] == 'ok'\n"
            if defect == "corrupt_first":
                code += "assert agent.fs.write('answers/first.json', '{}')['status'] == 'ok'\n"
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="answers", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify requested artifacts."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    accepted = defect in {None, "repair_first"}
    assert result["accepted"] is accepted, result
    assert result["first_verification_passed"] is accepted
    assert result["answer_artifacts"]["answer.json"]["passed"]
    assert result["passed"] is (defect not in {"corrupt_first", "missing_first"})
    report = result["measurement"]["answer_contracts"]
    assert report["all_latest_supported"] is (defect not in {"unsupported_first", "missing_first"})
    assert report["all_submissions_supported"] is (defect not in {"unsupported_first", "missing_first", "repair_first"})


@pytest.mark.asyncio
async def test_verified_campaign_stops_queued_dispatch_after_failure(tmp_path, monkeypatch):
    import evals.verified_continuity as evaluation

    called = []
    async def fail(trial):
        called.append(trial.root)
        raise RuntimeError("fixture infrastructure failed")
    monkeypatch.setattr(evaluation, "run_verified_case", fail)
    rows = await evaluation.campaign(tmp_path, ["routing", "missing"], ["metadata", "findings"], 1)
    assert len(called) == 1
    assert len(rows) == 4
    assert sum(r["terminal"] == "not_started_infrastructure_gate" for r in rows) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("call_limit", "provider_error"))
async def test_workflow_swallowed_model_errors_keep_terminal_and_cost(tmp_path, monkeypatch, failure):
    import evals.verified_continuity as evaluation
    from harness.adapters.providers.codex_responses import ProviderResponseError

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    if failure == "call_limit":
        monkeypatch.setattr(evaluation, "MAX_CALLS", 0)
    async def failed(self, request, stream=False):
        raise ProviderResponseError("server_error", LlmResponse(
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=17, candidates_token_count=2),
            custom_metadata={"provider_cost_usd": .002}))
        yield
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", failed)
    result = await run_verified_case(DevelopmentContinuation(tmp_path / "trial", "routing", "findings"))
    assert result["terminal"] == failure, result
    assert not result["accepted"]
    assert result["unaccounted_model_calls"] == 0
    assert result["provider_cost_usd"] == (0 if failure == "call_limit" else .002)
