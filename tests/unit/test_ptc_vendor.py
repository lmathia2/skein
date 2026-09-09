import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.adk.code_mode import ExecuteCodeTool
from harness.adk.code_mode.output import truncate
from harness.adk.code_mode.tool import (
    _BlockRunState,
    _connection_lost_result,
    _timeout_result,
)
from harness.tools.output import compact_tool_result


def test_vendored_sources_match_upstream_hashes():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "harness/_vendor/SOURCES.json").read_text())
    for path, expected in manifest.items():
        content = (root / path).read_bytes().replace(b'from harness._vendor.docker', b'from docker')
        assert hashlib.sha256(content).hexdigest() == expected, path


def test_adk_sdk_imports_without_installed_docker_or_dill():
    script = '''
import sys
import importlib.abc
class RejectExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'docker', 'dill'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, RejectExtras())
from harness._vendor import docker
from harness.adk.code_mode import ExecuteCodeTool, UnsafeLocalDockerBackend
client = docker.DockerClient(base_url='unix:///tmp/no-daemon.sock', version='1.45')
assert '_vendor/docker' in docker.__file__
assert client.api.base_url == 'http+docker://localhost'
client.close()
'''
    subprocess.run([sys.executable, "-c", script], check=True)


def test_adk_sandbox_image_is_reproducible_and_install_free():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "harness/adk/code_mode_sandbox/Dockerfile").read_text()
    assert "python:3.12-slim@sha256:" in dockerfile
    assert "COPY *.py /opt/skein/harness/adk/code_mode_sandbox/" in dockerfile
    assert "pip install" not in dockerfile


def test_adk_uncertain_execution_results_require_reconciliation() -> None:
    assert _timeout_result(10)["status"] == "timeout"
    uncertain = _connection_lost_result(_BlockRunState.UNKNOWN)
    assert uncertain["status"] == "blocked"
    assert uncertain["reconciliation_required"] is True
    not_run = _connection_lost_result(_BlockRunState.NOT_RUN)
    assert not_run["status"] == "error"
    assert not_run["reconciliation_required"] is False


@pytest.mark.asyncio
async def test_execute_code_close_releases_turns_without_after_agent() -> None:
    closed: list[str] = []

    async def close() -> None:
        closed.append("turn")

    backend = SimpleNamespace(identity="test")
    tool = ExecuteCodeTool(backend=backend, include_artifact_tools=False)
    tool._turns["invocation"] = SimpleNamespace(aclose=close)

    await tool.aclose()
    await tool.aclose()

    assert closed == ["turn"]


@pytest.mark.asyncio
async def test_adk_output_spills_to_shared_artifact_and_keeps_failure_status() -> None:
    saved: list[bytes] = []
    bounded = await truncate(
        "x" * 1_000,
        limit=100,
        stream_name="stdout",
        execution_id="cell",
        invocation_context=SimpleNamespace(artifact_service=None),
        artifact_writer=lambda content: saved.append(content) or "artifact://sha256/result",
    )
    result = compact_tool_result(
        {
            "status": "error",
            "stdout": bounded.text,
            "exit_code": 7,
            "artifact_uris": [bounded.artifact_uri],
            "truncated": True,
            "omitted_bytes": bounded.omitted_bytes,
        },
        max_chars=500,
    )

    assert saved == [b"x" * 1_000]
    assert result["status"] == "error" and result["exit_code"] == 7
    assert result["artifact_uris"] == ["artifact://sha256/result"]
    assert result["omitted_bytes"] > 0
