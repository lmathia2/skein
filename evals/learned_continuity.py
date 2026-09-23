"""Development screen for model-written findings and a delayed source question."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from google.adk.plugins.base_plugin import BasePlugin

from evals.continuity import Continuation
from evals.continuity_oracle import AnswerSpec, audit_answer_contracts, check_answer_artifacts
from evals.heldout_continuity import HELDOUT_CASES, heldout_fixture
from evals.memory_audit import canonical_prior, merged
from evals.qualification_continuity import QUALIFICATION_CASES, qualification_fixture
from evals.repeated_continuity import REPEATED_CASES, repeated_fixture
from evals.runner import _atomic_write
from evals.staged_continuity import STAGED_CASES, staged_fixture
from evals.validation_continuity import VALIDATION_CASES, validation_fixture
from harness.adapters.adk.steering import SteeringPlugin
from harness.core.config import parse_harness_composition
from harness.evidence.memory.models import ReadEvidence
from harness.evidence.state import rebuild_ledger

LEARNED_CASES = ("learned_lookup", "learned_join", "learned_unavailable")


def learned_fixture(case: str) -> dict:
    if case not in LEARNED_CASES:
        raise ValueError("unknown learned-checkpoint case")
    files = {}
    for index in range(8):
        files[f"shards/shard_{index:02}.json"] = json.dumps({
            "name": f"lyra-{index}", "burst": 83 + 17 * index,
            "reserve": 11 + 3 * index, "policy": f"policy_{index % 2}.toml",
        }, indent=2) + "\n"
    files.update({f"shards/policy_{i}.toml": f"interval_ms = {137 + 29 * i}\n" for i in range(2)})
    target = "shards/shard_02.json" if case == "learned_lookup" else "shards/shard_05.json"
    if case == "learned_unavailable":
        value = json.loads(files[target])
        value["policy"] = "policy_missing.toml"
        files[target] = json.dumps(value, indent=2) + "\n"
    selected = json.loads(files[target])
    expected = {"capacity": selected["burst"] - selected["reserve"]}
    operation = "capacity = burst minus reserve"
    if case != "learned_lookup":
        expected = {"window_ms": (selected["burst"] - selected["reserve"]) * 166}
        operation = "window_ms = (burst minus reserve) multiplied by interval_ms in its referenced policy"
    if case == "learned_unavailable":
        expected = None  # No numeric answer is supported; even JSON null is not an accepted artifact.
    goal = (
        "Prepare for a follow-up question about the eight lyra shards. Read their small JSON records "
        "and the two TOML policies under shards/ using agent.fs.read for path/version/range receipts. "
        "Preserve names, burst/reserve values, policy links, "
        "and intervals in one concise evidence-linked working-note checkpoint. Do not write answer.json yet. "
        "After that note, the coordinator will request one bounded acknowledgement cell, then send the "
        "specific question at a context checkpoint. Keep reusable data in Python if useful. "
        "Use only completed source evidence; do not search for oracle code. The eventual answer must "
        "pass the host-owned independent verification. No unrelated source changes are permitted."
    )
    followup = (
        f"Follow-up replaces the acknowledgement request: for {selected['name']}, write answer.json with "
        f"exactly {operation}. Reuse applicable findings or captured evidence; do not reread unchanged "
        "sources merely for confirmation. Then request verification. The source-evidence gate requires "
        "the decisive fields to have been captured before the answer write; existing captures count."
    )
    if case == "learned_unavailable":
        followup = (
            "Follow-up replaces the acknowledgement request: for lyra-5, compute window_ms = "
            "(burst minus reserve) multiplied by interval_ms in its referenced policy. "
            "If the required policy is unavailable, report blocked and do not create answer.json; "
            "do not borrow a different shard's policy or invent a value. Reuse applicable evidence."
        )
    def requirement(path, offset, count):
        return ReadEvidence(path=path, sha256=hashlib.sha256(files[path].encode()).hexdigest(),
                            offset=offset, returned_lines=count).model_dump()
    learning = [requirement(path, 2, 4) if path.endswith(".json") else requirement(path, 1, 1) for path in files]
    decisive = [requirement(target, 2, 3 if case == "learned_lookup" else 4)]
    if case == "learned_join":
        decisive.append(requirement("shards/" + selected["policy"], 1, 1))
    return {"family": case, "variant": 0, "files": files, "target": target,
            "expected": expected, "goal": goal, "followup": followup,
            "expect_abstention": case == "learned_unavailable",
            "learning_requirements": learning, "source_requirements": decisive}


class LearnedContinuation(Continuation):
    def __init__(self, root: Path, case: str, arm: str):
        if arm == "no_recall" and case not in (*HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES, *QUALIFICATION_CASES):
            raise ValueError("no-recall control requires an explicit held-out checkpoint protocol")
        super().__init__(root, "navigation", 0, "findings" if arm == "no_recall" else arm)
        self.arm = arm
        if case in (*HELDOUT_CASES, *STAGED_CASES, *REPEATED_CASES, *VALIDATION_CASES, *QUALIFICATION_CASES):
            payload = self.composition.model_dump(mode="json")
            # Fresh reconstruction intentionally requires active note retrieval.
            # Use the supported tail policy in BOTH arms, with the shared zero
            # historical-tail target and intact unconsumed acknowledgement.
            payload["harness"]["config"]["context"]["reconstruction"] = "handoff_tail"
            if arm == "no_recall":
                payload["harness"]["config"]["memory"].update(
                    working_notes=False, prior_runs=False, context_programs={"mode": "off"})
            self.composition = parse_harness_composition(payload)
        self.fixture = (qualification_fixture(case) if case in QUALIFICATION_CASES else
                        validation_fixture(case) if case in VALIDATION_CASES else
                        repeated_fixture(case) if case in REPEATED_CASES else
                        staged_fixture(case) if case in STAGED_CASES else
                        heldout_fixture(case) if case in HELDOUT_CASES else learned_fixture(case))
        self.task_id = f"{case}-{arm}"
        self.seed_checkpoint = False
        self.source_requirements = [ReadEvidence.model_validate(r) for r in self.fixture["source_requirements"]]

    async def prepare(self) -> None:
        if self.root.exists():
            raise ValueError("refusing to overwrite a trial directory")
        if self.prior_root is not None and (self.prior_run is None
                or self.prior_run.bindings.workspace.resolve() != self.workspace.resolve()
                or self.prior_run.bindings.state_root.resolve() != self.prior_root.resolve()):
            raise ValueError("shared qualification workspace requires its owned producer evidence")
        self.workspace.mkdir(parents=True, exist_ok=self.prior_root is not None)
        for path, content in self.fixture["files"].items():
            _atomic_write(self.workspace / path, content)
        self.open(prior=self.prior_root is not None)
        if self.prior_evidence is not None:
            frozen = canonical_prior(self.prior_evidence)
            _atomic_write(self.root / "prior-evidence.json", frozen)
            self.ledger.append(task_id=self.task_id, source="evaluation", source_id="owned-prior-binding",
                               kind="evaluation.prior_bound", payload={
                                   "source_tasks": list(self.bindings.prior_task_ids),
                                   "inputs_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
                                   "recall_enabled": self.composition.model_dump()["harness"]["config"]["memory"]["prior_runs"]})
        self.assembly.app.plugins.insert(0, _LearningCheckpoint(self))
        _atomic_write(self.root / "config.json", self.composition.model_dump_json(indent=2))
        _atomic_write(self.root / "fixture.json", json.dumps({
            "case": self.fixture["family"], "goal": self.fixture["goal"],
            "source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in self.fixture["files"].items()},
        }, indent=2))


class _LearningCheckpoint(BasePlugin):
    """Bounded experimental checkpoints; ADK still owns every model/tool call."""
    def __init__(self, trial: LearnedContinuation):
        super().__init__(name="evaluation_learning_checkpoint")
        # Runtime ownership is private: run-context telemetry must not traverse
        # the evaluator's oracle, locks, or the plugin/assembly cycle.
        self._trial = trial
        self._steering = next(p for p in trial.assembly.app.plugins if isinstance(p, SteeringPlugin))
        self.note = None
        self.ack_message = None
        self.followup_message = None
        self.ack_floor = 0
        self.pending = None
        self.config = trial.plugin.config
        self.stage = 0
        self.stage_floor = 0
        self._answer_rejection = None
        self._answer_rejection_note = None
        self._checkpoint_reminder = None

    @property
    def ack_token(self):
        return f"CHECKPOINT_READY_{self.stage + 1}" if self._trial.fixture.get("stages") else "CHECKPOINT_READY"

    def _answers_ready(self, events, note_sequence: int) -> bool:
        """Do not close an answer window until its own evidence-backed write exists."""
        specifications = [AnswerSpec.model_validate(raw) for raw in self._trial.fixture.get("answers", [])
                          if raw["checkpoint"] == self.stage - 1]
        if not specifications:
            return True
        boundary_kind = ("evaluation.learning_checkpoint" if self._trial.fixture.get("checkpoint_mode") == "live_worker"
                         else "compaction.created")
        cuts = [event.sequence for event in events if event.kind == boundary_kind]
        report = audit_answer_contracts(events, specifications, cuts, prior=self._trial.prior_evidence,
                                        boundary_kind=boundary_kind)
        checks = check_answer_artifacts(self._trial.workspace, {
            spec.path: json.dumps(spec.expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
            for spec in specifications})
        return all(
            checks[path]["passed"] and artifact["latest_supported"]
            and artifact["answers"][-1]["answer_sha256"] == checks[path]["sha256"]
            and artifact["answers"][-1]["sequence"] < note_sequence
            for path, artifact in report["artifacts"].items())

    def _reject_answer_checkpoint(self, note, owner: str) -> None:
        if self._answer_rejection_note == note.event_id:
            return
        if self._answer_rejection:
            self._steering.queue.ack([self._answer_rejection.message_id], owner)
        self._answer_rejection_note = note.event_id
        marker = self._trial.fixture["stages"][self.stage]["checkpoint_marker"]
        self._answer_rejection = self._steering.queue.enqueue(
            self._trial.task_id,
            "Answer checkpoint not advanced: a required answer lacks a valid value, prior completed source evidence, "
            "or matching managed-write bytes. Repair it from completed evidence, preserve a fresh checkpoint, "
            f"then print {marker}. Do not request final verification yet.",
            idempotency_key=f"evaluation-answer-rejected-{note.event_id}")

    async def before_model_callback(self, *, callback_context, llm_request):
        stages = self._trial.fixture.get("stages", [self._trial.fixture])
        if callback_context.agent_name != "coding_worker" or self.stage >= len(stages):
            return
        trial = self._trial
        stage = stages[self.stage]
        reuse_checkpoint = bool(stage.get("reuse_checkpoint"))
        if reuse_checkpoint and (self.stage == 0 or stage.get("required_changes") or not stage.get("checkpoint_marker")):
            raise ValueError("checkpoint reuse requires a later unchanged-source marker boundary")
        events = trial.ledger.read(trial.task_id)
        if self.note is None:
            notes = [e for e in events if e.sequence > self.stage_floor and e.kind == "memory.note" and (e.payload.get("text") or e.payload.get("entries"))]
            marker = stage.get("checkpoint_marker")
            ready = [e for e in events if e.sequence > self.stage_floor and e.kind == "repl.cell_completed"
                     and marker in str(e.payload.get("stdout", "")).splitlines()] if marker else []
            if marker and not ready:
                return
            live_worker = trial.fixture.get("checkpoint_mode") == "live_worker"
            if trial.arm != "no_recall" and not live_worker and not reuse_checkpoint and not notes:
                return
            note = ready[-1] if trial.arm == "no_recall" or live_worker or reuse_checkpoint else notes[-1]
            if marker and note.sequence > ready[-1].sequence:
                return
            change_floors = {}
            for path, digest in stage.get("required_changes", {}).items():
                changes = [e.sequence for e in events if self.stage_floor < e.sequence < note.sequence
                           and e.kind == "capability.completed" and e.payload.get("status") == "ok"
                           and e.payload.get("operation") in {"fs.write", "fs.edit"}
                           and path in e.payload.get("changed_paths", [])
                           and e.payload.get("content_hashes", {}).get(path) == digest]
                if not changes:
                    return
                change_floors[path] = max(changes)
            reads = [(e.sequence, ReadEvidence.model_validate(e.payload["read_evidence"])) for e in events
                     if e.sequence < note.sequence and e.kind == "capability.completed"
                     and e.payload.get("status") == "ok" and e.payload.get("operation") == "fs.read"
                     and e.payload.get("read_evidence")]
            for raw in stage["learning_requirements"]:
                need = ReadEvidence.model_validate(raw)
                ranges = merged((max(r.offset, need.offset), min(r.offset + r.returned_lines, need.offset + need.returned_lines))
                                for sequence, r in reads if sequence > change_floors.get(need.path, 0)
                                and (r.path, r.sha256) == (need.path, need.sha256))
                if sum(end - start for start, end in ranges) != need.returned_lines:
                    return
            if not self._answers_ready(events, note.sequence):
                self._reject_answer_checkpoint(note, str(callback_context.state["steering_owner"]))
                return
            if self._answer_rejection:
                self._steering.queue.ack([self._answer_rejection.message_id], str(callback_context.state["steering_owner"]))
                self._answer_rejection = self._answer_rejection_note = None
            self.note, self.ack_floor = note, events[-1].sequence
            if self._checkpoint_reminder:
                self._steering.queue.ack([self._checkpoint_reminder.message_id], str(callback_context.state["steering_owner"]))
                self._checkpoint_reminder = None
            if self.followup_message is not None:
                # Only our own completed phase instruction is consumed here;
                # unrelated user steering remains owned by the outer workflow.
                self._steering.queue.ack([self.followup_message.message_id], str(callback_context.state["steering_owner"]))
                self.followup_message = None
            self.ack_message = self._steering.queue.enqueue(trial.task_id,
                f"NEW checkpoint {self.stage + 1} of {len(stages)} recorded. This request supersedes earlier phase instructions. "
                f"Submit a new code cell printing {self.ack_token} on its own line now. "
                "An earlier acknowledgement does not satisfy this request. Do not propose done or verify yet; "
                "the coordinator must deliver the next stage/final question first. Do not write answer.json in this acknowledgement.",
                idempotency_key=f"evaluation-checkpoint-ack-{self.stage}-{note.event_id}")
            return
        acknowledgements = [e for e in events if e.sequence > self.ack_floor and e.kind == "repl.cell_completed"
                            and self.ack_token in str(e.payload.get("stdout", "")).splitlines()]
        if not acknowledgements or self.pending:
            return
        # An acknowledgement cell can mutate artifacts too. Recheck before
        # closing the interval; a repair needs a fresh checkpoint and handshake.
        if not self._answers_ready(events, self.note.sequence):
            if self.ack_message:
                self._steering.queue.ack([self.ack_message.message_id], str(callback_context.state["steering_owner"]))
            self._reject_answer_checkpoint(self.note, str(callback_context.state["steering_owner"]))
            self.note = self.ack_message = None
            return
        if self.ack_message:
            self._steering.queue.ack([self.ack_message.message_id], str(callback_context.state["steering_owner"]))
        worker_loss = None
        if stage.get("worker_loss"):
            task = rebuild_ledger(trial.events.read(trial.task_id))
            before = trial.plugin.handoff(task)["kernel"]
            if not trial.assembly.close or not before.get("live"):
                raise ValueError("worker-loss intervention requires a live idle worker")
            trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id=f"worker-stop-request-{self.stage}",
                                kind="evaluation.worker_stop_requested", status="started",
                                payload={"before": before, "after_ack_event_id": acknowledgements[0].event_id})
            try:
                closed = trial.assembly.close()
                if inspect.isawaitable(closed):
                    await closed
                after = trial.plugin.handoff(task)["kernel"]
                if after.get("live") or after.get("kernel_epoch"):
                    raise ValueError("worker-loss intervention did not discard the live epoch")
            except Exception:
                trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id=f"worker-stop-failed-{self.stage}",
                                    kind="evaluation.worker_stop_failed", status="failed", effect="unknown")
                raise
            worker_loss = {"before": before, "after": after}
            trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id=f"worker-stop-completed-{self.stage}",
                                kind="evaluation.worker_stopped", status="completed", payload=worker_loss)
        self.followup_message = self._steering.queue.enqueue(trial.task_id, stage["followup"],
                                                           idempotency_key=f"evaluation-followup-{self.stage}")
        self.pending = {"checkpoint_event_id": self.note.event_id,
                        "note_event_id": self.note.event_id if self.note.kind == "memory.note" else None,
                        "note_sequence": self.note.sequence if self.note.kind == "memory.note" else None,
                        "ack_event_id": acknowledgements[0].event_id,
                        "ack_token": self.ack_token,
                        "worker_loss": worker_loss,
                        "stage": self.stage,
                        "learning_model_calls": sum(e.kind == "metric.model" for e in events)}
        if reuse_checkpoint:
            historical_notes = [e for e in events if e.kind == "memory.note"]
            self.pending["reused_note_event_id"] = historical_notes[-1].event_id if historical_notes else None
            self.pending["checkpoint_policy"] = "historical_reuse_no_freshness_claim"
        if trial.fixture.get("checkpoint_mode") != "live_worker":
            # Explicit synthetic pressure, not a provider-window claim. Preserve the
            # normal packet and hard-window capacities, and restore the ratio after one cut.
            trial.plugin.config = self.config.model_copy(update={"compaction_threshold_ratio": 0.000001})

    async def after_model_callback(self, *, callback_context, llm_response):
        if llm_response.partial or callback_context.agent_name != "coding_worker":
            return
        trial = self._trial
        if not self.pending:
            stages = trial.fixture.get("stages", [trial.fixture])
            parts = llm_response.content.parts or [] if llm_response.content else []
            if (self.note is None and self.stage < len(stages) and self._checkpoint_reminder is None
                    and parts and not any(part.function_call for part in parts)
                    and (marker := stages[self.stage].get("checkpoint_marker"))):
                self._checkpoint_reminder = self._steering.queue.enqueue(trial.task_id,
                    "Checkpoint not recorded. Finish the required acquisition, answer/update and checkpoint, "
                    f"then submit an code cell printing {marker} on its own line. "
                    "A prose claim that it was printed is not an execution receipt. Await the new acknowledgement "
                    "and next question before proposing final completion.",
                    idempotency_key=f"evaluation-checkpoint-reminder-{self.stage}")
                trial.ledger.append(task_id=trial.task_id, source="evaluation",
                    source_id=f"checkpoint-reminder-{self.stage}", kind="evaluation.checkpoint_reminder",
                    status="observed", payload={"stage": self.stage, "marker": marker})
            return
        cuts = [e for e in trial.ledger.read(trial.task_id) if e.kind == "compaction.created"]
        if trial.fixture.get("checkpoint_mode") == "live_worker":
            if cuts:
                raise ValueError("live-worker intervention must not compact context")
            checkpoint = trial.ledger.append(
                task_id=trial.task_id, source="evaluation", source_id=f"learning-checkpoint-{self.stage}",
                kind="evaluation.learning_checkpoint", payload={**self.pending, "seeded_cells": trial.seed_cells,
                "boundary": "same_live_worker_no_context_cut"})
            trial.cut_sequence = trial.cut_sequence or checkpoint.sequence
        else:
            if len(cuts) != self.stage + 1 or cuts[-1].sequence <= self.stage_floor:
                raise ValueError("learning intervention did not publish exactly one new checkpoint")
            trial.cut_sequence = cuts[-1].sequence
            trial.plugin.config = self.config
            checkpoint = trial.ledger.append(
                task_id=trial.task_id, source="evaluation", source_id=f"learning-checkpoint-{self.stage}",
                kind="evaluation.learning_checkpoint", payload={**self.pending,
                "cut_sequence": trial.cut_sequence, "seeded_cells": trial.seed_cells})
        self.stage += 1
        self.stage_floor = checkpoint.sequence
        self.note = self.ack_message = self.pending = None
