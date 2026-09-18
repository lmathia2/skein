import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from google.adk.models import BaseLlm

from app.agent.builders import build_coding_worker
from app.agent.config import settings_from_composition
from harness.adapters.pier import HarborWorkspaceEnvironment, _AsyncBridge
from harness.core.config import RuntimeBindings, SkeinConfig, load_harness_composition
from harness.evidence.state import JsonlEventStore
from harness.evidence.state.receipts import ToolReceiptStore
from harness.evidence.state.recovery import unresolved_execution
from harness.execution.environment import LocalWorkspaceEnvironment
from harness.execution.tools import execute_edit, execute_write


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
@pytest.mark.parametrize('case', ['write_hash', 'write_absent', 'missing_parent', 'missing_empty', 'edit_hash', 'edit_missing', 'edit_ambiguous', 'edit_binary'])
async def test_file_conflicts_reject_before_local_or_remote_workspace_mutation(tmp_path, remote, case):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'sample.txt'
    original = b'\xff\x00' if case == 'edit_binary' else b'old\nold\n' if case == 'edit_ambiguous' else b'old\n'
    target.write_bytes(original)
    remote_writes = []

    class RemoteFiles:
        async def is_file(self, path):
            return Path(path).is_file()
        async def download_file(self, source, destination):
            Path(destination).write_bytes(Path(source).read_bytes())
        async def upload_file(self, *args, **kwargs):
            remote_writes.append('upload')
            raise AssertionError('precondition rejection cannot upload')
        async def exec(self, *args, **kwargs):
            remote_writes.append('exec')
            raise AssertionError('precondition rejection cannot dispatch mutation commands')

    environment = (HarborWorkspaceEnvironment(RemoteFiles(), _AsyncBridge(asyncio.get_running_loop()), workspace.as_posix())
                   if remote else LocalWorkspaceEnvironment(workspace))
    if case.startswith('write') or case in ('missing_parent', 'missing_empty'):
        result = await asyncio.to_thread(execute_write, environment,
            'absent/nested/sample.txt' if case in ('missing_parent', 'missing_empty') else 'sample.txt', '' if case == 'missing_empty' else 'new\n',
            expected_sha256=None if case == 'write_absent' else '0' * 64, expected_absent=case == 'write_absent')
    else:
        result = await asyncio.to_thread(execute_edit, environment, 'sample.txt',
            'missing' if case == 'edit_missing' else 'old', 'new', expected_sha256='0' * 64 if case == 'edit_hash' else None)
    assert result.status == 'error' and result.effect == 'none'
    assert not result.changed_paths and target.read_bytes() == original
    assert not (workspace / 'absent').exists() and not remote_writes


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,late_failure', [('write', False), ('edit', False), ('write', True), ('edit', True)])
async def test_ptc_preserves_conflict_effect_in_receipts_but_fences_failures_after_mutation(tmp_path, monkeypatch, operation, late_failure):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'sample.txt').write_text('old\n')
    state = tmp_path / 'state'
    composition = load_harness_composition()
    config = cast(SkeinConfig, composition.harness.config)
    events = JsonlEventStore(state / 'events')
    if late_failure:
        original = LocalWorkspaceEnvironment.atomic_write
        def fail_after_write(self, path, content, **kwargs):
            result = original(self, path, content, **kwargs)
            assert result.changed
            raise ValueError('failure after mutation')
        monkeypatch.setattr(LocalWorkspaceEnvironment, 'atomic_write', fail_after_write)
    worker = build_coding_worker(
        settings_from_composition(composition, RuntimeBindings(workspace=workspace, state_root=state, task_id='task')),
        cast(BaseLlm, 'test-model'), ptc_config=config.notebook_ptc.model_copy(update={'enabled': True}), event_store=events)
    assert worker.execute_code and worker.close
    try:
        args = "'sample.txt', 'new\\n'" if operation == 'write' else "'sample.txt', 'old', 'new'"
        guard = '' if late_failure else ", expected_sha256='" + '0' * 64 + "'"
        result = await worker.execute_code(f"rejected = agent.fs.{operation}({args}{guard})\nassert rejected['status'] == 'error'")
        assert result['status'] == 'ok' and result['effect'] == ('unknown' if late_failure else 'none')
        recorded = events.read('task')
        terminal = next(e for e in recorded if e.kind == 'capability.failed')
        assert terminal.payload['effect'] == result['effect']
        uri = terminal.payload['result_artifact_uri']
        saved = json.loads((state / 'artifacts' / 'sha256' / uri.rsplit('/', 1)[-1]).read_text())
        receipts = ToolReceiptStore(state / 'managed-tools.db').for_task('task')
        assert len(receipts) == 1 and receipts[0].status == 'failed'
        if late_failure:
            assert saved.get('effect') != 'none'
            assert unresolved_execution(recorded, receipts)
            assert (workspace / 'sample.txt').read_text() == 'new\n'
        else:
            assert saved['effect'] == json.loads(receipts[0].result_json)['effect'] == 'none'
            assert not unresolved_execution(recorded, receipts)
            assert (workspace / 'sample.txt').read_text() == 'old\n'
            repaired = await worker.execute_code(
                "current = agent.fs.read('sample.txt')\nassert current['status'] == 'ok'\n"
                "done = agent.fs.write('sample.txt', 'repaired\\n', expected_sha256=current['data']['sha256'])\nassert done['status'] == 'ok'")
            assert repaired['status'] == 'ok' and (workspace / 'sample.txt').read_text() == 'repaired\n'
            assert not unresolved_execution(events.read('task'), ToolReceiptStore(state / 'managed-tools.db').for_task('task'))
    finally:
        worker.close()
