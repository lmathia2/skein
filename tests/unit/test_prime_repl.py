from pathlib import Path

import pytest

from harness.repl.prime import PrimeRuntime


@pytest.mark.asyncio
async def test_prime_session_jsonl_restore_ownership_and_redaction(tmp_path: Path) -> None:
    from typing import cast

    from google.adk.models import BaseLlm

    from app.agent.builders import build_coding_worker
    from app.agent.config import settings_from_composition
    from harness.config import (
        RuntimeBindings,
        SkeinConfig,
        load_harness_composition,
        parse_harness_composition,
    )
    from harness.safety.redaction import SecretRedactor
    from harness.state import JsonlEventStore

    payload = load_harness_composition().model_dump(mode="python")
    payload["harness"]["config"]["notebook_ptc"] = {
        "enabled": True, "implementation": "prime_repl", "serialization": "jsonl",
        "state": "snapshot", "prime_native_execution": True,
    }
    composition = parse_harness_composition(payload)
    config = cast(SkeinConfig, composition.harness.config)
    settings = settings_from_composition(composition, RuntimeBindings(
        workspace=tmp_path, state_root=tmp_path / "state", task_id="task", project_trusted=True,
    ))
    events = JsonlEventStore(tmp_path / "state" / "events")

    def build():
        return build_coding_worker(
            settings, cast(BaseLlm, "test-model"), ptc_config=config.notebook_ptc,
            event_store=events, redactor=SecretRedactor(known_secrets=["test-secret-value"]),
        )

    worker = build()
    with pytest.raises(RuntimeError, match="active owner"):
        build()
    assert worker.execute_code is not None and worker.close is not None
    try:
        assert (await worker.execute_code("value = 41"))["status"] == "ok"
        assert (await worker.execute_code("value = 42\n1 / 0"))["status"] == "error"
        result = await worker.execute_code("print('test-secret-value')\nvalue")
        assert "test-secret-value" not in str(result)
        assert "42" in result["model_text"]
    finally:
        worker.close()
    replacement = build()
    assert replacement.execute_code is not None and replacement.close is not None
    try:
        assert (await replacement.execute_code("value"))["model_text"] == "42"
        with pytest.raises(TimeoutError):
            await replacement.execute_code("while True: pass", timeout_seconds=1)
        assert (await replacement.execute_code("value"))["reconciliation_required"]
    finally:
        replacement.close()
    assert not list(tmp_path.rglob("*.ipynb"))
    assert list(tmp_path.rglob("*.dill"))
    assert any(event.kind == "prime.state_restored" for event in events.read("task"))
    assert "test-secret-value" not in "\n".join(event.model_dump_json() for event in events.read("task"))


def test_prime_native_state_error_snapshot_and_bounds(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    popen = subprocess.Popen
    # No site-packages in the child: snapshot support must come from the wheel.
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: popen(
        [command[0], "-S", *command[1:]], **kwargs,
    ))
    runtime = PrimeRuntime(tmp_path, max_output_bytes=128)
    runtime.start()
    try:
        assert runtime.request("execute", code="value = 40\nawait asyncio.sleep(0)\nvalue + 2")["result"] == "42"
        assert runtime.request("execute", code="import dill\n'_vendor/dill' in dill.__file__")["result"] == "True"
        assert runtime.request("execute", code="value = 99\n1 / 0")["status"] == "error"
        assert runtime.request("execute", code="value")["result"] == "99"
        assert runtime.request("execute", code="print('x' * 1000)")["truncated"]
        receipt = runtime.snapshot(tmp_path / "snapshots")
    finally:
        runtime.close()
    runtime.start()
    try:
        assert runtime.restore(receipt, tmp_path / "snapshots")["status"] == "ok"
        assert runtime.request("execute", code="value")["result"] == "99"
        with pytest.raises(RuntimeError, match="integrity"):
            runtime.restore({**receipt, "sha256": "invalid"}, tmp_path / "snapshots")
        with pytest.raises(TimeoutError):
            runtime.request("execute", code="while True: pass", timeout=0.1)
        assert runtime.process is None
    finally:
        runtime.close()
