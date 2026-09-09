import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.adk.code_mode import ExecuteCodeTool


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
