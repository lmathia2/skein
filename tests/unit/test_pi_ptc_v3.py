from __future__ import annotations

import time
import subprocess
from types import SimpleNamespace

from harness.ptc.repl import PersistentPythonWorker, PythonExecutionResult
from harness.ptc.repl.preflight import helper_contract
from scripts.pi_code_tool_harbor import (
    _PierPtcBroker,
    _bounded_head_tail,
    _price_usage,
    _pricing_from_catalog,
    _prior_binding_reads,
    _ptc_record,
    _ptc_response,
    _load_plain_checkpoint,
    _model_contract,
    _save_plain_checkpoint,
)


def test_openrouter_catalog_pricing_and_usage_cost():
    rates = _pricing_from_catalog({'data': [{'id': 'meta/muse', 'pricing': {
        'prompt': '0.0000001', 'completion': '0.0000002',
        'input_cache_read': '0.000000002'}}]}, 'meta/muse')
    assert {key: round(value, 6) for key, value in rates.items()} == {
        'input': .1, 'output': .2, 'cacheRead': .002, 'cacheWrite': 0}
    usage = {'input': 1_000_000, 'cache': 2_000_000, 'cache_write': 3_000_000,
             'output': 500_000}
    assert round(_price_usage(usage, rates), 6) == .204


class Broker(_PierPtcBroker):
    def __init__(self):
        self.outcomes = []
        self.calls = []

    def write(self, path: str, content: str, expected_sha256: str | None = None,
              expected_absent: bool = False):
        self.calls.append(path)
        return {'status': 'ok', 'data': {'path': path, 'changed': True, 'sha256': '', 'diff': '+new\n-old'}}

    def bash(self, command: str, timeout_seconds: int = 120):
        time.sleep(.15)
        return {'status': 'timeout', 'data': {'stdout': 'partial', 'stderr': 'failed', 'exit_code': 124}}

    verify = bash


def test_preflight_blocks_four_errors_before_effects_and_abstains_on_opaque_values():
    broker = Broker()
    with PersistentPythonWorker(helper_contract=helper_contract(_PierPtcBroker)) as worker:
        for tail in ["bash('x', workdir='x')", "bash('x')['data']['stdot']", 'missing_name', '1 + "x"']:
            result = worker.execute("write('x', 'x')\n" + tail, broker, 5)
            assert result.failure_stage == 'source_validation', result
            assert 'No code in this cell executed' in result.error_message
        assert broker.calls == []
        assert worker.execute('saved = 3', broker, 5).status == 'ok'
        assert worker.execute('saved + 1', broker, 5).value_repr == '4'
        result = worker.execute("def identity(x):\n    return x\nidentity({'stdot': 4})['stdot']", broker, 5)
        assert result.value_repr == '4'
        assert worker.execute("bash = lambda **kw: 42\nbash(workdir='x')", broker, 5).value_repr == '42'
        assert worker.execute("bash(workdir='x')", broker, 5).value_repr == '42'


def test_model_contract_uses_real_signatures_without_internal_envelopes():
    contract = _model_contract(helper_contract(_PierPtcBroker))
    assert 'bash(command: str, timeout_seconds: int = 120) -> shell text' in contract
    assert 'exit_code' in contract and 'verify' in contract
    assert "'data'" not in contract and "'status'" not in contract


def test_shell_budget_is_per_call_and_preserves_state():
    with PersistentPythonWorker() as worker:
        worker.execute('sentinel = 42', Broker(), 5)
        result = worker.execute("bash('slow', 1); bash('slow', 1); sentinel", Broker(), .1,
                                shell_timeout_margin=.1)
        assert result.value_repr == '42', result
        assert worker.execute('sentinel', Broker(), 5).value_repr == '42'
        assert worker.execute('while True: pass', Broker(), .1).status == 'timeout'


def test_direct_helpers_are_readable_strings_with_legacy_mapping_and_verify_gate():
    with PersistentPythonWorker(helper_contract=helper_contract(_PierPtcBroker)) as worker:
        result = worker.execute("receipt = write('x', 'x'); print(receipt); (receipt.changed, receipt['data']['changed'])", Broker(), 5)
        assert result.status == 'ok' and 'write: x — changed' in result.stdout
        assert result.value_repr == '(True, True)'
        result = worker.execute("output = bash('bad'); print(output); (output.exit_code, output['data']['stderr'])", Broker(), 5)
        assert result.status == 'ok' and '[stderr]' in result.stdout
        assert result.value_repr == "(124, 'failed')"
        result = worker.execute("verify('bad')", Broker(), 5)
        assert result.status == 'error' and result.error_type == 'AssertionError'


def test_projection_diagnostics_receipts_caps_and_retention():
    result = PythonExecutionResult(status='ok', stdout='x' * 100000, state_delta=('b',))
    outcome = {'operation': 'bash', 'command': 'test', 'status': 'error',
               'data': {'stdout': 'x', 'stderr': 'compiler failure', 'exit_code': 1}}
    response = _ptc_response(result, 'r1', [outcome], {'b'})
    assert 'compiler failure' in response['text'] and '[exit 1]' in response['text']
    assert len(response['text'].encode()) <= 51200
    assert 'New variables' not in response['text']
    assert response['details']['broker_outcomes'][0]['selected_omitted_exit']
    assert _ptc_response(PythonExecutionResult(status='ok', value_repr='42'), 'value')['text'] == '=> 42'
    value = {'status': 'ok', 'data': {'path': 'x', 'changed': True, 'diff': '+new\n-old'}}
    outcome = {'operation': 'write', **value}
    result = PythonExecutionResult(status='ok', value_repr=repr(value), full_value_repr=repr(value))
    assert '+1/-1' in _ptc_response(result, 'r2', [outcome])['text']
    assert '+new' in _ptc_record(result, 'r2', [outcome])
    with PersistentPythonWorker(max_output_bytes=1024) as worker:
        result = worker.execute("'é' * 2000", Broker(), 5)
        assert len(result.full_value_repr) == 2002
        assert 'é' * 2000 in _ptc_record(result, 'r3')
    assert _prior_binding_reads("b = bash('x'); print(b)", {'b'}) == []
    assert _prior_binding_reads('print(b); b = 4', {'b'}) == ['b']


def test_preflight_abstains_after_dynamic_mutations_and_never_calls_repr_hooks():
    with PersistentPythonWorker(helper_contract=helper_contract(_PierPtcBroker)) as worker:
        result = worker.execute("items = [(new_name := i) for i in range(3)]\nnew_name", Broker(), 5)
        assert result.value_repr == '2', result
        result = worker.execute("def change():\n    global new_name\n    new_name = 'x'\nchange()\nnew_name + 'y'", Broker(), 5)
        assert result.value_repr == "'xy'", result
        result = worker.execute("class Sneaky:\n    def __repr__(self):\n        raise RuntimeError('hook executed')\nSneaky()", Broker(), 5)
        assert result.value_repr == '<Sneaky; inspect explicitly>', result


def test_many_failures_fit_observation_without_hiding_later_exit_codes():
    outcomes = [{'operation': 'bash', 'command': 'x' * 1000, 'status': 'error',
                 'data': {'exit_code': i + 1, 'stderr': 'diagnostic\n' * 1000}} for i in range(64)]
    result = PythonExecutionResult(status='ok', stdout='line\n' * 10000)
    response = _ptc_response(result, 'r', outcomes)
    assert '[exit 64]' in response['text']
    assert len(response['text'].encode()) <= 51200
    assert len(response['text'].splitlines()) <= 2000


def test_head_tail_projection_and_review_trigger():
    value = 'start\n' + 'x' * 1000 + '\nimportant failure at end'
    projected, clipped = _bounded_head_tail(value, 120)
    assert clipped and projected.startswith('start') and projected.endswith('important failure at end')
    extension = str(__import__('pathlib').Path(__file__).parents[2] / 'scripts/pi_skein_ptc_extension.mjs')
    script = (f"import {{advanceEvidence, assemblePrompt, needsEvidenceReview}} from {extension!r};"
              "let [m,v]=advanceEvidence([{operation:'bash',status:'ok'}],0,0);"
              "if (m!==1 || v!==0) process.exit(1);"
              "[m,v]=advanceEvidence([{operation:'verify',status:'ok'}],m,v);"
              "if (m!==2 || v!==2) process.exit(1);"
              "const p=assemblePrompt('verify(command: str)');"
              "if (!p.includes('bash(...) never counts') || p.includes('agent.state') || p.includes('code(more=')) process.exit(1);"
              "if (!needsEvidenceReview('', 2, 1) || !needsEvidenceReview('Known gap: x', 1, 1) || needsEvidenceReview('done; no known gaps', 1, 1)) process.exit(1)")
    subprocess.run(['node', '--input-type=module', '-e', script], check=True)


def test_verify_is_pipe_safe_and_bash_keeps_normal_shell_semantics():
    class Environment:
        commands = []

        def exec(self, command, **_kwargs):
            self.commands.append(command)
            return command

    class Bridge:
        def call(self, _value):
            return SimpleNamespace(stdout='', stderr='', return_code=0)

    environment = Environment()
    broker = _PierPtcBroker(environment, Bridge(), None, '/workspace')
    assert broker.bash('false | true')['status'] == 'ok'
    assert 'bash -o pipefail -c' not in environment.commands[-1]
    assert broker.verify('false | true')['status'] == 'ok'
    assert 'bash -o pipefail -c' in environment.commands[-1]


def test_plain_checkpoint_survives_worker_replacement(tmp_path):
    checkpoint_path = tmp_path / 'checkpoint.json'
    with PersistentPythonWorker(capture_committed=True) as worker:
        result = worker.execute("kept = {'items': [1, 2]}\ndef opaque(): return 3", Broker(), 5,
                                cell_id='cell-1')
    saved = _save_plain_checkpoint(checkpoint_path, result, 'cell-1')
    assert saved['values'] == {'kept': {'items': [1, 2]}}
    assert saved['omitted_names'] == ['opaque']
    loaded = _load_plain_checkpoint(checkpoint_path)
    with PersistentPythonWorker() as replacement:
        replacement.restore_plain(loaded['values'], loaded['source_cell_id'])
        restored = replacement.execute("kept, 'opaque' in dir()", Broker(), 5)
    assert restored.value_repr == "({'items': [1, 2]}, False)"


def test_process_output_is_durable_before_timeout(tmp_path):
    import asyncio
    import os
    import sys
    from scripts.pi_code_tool_harbor import _run_pi_process

    async def check():
        task = asyncio.create_task(_run_pi_process(
            [sys.executable, '-u', '-c', "import time; print('saved'); time.sleep(30)"],
            cwd=str(tmp_path), env=os.environ.copy(), logs_dir=tmp_path, timeout_seconds=1))
        for _ in range(50):
            await asyncio.sleep(.01)
            path = tmp_path / 'pi-events.jsonl'
            if path.exists() and 'saved' in path.read_text():
                break
        assert path.read_text() == 'saved\n'
        assert not task.done()
        _, timed_out = await task
        assert timed_out
        assert path.read_text() == 'saved\n'
    asyncio.run(check())
