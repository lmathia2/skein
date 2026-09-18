"""Bounded, seeded continuations through production PTC/memory and OpenRouter.

These test recovery/use of supplied public findings, not autonomous checkpoint
quality or DeepSWE reward. No provider calls without --live. No default changes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
from google.adk.agents import LlmAgent
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.genai import types

from app.agent.factory import build_harness
from evals.memory_audit import audit_emissions, audit_reads
from evals.prior_evidence import PriorEvidence, PriorRun
from evals.runner import _atomic_write
from harness.adapters.adk.context import ContextWindowPlugin
from harness.adapters.providers.codex_responses import ProviderResponseError
from harness.adapters.providers.openrouter_responses import OpenRouterResponsesLlm
from harness.core.agent import HarnessRegistry
from harness.core.config import RuntimeBindings, load_harness_composition, parse_harness_composition
from harness.core.models import TaskLedger, TaskRequest
from harness.evidence.ledger import LedgerBackedEventStore, open_ledger
from harness.evidence.memory.models import ReadEvidence
from harness.evidence.state import EventKind, JsonlEventStore
from harness.execution.safety import SecretRedactor
from scripts.run_harbor_eval import dotenv_value

PROFILE = Path(__file__).resolve().parents[1] / "harness/core/config/profiles/notebook-ptc-jsonl.yaml"
ARMS = ("full_history", "metadata", "described", "findings")
FAMILIES = ("navigation", "partial", "mutation", "worker", "rejected", "prior")
MODEL = "openai/gpt-5.6-luna"
MAX_CALLS = 12
INPUT_BUDGET = 200_000


def composition_for(arm: str):
    if arm not in ARMS:
        raise ValueError("unknown continuity arm")
    payload = load_harness_composition(PROFILE).model_dump(mode="json")
    config = payload["harness"]["config"]
    config["models"]["coding"] = {
        "provider": "openrouter", "name": MODEL, "reasoning": "max",
        "api_key": {"env": "OPENROUTER_API_KEY"},
    }
    config["agents"]["coding_worker"]["generation"]["max_output_tokens"] = 8192
    config["notebook_ptc"].update(emit_state_updates=arm in {"described", "findings"})
    config["memory"].update(working_notes=True, prior_runs=True, context_programs={"mode": "active"})
    # Controlled forced checkpoint only: identical compacted-arm budgets/cut input.
    # Full DeepSWE must use provider-calibrated phase-boundary thresholds instead.
    config["context"].update(
        window_management=arm != "full_history", continuity_representation=arm if arm != "full_history" else "metadata",
        reconstruction="fresh" if arm != "full_history" else "handoff_tail", compaction_timing="immediate", work_packet_tokens=6000,
        max_context_tokens=1_050_000, compaction_tokens=4000, ledger_tokens=600,
        recent_event_tokens=0, steering_tokens=200, max_task_input_tokens=INPUT_BUDGET,
    )
    return parse_harness_composition(payload)


def fixture(family: str, variant: int) -> dict[str, Any]:
    if family not in FAMILIES or variant not in (0, 1):
        raise ValueError("unknown fixture")
    files = {}
    for index in range(16):
        value = 10000 + FAMILIES.index(family) * 1000 + variant * 100 + index
        lines = [f"# {family}-{variant} module {index:02}: line {line:02} public fixture evidence" for line in range(1, 25)]
        lines[2] = f"ROUTE_LABEL = '{family}-{variant}-route-{index:02}'"
        lines[4] = f"VALUE = {value}  # {family}-{variant} module {index:02} primary value"
        lines[17] = f"SAFE_VALUE = {value + 77}  # {family}-{variant} module {index:02} validated alternative"
        files[f"src/module_{index:02}.py"] = "\n".join(lines) + "\n"
    target = f"src/module_{variant:02}.py"
    expected = 10000 + FAMILIES.index(family) * 1000 + variant * 100 + variant
    key = "VALUE"
    if family == "rejected" or (family == "partial" and variant == 1):
        key, expected = "SAFE_VALUE", expected + 77
    if family in {"mutation", "prior"}:
        expected += 500
    label = f"{family}-{variant}-route-{variant:02}"
    goal = (f"Continue the checkpoint. Find the module labelled {label!r}. Write answer.json containing exactly "
            f'{{"value": <integer>}}, taking {key} from the relevant source. '
            "Use retained evidence when its version and coverage apply; recover historical evidence without re-reading "
            "unchanged files where possible. Do not copy entire source listings into output. Finish the artifact, not a plan.")
    if family in {"mutation", "prior"}:
        goal += " The workspace changed since the checkpoint; use the CURRENT source value, not the historical one."
    if family == "worker":
        goal += " The Python worker may have restarted; check its actual state before relying on an old binding."
    if family == "rejected":
        goal += " The earlier primary-value approach was rejected. Preserve the recorded correction."
    return {"family": family, "variant": variant, "files": files, "target": target,
            "expected": {"value": expected}, "goal": goal, "key": key}


def wire_text(body: dict[str, Any]) -> str:
    """Decode public message/tool-output text, not private reasoning or headers."""
    texts = []
    for item in body.get("input", []):
        if item.get("type") == "function_call_output":
            output = item.get("output", "")
            try:
                decoded = json.loads(output)
            except (ValueError, TypeError):
                decoded = None
            texts.append(str(decoded.get("model_text", output)) if isinstance(decoded, dict) else str(output))
        else:
            for part in item.get("content", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    return "\n".join(texts)


class Continuation:
    """One isolated fixture; shared with deterministic tests of the real wiring."""

    def __init__(self, root: Path, family: str, variant: int, arm: str):
        self.root, self.arm = root, arm
        self.fixture = fixture(family, variant)
        self.seed_read_limit = 10 if family == "partial" else 24
        self.workspace, self.state = root / "workspace", root / "state"
        self.composition = composition_for(arm)
        self.task_id = f"{family}-{variant}-{arm}"
        self.records: list[dict[str, Any]] = []
        self.history: list[types.Content] = []
        self.calls = 0
        self.assembly: Any = None
        self.registry: HarnessRegistry | None = None
        self.command_image: str | None = None
        self.cut_sequence = 0
        self.seed_cells = 0
        self.seed_checkpoint = True
        self.source_requirements: list[ReadEvidence] | None = None
        self.prior_root: Path | None = None
        self.prior_evidence: PriorEvidence | None = None
        self.prior_run: PriorRun | None = None
        self.redactor = SecretRedactor(known_secrets=(os.environ.get("OPENROUTER_API_KEY", ""),))

    def open(self, *, prior: bool = False) -> None:
        if prior and self.prior_root is None:
            raise ValueError("authorized prior state root is required")
        self.ledger = open_ledger(self.state, "jsonl")
        self.events = LedgerBackedEventStore(JsonlEventStore(self.state / "events"), self.ledger)
        if not self.events.read(self.task_id):
            task = TaskLedger.from_request(TaskRequest(goal=self.fixture["goal"]), task_id=self.task_id,
                                          workspace_id="continuity-fixture", base_revision="fixture-v1")
            self.events.append(self.task_id, EventKind.TASK_CREATED, {"ledger": task.model_dump(mode="json")})
        self.bindings = RuntimeBindings(
            workspace=self.workspace, state_root=self.state, task_id=self.task_id,
            conversation_id="continuity-fixture", user_id="fixture-owner",
            prior_task_ids=("fixture-prior",) if prior else (),
            prior_state_roots=(self.prior_root,) if prior and self.prior_root is not None else (),
        )
        if self.prior_run is not None:
            self.prior_evidence = PriorEvidence(self.bindings, (self.prior_run,))
        self.assembly = build_harness(self.composition, self.bindings, registry=self.registry)
        self.agent = cast(LlmAgent, self.assembly.agents["coding_worker"])
        self.tool: Any = self.agent.tools[0]
        self.context = SimpleNamespace(agent_name="coding_worker", invocation_id="continuation",
                                       function_call_id="", state={"task_id": self.task_id, "task_phase": "implement"})
        self.plugin = next(p for p in self.assembly.app.plugins if isinstance(p, ContextWindowPlugin))

    def record(self, route: str, text: str) -> None:
        identity = f"exposure-{len(self.records)}"
        event = self.ledger.append(task_id=self.task_id, source="evaluation", source_id=identity,
                                   kind="evaluation.exposure", payload={"route": route})
        self.records.append({"id": identity, "sequence": event.sequence, "route": route,
                             "text": self.redactor.redact_text(text)})

    async def cell(self, code: str, *, call_id: str | None = None, seed: bool = False) -> dict[str, Any]:
        self.calls += 1
        self.context.function_call_id = call_id or f"fixture-cell-{self.calls}"
        result = await self.tool(code=code, tool_context=self.context)
        if seed:
            self.seed_cells += 1
        if call_id is None:
            self.history.append(types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="execute_code", id=self.context.function_call_id, args={"code": code}))]))
        self.history.append(types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            name="execute_code", id=self.context.function_call_id, response=result))]))
        operations = {event.payload.get("operation") for event in self.ledger.read(self.task_id)
                      if event.kind == "capability.requested" and event.payload.get("attempt_id") == result.get("attempt_id")}
        route = "artifact_output" if operations and all(str(op).startswith("artifacts.") for op in operations) else (
            "shell_output" if operations == {"shell.run"} else "ptc_output")
        self.record(route, str(result.get("model_text", "")))
        if seed and result["status"] != "ok":
            raise RuntimeError(f"fixture setup failed: {result}")
        return result

    async def prepare(self) -> None:
        if self.root.exists():
            raise ValueError("refusing to overwrite a trial directory")
        self.workspace.mkdir(parents=True)
        for path, content in self.fixture["files"].items():
            _atomic_write(self.workspace / path, content)
        is_prior = self.fixture["family"] == "prior"
        current_task, current_state = self.task_id, self.state
        if is_prior:
            self.task_id, self.state = "fixture-prior", self.root / "prior-state"
            self.prior_root = self.state
        self.open()
        self.history = [types.Content(role="user", parts=[types.Part.from_text(text=self.fixture["goal"])])]
        paths = list(self.fixture["files"])
        target = self.fixture["target"]
        limit = self.seed_read_limit
        await self.cell(
            f"sources = {{p: agent.fs.read(p, limit={limit}) for p in {paths!r}}}\n"
            f"agent.state.annotate('sources', 'Relevant module for the continuation; captured source version', "
            f"selector=({target!r}, 'data', 'text'))\n"
            f"print(sources[{target!r}]['data']['text'])", seed=True)
        finding = {"id": "target", "kind": "observation", "text": f"The relevant route is implemented in {target}.",
                   "related_paths": [target], "task_links": ["continue"]}
        entries_code = f"entries = [{finding!r}]\nentries[0]['evidence_refs'] = [sources[{target!r}]['read_reference']['artifact_uri']]\n"
        if self.fixture["family"] == "rejected":
            entries_code += ("entries.append({'id': 'reject-primary', 'kind': 'rejected_approach', "
                             "'text': 'The primary VALUE approach failed validation; use SAFE_VALUE.', "
                             f"'related_paths': [{target!r}]}})\n")
        await self.cell(entries_code + "import json, shlex\n"
                        "checkpoint = agent.shell.run('memory note write --text ' + shlex.quote('Continue the fixture objective; detailed findings retained.') "
                        "+ ' --expected-version 0 --operation-id fixture-note --entries ' + shlex.quote(json.dumps(entries)))\n"
                        "assert checkpoint['status'] == 'ok'\nprint(checkpoint['model_text'])", seed=True)
        if self.fixture["family"] in {"mutation", "prior"}:
            old = self.fixture["files"][target]
            new = old.replace(f"VALUE = {self.fixture['expected']['value'] - 500} ", f"VALUE = {self.fixture['expected']['value']} ", 1)
            if self.fixture["family"] == "mutation" and self.fixture["variant"] == 0:
                await self.cell(f"print(agent.fs.edit({target!r}, old_text={old!r}, new_text={new!r}, "
                                f"expected_sha256={hashlib.sha256(old.encode()).hexdigest()!r}))", seed=True)
            else:
                _atomic_write(self.workspace / target, new)  # explicitly injected external change
        if is_prior:
            await self.close()
            self.task_id, self.state = current_task, current_state
            self.records, self.history = [], [types.Content(role="user", parts=[types.Part.from_text(text=self.fixture["goal"])])]
            self.open(prior=True)
            await self.cell("checkpoint = agent.shell.run('memory note write --text "
                            "\"Consult authorized prior findings, then validate the current workspace.\" "
                            "--expected-version 0 --operation-id current-note')\nassert checkpoint['status'] == 'ok'", seed=True)
        if self.fixture["family"] == "worker" and self.fixture["variant"] == 1:
            await self.close()
            self.open()
        # Evidence is outside the exact tail in every compacted arm. The repeated
        # neutral marker is checkpoint pressure, never source or grading evidence.
        self.history.append(types.Content(role="user", parts=[types.Part.from_text(text="Checkpoint padding. " * 3000)]))
        self.history.append(types.Content(role="user", parts=[types.Part.from_text(text=self.fixture["goal"])]))
        _atomic_write(self.root / "config.json", self.composition.model_dump_json(indent=2))
        _atomic_write(self.root / "fixture.json", json.dumps(self.fixture, indent=2))

    async def request(self) -> LlmRequest:
        declaration = FunctionTool(self.tool)._get_declaration()
        assert declaration is not None
        request = LlmRequest(model=MODEL, contents=list(self.history), config=types.GenerateContentConfig(
            system_instruction=self.agent.static_instruction, max_output_tokens=8192,
            tools=[types.Tool(function_declarations=[declaration])],
        ))
        await self.plugin.before_model_callback(callback_context=self.context, llm_request=request)
        callback: Any = self.agent.before_model_callback
        if callback is not None and await callback(self.context, request) is not None:
            raise RuntimeError("production worker yielded before provider dispatch")
        for event in self.ledger.read(self.task_id):
            if event.kind == EventKind.COMPACTION_CREATED and not self.cut_sequence:
                self.cut_sequence = event.sequence
        if not self.cut_sequence:
            self.cut_sequence = self.ledger.read(self.task_id)[-1].sequence  # reference checkpoint, not a cut
        return request

    async def close(self) -> None:
        if self.assembly and self.assembly.close:
            value = self.assembly.close()
            if inspect.isawaitable(value):
                await value
        self.assembly = None

    def measure(self) -> dict[str, Any]:
        events = self.ledger.read(self.task_id)
        snapshots = []
        roots = [self.state, *([self.prior_root] if self.prior_root else [])]
        for state in roots:
            source = open_ledger(state, "jsonl")
            for event in source.read("fixture-prior" if state == self.prior_root else self.task_id):
                evidence, uri = event.payload.get("read_evidence"), event.payload.get("result_artifact_uri")
                if evidence and uri:
                    artifact = state / "artifacts" / "sha256" / uri.rsplit("/", 1)[-1]
                    raw = artifact.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != uri.rsplit("/", 1)[-1]:
                        raise ValueError("captured source artifact is corrupt")
                    payload = json.loads(raw)
                    snapshots.append({"read_evidence": evidence, "text": payload["data"]["text"]})
        exposure = {"snapshots": snapshots, "records": self.records}
        _atomic_write(self.root / "exposure.json", json.dumps(exposure, ensure_ascii=False))
        measured: dict[str, Any] = {"reads": audit_reads(events, cut_sequence=self.cut_sequence),
                    "exposure": audit_emissions(snapshots, self.records, cut_sequence=self.cut_sequence)}
        if initial_ranges := self.fixture.get("initial_ranges"):
            first_cut = next((e.sequence for e in events if e.kind == EventKind.COMPACTION_CREATED), None)
            preparation = audit_reads([e for e in events if first_cut is None or e.sequence < first_cut], cut_sequence=0)
            overshot = [r for r in preparation["reads"] if r["path"] in initial_ranges
                        and not (initial_ranges[r["path"]][0] <= r["offset"]
                                 and r["offset"] + r["returned_lines"] <= sum(initial_ranges[r["path"]]))]
            opaque = sum(preparation["operations"].get(kind, 0) for kind in ("search", "unclassified_shell"))
            opaque += preparation["counts"].get("unaddressed_reads", 0)
            measured["initial_capture"] = {
                "version": "initial-capture-boundary-v1", "cut_sequence": first_cut,
                "allowed_ranges": initial_ranges, "overshot_reads": overshot, "opaque_routes": opaque,
                "respected": first_cut is not None and not overshot and opaque == 0,
                "scope": "completed fs.read ranges and classified preparation routes; not semantic model dependence",
            }
        if self.fixture.get("stages"):
            cuts = [e.sequence for e in events if e.kind == EventKind.COMPACTION_CREATED]
            measured["checkpoint_intervals"] = [{
                "cut_sequence": cut, "until_sequence": end,
                "reads": audit_reads([e for e in events if e.sequence < end], cut_sequence=cut),
                "exposure": audit_emissions(snapshots, [r for r in self.records if r["sequence"] < end], cut_sequence=cut),
            } for cut, end in zip(cuts, [*cuts[1:], events[-1].sequence + 1], strict=True)]
        return measured


def account_response(result: dict[str, Any], response: LlmResponse, estimated: int) -> int:
    """Retain reported usage on both completed and failed provider responses."""
    usage = response.usage_metadata
    if usage:
        result["input_tokens"] += usage.prompt_token_count or 0
        result["cached_input_tokens"] += usage.cached_content_token_count or 0
        result["output_tokens"] += usage.candidates_token_count or 0
        result["reasoning_tokens"] += usage.thoughts_token_count or 0
    else:
        result["usage_missing_calls"] += 1
    cost = (response.custom_metadata or {}).get("provider_cost_usd")
    result["cost_missing_calls"] += cost is None
    result["provider_cost_usd"] += cost or 0
    return (usage.prompt_token_count or estimated) if usage else estimated


async def run_case(
    root: Path, family: str, variant: int, arm: str, *, trial: Continuation | None = None,
) -> dict[str, Any]:
    trial = trial or Continuation(root, family, variant, arm)
    result: dict[str, Any] = {"family": family, "variant": variant, "arm": arm, "passed": False,
                              "terminal": "call_limit", "model_calls": 0, "input_tokens": 0,
                              "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                              "provider_cost_usd": 0.0, "usage_missing_calls": 0, "cost_missing_calls": 0}
    started = time.monotonic()
    charged_input = 0
    completed_calls = 0
    try:
        await trial.prepare()
        model = cast(OpenRouterResponsesLlm, trial.agent.model)
        async def capture(request: httpx.Request) -> None:
            body = json.loads(request.content)
            index = len(list((root / "wire").glob("*.json")))
            _atomic_write(root / "wire" / f"{index:03}.json", json.dumps(trial.redactor.redact(body)))
            trial.record("provider_request", wire_text(body))
        model._client_factory = lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=30), event_hooks={"request": [capture]})
        for _ in range(MAX_CALLS):
            request = await trial.request()
            # Reserve the outgoing request before spending; actual usage remains
            # authoritative. This fixture bound is separate from provider limits.
            estimated = len(request.model_dump_json()) // 3 + 8192
            if charged_input + estimated > INPUT_BUDGET:
                result["terminal"] = "task_input_budget_exhausted"
                break
            result["model_calls"] += 1
            result["unaccounted_model_calls"] = result["model_calls"] - completed_calls
            _atomic_write(root / "progress.json", json.dumps(result, indent=2))
            final = None
            async for response in model.generate_content_async(request):
                if not response.partial:
                    final = response
            if final is None or final.content is None:
                raise RuntimeError("provider returned no final content")
            completed_calls += 1
            result["unaccounted_model_calls"] = result["model_calls"] - completed_calls
            charged_input += account_response(result, final, estimated)
            if final.usage_metadata:
                trial.context.state["context_provider_input_tokens"] = final.usage_metadata.prompt_token_count or 0
            _atomic_write(root / "progress.json", json.dumps(result, indent=2))
            public = types.Content(role="model", parts=[p for p in final.content.parts or [] if not p.thought])
            trial.history.append(public)
            calls = [part.function_call for part in public.parts or [] if part.function_call]
            if not calls:
                result["terminal"] = "fixture_completed"
                break
            for call in calls:
                if call.name != "execute_code" or not isinstance((call.args or {}).get("code"), str):
                    raise ValueError("provider generated an invalid PTC call")
                await trial.cell((call.args or {})["code"], call_id=call.id)
        answer = trial.workspace / "answer.json"
        if answer.is_file() and not answer.is_symlink() and answer.stat().st_size < 4096:
            result["passed"] = json.loads(answer.read_text()) == trial.fixture["expected"]
        if result["terminal"] == "fixture_completed" and not result["passed"]:
            result["terminal"] = "fixture_wrong_or_missing_artifact"
    except Exception as exc:
        if isinstance(exc, ProviderResponseError):
            account_response(result, exc.response, 0)
            completed_calls += 1
            result["provider_reason"] = exc.reason
        result["terminal"] = "provider_error" if "OpenRouter" in str(exc) or isinstance(exc, (httpx.HTTPError, ProviderResponseError)) else "harness_or_fixture_error"
        result["error"] = trial.redactor.redact_text(f"{type(exc).__name__}: {exc}")[:2000]
    finally:
        await trial.close()
    if hasattr(trial, "ledger"):
        try:
            result["measurement"] = trial.measure()
        except (ValueError, KeyError, OSError) as exc:
            result["measurement_error"] = str(exc)[:1000]
    result.update(wall_seconds=time.monotonic() - started, tool_cells=trial.calls, seeded_tool_cells=trial.seed_cells,
                  unaccounted_model_calls=result["model_calls"] - completed_calls,
                  uncached_input_tokens=result["input_tokens"] - result["cached_input_tokens"], result_path=str(root / "result.json"))
    _atomic_write(root / "result.json", json.dumps(result, indent=2))
    print(json.dumps({key: result[key] for key in ("family", "variant", "arm", "passed", "terminal", "model_calls")}), flush=True)
    return result


async def campaign(output: Path, variants: int, concurrency: int, arms: list[str], families: tuple[str, ...] = FAMILIES) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    infrastructure_failed = asyncio.Event()
    async def bounded(family: str, variant: int, arm: str):
        async with semaphore:
            root = output / f"{family}-{variant}-{arm}"
            if infrastructure_failed.is_set():
                result = {"family": family, "variant": variant, "arm": arm, "passed": False,
                          "terminal": "not_started_infrastructure_gate"}
                _atomic_write(root / "result.json", json.dumps(result, indent=2))
                return result
            try:
                async with asyncio.timeout(900):
                    result = await run_case(root, family, variant, arm)
                if result["terminal"] in {"provider_error", "harness_or_fixture_error"} or "measurement_error" in result:
                    infrastructure_failed.set()
                return result
            except TimeoutError:
                progress = root / "progress.json"
                result = json.loads(progress.read_text()) if progress.exists() else {"family": family, "variant": variant, "arm": arm}
                result.update(passed=False, terminal="wall_time_limit")
                infrastructure_failed.set()
                _atomic_write(root / "result.json", json.dumps(result, indent=2))
                return result
    results = await asyncio.gather(*(bounded(family, variant, arm) for variant in range(variants)
                                     for family in families for arm in arms))
    _atomic_write(output / "results.json", json.dumps(results, indent=2))
    _atomic_write(output / "summary.json", json.dumps(summarize(results), indent=2))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Matched fixture-level gate; missing measurements never become zero benefit."""
    totals = {}
    for arm in ARMS:
        selected = [r for r in results if r["arm"] == arm]
        if not selected:
            continue
        started = sum(r["terminal"] != "not_started_infrastructure_gate" for r in selected)
        totals[arm] = {"passed": sum(r["passed"] for r in selected), "planned": len(selected),
                       "started": started, "not_started": len(selected) - started,
                       **{key: sum(r.get(key, 0) for r in selected) for key in (
                           "model_calls", "input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens",
                           "provider_cost_usd", "cost_missing_calls", "usage_missing_calls", "wall_seconds")}}
    pairs = []
    for family in FAMILIES:
        for variant in (0, 1):
            arms = {r["arm"]: r for r in results if (r["family"], r["variant"]) == (family, variant)}
            if not {"metadata", "findings"} <= arms.keys():
                continue
            reusable = family not in {"mutation", "prior"} and (family, variant) != ("partial", 1)
            pairs.append({"family": family, "variant": variant, "reusable": reusable,
                          "baseline_passed": arms["metadata"]["passed"], "treatment_passed": arms["findings"]["passed"],
                          "duplicate_lines": {arm: arms[arm]["measurement"]["exposure"]["counts"].get(
                              "post_cut_emitted_duplicate_lines", 0) if "measurement" in arms[arm] else None
                                              for arm in ("metadata", "findings")}})
    measured = [p for p in pairs if p["reusable"] and all(v is not None for v in p["duplicate_lines"].values())]
    before = sum(p["duplicate_lines"]["metadata"] for p in measured)
    after = sum(p["duplicate_lines"]["findings"] for p in measured)
    reduction = 1 - after / before if before else None
    complete = len(results) == 48 and len(pairs) == 12 and all(r["terminal"] != "not_started_infrastructure_gate" for r in results)
    priced = all(r.get("cost_missing_calls", 0) == 0 and r.get("unaccounted_model_calls", 0) == 0
                 for r in results if r["arm"] in {"metadata", "findings"})
    baseline_cost = totals.get("metadata", {}).get("provider_cost_usd", 0)
    cost_ratio = totals.get("findings", {}).get("provider_cost_usd", 0) / baseline_cost if baseline_cost and priced else None
    qualified = (complete and all(p["treatment_passed"] for p in pairs) and len(measured) == 7
                 and reduction is not None and reduction >= .25
                 and cost_ratio is not None and cost_ratio <= 1
                 and all(r["terminal"] == "fixture_completed" for r in results if r["arm"] == "findings"))
    return {"arms": totals, "pairs": pairs, "measured_reusable_pairs": len(measured),
            "duplicate_line_reduction": reduction, "treatment_cost_ratio": cost_ratio,
            "deep_swe_gate": "qualified" if qualified else "hold",
            "campaign_complete": complete, "note": "Exact-line measurement is a lower bound; missing exposure is not zero. "
            "Safety-contract tests remain an independent prerequisite; this gate never changes defaults."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--variants", type=int, choices=(1, 2), default=2)
    parser.add_argument("--concurrency", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    parser.add_argument("--families", nargs="+", choices=FAMILIES, default=list(FAMILIES))
    args = parser.parse_args()
    if len(set(args.arms)) != len(args.arms) or len(set(args.families)) != len(args.families):
        parser.error("arms and families must not contain duplicates")
    manifest = {"version": "continuity-controlled-v1", "model": MODEL, "reasoning": "max",
                "concurrency": args.concurrency, "max_model_calls": MAX_CALLS, "input_budget": INPUT_BUDGET,
                "arms": args.arms, "fixtures": [fixture(f, v) for v in range(args.variants) for f in args.families],
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "git_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"])).hexdigest(),
                "gate": "No fixture correctness regression; at least 25% fewer emitted duplicate mapped source lines "
                        "than metadata on reusable cases, without route-shifting; zero safety-contract failures.",
                "limitations": ["Seeded public findings, not autonomous note quality", "Exact-line exposure is a lower bound",
                                "Controlled forced cuts, not provider-pressure quality trials", "No default promotion"]}
    if args.output.exists():
        raise ValueError("use a fresh campaign output directory")
    _atomic_write(args.output / "manifest.json", json.dumps(manifest, indent=2))
    if not args.live:
        print(json.dumps({"manifest": str(args.output / "manifest.json"), "trials": len(manifest["fixtures"]) * len(args.arms), "live": False}))
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_value(args.dotenv, "OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is unavailable")
    os.environ["OPENROUTER_API_KEY"] = key
    asyncio.run(campaign(args.output, args.variants, args.concurrency, args.arms, tuple(args.families)))


if __name__ == "__main__":
    main()
