"""Explicit opt-in live check for the prebuilt Docker image (no model calls)."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from harness.adk.code_mode.runtime.protocol import DoneFrame, ReadyFrame, RunFrame
from harness.evals.ptc_worker import ReusableDockerBackend


@pytest.mark.asyncio
async def test_prebuilt_image_reuses_session_but_not_cross_run_state(tmp_path):
    image = os.environ.get("SKEIN_TEST_ADK_IMAGE")
    if not image:
        pytest.skip("set SKEIN_TEST_ADK_IMAGE to opt into local Docker execution")
    pool = tempfile.TemporaryDirectory(prefix=".skein-ptc-", dir=Path.cwd())
    backend = ReusableDockerBackend(image, Path(pool.name))

    async def check(codes):
        workspace = tmp_path / f"example-{len(list(tmp_path.glob('example-*')))}"
        workspace.mkdir()
        session = await backend.start(
            tools_files={}, workdir_path=str(workspace), timeout_seconds=10
        )
        outputs = []
        try:
            async with asyncio.timeout(20):
                assert isinstance(await anext(session.frames()), ReadyFrame)
                for code in codes:
                    await session.begin_block([])
                    await session.send(RunFrame(code=code))
                    async for frame in session.frames():
                        if isinstance(frame, DoneFrame):
                            break
                    result = await session.wait()
                    assert result.exit_code == 0, result.stderr
                    outputs.append(result.stdout.strip())
        finally:
            await session.close()
        return workspace, outputs

    try:
        first, output = await check(
            [
                """import pathlib, subprocess
value = 40
pathlib.Path('/tmp/prior').write_text('secret')
pathlib.Path('prior.txt').write_text('first')
pathlib.Path('unsafe-link').symlink_to('/etc/passwd')
subprocess.Popen(['sleep', '99'])
try: pathlib.Path('/tools/tamper').write_text('bad')
except OSError: print('tools-read-only')
print(value)""",
                "value += 2; print(value)",
            ]
        )
        assert output == ["tools-read-only\n40", "42"]
        assert (first / "prior.txt").read_text() == "first"
        assert not (first / "unsafe-link").exists()
        _, output = await check(
            [
                """import pathlib
print(globals().get('value', 'fresh'))
print(pathlib.Path('/tmp/prior').exists())
print(pathlib.Path('prior.txt').exists())
print(any(b'sleep\\x0099' in (p / 'cmdline').read_bytes() for p in pathlib.Path('/proc').glob('[0-9]*') if (p / 'cmdline').exists()))"""
            ]
        )
        assert output == ["fresh\nFalse\nFalse\nFalse"]
        assert backend.metrics["ptc_container_starts"] == 1
        assert backend.metrics["ptc_resets"] == 4
    finally:
        await backend.aclose()
        assert not list((Path(pool.name) / "tools").iterdir())
        assert not list((Path(pool.name) / "workspace").iterdir())
        pool.cleanup()
