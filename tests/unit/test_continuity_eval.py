import json

import pytest

from evals.continuity import ARMS, FAMILIES, Continuation, summarize, wire_text
from harness.adapters.providers.openrouter_responses import build_openrouter_request_body


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ("visible", "recoverable", "unread"))
async def test_evidence_placement_changes_only_available_capture(tmp_path, monkeypatch, placement):
    from evals.evidence_use import EvidenceContinuation

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = EvidenceContinuation(tmp_path / placement, placement)
    try:
        await trial.prepare()
        request = await trial.request()
        body = build_openrouter_request_body(request, model="openai/gpt-5.6-luna", reasoning_effort="max")
        text = wire_text(body)
        assert "taking SAFE_VALUE" in text
        assert ("SAFE_VALUE = 11178" in text) == (placement == "visible")
        assert "Checkpoint padding." not in text
        reads = [e for e in trial.ledger.read(trial.task_id)
                 if e.payload.get("read_evidence", {}).get("path") == trial.fixture["target"]]
        assert len(reads) == 1
        assert reads[0].payload["read_evidence"]["returned_lines"] == (10 if placement == "unread" else 24)
        assert trial.measure()["reads"]["counts"].get("post_cut_reads", 0) == 0
        uri = reads[0].payload["result_artifact_uri"]
        recovered = await trial.cell(f"print(agent.shell.run('memory query --program read.recover --artifact-uri {uri}')['model_text'])")
        payload = json.loads(recovered["model_text"])["data"]
        assert ("SAFE_VALUE = 11178" in payload["text"]) == (placement != "unread")
        shapes = await trial.cell(
            "help_result = agent.help('shell.run', details=True)['shell.run']['result']\n"
            "assert 'managed_cli' in help_result\n"
            f"result = agent.shell.run('memory query --program read.recover --artifact-uri {uri}')\n"
            "assert result['status'] == result['data']['status'] == 'ok'\n"
            "body = result['data']['data']\n"
            "assert 'text' in body and 'source_coverage' in body\n"
            "descriptor = agent.state.describe('sources')\n"
            "assert descriptor['name'] == 'sources' and descriptor['type'] == 'dict'\n"
            "assert 'data' not in descriptor and 'status' not in descriptor\n"
            "try:\n"
            "    agent.state.describe('missing_binding')\n"
            "except KeyError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('missing descriptor must raise KeyError')")
        assert shapes["status"] == "ok"
    finally:
        await trial.close()


@pytest.mark.asyncio
async def test_evidence_diagnostic_stops_dispatch_on_infrastructure_failure(tmp_path, monkeypatch):
    import evals.evidence_use as evaluation

    called = []
    async def fail(root, *args, **kwargs):
        called.append(root)
        return {"passed": False, "terminal": "provider_error"}
    monkeypatch.setattr(evaluation, "run_case", fail)
    rows = await evaluation.campaign(tmp_path, 3, 1)
    assert len(called) == 1
    assert len(rows) == 9
    assert sum(r["terminal"] == "not_started_infrastructure_gate" for r in rows) == 8
    assert json.loads((tmp_path / "summary.json").read_text())["deep_swe_gate"] == "hold"


@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("variant", (0, 1))
async def test_seed_cut_recover_and_current_version_gate(tmp_path, monkeypatch, family, variant):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = Continuation(tmp_path / "trial", family, variant, "findings")
    try:
        await trial.prepare()
        request = await trial.request()
        body = build_openrouter_request_body(request, model="openai/gpt-5.6-luna", reasoning_effort="max")
        assert [tool["name"] for tool in body["tools"]] == ["execute_code"]
        assert "Checkpoint padding." not in wire_text(body)
        assert trial.cut_sequence > 0
        source_task = "fixture-prior" if family == "prior" else trial.task_id
        source_root = trial.prior_root if family == "prior" else trial.state
        from harness.evidence.ledger import open_ledger
        reads = [e for e in open_ledger(source_root, "jsonl").read(source_task)
                 if e.payload.get("read_evidence", {}).get("path") == trial.fixture["target"]]
        captured = reads[0]
        uri = captured.payload["result_artifact_uri"]
        command = f"memory query --program read.recover --artifact-uri {uri}"
        if family == "prior":
            command += " --tasks fixture-prior"
        recovered = await trial.cell(f"recovered = agent.shell.run({command!r})\nprint(recovered['model_text'])")
        assert recovered["status"] == "ok"
        assert "historical_snapshot" in recovered["model_text"]
        if family == "partial":
            coverage = captured.payload["source_coverage"]
            assert coverage == {"total_lines": 24, "whole_file": False, "next_unread_offset": 11}
            assert json.loads(recovered["model_text"])["data"]["source_coverage"] == coverage
            described = await trial.cell(f"print(agent.state.describe('sources', selector=({trial.fixture['target']!r}, 'data', 'text')))")
            assert "'whole_file': False" in described["model_text"]
            cuts = [event for event in trial.ledger.read(trial.task_id) if event.kind == "compaction.created"]
            assert '"whole_file": false' in cuts[0].payload["header"]["parts"][0]["text"]
        if family in {"mutation", "prior"}:
            # A recovered old snapshot is never permission for a stale write.
            path = trial.fixture["target"]
            denied = await trial.cell(f"print(agent.fs.edit({path!r}, old_text='VALUE', new_text='WRONG', "
                                      f"expected_sha256={captured.payload['read_evidence']['sha256']!r}))")
            assert "WRONG" not in (trial.workspace / path).read_text()
            assert "conflict" in denied["model_text"].lower() or "mismatch" in denied["model_text"].lower()
        again = await trial.request()
        next_body = build_openrouter_request_body(again, model="openai/gpt-5.6-luna", reasoning_effort="max")
        assert body["instructions"] == next_body["instructions"] and body["tools"] == next_body["tools"]
        trial.record("provider_request", wire_text(next_body))
        measurement = trial.measure()
        assert measurement["reads"]["counts"].get("post_cut_reads", 0) == 0
        assert json.loads((trial.root / "exposure.json").read_text())["snapshots"]
        if (family, variant) == ("partial", 1):
            missing = await trial.cell(
                f"tail = agent.fs.read({trial.fixture['target']!r}, offset=11, limit=14)\n"
                "assert tail['data']['offset'] == 11\n"
                f"assert 'SAFE_VALUE = {trial.fixture['expected']['value']}' in tail['data']['text']")
            assert missing["status"] == "ok"
            assert trial.measure()["reads"]["counts"]["post_cut_reads"] == 1
    finally:
        await trial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ARMS)
async def test_arm_cut_and_representation_are_real_config_changes(tmp_path, monkeypatch, arm):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = Continuation(tmp_path / arm, "navigation", 0, arm)
    try:
        await trial.prepare()
        request = await trial.request()
        text = request.model_dump_json()
        cuts = [e for e in trial.ledger.read(trial.task_id) if e.kind == "compaction.created"]
        assert bool(cuts) == (arm != "full_history")
        if arm != "full_history":
            assert "Checkpoint padding." not in text
        if arm == "findings":
            assert '"kind": "findings"' in text.replace('\\"', '"')
    finally:
        await trial.close()


def test_wire_measurement_decodes_only_public_tool_text():
    body = {"input": [{"type": "function_call_output", "output": json.dumps({"model_text": "source\nline"})},
                      {"type": "reasoning", "summary": [{"text": "private"}]}]}
    assert wire_text(body) == "source\nline"


@pytest.mark.asyncio
async def test_disabled_prior_recall_does_not_inherit_supplied_bindings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = Continuation(tmp_path / "prior", "prior", 0, "findings")
    try:
        await trial.prepare()
        await trial.close()
        from harness.core.config import parse_harness_composition
        payload = trial.composition.model_dump(mode="json")
        payload["harness"]["config"]["memory"]["prior_runs"] = False
        trial.composition = parse_harness_composition(payload)
        trial.open(prior=True)
        response = await trial.cell("print(agent.shell.run('memory query --program working_set --tasks fixture-prior')['model_text'])")
        assert json.loads(response["model_text"])["status"] == "denied"
    finally:
        await trial.close()


def test_live_gate_requires_complete_pairs_measured_benefit_and_correctness():
    rows = [{"family": family, "variant": variant, "arm": arm, "passed": True, "terminal": "fixture_completed", "provider_cost_usd": 1,
             "measurement": {"exposure": {"counts": {"post_cut_emitted_duplicate_lines": 8 if arm == "metadata" else 2}}}}
            for family in FAMILIES for variant in (0, 1) for arm in ARMS]
    assert summarize(rows)["deep_swe_gate"] == "qualified"
    assert summarize(rows[:-1])["deep_swe_gate"] == "hold"
    rows[-1]["provider_cost_usd"] = 2
    assert summarize(rows)["deep_swe_gate"] == "hold"
    rows[-1]["provider_cost_usd"] = 1
    rows[-1]["passed"] = False
    assert summarize(rows)["deep_swe_gate"] == "hold"
    rows[-1]["passed"] = True
    for row in rows:
        row["measurement"]["exposure"]["counts"] = {}
    assert summarize(rows)["deep_swe_gate"] == "hold"
    assert summarize(rows)["duplicate_line_reduction"] is None


@pytest.mark.asyncio
async def test_campaign_stops_paid_dispatch_after_infrastructure_failure(tmp_path, monkeypatch):
    import evals.continuity as evaluation
    called = []
    async def failed(root, family, variant, arm):
        called.append(root)
        return {"family": family, "variant": variant, "arm": arm, "passed": False, "terminal": "provider_error"}
    monkeypatch.setattr(evaluation, "run_case", failed)
    await evaluation.campaign(tmp_path, 2, 1, list(ARMS))
    assert len(called) == 1
    rows = json.loads((tmp_path / "results.json").read_text())
    assert len(rows) == 48
    assert sum(r["terminal"] == "not_started_infrastructure_gate" for r in rows) == 47
    assert json.loads((tmp_path / "summary.json").read_text())["deep_swe_gate"] == "hold"


@pytest.mark.asyncio
async def test_diagnostic_subset_does_not_launch_other_families(tmp_path, monkeypatch):
    import evals.continuity as evaluation
    async def completed(root, family, variant, arm):
        assert family == "partial"
        return {"family": family, "variant": variant, "arm": arm, "passed": True, "terminal": "fixture_completed"}
    monkeypatch.setattr(evaluation, "run_case", completed)
    await evaluation.campaign(tmp_path, 2, 6, list(ARMS), ("partial",))
    rows = json.loads((tmp_path / "results.json").read_text())
    assert len(rows) == 8
    assert json.loads((tmp_path / "summary.json").read_text())["deep_swe_gate"] == "hold"


@pytest.mark.asyncio
async def test_unconsumed_large_result_does_not_republish_the_same_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    trial = Continuation(tmp_path / "same-cut", "navigation", 1, "metadata")
    try:
        await trial.prepare()
        await trial.request()
        initial = [event for event in trial.ledger.read(trial.task_id) if event.kind == "compaction.created"]
        await trial.cell("queries = {}\n"
                         "for name, cmd in [('working_set', 'memory query --program working_set')] + "
                         "[(f'module_{i:02}', f'memory query --program reads.lookup --path src/module_{i:02}.py') for i in range(1,8)]:\n"
                         "    r = agent.shell.run(cmd)\n"
                         "    queries[name] = r\n"
                         "for name, r in queries.items():\n"
                         "    print(f'--- {name} ---')\n"
                         "    print(r['model_text'][:4000])")
        request = await trial.request()
        cuts = [event for event in trial.ledger.read(trial.task_id) if event.kind == "compaction.created"]
        assert len(cuts) == len(initial) == 1
        assert cuts[0].payload["header"] == initial[0].payload["header"]
        assert any(part.function_response for content in request.contents for part in content.parts or [])
    finally:
        await trial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_usage", (False, True))
async def test_failed_provider_response_is_not_a_harness_error_and_keeps_usage(tmp_path, monkeypatch, has_usage):
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from evals.continuity import run_case
    from harness.adapters.providers.codex_responses import ProviderResponseError
    from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm

    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    async def failed(self, request, stream=False):
        raise ProviderResponseError("server_error", LlmResponse(
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=31, candidates_token_count=7) if has_usage else None,
            custom_metadata={"provider_cost_usd": .001} if has_usage else None))
        yield  # preserve the provider's async-generator interface
    monkeypatch.setattr(OpenRouterResponsesLlm, "generate_content_async", failed)
    result = await run_case(tmp_path / "failed", "navigation", 0, "findings")
    assert result["terminal"] == "provider_error"
    assert result["provider_reason"] == "server_error"
    assert result["model_calls"] == 1 and result["unaccounted_model_calls"] == 0
    assert result["input_tokens"] == (31 if has_usage else 0)
    assert result["provider_cost_usd"] == (.001 if has_usage else 0)
    assert result["usage_missing_calls"] == result["cost_missing_calls"] == int(not has_usage)
