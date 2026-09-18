"""Six development cases: new formats, meanings, and continuity transitions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from google.genai import types

from evals.continuity import Continuation
from evals.runner import _atomic_write
from harness.evidence.memory.models import ReadEvidence

CASES = ("routing", "missing", "restart", "freshness", "correction", "join")


def decisive_sources(case: dict) -> list[ReadEvidence]:
    """Host-owned line requirements; never injected as answers into the prompt."""
    family, target = case["family"], case["target"]
    ranges = {
        "routing": [(2, 1), (12, 2)], "missing": [(2, 1), (12, 2)],
        "restart": [(2, 3)], "freshness": [(2, 3)], "correction": [(1, 1), (3, 1)],
        "join": [(2, 1), (5, 2)],
    }[family]
    sources = dict(case["files"])
    if family == "freshness":
        value = json.loads(sources[target])
        value.update(region="ap-south", generation=4)
        sources[target] = json.dumps(value, indent=2) + "\n"
    selected = [(target, offset, count) for offset, count in ranges]
    if family == "join":
        selected.append(("config/backoff.toml", 2, 1))
    return [ReadEvidence(path=path, sha256=hashlib.sha256(sources[path].encode()).hexdigest(),
                         offset=offset, returned_lines=count) for path, offset, count in selected]


def fixture(case: str) -> dict:
    if case not in CASES:
        raise ValueError("unknown development case")
    files = {}
    for index in range(16):
        if case in {"routing", "missing"}:
            path = f"config/route_{index:02}.toml"
            content = f'[service]\nname = "orchid-{index}"\nretired_port = {7000 + index}\n'
            content += "# public service configuration\n" * 8
            content += f'active_port = {8400 + index}\nprotocol = "https"\n'
        elif case in {"restart", "freshness", "join"}:
            path = f"config/route_{index:02}.json"
            content = json.dumps({"name": f"orchid-{index}", "region": "eu-north",
                                  "generation": 3, "policy": "backoff.toml", "attempts": 3}, indent=2) + "\n"
        else:
            path = f"config/route_{index:02}.py"
            content = f'NAME = "orchid-{index}"\nFAST_LIMIT = {64 + index}\nSAFE_LIMIT = {12 + index}\n'
        files[path] = content
    target = next(iter(files))
    related = [target]
    limits = {path: (3 if case == "missing" else 100) for path in files}
    learned = f"The route named orchid-0 is defined in {target}."
    if case in {"routing", "missing"}:
        expected = {"port": 8400, "protocol": "https"}
        requirement = "Return its active_port as port and its protocol; retired_port is not active."
        learned += " Its active_port is 8400 and protocol is https." if case == "routing" else (
            " Only the three-line header was captured; the active port and protocol remain unread.")
    elif case == "restart":
        expected = {"region": "eu-north", "generation": 3}
        requirement = "Return its region and generation. The Python worker has restarted; recover retained evidence if applicable."
        learned += " The captured region is eu-north and generation is 3."
    elif case == "freshness":
        expected = {"region": "ap-south", "generation": 4}
        requirement = "Return its CURRENT region and generation. An external update happened after the checkpoint."
        learned += " At capture time the region was eu-north and generation was 3; this is historical only."
    elif case == "correction":
        expected = {"limit": 12}
        requirement = "Return the limit using the recorded corrected approach, not the rejected fast-path choice."
        learned += " FAST_LIMIT is 64; SAFE_LIMIT is 12."
    else:
        files["config/backoff.toml"] = '[retry]\npause_ms = 240\n'
        limits["config/backoff.toml"] = 100
        related.append("config/backoff.toml")
        expected = {"wait_ms": 720}
        requirement = "Return wait_ms = attempts multiplied by pause_ms from its referenced policy file."
        learned += " It sets attempts=3 and references backoff.toml; config/backoff.toml sets pause_ms=240."
    goal = (f"Continue work on the route named orchid-0. {requirement} "
            f"Write answer.json with exactly these keys: {', '.join(expected)}; do not add route or other keys. "
            "Reuse applicable retained findings "
            "and source ranges; fetch only necessary missing or changed evidence. Do not dump whole files. "
            "Completion requires completed managed source reads covering the decisive fields before the answer write; "
            "previously captured applicable ranges already satisfy this requirement and need not be reread. "
            "Then request verification; a self-check is not the independent completion verdict. "
            "The required oracle is a host-owned virtual test target, not a readable test file; "
            "request verification rather than searching for oracle code.")
    return {"family": case, "variant": 0, "files": files, "target": target, "related": related,
            "limits": limits, "learned": learned, "expected": expected, "goal": goal}


class DevelopmentContinuation(Continuation):
    def __init__(self, root: Path, case: str, arm: str):
        super().__init__(root, "navigation", 0, arm)
        self.fixture = fixture(case)
        self.task_id = f"development-{case}-{arm}"

    async def prepare(self) -> None:
        if self.root.exists():
            raise ValueError("refusing to overwrite a trial directory")
        self.workspace.mkdir(parents=True)
        for path, content in self.fixture["files"].items():
            _atomic_write(self.workspace / path, content)
        self.open()
        self.history = [types.Content(role="user", parts=[types.Part.from_text(text=self.fixture["goal"])])]
        target = self.fixture["target"]
        await self.cell(
            f"limits = {self.fixture['limits']!r}\n"
            "sources = {path: agent.fs.read(path, limit=limit) for path, limit in limits.items()}\n"
            "assert all(result['status'] == 'ok' for result in sources.values())\n"
            f"agent.state.annotate('sources', 'Captured evidence for orchid-0', selector=({target!r}, 'data', 'text'))\n"
            f"print(sources[{target!r}]['data']['text'])", seed=True)
        entry = {"id": "route", "kind": "observation", "text": self.fixture["learned"],
                 "related_paths": self.fixture["related"]}
        await self.cell(
            f"entries = [{entry!r}]\n"
            f"entries[0]['evidence_refs'] = [sources[p]['read_reference']['artifact_uri'] for p in {self.fixture['related']!r}]\n"
            + ("entries.append({'id': 'reject-fast', 'kind': 'rejected_approach', 'text': 'The fast-path limit was rejected for the current route; use SAFE_LIMIT.'})\n"
               if self.fixture["family"] == "correction" else "")
            + "import json, shlex\n"
            "note = agent.shell.run('memory note write --text ' + shlex.quote('Checkpoint retained; consult the evidence-linked findings for the next step.') "
            "+ ' --expected-version 0 --operation-id development-seed --entries ' + shlex.quote(json.dumps(entries)))\n"
            "assert note['status'] == 'ok'\nprint(note['model_text'])", seed=True)
        if self.fixture["family"] == "freshness":
            value = json.loads(self.fixture["files"][target])
            value.update(region="ap-south", generation=4)
            _atomic_write(self.workspace / target, json.dumps(value, indent=2) + "\n")
        if self.fixture["family"] == "restart":
            await self.close()
            self.open()
        self.history.extend([types.Content(role="user", parts=[types.Part.from_text(text="Checkpoint padding. " * 3000)]),
                             types.Content(role="user", parts=[types.Part.from_text(text=self.fixture["goal"])])])
        _atomic_write(self.root / "config.json", self.composition.model_dump_json(indent=2))
        # Keep expected answers only in host memory during the trial. The manifest
        # identifies fixture source bytes without duplicating them beside the workspace.
        _atomic_write(self.root / "fixture.json", json.dumps({
            "case": self.fixture["family"], "goal": self.fixture["goal"],
            "source_hashes": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in self.fixture["files"].items()},
        }, indent=2))
