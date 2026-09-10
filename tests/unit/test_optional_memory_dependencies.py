from __future__ import annotations

import subprocess
import sys


def test_default_cli_and_jsonl_memory_do_not_import_optional_databases() -> None:
    code = r'''
import builtins
import tempfile
from pathlib import Path

original_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split(".", 1)[0] in {"duckdb", "lancedb"}:
        raise ImportError(f"blocked optional dependency: {name}")
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked

import harness.evals.runner
from harness.ledger import JsonlLedgerStore

assert harness.evals.runner is not None
with tempfile.TemporaryDirectory() as directory:
    ledger = JsonlLedgerStore(Path(directory) / "ledger.jsonl")
    ledger.append(task_id="task", source="test", source_id="one", kind="observed")
    assert ledger.read("task")[0].kind == "observed"
'''
    subprocess.run((sys.executable, "-c", code), check=True)
