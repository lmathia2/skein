from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.adk.code_mode.runtime.base import SandboxBackend
from harness.evals.ptc_worker import ReusableDockerBackend


class _Container:
    def __init__(self) -> None:
        self.removed = False
        self.fail_reset = False

    def exec_run(self, *_args, **_kwargs):
        return SimpleNamespace(exit_code=1 if self.fail_reset else 0)

    def remove(self, *, force: bool) -> None:
        assert force
        self.removed = True


class _Client:
    def __init__(self, containers: list[_Container]) -> None:
        self.created = containers
        self.images = SimpleNamespace(get=lambda _image: SimpleNamespace(id="sha256:pinned"))
        self.containers = SimpleNamespace(run=self.run)

    def run(self, *_args, **_kwargs):
        container = _Container()
        self.created.append(container)
        return container

    def close(self) -> None:
        pass


def test_reset_failure_discards_dirty_container(monkeypatch, tmp_path: Path) -> None:
    from harness._vendor import docker

    created: list[_Container] = []
    monkeypatch.setattr(docker, "from_env", lambda **_kwargs: _Client(created))
    worker = ReusableDockerBackend("image:tag", tmp_path / "pool")
    assert isinstance(worker, SandboxBackend)
    assert worker.identity == "sha256:pinned"
    worker._ensure_container()
    first = created[-1]
    (worker.workspace / "leak").write_text("secret")
    (worker.tools / "tamper").write_text("bad")
    first.fail_reset = True
    with pytest.raises(RuntimeError, match="verify clean state"):
        worker._reset()
    assert first.removed and worker.container is None
    worker._ensure_container()
    assert len(created) == 2
    worker._reset()
    assert not list(worker.workspace.iterdir())
    assert not list(worker.tools.iterdir())


@pytest.mark.asyncio
async def test_worker_rejects_concurrent_lease_and_unsafe_tools(
    monkeypatch, tmp_path: Path
) -> None:
    from harness._vendor import docker

    monkeypatch.setattr(docker, "from_env", lambda **_kwargs: _Client([]))
    worker = ReusableDockerBackend("image:tag", tmp_path / "pool")
    workspace = tmp_path / "turn"
    workspace.mkdir()
    worker.leased = True
    with pytest.raises(RuntimeError, match="already leased"):
        await worker.start(tools_files={}, workdir_path=str(workspace), timeout_seconds=1)
    worker.leased = False
    with pytest.raises(ValueError, match="escapes"):
        await worker.start(
            tools_files={"../escape.py": "bad"},
            workdir_path=str(workspace),
            timeout_seconds=1,
        )
