"""Development screen for model-written findings and a delayed source question."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from google.adk.plugins.base_plugin import BasePlugin

from evals.continuity import Continuation
from evals.heldout_continuity import HELDOUT_CASES, heldout_fixture
from evals.memory_audit import merged
from evals.runner import _atomic_write
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
        if arm == "no_recall" and case not in HELDOUT_CASES:
            raise ValueError("no-recall control requires an explicit held-out checkpoint protocol")
        super().__init__(root, "navigation", 0, "findings" if arm == "no_recall" else arm)
        self.arm = arm
        if case in HELDOUT_CASES:
            payload = self.composition.model_dump(mode="json")
            # Fresh reconstruction intentionally requires active note retrieval.
            # Use the supported tail policy in BOTH arms, with the shared zero
            # historical-tail target and intact unconsumed acknowledgement.
            payload["harness"]["config"]["context"]["reconstruction"] = "handoff_tail"
            if arm == "no_recall":
                payload["harness"]["config"]["memory"].update(
                    working_notes=False, prior_runs=False, context_programs={"mode": "off"})
            self.composition = parse_harness_composition(payload)
        self.fixture = heldout_fixture(case) if case in HELDOUT_CASES else learned_fixture(case)
        self.task_id = f"{case}-{arm}"
        self.seed_checkpoint = False
        self.source_requirements = [ReadEvidence.model_validate(r) for r in self.fixture["source_requirements"]]

    async def prepare(self) -> None:
        if self.root.exists():
            raise ValueError("refusing to overwrite a trial directory")
        self.workspace.mkdir(parents=True)
        for path, content in self.fixture["files"].items():
            _atomic_write(self.workspace / path, content)
        self.open()
        self.assembly.app.plugins.insert(0, _LearningCheckpoint(self))
        _atomic_write(self.root / "config.json", self.composition.model_dump_json(indent=2))
        _atomic_write(self.root / "fixture.json", json.dumps({
            "case": self.fixture["family"], "goal": self.fixture["goal"],
            "source_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in self.fixture["files"].items()},
        }, indent=2))


class _LearningCheckpoint(BasePlugin):
    """One bounded experimental intervention; ADK still owns every model/tool call."""
    def __init__(self, trial: LearnedContinuation):
        super().__init__(name="evaluation_learning_checkpoint")
        # Runtime ownership is private: run-context telemetry must not traverse
        # the evaluator's oracle, locks, or the plugin/assembly cycle.
        self._trial = trial
        self._steering = next(p for p in trial.assembly.app.plugins if isinstance(p, SteeringPlugin))
        self.note = None
        self.ack_message = None
        self.ack_floor = 0
        self.pending = None
        self.config = trial.plugin.config

    async def before_model_callback(self, *, callback_context, llm_request):
        if callback_context.agent_name != "coding_worker" or self._trial.cut_sequence:
            return
        trial = self._trial
        events = trial.ledger.read(trial.task_id)
        if self.note is None:
            notes = [e for e in events if e.kind == "memory.note" and (e.payload.get("text") or e.payload.get("entries"))]
            marker = trial.fixture.get("checkpoint_marker")
            ready = [e for e in events if e.kind == "repl.cell_completed"
                     and marker in str(e.payload.get("stdout", "")).splitlines()] if marker else []
            if marker and not ready:
                return
            if trial.arm != "no_recall" and not notes:
                return
            note = ready[-1] if trial.arm == "no_recall" else notes[-1]
            if marker and note.sequence > ready[-1].sequence:
                return
            reads = [ReadEvidence.model_validate(e.payload["read_evidence"]) for e in events
                     if e.sequence < note.sequence and e.kind == "capability.completed"
                     and e.payload.get("status") == "ok" and e.payload.get("operation") == "fs.read"
                     and e.payload.get("read_evidence")]
            for raw in trial.fixture["learning_requirements"]:
                need = ReadEvidence.model_validate(raw)
                ranges = merged((max(r.offset, need.offset), min(r.offset + r.returned_lines, need.offset + need.returned_lines))
                                for r in reads if (r.path, r.sha256) == (need.path, need.sha256))
                if sum(end - start for start, end in ranges) != need.returned_lines:
                    return
            self.note, self.ack_floor = note, events[-1].sequence
            self.ack_message = self._steering.queue.enqueue(trial.task_id,
                "Checkpoint recorded. Before the follow-up, acknowledge it with one execute_code cell "
                "printing CHECKPOINT_READY. Do not write answer.json in that acknowledgement.",
                idempotency_key="evaluation-checkpoint-ack")
            return
        acknowledgements = [e for e in events if e.sequence > self.ack_floor and e.kind == "repl.cell_completed"]
        if not acknowledgements or self.pending:
            return
        if self.ack_message:
            self._steering.queue.ack([self.ack_message.message_id], str(callback_context.state["steering_owner"]))
        worker_loss = None
        if trial.fixture.get("worker_loss"):
            task = rebuild_ledger(trial.events.read(trial.task_id))
            before = trial.plugin.handoff(task)["kernel"]
            if not trial.assembly.close or not before.get("live"):
                raise ValueError("worker-loss intervention requires a live idle worker")
            trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id="worker-stop-request",
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
                trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id="worker-stop-failed",
                                    kind="evaluation.worker_stop_failed", status="failed", effect="unknown")
                raise
            worker_loss = {"before": before, "after": after}
            trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id="worker-stop-completed",
                                kind="evaluation.worker_stopped", status="completed", payload=worker_loss)
        self._steering.queue.enqueue(trial.task_id, trial.fixture["followup"],
                                    idempotency_key="evaluation-followup")
        self.pending = {"checkpoint_event_id": self.note.event_id,
                        "note_event_id": self.note.event_id if self.note.kind == "memory.note" else None,
                        "note_sequence": self.note.sequence if self.note.kind == "memory.note" else None,
                        "ack_event_id": acknowledgements[0].event_id,
                        "worker_loss": worker_loss,
                        "learning_model_calls": sum(e.kind == "metric.model" for e in events)}
        # Explicit synthetic pressure, not a provider-window claim. Preserve the
        # normal packet and hard-window capacities, and restore the ratio after one cut.
        trial.plugin.config = self.config.model_copy(update={"compaction_threshold_ratio": 0.000001})

    async def after_model_callback(self, *, callback_context, llm_response):
        if llm_response.partial or callback_context.agent_name != "coding_worker" or not self.pending or self._trial.cut_sequence:
            return
        trial = self._trial
        cuts = [e for e in trial.ledger.read(trial.task_id) if e.kind == "compaction.created"]
        if len(cuts) != 1:
            raise ValueError("learning intervention did not publish exactly one checkpoint")
        trial.cut_sequence = cuts[0].sequence
        trial.plugin.config = self.config
        trial.ledger.append(task_id=trial.task_id, source="evaluation", source_id="learning-checkpoint",
                            kind="evaluation.learning_checkpoint", payload={**self.pending,
                            "cut_sequence": trial.cut_sequence, "seeded_cells": trial.seed_cells})
