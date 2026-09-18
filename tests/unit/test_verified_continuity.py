import json
import os
import shlex

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from evals.continuity import Continuation
from evals.continuity_cases import CASES, DevelopmentContinuation
from evals.continuity_oracle import ORACLE_COMMAND, HostOracleSandbox
from evals.verified_continuity import _live_worker_integrity, run_verified_case
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm
from harness.evidence.state import EventKind
from harness.execution.sandbox import SandboxRequest, SandboxResult


@pytest.mark.asyncio
async def test_root_review_control_survives_small_section_target_in_provider_request(tmp_path, monkeypatch):
    from harness.adapters.providers.openrouter_responses import build_openrouter_request_body
    from harness.evidence.state import rebuild_ledger

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    trial.fixture["goal"] += " Preserve complete source coverage and the original requested behavior." * 20
    calls, reviews, prefixes, navigation_packets, read_counts = 0, [], [], [], []
    retained_access = None

    async def solve(self, request, stream=False):
        nonlocal calls, retained_access
        calls += 1
        body = build_openrouter_request_body(request, model=self.model, reasoning_effort="max")
        prefixes.append(body.get("instructions"))
        for item in body["input"]:
            for content in item.get("content", []):
                text = content.get("text", "")
                if text.startswith("## TASK\n"):
                    task, _ = json.JSONDecoder().raw_decode(text.removeprefix("## TASK\n"))
                    if task["phase"] == "review":
                        ledger = rebuild_ledger(trial.events.read(trial.task_id))
                        assert task["next_action"] == ledger.next_action
                        assert "Do not repeat broad exploration." in task["next_action"]
                        assert task["goal"] == trial.fixture["goal"]
                        # This development fixture predates the root request and
                        # seeds its ledger separately; preserve that exact input.
                        assert task["verification_requirements"] == ledger.verification_requirements
                        assert task["permitted_paths"] == ledger.permitted_paths
                        reviews.append(task)
                        assert "## EVIDENCE NAVIGATION\n" in text
                        navigation = text.split("## EVIDENCE NAVIGATION\n", 1)[1].split("\n\n## ", 1)[0]
                        required, advisory = navigation.split("\nAdvisory memory (not execution authority):\n")
                        metadata = json.loads(required.removeprefix("Required continuation metadata:\n"))
                        assert metadata["navigation"]["parameters"]["phase"] == "review"
                        entries = json.loads(advisory)["entries"]
                        retained = next(e["value"] for e in entries if e["kind"] == "live_bindings"
                                        and e["value"].get("content_expression") == "reads[0]['data']['text']")
                        assert retained["read_reference"]["path"] == "config/route_00.toml"
                        assert retained["read_reference"]["artifact_uri"].startswith("artifact://sha256/")
                        retained_access = retained["content_expression"]
                        assert "value_fingerprint" not in retained and "inspect_expression" not in retained
                        navigation_packets.append(navigation)
                        read_counts.append(sum(bool(e.payload.get("read_evidence")) for e in trial.events.read(trial.task_id)))
        if calls == 1:
            code = ("import json, tomllib\nreads = agent.parallel([{'operation':'fs.read','arguments':{'path':'config/route_00.toml'}}])\n"
                    "source = tomllib.loads(reads[0]['data']['text'])['service']\n"
                    "agent.fs.write('answer.json', json.dumps({'port': source['active_port'], 'protocol': source['protocol']}))")
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="solve", args={"code": code}))
        elif calls == 3:
            assert retained_access is not None
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="review-reuse", args={
                "code": f"review_text = {retained_access}\n"
                        "review_source = tomllib.loads(review_text)['service']\n"
                        "assert review_source == source\n"
                        "print(review_source['protocol'])"}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify the source-backed answer."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["terminal"] == "verified_completion", result
    assert result["first_verification_passed"] and not result["unresolved_execution"]
    assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    assert reviews and all(prefix == prefixes[0] for prefix in prefixes)
    assert len(navigation_packets) >= 2 and len(set(navigation_packets)) == 1
    assert len(set(read_counts)) == 1  # Scripted reuse exercise, not a live-model efficiency claim.


@pytest.mark.asyncio
@pytest.mark.parametrize("same_cell", [False, True])
async def test_worker_loss_notice_supplies_direct_recovery_for_verified_answer(tmp_path, monkeypatch, same_cell):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0
    recovery_handles = []
    initial_reads = 0

    async def solve(self, request, stream=False):
        nonlocal calls, initial_reads
        calls += 1
        if calls == 1:
            initial_reads = sum(bool(e.payload.get("read_evidence")) for e in trial.events.read(trial.task_id))
            code = "reads = agent.parallel([{'operation':'fs.read','arguments':{'path':'config/route_00.toml'}}])"
            if same_cell:
                code += "\nunfinished_result = 999\n1 / 0"
        elif calls == 2 and not same_cell:
            code = "unfinished_result = 999\n1 / 0"
        elif calls == 3 - int(same_cell):
            failure = next(part.function_response.response for content in reversed(request.contents)
                           for part in content.parts or () if part.function_response
                           and part.function_response.response.get("status") == "error")
            assert failure["kernel"]["live"] is False and failure["effect"] == ("observed" if same_cell else "none")
            notice, _ = json.JSONDecoder().raw_decode(failure["model_text"].split(
                "State updates (advisory; sources historical):\n", 1)[1])
            assert notice["program"] == "ptc_state_updates@4"
            handle = next(row for row in notice["entries"] if row.get("historical_read", {}).get("path") == "config/route_00.toml")
            recovery_handles.append(handle)
            code = (
                "import json, tomllib\n"
                f"page = {handle['recover_expression']}\n"
                + notice['decode_completed_read']['then_python'] + "\n"
                "assert saved_result['status'] == 'ok' and source_text is not None\n"
                "source = tomllib.loads(source_text)['service']\n"
                "agent.fs.write('answer.json', json.dumps({'port': source['active_port'], 'protocol': source['protocol']}))"
            )
        else:
            code = None
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{calls}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify the recovered source-backed answer."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert recovery_handles and result["terminal"] == "verified_completion", result
    assert result["first_verification_passed"] and not result["unresolved_execution"]
    assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    # The fixture seeds partial captures before the model starts. Only its first
    # submitted cell acquires a complete read; recovery/review add no source reads.
    assert sum(bool(e.payload.get("read_evidence")) for e in trial.events.read(trial.task_id)) == initial_reads + 1


@pytest.mark.asyncio
async def test_real_required_packet_overflow_stops_before_provider_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.fixture["goal"] = "Required task constraint. " * 350

    async def unexpected_provider(*args, **kwargs):
        raise AssertionError("an incomplete task must not reach the provider")
        yield

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", unexpected_provider)
    result = await run_verified_case(trial)
    assert result["terminal"] == "context_control_budget_exceeded", result
    assert not result["accepted"] and not result["verification_reports"]
    assert result["model_calls"] == result["wire_attempts"] == result["provider_cost_usd"] == 0
    assert "measurement_error" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ("no_recall", "findings"))
@pytest.mark.parametrize("budget", (True, False, "context"))
async def test_pre_cut_callback_failure_retains_terminal_and_measurements(tmp_path, monkeypatch, arm, budget):
    from evals.learned_continuity import LearnedContinuation
    from harness.core.context.compiler import ContextBudgetExceeded
    from harness.evidence.telemetry.adk_plugin import HarnessMetricsPlugin, TaskInputBudgetExceeded

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LearnedContinuation(tmp_path / "trial", "qualification_routes_2", arm)

    async def fail_before_dispatch(self, **kwargs):
        # ADK wraps plugin exceptions in RuntimeError, as in the v18 live exit.
        if budget == "context":
            raise ContextBudgetExceeded(3000, 2000)
        if budget:
            raise TaskInputBudgetExceeded("bounded fixture reservation")
        raise RuntimeError("unrelated callback failure")

    async def unexpected_provider(*args, **kwargs):
        raise AssertionError("provider must not be called after callback rejection")
        yield  # Keep the provider's async-generator interface.

    monkeypatch.setattr(HarnessMetricsPlugin, "before_model_callback", fail_before_dispatch)
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", unexpected_provider)
    result = await run_verified_case(trial)
    assert result["terminal"] == ("context_control_budget_exceeded" if budget == "context" else
                                  "task_input_budget_exhausted" if budget else "harness_or_fixture_error")
    assert not result["accepted"] and not result["checkpoint_exercised"]
    assert result["published_checkpoint_sequences"] == []
    assert "measurement_error" not in result
    assert result["measurement"]["checkpoint_intervals"] == []
    assert all(result[key] == 0 for key in (
        "model_calls", "provider_cost_usd", "unaccounted_model_calls", "cost_missing_calls", "wire_attempts"))


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
@pytest.mark.parametrize("rejection", ("malformed", "unauthorized", "invalid_page", "search_unavailable", "search_invalid"))
async def test_rejected_readonly_operation_can_recover_and_complete_from_supported_evidence(tmp_path, monkeypatch, rejection):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = DevelopmentContinuation(tmp_path / "trial", "missing", "findings")
    trial.command_image = os.getenv("SKEIN_EVAL_DOCKER_IMAGE")
    calls = 0

    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            bad = {
                "malformed": "agent.artifacts.load('artifact://sha256/abc...')",
                "unauthorized": "agent.artifacts.load('artifact://sha256/' + '0' * 64)",
                "invalid_page": "agent.artifacts.load(r['read_reference']['artifact_uri'], offset=-1)",
                "search_unavailable": "agent.shell.run('search grep --pattern route --path config/route_00.toml')",
                "search_invalid": "agent.shell.run('search grep --pattern x | echo unsafe')",
            }[rejection]
            code = (
                "import tomllib\nr = agent.fs.read('config/route_00.toml')\n"
                f"rejected = {bad}\nassert rejected['status'] != 'ok'\n"
                "items = agent.artifacts.list()['data']['artifacts']\n"
                "uri = next(item['uri'] for item in items if item['uri'] == r['read_reference']['artifact_uri'])\n"
                "loaded = agent.artifacts.load(uri)\nassert loaded['status'] == 'ok'\n"
                "source = tomllib.loads(json.loads(loaded['data']['text'])['data']['text'])['service']\n"
                "assert agent.fs.write('answer.json', json.dumps({'port': source['active_port'], 'protocol': source['protocol']}))['status'] == 'ok'\n"
            )
            part = types.Part(function_call=types.FunctionCall(name="execute_code", id="solve", args={"code": code}))
        else:
            part = types.Part(text=json.dumps({"status": "verify", "message": "Verify the recovered source-backed answer."}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["terminal"] == "verified_completion", result
    assert result["first_verification_passed"] and not result["unresolved_execution"]
    assert result["measurement"]["answer_evidence"]["all_answers_source_available"]
    assert not result.get("measurement_error")


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
@pytest.mark.parametrize("diagnostic", [False, True])
async def test_verified_campaign_stops_queued_dispatch_after_failure(tmp_path, monkeypatch, diagnostic):
    import evals.verified_continuity as evaluation

    called = []
    async def fail(trial):
        called.append(trial.root)
        raise RuntimeError("fixture infrastructure failed")
    monkeypatch.setattr(evaluation, "run_verified_case", fail)
    rows = await evaluation.campaign(tmp_path, ["routing", "missing"], ["metadata", "findings"], 1,
                                     diagnostic=diagnostic)
    assert len(called) == 1
    assert len(rows) == 4
    assert sum(r["terminal"] == "not_started_infrastructure_gate" for r in rows) == 3
    assert all(row["diagnostic_reuse"] is diagnostic for row in rows)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["screen_scope"] == ("diagnostic_reuse" if diagnostic else "development_or_mixed")
    assert summary["promotion"] == "hold"


def test_diagnostic_manifest_freezes_reuse_label_without_provider_dispatch(tmp_path, monkeypatch):
    import sys

    import evals.verified_continuity as evaluation
    from evals.transfer_continuity import TRANSFER_CASES

    output = tmp_path / "dry"
    monkeypatch.setattr(sys, "argv", ["verified_continuity", "--output", str(output), "--diagnostic",
                                     "--cases", *TRANSFER_CASES, "--arms", "no_recall", "findings"])
    monkeypatch.setattr(evaluation, "dotenv_value", lambda *args: pytest.fail("dry run must not read a credential"))
    evaluation.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["version"] == "verified-continuity-v25" and manifest["diagnostic_reuse"] is True
    assert manifest["planned_episodes"] == 6 and manifest["max_model_calls"] == 24
    assert manifest["input_budget"] == 350_000 and manifest["promotion"] is False
    assert not (output / "results.json").exists()


def test_live_worker_manifest_is_isolated_and_bounded(tmp_path, monkeypatch):
    import sys

    import evals.verified_continuity as evaluation

    output = tmp_path / "dry"
    monkeypatch.setattr(sys, "argv", ["verified_continuity", "--output", str(output),
                                     "--cases", "live_worker_repository", "live_worker_config",
                                     "live_worker_missing_range", "--arms", "baseline", "locator"])
    monkeypatch.setattr(evaluation, "dotenv_value", lambda *args: pytest.fail("dry run must not read a credential"))
    evaluation.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["version"] == "verified-continuity-v25"
    assert manifest["planned_episodes"] == 6 and manifest["live_worker_contract"]["iteration_budget"] == 4
    assert "on_demand" in manifest["live_worker_contract"]
    assert manifest["live_worker_contract"]["excluded"] == [
        "context compaction", "worker loss", "working notes", "prior recall"]


def test_live_worker_integrity_rejects_failed_cell_and_epoch_change():
    class Event:
        def __init__(self, sequence, kind, epoch=None, *, preserved=False):
            self.sequence, self.kind = sequence, kind
            self.payload = ({"kernel_epoch": epoch} if epoch else {}) | {
                "exception": {"state_preserved": preserved}}

    clean = [Event(1, EventKind.REPL_CELL_SUBMITTED, "epoch-1"),
             Event(2, EventKind.REPL_CELL_COMPLETED, "epoch-1")]
    assert _live_worker_integrity(clean)["passed"]
    rejected = [*clean, Event(3, EventKind.REPL_CELL_FAILED, "epoch-1", preserved=True)]
    assert _live_worker_integrity(rejected)["passed"]
    broken = [*clean, Event(3, EventKind.REPL_CELL_FAILED, "epoch-1"),
              Event(4, EventKind.REPL_CELL_SUBMITTED, "epoch-2")]
    report = _live_worker_integrity(broken)
    assert not report["passed"] and report["kernel_epochs"] == ["epoch-1", "epoch-2"]
    assert report["disruptive_cell_sequences"] == [3]


@pytest.mark.asyncio
async def test_live_worker_control_reuses_retained_value_and_reads_only_missing_range(tmp_path, monkeypatch):
    from evals.live_worker_reuse import LiveWorkerContinuation

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = LiveWorkerContinuation(tmp_path / "trial", "live_worker_missing_range", "locator")
    tail_offset = len(trial.fixture["files"]["settings.toml"].splitlines()) - 2
    calls = 0

    async def solve(self, request, stream=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            code = (
                "import tomllib\n"
                "settings_head = agent.fs.read('settings.toml', offset=1, limit=4)\n"
                "dispatch_result = agent.fs.read('dispatch.toml')\n"
                "assert settings_head['status'] == dispatch_result['status'] == 'ok'\n"
                "print('LEARNING_COMPLETE')"
            )
        elif calls == 2:
            code = "print('CHECKPOINT_READY_1')"
        elif calls == 3:
            code = (
                "import json\n"
                f"settings_tail = agent.fs.read('settings.toml', offset={tail_offset}, limit=3)\n"
                "assert settings_tail['status'] == 'ok'\n"
                "dispatch = tomllib.loads(dispatch_result['data']['text'])['service']\n"
                "current = tomllib.loads(settings_tail['data']['text'])['current']\n"
                "answer = {'total_quota': dispatch['workers'] * current['quota'], 'enabled': current['enabled']}\n"
                "assert agent.fs.write('answer.json', json.dumps(answer))['status'] == 'ok'"
            )
        else:
            code = None
        part = (types.Part(function_call=types.FunctionCall(name="execute_code", id=f"step-{calls}", args={"code": code}))
                if code else types.Part(text=json.dumps({"status": "verify", "message": "Verify the source-backed answer."})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
                          custom_metadata={"provider_cost_usd": .001})

    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", solve)
    result = await run_verified_case(trial)
    assert result["terminal"] == "verified_completion", result
    assert result["first_verification_passed"] and result["checkpoint_exercised"]
    assert result["live_worker_integrity"]["passed"]
    assert not result["published_checkpoint_sequences"] and len(result["answer_boundary_sequences"]) == 1
    assert result["measurement"]["answer_contracts"]["boundary_kind"] == "evaluation.learning_checkpoint"
    reads = [e.payload["read_evidence"] for e in trial.ledger.read(trial.task_id)
             if e.kind == "capability.completed" and e.payload.get("operation") == "fs.read"]
    assert [read["path"] for read in reads].count("dispatch.toml") == 1
    assert [(read["offset"], read["returned_lines"]) for read in reads if read["path"] == "settings.toml"] == [(1, 4), (tail_offset, 3)]


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
