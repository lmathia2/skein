import hashlib
import json
from dataclasses import replace

import pytest
import pytest_asyncio

from evals.continuity import Continuation
from evals.memory_audit import audit_answer_evidence
from evals.prior_evidence import PriorEvidence, PriorRun
from harness.core.config import RuntimeBindings
from harness.evidence.ledger.models import canonical_json
from harness.evidence.memory.models import ReadEvidence, ViewResult
from harness.evidence.state.events import EventKind
from harness.evidence.state.receipts import ToolReceiptStore
from harness.execution.tools.adk_adapter import _ArtifactResolver
from harness.ptc.notebook.artifacts import put_artifact


@pytest_asyncio.fixture
async def prior_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-fixture-key")
    source = Continuation(tmp_path / "producer", "navigation", 0, "findings")
    source.task_id = "fixture-prior"
    source.workspace.mkdir(parents=True)
    text = "# shared policy\nRATE = 17\nCAP = 93\nUNSEEN = 48\n"
    (source.workspace / "policy.py").write_text(text)
    source.open()
    try:
        result = await source.cell(
            "import json, shlex\n"
            "r = agent.fs.read('policy.py', offset=2, limit=2)\n"
            "entry = {'id': 'policy', 'kind': 'observation', 'text': r['data']['text'], "
            "'evidence_refs': [r['read_reference']['artifact_uri']]}\n"
            "assert agent.shell.run('memory note write --text Policy --expected-version 0 --operation-id policy '"
            "'--entries ' + shlex.quote(json.dumps([entry])))['status'] == 'ok'\n")
        assert result["status"] == "ok", result
    finally:
        await source.close()
    # Unit admission fixture only: this host terminal does not claim the fresh
    # producer/consumer qualification protocol or model behavior has been tested.
    source.ledger.append(task_id=source.task_id, source="evaluation", source_id="finish",
                         kind="task.finished", payload={"verification": {"passed": True}})
    consumer = Continuation(tmp_path / "consumer", "navigation", 1, "findings")
    consumer.workspace = source.workspace
    consumer.prior_root = source.state
    consumer.open(prior=True)
    bindings = RuntimeBindings(workspace=source.workspace, state_root=source.state, task_id=source.task_id,
                               user_id="fixture-owner", conversation_id="continuity-fixture")
    current = bindings.model_copy(update={"task_id": consumer.task_id, "state_root": consumer.state,
                                          "prior_task_ids": (source.task_id,), "prior_state_roots": (source.state,)})
    prior = PriorEvidence(current, (PriorRun(bindings, tuple(source.ledger.read(source.task_id)),
                                            tuple(ToolReceiptStore(source.state / "managed-tools.db").for_task(source.task_id))),))
    need = ReadEvidence(path="policy.py", sha256=hashlib.sha256(text.encode()).hexdigest(), offset=2, returned_lines=2)
    try:
        yield source, consumer, prior, need
    finally:
        await consumer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["working_set", "history.page", "event.read", "read.recover", "reads.lookup"])
async def test_prior_availability_requires_actual_completed_retrieval_and_current_identity(prior_pair, route):
    source, consumer, prior, need = prior_pair
    read = next(e for e in source.ledger.read(source.task_id) if e.payload.get("read_evidence"))
    command = f"memory query --program {route} --tasks fixture-prior"
    if route == "read.recover":
        command += f" --event-id {read.event_id}"
    if route == "event.read":
        note = next(e for e in source.ledger.read(source.task_id) if e.kind == "memory.note")
        command += f" --event-id {note.event_id}"
    result = await consumer.cell(f"r = agent.shell.run({command!r})\nassert r['status'] == 'ok', r")
    assert result["status"] == "ok", result
    assert (await consumer.cell("agent.fs.write('answer.json', '{}')"))["status"] == "ok"
    events = consumer.ledger.read(consumer.task_id)
    assert audit_answer_evidence(events, required=[need], prior=prior)["last_answer"] != "available"
    # One necessary freshness line carries a whole-file hash but not lines 2-3.
    assert (await consumer.cell("agent.fs.read('policy.py', offset=1, limit=1)\nagent.fs.write('answer.json', '{}')"))["status"] == "ok"
    events = consumer.ledger.read(consumer.task_id)
    report = audit_answer_evidence(events, required=[need], prior=prior)
    assert report["first_answer"] != "available"
    assert (report["last_answer"] == "available") is (route != "reads.lookup")
    assert audit_answer_evidence(events, required=[need])["last_answer"] != "available"
    if route != "reads.lookup":
        inherited = report["answers"][-1]["requirements"][0]["prior_evidence"]
        assert inherited[0]["source_task_id"] == source.task_id
        assert inherited[0]["source_event_id"] == read.event_id
        assert inherited[0]["route"] == (route if route == "read.recover" else "finding")
        # A metadata hash cannot manufacture an uncaptured producer line.
        missing = need.model_copy(update={"offset": 4, "returned_lines": 1})
        assert audit_answer_evidence(events, required=[missing], prior=prior)["last_answer"] != "available"
        # Public retrieval receipt without the matching completed capability is pending.
        terminal = inherited[0]["retrieval_event_id"]
        pending = [e for e in events if e.event_id != terminal]
        assert audit_answer_evidence(pending, required=[need], prior=prior)["last_answer"] != "available"
    assert audit_answer_evidence(events, required=[need], prior=prior) == report
    if route == "working_set":
        assert (await consumer.cell(
            f"agent.fs.edit('answer.json', '{{}}', '{{}}')\nfresh = agent.shell.run({command!r})\n"
            "assert fresh['data']['data']['findings'][0]['consumer_versions']['status'] == 'matching_observations'\n"
            "agent.fs.write('answer.json', '{}')"))["status"] == "ok"
        repeated = consumer.ledger.read(consumer.task_id)
        # The producer is unchanged, but the later consumer observation changes
        # the view's applicability and independently watermarked input snapshot.
        assert len([e for e in repeated if e.kind == "memory.retrieval"]) == 2
        assert audit_answer_evidence(repeated, required=[need], prior=prior)["last_answer"] == "available"


@pytest.mark.asyncio
async def test_prior_stale_source_requires_fresh_range_and_late_recall_cannot_repair_old_answer(prior_pair):
    _, consumer, prior, need = prior_pair
    assert (await consumer.cell("agent.fs.read('policy.py', offset=1, limit=1)\nagent.fs.write('answer.json', '{}')"))["status"] == "ok"
    assert (await consumer.cell("agent.shell.run('memory query --program working_set --tasks fixture-prior')"))["status"] == "ok"
    assert audit_answer_evidence(consumer.ledger.read(consumer.task_id), required=[need], prior=prior)["last_answer"] != "available"
    changed = "# shared policy\nRATE = 29\nCAP = 93\nUNSEEN = 48\n"
    assert (await consumer.cell(f"agent.fs.write('policy.py', {changed!r})\nagent.fs.read('policy.py', offset=1, limit=1)\nagent.fs.write('answer.json', '{{}}')"))["status"] == "ok"
    current_need = need.model_copy(update={"sha256": hashlib.sha256(changed.encode()).hexdigest()})
    events = consumer.ledger.read(consumer.task_id)
    assert audit_answer_evidence(events, required=[need], prior=prior)["last_answer"] != "available"
    assert audit_answer_evidence(events, required=[current_need], prior=prior)["last_answer"] != "available"
    assert (await consumer.cell("agent.fs.read('policy.py', offset=2, limit=2)\nagent.fs.write('answer.json', '{}')"))["status"] == "ok"
    report = audit_answer_evidence(consumer.ledger.read(consumer.task_id), required=[current_need], prior=prior)
    assert report["last_answer"] == "available"
    assert not report["all_answers_source_available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("page,covered", [("--offset 2 --limit 1", 1), ("--byte-offset 3", 0)])
async def test_prior_recovery_counts_only_complete_selected_source_lines(prior_pair, page, covered):
    source, consumer, prior, need = prior_pair
    read = next(e for e in source.ledger.read(source.task_id) if e.payload.get("read_evidence"))
    command = f"memory query --program read.recover --tasks fixture-prior --event-id {read.event_id} {page}"
    result = await consumer.cell(f"agent.fs.read('policy.py', offset=1, limit=1)\nagent.shell.run({command!r})\nagent.fs.write('answer.json', '{{}}')")
    assert result["status"] == "ok", result
    report = audit_answer_evidence(consumer.ledger.read(consumer.task_id), required=[need], prior=prior)
    assert report["last_answer"] != "available"
    assert report["answers"][-1]["requirements"][0]["covered_lines"] == covered


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["owner", "conversation", "workspace", "root", "unselected", "unfinished", "failed", "pending", "corrupt"])
async def test_prior_admission_rejects_foreign_unfinished_and_unresolved_sources(prior_pair, tmp_path, fault):
    _, _, prior, _ = prior_pair
    source = prior.sources[0]
    if fault in {"owner", "conversation", "workspace", "root"}:
        field, value = {"owner": ("user_id", "foreign"), "conversation": ("conversation_id", "foreign"),
                        "workspace": ("workspace", tmp_path / "foreign"), "root": ("state_root", tmp_path / "foreign")}[fault]
        source = replace(source, bindings=source.bindings.model_copy(update={field: value}))
    elif fault == "unselected":
        with pytest.raises(ValueError, match="scope"):
            replace(prior, current=prior.current.model_copy(update={"prior_task_ids": (), "prior_state_roots": ()}))
        return
    elif fault == "unfinished":
        source = replace(source, events=tuple(e for e in source.events if e.kind != "task.finished"))
    elif fault == "failed":
        event = source.events[-1]
        payload = {"verification": {"passed": False}}
        source = replace(source, events=(*source.events[:-1], event.model_copy(update={
            "payload": payload, "payload_hash": hashlib.sha256(canonical_json(payload).encode()).hexdigest()})))
    elif fault == "pending":
        last = next(e for e in reversed(source.events) if e.kind == EventKind.REPL_CELL_COMPLETED)
        source = replace(source, events=tuple(e for e in source.events if e != last))
    else:
        source = replace(source, events=(*source.events[:-1], source.events[-1].model_copy(update={"payload_hash": "0" * 64})))
    with pytest.raises(ValueError):
        replace(prior, sources=(source,))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["receipt", "manifest", "finding", "consumer", "range", "artifact"])
async def test_prior_retrieval_integrity_is_checked_independently(prior_pair, fault):
    source, consumer, prior, need = prior_pair
    read = next(e for e in source.ledger.read(source.task_id) if e.payload.get("read_evidence"))
    command = (f"memory query --program read.recover --tasks fixture-prior --event-id {read.event_id}"
               if fault == "range" else "memory query --program working_set --tasks fixture-prior")
    result = await consumer.cell(f"agent.fs.read('policy.py', offset=1, limit=1)\nagent.shell.run({command!r})\nagent.fs.write('answer.json', '{{}}')")
    assert result["status"] == "ok", result
    events = consumer.ledger.read(consumer.task_id)
    assert audit_answer_evidence(events, required=[need], prior=prior)["last_answer"] == "available"
    terminal = next(e for e in events if e.kind == "capability.completed" and e.payload.get("operation") == "shell.run")
    resolver = _ArtifactResolver(workspace=consumer.workspace, state_root=consumer.state)
    if fault == "receipt":
        altered = [e for e in events if e.kind != "memory.retrieval"]
    elif fault == "artifact":
        path, _ = resolver._target(terminal.payload["result_artifact_uri"])
        path.write_bytes(b"{}")
        altered = events
    else:
        body = json.loads(resolver._read_content(terminal.payload["result_artifact_uri"]))
        view = body["data"]
        if fault == "manifest":
            view["source_manifest"][source.task_id]["hash"] = "0" * 64
        elif fault == "finding":
            view["data"]["findings"][0]["finding"]["text"] = "Forged policy"
        elif fault == "consumer":
            view["task_id"] = "other"
        else:
            view["data"]["selected_lines"] += 1
        view["content_hash"] = ""
        body["data"] = ViewResult.model_validate(view).model_dump(mode="json")
        raw = (canonical_json(body) + "\n").encode()
        uri = put_artifact(consumer.state / "artifacts" / "sha256", raw)
        altered = []
        for event in events:
            payload = dict(event.payload)
            if event.event_id == terminal.event_id:
                payload.update(result_artifact_uri=uri, result_bytes=len(raw), result_hash=hashlib.sha256(
                    json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest())
            elif event.kind == "memory.retrieval":
                payload["result_hash"] = hashlib.sha256(canonical_json(body["data"]).encode()).hexdigest()
            altered.append(type(event).model_validate({**event.model_dump(), "payload": payload, "payload_hash": ""}))
    with pytest.raises(ValueError):
        audit_answer_evidence(altered, required=[need], prior=prior)
