"""Controlled memory continuations through Skein's actual verification workflow."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from google.adk import Runner
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from app.agent.factory import default_harness_registry
from evals.continuity import (
    INPUT_BUDGET,
    MAX_CALLS,
    Continuation,
    account_response,
    wire_text,
)
from evals.continuity_oracle import (
    ORACLE_COMMAND,
    AnswerSpec,
    HostOracleSandbox,
    audit_answer_contracts,
)
from evals.memory_audit import audit_answer_evidence
from evals.repeated_continuity import REPEATED_CASES, repeated_fixture
from evals.runner import _atomic_write
from evals.validation_continuity import VALIDATION_CASES, validation_fixture
from harness.adapters.providers.codex_responses import ProviderResponseError
from harness.adapters.providers.openrouter_responses import (
    OpenRouterResponsesLlm,
    build_openrouter_request_body,
)
from harness.core.config import parse_harness_composition
from harness.core.models import TaskRequest
from harness.evidence.state import EventKind
from harness.execution.environment.local import LocalWorkspaceEnvironment
from harness.execution.environment.runtime import ExecutionRuntime, LocalRepositoryRuntime
from harness.execution.sandbox.docker import DockerSandbox
from harness.execution.sandbox.factory import create_configured_command_sandbox
from harness.execution.tools.adk_adapter import _ArtifactResolver
from scripts.run_harbor_eval import dotenv_value


class EvaluationLimit(RuntimeError):
    pass


class _MeasuredModel(BaseLlm):
    """Observe the real provider, without replacing ADK's model/tool loop."""

    _delegate: BaseLlm = PrivateAttr()
    _trial: Continuation = PrivateAttr()
    _result: dict[str, Any] = PrivateAttr()
    _charged_input: int = PrivateAttr(default=0)

    def __init__(self, delegate: BaseLlm, trial: Continuation, result: dict[str, Any]):
        super().__init__(model=delegate.model)
        self._delegate, self._trial, self._result = delegate, trial, result

    @property
    def capabilities(self):
        return self._delegate.capabilities

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        result = self._result
        if result["model_calls"] >= self._trial.fixture.get("max_model_calls", MAX_CALLS):
            result["terminal"] = "call_limit"
            raise EvaluationLimit("call_limit")
        body = build_openrouter_request_body(llm_request, model=self.model,
                                            reasoning_effort=getattr(self._delegate, "reasoning_effort", None))
        estimated = len(json.dumps(body)) // 3 + 8192
        if self._charged_input + estimated > self._trial.fixture.get("input_budget", INPUT_BUDGET):
            result["terminal"] = "task_input_budget_exhausted"
            raise EvaluationLimit("task_input_budget_exhausted")
        index = result["model_calls"]
        result["model_calls"] += 1
        result["unaccounted_model_calls"] += 1
        _atomic_write(self._trial.root / "progress.json", json.dumps(result, indent=2))
        try:
            async for response in self._delegate.generate_content_async(llm_request, stream=stream):
                if not response.partial:
                    self._charged_input += account_response(result, response, estimated)
                    result["unaccounted_model_calls"] -= 1
                    public = types.Content(role="model", parts=[
                        p for p in (response.content.parts or []) if not p.thought
                    ]) if response.content else None
                    if public and not any(p.function_call for p in public.parts or []):
                        with suppress(ValueError, RecursionError):
                            proposal = json.loads("".join(p.text or "" for p in public.parts or []))
                            if isinstance(proposal, dict):
                                result["last_proposal_status"] = proposal.get("status")
                    _atomic_write(self._trial.root / "responses" / f"{index:03}.json",
                                  json.dumps(self._trial.redactor.redact(public.model_dump(mode="json") if public else None)))
                    _atomic_write(self._trial.root / "progress.json", json.dumps(result, indent=2))
                yield response
        except Exception as exc:
            if isinstance(exc, ProviderResponseError):
                account_response(result, exc.response, 0)
                result["unaccounted_model_calls"] -= 1
            result["terminal"] = "provider_error" if isinstance(exc, (httpx.HTTPError, ProviderResponseError)) or "OpenRouter" in str(exc) else "harness_or_fixture_error"
            result["error"] = self._trial.redactor.redact_text(f"{type(exc).__name__}: {exc}")[:2000]
            raise


async def run_verified_case(trial: Continuation) -> dict[str, Any]:
    """Keep first verification, accepted completion, and final oracle result separate."""
    from evals.continuity_cases import CASES, decisive_sources

    root = trial.root
    for key, ceiling in (("max_model_calls", 24), ("input_budget", 500_000)):
        if key in trial.fixture and (type(trial.fixture[key]) is not int or not 1 <= trial.fixture[key] <= ceiling):
            raise ValueError(f"invalid bounded fixture {key}")
    required = trial.source_requirements if trial.source_requirements is not None else (
        decisive_sources(trial.fixture) if trial.fixture["family"] in CASES else [])
    specifications = [AnswerSpec.model_validate(item) for item in trial.fixture.get("answers", [])]
    if "answers" in trial.fixture:
        paths = [spec.path for spec in specifications]
        if not 1 <= len(paths) <= 16 or len(set(paths)) != len(paths) or "answer.json" not in paths:
            raise ValueError("multi-answer fixtures require unique contracts including answer.json")
        if not set(paths) <= set(trial.fixture.get("permitted_paths", ["answer.json"])):
            raise ValueError("answer contracts must be explicitly included in permitted paths")
        checkpoint_count = len(trial.fixture.get("stages", [trial.fixture]))
        if any(spec.checkpoint >= checkpoint_count for spec in specifications):
            raise ValueError("answer contract references an unavailable checkpoint")
        primary = next(spec for spec in specifications if spec.path == "answer.json")
        if json.dumps(primary.expected, sort_keys=True, allow_nan=False) != json.dumps(trial.fixture["expected"], sort_keys=True, allow_nan=False):
            raise ValueError("primary answer contract contradicts fixture expected value")
        required = primary.required
    result: dict[str, Any] = {
        "family": trial.fixture["family"], "variant": trial.fixture["variant"], "arm": trial.arm,
        "passed": False, "accepted": False, "terminal": "workflow_stopped", "model_calls": 0,
        "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
        "provider_cost_usd": 0.0, "usage_missing_calls": 0, "cost_missing_calls": 0,
        "unaccounted_model_calls": 0,
        "source_evidence_required": bool(required),
        "max_model_calls": trial.fixture.get("max_model_calls", MAX_CALLS),
        "input_budget": trial.fixture.get("input_budget", INPUT_BUDGET),
    }
    started = time.monotonic()
    oracle: HostOracleSandbox | None = None
    try:
        # The initial forced checkpoint is the intervention. Later requests use
        # the phase-boundary policy, not a 6k immediate-cut stress loop.
        composition = trial.composition.model_dump(mode="json")
        composition["harness"]["config"]["context"].update(
            compaction_timing="phase_boundary", work_packet_tokens=20_000,
            max_task_input_tokens=result["input_budget"],
        )
        trial.composition = parse_harness_composition(composition)
        def evidence_check(answer_hashes: dict[str, str]) -> bool:
            if trial.fixture.get("expect_abstention"):
                return False
            events = trial.ledger.read(trial.task_id)
            checkpoints = [e for e in events if e.kind == "evaluation.learning_checkpoint"]
            if trial.fixture.get("stages"):
                if [e.payload.get("stage") for e in checkpoints] != list(range(len(trial.fixture["stages"]))):
                    return False
                for path, digest in trial.fixture["final_source_hashes"].items():
                    source = trial.workspace / path
                    if (source.is_symlink() or not source.is_file() or source.stat().st_size > 1_000_000
                            or hashlib.sha256(source.read_bytes()).hexdigest() != digest):
                        return False
            if specifications:
                cuts = [e.sequence for e in events if e.kind == EventKind.COMPACTION_CREATED]
                if trial.fixture.get("stages") and [e.payload.get("cut_sequence") for e in checkpoints] != cuts:
                    return False
                audit = audit_answer_contracts(events, specifications, cuts)
                return bool(set(answer_hashes) == set(audit["artifacts"]) and audit["all_latest_supported"]
                            and all(report["answers"][-1].get("answer_sha256") == answer_hashes[path]
                                    for path, report in audit["artifacts"].items()))
            audit = audit_answer_evidence(events, required=required)
            answers = audit["answers"]
            return bool(answers and answers[-1]["status"] == "available"
                        and answers[-1].get("answer_sha256") == answer_hashes["answer.json"]
                        and (trial.seed_checkpoint or (trial.cut_sequence > 0
                             and answers[-1]["write_request_sequence"] > trial.cut_sequence)))

        def runtime(settings, config, known_secrets):
            nonlocal oracle
            commands = DockerSandbox(
                settings.workspace, settings.state_root / "artifacts" / "commands",
                image=trial.command_image, known_secrets=known_secrets,
                max_output_bytes=config.tools.output.max_bytes,
                environment={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
                             "GIT_CONFIG_VALUE_0": "/workspace"},
            ) if trial.command_image else create_configured_command_sandbox(
                settings.workspace, settings.state_root, config.sandbox,
                max_output_bytes=config.tools.output.max_bytes, known_secrets=known_secrets,
            )
            oracle = HostOracleSandbox(commands, trial.fixture["expected"], evidence_check if required else None,
                                       additional_answers={spec.path: spec.expected for spec in specifications
                                                           if spec.path != "answer.json"})
            return ExecutionRuntime(
                files=LocalWorkspaceEnvironment(settings.workspace),
                repository=LocalRepositoryRuntime(settings.workspace),
                commands=oracle,
            )
        trial.registry = default_harness_registry(execution_runtime_factory=runtime)
        await trial.prepare()
        _atomic_write(root / "execution-runtime.json", json.dumps({
            "ptc_worker": "guarded_local", "commands": "docker" if trial.command_image else "local",
            "image": trial.command_image, "oracle": "host_owned_virtual_test",
            "source_evidence_required": bool(required),
        }, indent=2))
        # Publish the seeded checkpoint via the production context plugin. The
        # real root workflow picks it up through its ordinary durable-state path.
        if trial.seed_checkpoint:
            continuation_config = trial.plugin.config
            try:
                trial.plugin.config = continuation_config.model_copy(update={
                    "compaction_timing": "immediate", "work_packet_tokens": 6000,
                })
                await trial.request()
            finally:
                trial.plugin.config = continuation_config
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(trial.workspace), *args], check=True, capture_output=True)
        revision = subprocess.check_output(["git", "-C", str(trial.workspace), "rev-parse", "HEAD"], text=True).strip()
        trial.events.append(trial.task_id, EventKind.LEDGER_PATCHED, {"set_fields": {"base_revision": revision}})
        request = TaskRequest(goal=trial.fixture["goal"], permitted_paths=trial.fixture.get("permitted_paths", ["answer.json"]),
                              verification_level="behavioral", max_iterations=12,
                              verification_requirements=[ORACLE_COMMAND])
        _atomic_write(root / "request.json", request.model_dump_json(indent=2))
        model = trial.agent.model
        if not isinstance(model, OpenRouterResponsesLlm):
            raise TypeError("continuation requires an instantiated provider adapter")
        async def capture(request: httpx.Request) -> None:
            body = json.loads(request.content)
            index = len(list((root / "wire").glob("*.json")))
            _atomic_write(root / "wire" / f"{index:03}.json", json.dumps(trial.redactor.redact(body)))
            trial.record("provider_request", wire_text(body))
        model._client_factory = lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=30), event_hooks={"request": [capture]})
        trial.agent.model = _MeasuredModel(model, trial, result)
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name=trial.assembly.app.name, user_id="fixture-owner")
        runner = Runner(app=trial.assembly.app, session_service=sessions)
        async for event in runner.run_async(user_id="fixture-owner", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=request.model_dump_json())])):
            for part in event.content.parts or [] if event.content else []:
                response = part.function_response
                if response and response.name == "execute_code":
                    trial.record("ptc_output", str((response.response or {}).get("model_text", "")))
    except Exception as exc:
        result["terminal"] = str(exc) if isinstance(exc, EvaluationLimit) else (
            "provider_error" if isinstance(exc, (httpx.HTTPError, ProviderResponseError)) or "OpenRouter" in str(exc)
            else "harness_or_fixture_error")
        result["error"] = trial.redactor.redact_text(f"{type(exc).__name__}: {exc}")[:2000]
    finally:
        await trial.close()
    if hasattr(trial, "ledger"):
        result["oracle_mode"] = "host_owned_virtual_test"
        events = trial.ledger.read(trial.task_id)
        reports = [e.payload["report"] for e in events if e.kind == EventKind.VERIFICATION_COMPLETED]
        result["tool_cells"] = sum(e.kind == EventKind.REPL_CELL_SUBMITTED for e in events) - trial.seed_cells
        result["verification_reports"] = reports
        result["first_verification_passed"] = reports[0]["passed"] if reports else None
        result["accepted"] = any(e.kind == EventKind.TASK_FINISHED for e in events)
        if not trial.seed_checkpoint:
            result["learning_checkpoints"] = [e.payload for e in events if e.kind == "evaluation.learning_checkpoint"]
            result["checkpoint_reminders"] = [e.payload for e in events if e.kind == "evaluation.checkpoint_reminder"]
            published = [e.sequence for e in events if e.kind == EventKind.COMPACTION_CREATED]
            result["published_checkpoint_sequences"] = published
            result["checkpoint_exercised"] = len(result["learning_checkpoints"]) == len(trial.fixture.get("stages", [trial.fixture]))
            if published and not trial.cut_sequence:
                trial.cut_sequence = published[0]  # Publication and a completed continuation remain distinct.
        answer = trial.workspace / "answer.json"
        if oracle is not None:
            result["answer_artifacts"] = oracle.check_artifacts()
            result["passed"] = all(check["passed"] for check in result["answer_artifacts"].values())
        if result["accepted"]:
            result["terminal"] = "verified_completion" if result["passed"] else "false_acceptance"
        elif result["terminal"] == "workflow_stopped":
            result["terminal"] = "workflow_blocked"
        if trial.fixture.get("expect_abstention"):
            result["passed"] = False  # Artifact correctness and correct abstention are separate outcomes.
            result["correct_abstention"] = bool(
                result["terminal"] == "workflow_blocked" and result.get("checkpoint_exercised")
                and result.get("last_proposal_status") == "blocked"
                and any(e.kind == EventKind.TASK_BLOCKED for e in events) and not answer.exists()
                and not answer.is_symlink()
                and not any(e.payload.get("effect") == "unknown" for e in events)
                and not subprocess.check_output(["git", "-C", str(trial.workspace), "status", "--porcelain", "--untracked-files=all"], text=True).strip()
            )
            if result["correct_abstention"]:
                result["terminal"] = "expected_abstention"
        try:
            result["measurement"] = trial.measure()
            if command := trial.fixture.get("validation_command"):
                resolver = _ArtifactResolver(workspace=trial.workspace, state_root=trial.state)
                command_hash = hashlib.sha256(command.encode()).hexdigest()
                outcomes = []
                for event in events:
                    if event.kind not in {"capability.completed", "capability.failed", "capability.blocked"} or event.payload.get("command_sha256") != command_hash:
                        continue
                    body = json.loads(resolver._read_content(event.payload["result_artifact_uri"], max_source_bytes=1_000_000))
                    outcomes.append({"event_id": event.event_id, "kind": event.kind,
                                     "effect": event.payload.get("effect"), "status": body.get("status"),
                                     "exit_code": body.get("exit_code")})
                result["validation_outcomes"] = outcomes
                # Withholding an answer is not reconciliation of an unknown effect.
                result["answer_withheld"] = bool(
                    result.get("last_proposal_status") == "blocked" and not result["accepted"]
                    and any(e.kind == EventKind.TASK_BLOCKED for e in events)
                    and not answer.exists() and not answer.is_symlink())
            if required:
                result["measurement"]["answer_evidence"] = audit_answer_evidence(
                    events, required=required,
                )
            if specifications:
                result["measurement"]["answer_contracts"] = audit_answer_contracts(
                    events, specifications, [e.sequence for e in events if e.kind == EventKind.COMPACTION_CREATED],
                )
        except (ValueError, KeyError, OSError) as exc:
            result["measurement_error"] = str(exc)[:1000]
    result.update(wall_seconds=time.monotonic() - started,
                  uncached_input_tokens=result["input_tokens"] - result["cached_input_tokens"],
                  wire_attempts=len(list((root / "wire").glob("*.json"))))
    result["extra_wire_attempts"] = max(0, result["wire_attempts"] - result["model_calls"])
    _atomic_write(root / "result.json", json.dumps(result, indent=2))
    return result


async def campaign(output: Path, cases: list[str], arms: list[str], concurrency: int, sandbox_image: str | None = None) -> list[dict[str, Any]]:
    from evals.continuity_cases import DevelopmentContinuation
    from evals.heldout_continuity import HELDOUT_CASES
    from evals.learned_continuity import LEARNED_CASES, LearnedContinuation
    from evals.staged_continuity import STAGED_CASES

    semaphore = asyncio.Semaphore(concurrency)
    failed = asyncio.Event()
    async def bounded(case: str, arm: str) -> dict[str, Any]:
        async with semaphore:
            root = output / f"{case}-{arm}"
            result: dict[str, Any]
            if failed.is_set():
                result = {"family": case, "arm": arm, "accepted": False, "passed": False,
                          "terminal": "not_started_infrastructure_gate"}
            else:
                try:
                    async with asyncio.timeout(900):
                        trial = LearnedContinuation(root, case, arm) if case in (*LEARNED_CASES, *HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES) else DevelopmentContinuation(root, case, arm)
                        trial.command_image = sandbox_image
                        result = await run_verified_case(trial)
                except TimeoutError:
                    progress = root / "progress.json"
                    result = json.loads(progress.read_text()) if progress.exists() else {"family": case, "arm": arm}
                    result.update(accepted=False, passed=False, terminal="wall_time_limit")
                except Exception as exc:
                    progress = root / "progress.json"
                    result = json.loads(progress.read_text()) if progress.exists() else {"family": case, "arm": arm}
                    result.update(accepted=False, passed=False, terminal="harness_or_fixture_error", error=type(exc).__name__)
                if result["terminal"] in {"harness_or_fixture_error", "provider_error", "false_acceptance", "wall_time_limit"} or "measurement_error" in result:
                    failed.set()
            _atomic_write(root / "result.json", json.dumps(result, indent=2))
            print(json.dumps({key: result.get(key) for key in ("family", "arm", "accepted", "passed", "terminal", "model_calls")}), flush=True)
            return result
    rows = await asyncio.gather(*(bounded(case, arm) for case in cases for arm in arms))
    _atomic_write(output / "results.json", json.dumps(rows, indent=2))
    summary = {arm: {
        "planned": sum(r["arm"] == arm for r in rows),
        "verified_correct": sum(r["terminal"] == "verified_completion" for r in rows if r["arm"] == arm),
        "correct_abstentions": sum(bool(r.get("correct_abstention")) for r in rows if r["arm"] == arm),
        **{key: sum(r.get(key, 0) for r in rows if r["arm"] == arm) for key in (
            "model_calls", "tool_cells", "input_tokens", "uncached_input_tokens", "output_tokens",
            "provider_cost_usd", "unaccounted_model_calls", "cost_missing_calls", "extra_wire_attempts")},
    } for arm in arms}
    _atomic_write(output / "summary.json", json.dumps({"arms": summary, "promotion": "hold",
        "screen_scope": "repeated_evidence_use" if all(case in REPEATED_CASES for case in cases) else "staged_source_revision" if all(case in STAGED_CASES for case in cases) else "heldout_worker_loss" if all(case in HELDOUT_CASES for case in cases) else "development_or_mixed",
        "note": "Controlled workflow screen. Inspect paired reads/exposure, protocol exercise and every terminal reason; not broad reliability evidence."}, indent=2))
    return rows


def main() -> None:
    from evals.continuity import MODEL
    from evals.continuity_cases import CASES, decisive_sources, fixture
    from evals.heldout_continuity import HELDOUT_CASES, heldout_fixture
    from evals.learned_continuity import LEARNED_CASES, learned_fixture
    from evals.staged_continuity import STAGED_CASES, staged_fixture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dotenv", type=Path, default=Path.home() / ".env")
    parser.add_argument("--cases", nargs="+", choices=(*CASES, *LEARNED_CASES, *HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES), default=["routing", "missing"])
    parser.add_argument("--arms", nargs="+", choices=("metadata", "findings", "no_recall"), default=["metadata", "findings"])
    parser.add_argument("--concurrency", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--sandbox-image", help="Already available immutable Docker image for live command isolation")
    args = parser.parse_args()
    if "no_recall" in args.arms and any(case not in (*HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES) for case in args.cases):
        parser.error("no_recall is only defined for held-out checkpoint cases")
    if args.live and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.sandbox_image or ""):
        parser.error("live trials require --sandbox-image sha256:IMAGE_ID; preflight the cached image first")
    if args.live:
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", args.sandbox_image, "--format", "{{.Id}}"], text=True,
        ).strip()
        if image_id != args.sandbox_image:
            raise ValueError("cached image identity mismatch")
    if len(set(args.cases)) != len(args.cases) or len(set(args.arms)) != len(args.arms):
        parser.error("cases and arms must be unique")
    if args.output.exists():
        raise ValueError("use a fresh campaign output directory")
    fixtures = ({case: fixture(case) for case in CASES} | {case: learned_fixture(case) for case in LEARNED_CASES}
                | {case: heldout_fixture(case) for case in HELDOUT_CASES}
                | {case: staged_fixture(case) for case in STAGED_CASES}
                | {case: repeated_fixture(case) for case in REPEATED_CASES}
                | {case: validation_fixture(case) for case in VALIDATION_CASES})
    manifest = {"version": "verified-continuity-v12", "model": MODEL, "reasoning": "max",
        "cases": args.cases, "arms": args.arms, "concurrency": args.concurrency,
        "max_model_calls": max(fixtures[c].get("max_model_calls", MAX_CALLS) for c in args.cases),
        "input_budget": max(fixtures[c].get("input_budget", INPUT_BUDGET) for c in args.cases), "max_output_tokens": 8192,
        "case_budgets": {c: {"max_model_calls": fixtures[c].get("max_model_calls", MAX_CALLS),
                             "input_budget": fixtures[c].get("input_budget", INPUT_BUDGET)} for c in args.cases},
        "wall_seconds_per_case": 900, "promotion": False,
        "oracle": "host_owned_virtual_test", "sandbox_image": args.sandbox_image,
        "source_evidence_required": True,
        "answer_contract": "Each declared artifact requires its expected JSON value, matching managed-write hash, completed decisive sources and any required successful validation before its write request, and its assigned cut window; every artifact is checked at final verification.",
        "arm_contract": {"no_recall": "Trace retained for control/measurement; working notes, prior recall and model-visible memory programs off. PTC, artifacts, safe restore and the findings-sized read index remain shared."},
        "context_policy": {"initial_checkpoint": "forced_immediate_6k",
                           "learned_checkpoint": "after model-authored note and acknowledged result; one synthetic pressure cut",
                           "heldout_checkpoint": "model checkpoint marker and acknowledgement; quiescent worker stop; handoff_tail with zero historical-tail target in both arms",
                           "staged_checkpoint": "two acknowledged worker-loss cuts; model applies authorized policy change; new-version capture, both cuts and final source hashes required by oracle",
                           "repeated_checkpoint": "one or three acknowledged worker-loss cuts; each answer individually source/window-gated; unchanged phases reuse historical checkpoints without asserting freshness",
                           "checkpoint_feedback": "At most one pending-marker reminder per stage after a prose response; only completed cells and required evidence advance checkpoints.",
                           "continuation": "phase_boundary", "work_packet_tokens": 20_000,
                           "max_context_tokens": 1_050_000, "provider_window_verified": False},
        "fixture_hashes": {case: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() for case, value in fixtures.items()},
        "decisive_sources": {case: [read.model_dump() for read in decisive_sources(fixture(case))] for case in CASES}
                            | {case: fixtures[case]["source_requirements"] for case in (*LEARNED_CASES, *HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES)},
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"])).hexdigest(),
        "driver_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ("verified_continuity.py", "continuity.py", "continuity_cases.py", "continuity_oracle.py", "memory_audit.py", "learned_continuity.py", "heldout_continuity.py", "staged_continuity.py", "repeated_continuity.py", "validation_continuity.py")},
        "limitations": ["learned_*, heldout_*, staged_* and reuse_* charge model acquisition/checkpoint/acknowledgement; other cases use seeded findings", "Forced checkpoint, not natural pressure",
                        "Shell commands isolated in Docker; PTC retains its existing guarded local worker", "No automatic promotion or paid expansion"]}
    _atomic_write(args.output / "manifest.json", json.dumps(manifest, indent=2))
    if not args.live:
        print(json.dumps({"manifest": str(args.output / "manifest.json"), "trials": len(args.cases) * len(args.arms), "live": False}))
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_value(args.dotenv, "OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is unavailable")
    os.environ["OPENROUTER_API_KEY"] = key
    asyncio.run(campaign(args.output, args.cases, args.arms, args.concurrency, args.sandbox_image))


if __name__ == "__main__":
    main()
