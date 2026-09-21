"""Host-side Pier adapter for the isolated Pi Code Tool comparison."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, wraps
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from harness.adapters.pier import HarborWorkspaceEnvironment, _AsyncBridge
from harness.evidence.state import JsonlEventStore
from harness.execution.environment import sha256_bytes
from harness.ptc.repl import PersistentPythonWorker, default_help_catalog
from harness.ptc.repl.preflight import helper_contract
from scripts.pi_harbor_mcp import TOOLS

try:
    from pier.agents.base import BaseAgent
    from pier.environments.base import BaseEnvironment
    from pier.models.agent.context import AgentContext
except ImportError:
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext

ROOT = Path(__file__).resolve().parents[1]
PI_CLI = Path('/Users/mathiasl/src/pi/packages/coding-agent/dist/bundle/cli.js')
EXTENSION = ROOT / 'scripts/pi_code_tool_extension.mjs'
PACKAGE = Path('/Users/mathiasl/src/pi-code-tool')
PTC_OBSERVATION_BYTES = 50 * 1024
PTC_RESULT_BYTES = 256_000
PTC_RESULT_COUNT = 32
OPENROUTER_MODELS_URL = 'https://openrouter.ai/api/v1/models'
MLX_DSPARK_BASE_URL = 'http://127.0.0.1:8484/v1'


def _pricing_from_catalog(payload: dict, model_name: str) -> dict[str, float]:
    model = next((item for item in payload.get('data', []) if item.get('id') == model_name), None)
    if model is None:
        raise ValueError(f'OpenRouter catalog has no exact model {model_name!r}')
    pricing = model.get('pricing') or {}
    if not pricing.get('prompt') or not pricing.get('completion'):
        raise ValueError(f'OpenRouter catalog has incomplete pricing for {model_name!r}')
    return {
        'input': float(pricing['prompt']) * 1_000_000,
        'output': float(pricing['completion']) * 1_000_000,
        'cacheRead': float(pricing.get('input_cache_read') or 0) * 1_000_000,
        'cacheWrite': float(pricing.get('input_cache_write') or 0) * 1_000_000,
    }


@lru_cache
def _openrouter_pricing(model_name: str) -> dict:
    request = Request(OPENROUTER_MODELS_URL, headers={'User-Agent': 'skein-eval/1'})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return {
        'model': model_name,
        'source': OPENROUTER_MODELS_URL,
        'fetched_at_unix': int(time.time()),
        'usd_per_million_tokens': _pricing_from_catalog(payload, model_name),
    }


def _price_usage(usage: dict, rates: dict[str, float]) -> float:
    return sum((
        usage['input'] * rates['input'],
        usage['cache'] * rates['cacheRead'],
        usage['cache_write'] * rates['cacheWrite'],
        usage['output'] * rates['output'],
    )) / 1_000_000


def _provider_config(provider_name: str, model_name: str,
                     max_output_tokens: int | None) -> tuple[dict, dict]:
    if provider_name == 'openrouter':
        pricing = _openrouter_pricing(model_name)
        override = {'cost': pricing['usd_per_million_tokens']}
        if max_output_tokens is not None:
            override['maxTokens'] = max_output_tokens
        return {'providers': {'openrouter': {'modelOverrides': {model_name: override}}}}, pricing
    if provider_name != 'mlx-dspark':
        raise ValueError(f'Unsupported Pi provider: {provider_name}')
    rates = {'input': 0.0, 'output': 0.0, 'cacheRead': 0.0, 'cacheWrite': 0.0}
    model = {'id': model_name, 'name': f'{model_name} (local mlx-dspark)',
             'reasoning': True, 'contextWindow': 100_000,
             'maxTokens': max_output_tokens or 16_384, 'cost': rates,
             'samplingParams': {'temperature': 1.0, 'top_p': .95, 'top_k': 20,
                                'chat_template_kwargs': {'enable_thinking': True}}}
    provider = {'baseUrl': MLX_DSPARK_BASE_URL, 'api': 'openai-completions',
                'apiKey': 'local', 'compat': {'supportsDeveloperRole': False,
                                               'supportsReasoningEffort': False},
                'models': [model]}
    pricing = {'model': model_name, 'source': MLX_DSPARK_BASE_URL,
               'fetched_at_unix': int(time.time()), 'usd_per_million_tokens': rates}
    return {'providers': {'mlx-dspark': provider}}, pricing


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode('utf-8')
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode('utf-8', errors='ignore'), True


def _bounded_head_tail(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode('utf-8')
    if len(encoded) <= limit:
        return value, False
    marker = b'\n...[middle omitted]...\n'
    available = max(limit - len(marker), 0)
    head = encoded[:available * 2 // 3].decode('utf-8', errors='ignore')
    tail = encoded[-(available // 3):].decode('utf-8', errors='ignore') if available else ''
    return head + marker.decode() + tail, True


def _fence(text: str) -> str:
    fence = '`' * max(3, max((len(x) for x in re.findall(r'`+', text)), default=0) + 1)
    return f'{fence}text\n{text}\n{fence}'


def _ptc_record(result, result_id: str, outcomes: list[dict] = ()) -> str:
    parts = [f'Result {result_id}: {result.status}']
    for label, text in [('stdout', result.full_stdout or result.stdout),
                        ('value', result.full_value_repr or result.value_repr),
                        ('stderr', result.full_stderr or result.stderr),
                        ('error', result.error_message)]:
        if text:
            parts.append(f'{label}:\n{text}')
    for outcome in outcomes:
        parts.append(json.dumps(outcome, ensure_ascii=False))
    record = '\n\n'.join(parts)
    clipped, truncated = _bounded_text(record, PTC_RESULT_BYTES - 160)
    return clipped + (f'\n[retention truncated: original {len(record.encode())} bytes; discarded suffix unavailable]' if truncated else '')


def _ptc_response(result, result_id: str, outcomes: list[dict] = (),
                  prior_names: set[str] = frozenset(), reuse: list[str] = (),
                  checkpoint: dict | None = None) -> dict:
    stdout = result.full_stdout or result.stdout
    value = result.full_value_repr or result.value_repr
    stderr = result.full_stderr or result.stderr
    selected = '\n'.join(part for part in (stdout, f'=> {value}' if value else '',
                                            f'[stderr]\n{stderr}' if stderr else '') if part)
    notices, telemetry = [], []
    omitted = result.output_truncated
    for index, outcome in enumerate(outcomes):
        data = outcome.get('data', {})
        operation = outcome['operation']
        notice = None
        if operation in {'bash', 'verify'}:
            stderr = data.get('stderr', '')
            code = data.get('exit_code')
            # Conservative: do not infer visibility from a coincidental substring.
            if stderr or code != 0 or outcome['status'] != 'ok':
                diagnostic, clipped = _bounded_text('\n'.join(stderr.splitlines()[:max(1, 700 // max(1, len(outcomes)))]), min(2000, 18000 // max(1, len(outcomes))))
                clipped |= diagnostic != stderr
                notice = f"Shell call {index + 1}: {outcome['status']} [exit {code}]\nCommand (data):\n{_fence(outcome.get('command', '')[:120])}"
                if diagnostic:
                    notice += f'\nstderr (data):\n{_fence(diagnostic)}'
                if outcome.get('error'):
                    notice += '\n' + outcome['error']
                omitted |= clipped
            telemetry.append({'call': index + 1, 'operation': operation, 'status': outcome['status'],
                'command': outcome.get('command', ''),
                'exit_code': code, 'timeout': data.get('timed_out', False),
                'stdout_bytes': outcome.get('original_stream_bytes', {}).get('stdout', len(data.get('stdout', '').encode())), 'stderr_bytes': outcome.get('original_stream_bytes', {}).get('stderr', len(stderr.encode())),
                'selected_omitted_stderr': bool(stderr and stderr not in selected),
                'selected_omitted_exit': code != 0 and f'[exit {code}]' not in selected,
                'notice_supplied': notice is not None, 'duration_ms': outcome.get('duration_ms')})
        elif operation in {'write', 'edit'}:
            diff = data.get('diff', '') or ''
            # Exact matching only; arbitrary dictionaries are left untouched.
            envelope = repr({'status': outcome['status'], 'data': data})
            receipt = f"{operation}: {json.dumps(data.get('path'))} — {'changed' if data.get('changed') else 'no change'}; +{sum(x.startswith('+') and not x.startswith('+++') for x in diff.splitlines())}/-{sum(x.startswith('-') and not x.startswith('---') for x in diff.splitlines())} lines."
            if envelope in selected:
                selected = selected.replace(envelope, receipt)
                omitted |= bool(diff)
            if outcome['status'] != 'ok':
                notice = f"{operation} failed: {outcome.get('error', '')}"
            elif result.status != 'ok':
                notice = receipt + ' Filesystem effects were not rolled back.'
        elif operation == 'read' and not data.get('complete', True):
            notice = f"Partial read (path data): {json.dumps(data.get('path'))}; lines {data.get('offset')}-{data.get('offset', 1) + data.get('returned_lines', 0) - 1} of {data.get('total_lines')}. Next offset: {data.get('next_offset')}; None means end of file, not full acquisition. Do not overwrite a file from a partial read."
        if operation not in {'bash', 'verify'}:
            telemetry.append({'call': index + 1, 'operation': operation, 'status': outcome['status'],
                              'complete': data.get('complete'), 'changed': data.get('changed'),
                              'duration_ms': outcome.get('duration_ms')})
        if outcome['status'] == 'error' and not notice:
            notice = f"{operation} failed: {outcome.get('error', '')}"
        if notice:
            notice, notice_cut = _bounded_text('\n'.join(notice.splitlines()[:20]), max(200, 22000 // max(1, len(outcomes))))
            omitted |= notice_cut
            notices.append(notice)
    if result.error_message:
        notices.append(_bounded_text(f'{result.error_type}: {result.error_message}', 2000)[0])
    if result.status == 'timeout' or result.failure_stage == 'transport':
        if checkpoint is not None:
            omitted_names = checkpoint.get('omitted_names', [])
            notices.append('Worker discarded; the last committed plain-data checkpoint will be restored on the next cell.' +
                (f" Opaque bindings lost: {', '.join(omitted_names[:12])}." if omitted_names else ''))
        else:
            notices.append('Worker discarded with no committed checkpoint. External effects may be unknown; reconcile before retrying.')
    elif result.status == 'error':
        notices.append('Plain-data namespace rolled back; external effects are not rolled back.' if result.state_preserved else 'Cell failed; inspect state before continuing.')
    if result.state_deleted:
        notices.append('Deleted or unavailable variables: ' + ', '.join(result.state_deleted[:12]))
    new = sorted(set(result.state_delta) - prior_names)
    # Reserve half the cap for operational evidence. Bound each record at capture.
    mandatory, notice_clipped = _bounded_text('\n\n'.join(dict.fromkeys(notices)), PTC_OBSERVATION_BYTES // 2)
    line_budget = max(1, 1990 - len(mandatory.splitlines()))
    selected_lines = selected.splitlines(keepends=True)
    if len(selected_lines) > line_budget:
        head_count = line_budget * 2 // 3
        selected = ''.join(selected_lines[:head_count]) + '\n...[middle lines omitted]...\n' + ''.join(selected_lines[-(line_budget - head_count):])
    body, clipped = _bounded_head_tail(selected, PTC_OBSERVATION_BYTES - len(mandatory.encode()) - 500)
    omitted |= clipped or notice_clipped or len(selected_lines) > line_budget
    text = '\n\n'.join(x for x in (body, mandatory) if x) or '(no output)'
    if omitted:
        text += f"\nDetails omitted: code(more='{result_id}', offset=0). Retention: 256 KB, last 32 cells; discarded suffixes are unavailable."
    return {'text': text, 'details': {
        'result_id': result_id, 'status': result.status, 'state_count': result.state_count,
        'state_delta': new, 'state_deleted': list(result.state_deleted), 'state_preserved': result.state_preserved,
        'failure_stage': result.failure_stage, 'duration_ms': result.duration_ms,
        'output_truncated': omitted, 'broker_outcomes': telemetry,
        'prior_bindings_read_before_assignment': list(reuse),
        'checkpoint': ({'source_cell_id': checkpoint['source_cell_id'],
                        'value_count': len(checkpoint['values']),
                        'omitted_names': checkpoint.get('omitted_names', [])}
                       if checkpoint is not None else None),
    }}


def _load_plain_checkpoint(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
        if value.get('version') != 1 or not isinstance(value.get('source_cell_id'), str) or not isinstance(value.get('values'), dict):
            return None
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None


def _save_plain_checkpoint(path: Path, result, source_cell_id: str) -> dict | None:
    if result.checkpoint_values is None:
        return None
    value = {'version': 1, 'source_cell_id': source_cell_id,
             'values': result.checkpoint_values,
             'omitted_names': list(result.checkpoint_omitted_names)}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                         allow_nan=False)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(encoded)
    os.replace(temporary, path)
    return value


def _model_contract(contract: dict) -> str:
    labels = {'read': 'text', 'write': 'receipt', 'edit': 'receipt',
              'bash': 'shell text', 'verify': 'shell text (raises on failure)'}
    lines = []
    for name, spec in contract.items():
        signature = str(spec['signature']).split(' ->', 1)[0].replace("'", '')
        lines.append(f'{name}{signature} -> {labels[name]} '
                     f'(attributes: {", ".join(spec["result"]["data"])})')
    return '\n'.join(lines)


def _prior_binding_reads(code: str, names: set[str]) -> list[str]:
    """Straight-line AST metric; skips nested scopes, loops and conditional bodies."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    available, used = set(names), set()
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
            value = statement.value
            if value is not None:
                used.update(n.id for n in ast.walk(value) if isinstance(n, ast.Name)
                            and isinstance(n.ctx, ast.Load) and n.id in available)
            if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
                used.update({statement.target.id} & available)
        available.difference_update(n.id for n in ast.walk(statement) if isinstance(n, ast.Name)
                                    and isinstance(n.ctx, (ast.Store, ast.Del)))
    return sorted(used)


def _capture_outcome(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        # Reserve before effects; bounded batches cannot silently lose later failures.
        with self.outcome_lock:
            if len(self.outcomes) >= 64:
                raise ValueError('At most 64 helper calls per cell; split this batch')
            outcome = {'operation': method.__name__, 'status': 'pending'}
            if method.__name__ in {'bash', 'verify'}:
                outcome['command'] = kwargs.get('command', args[0] if args else '')
            self.outcomes.append(outcome)
        started = time.monotonic()
        try:
            result = method(self, *args, **kwargs)
            data = dict(result.get('data', {}))
            for key, value in data.items():
                if isinstance(value, str):
                    data[key], clipped = _bounded_text(value, PTC_RESULT_BYTES)
                    if clipped:
                        data[key] += '\n[retention truncated; suffix unavailable]'
            outcome.update({**result, 'data': data})
            outcome['original_stream_bytes'] = {key: len(result.get('data', {}).get(key, '').encode()) for key in ('stdout', 'stderr')}
            if method.__name__ in {'bash', 'verify'}:
                outcome['command'] = kwargs.get('command', args[0] if args else '')
            return result
        except Exception as error:
            outcome.update(status='error', error=str(error))
            raise
        finally:
            outcome['duration_ms'] = int((time.monotonic() - started) * 1000)
    return call


class _PierPtcBroker:
    """Expose Pier workspace operations to Skein's resident CPython worker."""

    def __init__(self, environment: BaseEnvironment, bridge: _AsyncBridge,
                 files: HarborWorkspaceEnvironment, workspace: str):
        self.environment = environment
        self.bridge = bridge
        self.files = files
        self.workspace = workspace
        self.outcomes: list[dict] = []
        self.outcome_lock = threading.Lock()

    @_capture_outcome
    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict:
        content = self.files.read_bytes(path)
        lines = content.decode('utf-8', errors='replace').splitlines(keepends=True)
        start = max(offset - 1, 0)
        selected = lines[start:start + limit]
        text = ''.join(selected)
        next_offset = start + len(selected) + 1 if start + len(selected) < len(lines) else None
        return {'status': 'ok', 'data': {'path': path, 'text': text,
                'sha256': sha256_bytes(content), 'offset': offset,
                'returned_lines': len(selected), 'total_lines': len(lines),
                'complete': offset == 1 and next_offset is None, 'next_offset': next_offset}}

    @_capture_outcome
    def write(self, path: str, content: str, expected_sha256: str | None = None,
              expected_absent: bool = False) -> dict:
        result = self.files.atomic_write(path, content.encode(),
            expected_sha256=expected_sha256, expected_absent=expected_absent)
        return {'status': 'ok', 'data': {'path': result.path, 'changed': result.changed,
                'sha256': result.after_sha256, 'diff': result.diff}}

    @_capture_outcome
    def edit(self, path: str, old_text: str, new_text: str,
             expected_sha256: str | None = None) -> dict:
        result = self.files.replace_text(path, old_text, new_text,
            expected_sha256=expected_sha256)
        return {'status': 'ok', 'data': {'path': result.path, 'changed': result.changed,
                'sha256': result.after_sha256, 'diff': result.diff}}

    @_capture_outcome
    def bash(self, command: str, timeout_seconds: int = 120) -> dict:
        return self._run_shell(command, timeout_seconds, pipefail=False)

    @_capture_outcome
    def verify(self, command: str, timeout_seconds: int = 120) -> dict:
        return self._run_shell(command, timeout_seconds, pipefail=True)

    def _run_shell(self, command: str, timeout_seconds: int, *, pipefail: bool) -> dict:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError('timeout_seconds must be an integer from 1 to 600')
        # Container-side timeout kills the command group; Pier only kills its host client.
        shell = 'bash -o pipefail -c' if pipefail else 'bash -c'
        wrapped = f'timeout --verbose --signal=TERM --kill-after=5s {timeout_seconds}s {shell} {shlex.quote(command)}'
        try:
            result = self.bridge.call(self.environment.exec(
                wrapped, cwd=self.workspace, timeout_sec=timeout_seconds + 15))
        except Exception as error:
            return {'status': 'error', 'data': {
                'exit_code': None, 'stdout': '', 'stderr': '', 'output': str(error),
                'effects_unknown': True}, 'error': f'{error}; command termination unknown; reconcile before retrying.'}
        stdout, stderr = result.stdout or '', result.stderr or ''
        timed_out = 'timeout: sending signal' in stdout + stderr and result.return_code in (124, 137)
        output = stdout + ('\n[stderr]\n' + stderr if stderr else '') + f'\n[exit {result.return_code}]'
        return {'status': 'timeout' if timed_out else 'ok' if result.return_code == 0 else 'error',
                'data': {'exit_code': result.return_code, 'stdout': stdout, 'stderr': stderr,
                         'output': output, 'timed_out': timed_out, 'timeout_seconds': timeout_seconds}}

    def call(self, capability: str, arguments: dict) -> dict:
        raise ValueError(f'Unsupported capability: {capability}')

    def parallel(self, operations: list[dict]) -> list[dict]:
        if any(item.get('operation') != 'fs.read' for item in operations):
            raise ValueError('agent.parallel supports independent fs.read operations only')
        with ThreadPoolExecutor(max_workers=min(len(operations), 8) or 1) as executor:
            return list(executor.map(
                lambda item: self.read(**dict(item.get('arguments') or {})), operations))

    def artifacts_load(self, uri: str, offset: int = 0, limit: int = 16_000):
        raise ValueError('Artifact storage is unavailable in this isolated Pi arm')

    def artifacts_list(self):
        return []

    def artifacts_publish(self, value, name: str, description: str | None = None):
        raise ValueError('Artifact storage is unavailable in this isolated Pi arm')


def _openrouter_key() -> str:
    value = os.getenv('OPENROUTER_API_KEY')
    if value:
        return value
    for line in (Path.home() / '.env').read_text().splitlines():
        if line.startswith('OPENROUTER_API_KEY='):
            value = line.split('=', 1)[1].strip().strip('"\'')
            if value:
                return value
    raise RuntimeError('OPENROUTER_API_KEY is unavailable')


async def _run_pi_process(argv, *, cwd, env, logs_dir, timeout_seconds):
    """Write child output directly to disk, including on cancellation or host death."""
    with (logs_dir / 'pi-events.jsonl').open('wb') as stdout, (logs_dir / 'pi-stderr.log').open('wb') as stderr:
        process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env,
                                                       stdout=stdout, stderr=stderr,
                                                       start_new_session=True)
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        return process.returncode, timed_out


class PiCodeToolPierAgent(BaseAgent):
    """Run Pi on the host; route the code tool's effects into Pier."""

    def __init__(self, *args, model_name: str | None = 'openai/gpt-5.6-luna',
                 provider_name: str = 'openrouter', reasoning: str | None = 'max',
                 max_output_tokens: int | None = None,
                 execution_timeout_seconds: int = 6900, **kwargs):
        super().__init__(*args, model_name=model_name, **kwargs)
        if execution_timeout_seconds < 1:
            raise ValueError("execution_timeout_seconds must be positive")
        self.execution_timeout_seconds = execution_timeout_seconds
        self.provider_name = provider_name
        self.reasoning = reasoning
        self.max_output_tokens = max_output_tokens
        self.workspace: str | None = None

    @staticmethod
    def name() -> str:
        return 'pi-code-tool'

    def version(self) -> str:
        return f"pi-local+pi-code-tool-{PACKAGE.name}"

    extension = EXTENSION
    package_env = 'PI_CODE_TOOL_PACKAGE'
    package = PACKAGE
    uses_skein_ptc = False
    parity_loop: str | None = None

    async def setup(self, environment: BaseEnvironment) -> None:
        result = await environment.exec('pwd -P', timeout_sec=10)
        if result.return_code or not (result.stdout or '').strip().startswith('/'):
            raise RuntimeError('Could not locate Harbor task workspace')
        self.workspace = result.stdout.strip()
        if self.workspace == '/':
            raise RuntimeError('Harbor task workspace cannot be root')

    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        if self.workspace is None:
            await self.setup(environment)
        assert self.workspace is not None
        loop = asyncio.get_running_loop()
        bridge = _AsyncBridge(loop)
        files = HarborWorkspaceEnvironment(environment, bridge, self.workspace)
        contract = helper_contract(_PierPtcBroker)
        catalog = default_help_catalog()
        for name, alias in [('read', 'fs.read'), ('write', 'fs.write'), ('edit', 'fs.edit'), ('bash', 'shell.run')]:
            catalog[alias] = {'signature': f'{name}{contract[name]["signature"]}', 'result': contract[name]['result']}
        worker = (PersistentPythonWorker(max_output_bytes=PTC_OBSERVATION_BYTES,
                                         state_recovery='snapshot', helper_contract=contract,
                                         help_catalog=catalog, capture_committed=True)
                  if self.uses_skein_ptc else None)
        broker = _PierPtcBroker(environment, bridge, files, self.workspace)
        native_ledger = (JsonlEventStore(self.logs_dir / 'skein-ptc-events')
                         if self.parity_loop == 'adk' else None)
        ptc_results: dict[str, str] = {}
        prior_names: set[str] = set()
        checkpoint_path = self.logs_dir / 'ptc-checkpoint.json'
        checkpoint = _load_plain_checkpoint(checkpoint_path) if worker is not None else None
        needs_restore = False
        if worker is not None and checkpoint is not None:
            await asyncio.to_thread(worker.restore_plain, checkpoint['values'], checkpoint['source_cell_id'])
            prior_names.update(checkpoint['values'])

        async def dispatch(name: str, input_: dict) -> str | dict:
            nonlocal checkpoint, needs_restore
            if name == 'describe_contract':
                return _model_contract(contract)
            if name == 'bash':
                result = await environment.exec(str(input_['command']), cwd=self.workspace,
                    timeout_sec=45 if self.package_env == 'PI_CODEMODE_PACKAGE' else 120)
                return json.dumps({'exit_code': result.return_code,
                                   'stdout': result.stdout or '', 'stderr': result.stderr or ''})
            if name == 'read':
                return (await asyncio.to_thread(files.read_bytes, str(input_['path']))).decode('utf-8')
            if name == 'edit':
                result = await asyncio.to_thread(files.replace_text, str(input_['path']),
                                                 str(input_['old_text']), str(input_['new_text']))
                return json.dumps({'changed': result.changed, 'sha256': result.after_sha256})
            if name == 'write':
                result = await asyncio.to_thread(files.atomic_write, str(input_['path']),
                                                 str(input_['content']).encode())
                return json.dumps({'changed': result.changed, 'sha256': result.after_sha256})
            if name == 'execute_code' and worker is not None:
                code = str(input_['code'])
                if needs_restore:
                    if checkpoint is not None:
                        await asyncio.to_thread(worker.restore_plain, checkpoint['values'], checkpoint['source_cell_id'])
                        prior_names.clear()
                        prior_names.update(checkpoint['values'])
                    needs_restore = False
                broker.outcomes = []
                reuse = _prior_binding_reads(code, prior_names)
                cell_id = uuid4().hex
                result = await asyncio.to_thread(worker.execute, code, broker,
                    120, cell_id=cell_id, shell_timeout_margin=30)
                if result.status == 'ok':
                    checkpoint = await asyncio.to_thread(_save_plain_checkpoint,
                                                         checkpoint_path, result, cell_id)
                elif result.status == 'timeout' or result.failure_stage == 'transport':
                    needs_restore = True
                result_id = f'r_{uuid4().hex[:12]}'
                ptc_results[result_id] = _ptc_record(result, result_id, broker.outcomes)
                if len(ptc_results) > PTC_RESULT_COUNT:
                    del ptc_results[next(iter(ptc_results))]
                response = _ptc_response(result, result_id, broker.outcomes, prior_names, reuse,
                                         checkpoint)
                if native_ledger is not None:
                    native_ledger.append('parity', 'parity.ptc_cell', {
                        'cell_id': cell_id, 'source': code, 'status': result.status,
                        'broker_outcomes': broker.outcomes,
                        'retained_result': ptc_results[result_id], 'projection': response,
                    })
                prior_names.difference_update(result.state_deleted)
                if result.status == 'ok':
                    prior_names.update(result.state_delta)
                elif result.status == 'timeout' or result.failure_stage == 'transport':
                    prior_names.clear()
                    if checkpoint is not None:
                        prior_names.update(checkpoint['values'])
                return response
            if name == 'read_result' and worker is not None:
                result_id = str(input_['result_id'])
                if result_id not in ptc_results:
                    raise ValueError(f'Unknown or expired result ID: {result_id}')
                offset = max(int(input_.get('offset', 0)), 0)
                limit = min(max(int(input_.get('limit', PTC_OBSERVATION_BYTES)), 1),
                            PTC_OBSERVATION_BYTES)
                record = ptc_results[result_id]
                chunk, _ = _bounded_text(''.join(record[offset:offset + limit].splitlines(keepends=True)[:1995]), PTC_OBSERVATION_BYTES - 100)
                next_offset = offset + len(chunk) if offset + len(chunk) < len(record) else None
                footer = (f'\n[next offset: {next_offset}]' if next_offset is not None else
                          '\n[end of result]')
                return {'text': chunk + footer, 'details': {'result_id': result_id,
                    'offset': offset, 'next_offset': next_offset, 'total_chars': len(record),
                    'retained_bytes': len(record.encode()), 'retention_truncated': '[retention truncated' in record,
                    'expires_after': '32 subsequent cell results'}}
            raise ValueError(f'Unknown tool: {name}')

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(line.split(b':', 1)[1].strip()) for line in headers.split(b'\r\n')
                              if line.lower().startswith(b'content-length:'))
                if length > 2_000_000:
                    raise ValueError('Bridge request too large')
                request = json.loads(await reader.readexactly(length))
                value = await dispatch(request['name'], request['input'])
                status, body = '200 OK', {'value': value}
            except Exception as error:
                status, body = '400 Bad Request', {'error': str(error)}
            payload = json.dumps(body).encode()
            writer.write(f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\n'
                         f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode() + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        models_config, pricing = await asyncio.to_thread(
            _provider_config, self.provider_name, self.model_name, self.max_output_tokens)
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        agent_dir = self.logs_dir / 'pi-state'
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / 'models.json').write_text(json.dumps(models_config, sort_keys=True))
        pi_cwd = Path(tempfile.mkdtemp(prefix='pi-harbor-', dir='/private/tmp'))
        if self.package_env == 'PI_CODEMODE_PACKAGE':
            config_dir = agent_dir / '.pi/agent'
            config_dir.mkdir(parents=True, exist_ok=True)
            server_def = {'command': sys.executable,
                'args': [str(ROOT / 'scripts/pi_harbor_mcp.py')],
                'env': {'PI_HARBOR_BRIDGE_URL': f'http://127.0.0.1:{port}/tool',
                        'PATH': os.environ.get('PATH', '')}}
            (config_dir / 'codemode.json').write_text(json.dumps({
                'mode': 'on', 'executor': {'type': 'quickjs', 'timeoutMs': 120_000},
                'mcp': {'servers': {'harbor': server_def}}},
                sort_keys=True))
            cache_dir = agent_dir / '.cache/pi-codemode'
            cache_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(json.dumps(server_def, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
            (cache_dir / 'mcp-metadata.json').write_text(json.dumps({
                'version': 1, 'servers': {'harbor': {'configHash': digest,
                    'tools': list(TOOLS.values()), 'cachedAt': int(time.time() * 1000)}}}))
        env = {**os.environ,
               'HOME': str(agent_dir) if self.package_env == 'PI_CODEMODE_PACKAGE'
                    else os.environ.get('HOME', str(Path.home())),
               'PI_CODING_AGENT_DIR': str(agent_dir),
               self.package_env: str(self.package),
               'PI_HARBOR_BRIDGE_URL': f'http://127.0.0.1:{port}/tool',
               'PTC_EVIDENCE_REVIEW': '1' if self.uses_skein_ptc else '0'}
        if self.provider_name == 'openrouter':
            env['OPENROUTER_API_KEY'] = _openrouter_key()
        argv = ['node', str(PI_CLI), '-e', str(self.extension), '--no-builtin-tools',
                '--no-session', '--print', '--mode', 'json', '--provider', self.provider_name,
                '--model', self.model_name or 'openai/gpt-5.6-luna']
        if self.reasoning is not None:
            argv += ['--thinking', self.reasoning]
        argv += ['--', instruction]
        if self.parity_loop is not None:
            env.pop('SKEIN_PARITY_REPLAY', None)  # Offline fixtures never enter a live campaign.
            env['SKEIN_PI_ROOT'] = str(PI_CLI.parents[4])
            env['SKEIN_PARITY_PROVIDER'] = self.provider_name
            env['SKEIN_PARITY_MODEL'] = self.model_name or 'openai/gpt-5.6-luna'
            env['SKEIN_PARITY_REASONING'] = self.reasoning or 'off'
            env['SKEIN_PARITY_LOGS'] = str(self.logs_dir)
            env['SKEIN_PARITY_WORKSPACE'] = self.workspace
            env['SKEIN_PARITY_TASK'] = instruction
            env['SKEIN_PARITY_MAX_TOKENS'] = str(self.max_output_tokens or 32768)
            argv = ([sys.executable, '-m', 'scripts.skein_pi_parity']
                    if self.parity_loop == 'adk' else
                    ['node', str(ROOT / 'scripts/pi_parity_transport.mjs'), '--pi-loop'])
            env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        try:
            exit_code, timed_out = await _run_pi_process(argv, cwd=str(pi_cwd), env=env,
                logs_dir=self.logs_dir, timeout_seconds=self.execution_timeout_seconds)
        finally:
            if worker is not None:
                await asyncio.to_thread(worker.close)
            server.close()
            await server.wait_closed()
            shutil.rmtree(pi_cwd)
        usage = {'input': 0, 'cache': 0, 'cache_write': 0,
                 'output': 0, 'pi_reported_cost': 0.0, 'steps': 0}
        for line in (self.logs_dir / 'pi-events.jsonl').open('rb'):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') == 'message_end' and event.get('message', {}).get('role') == 'assistant':
                data = event['message'].get('usage', {})
                usage['input'] += int(data.get('input', 0))
                usage['cache'] += int(data.get('cacheRead', 0))
                usage['cache_write'] += int(data.get('cacheWrite', 0))
                usage['output'] += int(data.get('output', 0))
                usage['pi_reported_cost'] += float(data.get('cost', {}).get('total', 0))
                usage['steps'] += 1
        usage['cost'] = _price_usage(usage, pricing['usd_per_million_tokens'])
        context.n_input_tokens = usage['input'] + usage['cache'] + usage['cache_write']
        context.n_cache_tokens = usage['cache']
        context.n_output_tokens = usage['output']
        context.cost_usd = usage['cost']
        context.n_agent_steps = usage['steps']
        context.metadata = {'pi_code_tool': {'exit_code': exit_code, 'timed_out': timed_out, 'execution_timeout_seconds': self.execution_timeout_seconds, 'usage': usage,
                            'parity_loop': self.parity_loop,
                            'model': self.model_name, 'provider': self.provider_name,
                            'reasoning': self.reasoning,
                            'max_output_tokens': self.max_output_tokens,
                            'pricing': pricing}}
        if timed_out:
            raise TimeoutError(f'Pi exceeded its per-trial {self.execution_timeout_seconds}s budget; partial trace retained')
        if exit_code:
            raise RuntimeError(f'Pi exited {exit_code}; see pi-stderr.log')


class PiCodemodePierAgent(PiCodeToolPierAgent):
    extension = ROOT / 'scripts/pi_codemode_extension.mjs'
    package_env = 'PI_CODEMODE_PACKAGE'
    package = Path('/Users/mathiasl/src/pi-codemode')

    @staticmethod
    def name() -> str:
        return 'pi-codemode'

    def version(self) -> str:
        return 'pi-local+boozedog-pi-codemode-0.4.0'


class PiSkeinPtcPierAgent(PiCodeToolPierAgent):
    extension = ROOT / 'scripts/pi_skein_ptc_extension.mjs'
    uses_skein_ptc = True

    @staticmethod
    def name() -> str:
        return 'pi-skein-ptc'

    def version(self) -> str:
        return 'pi-local+skein-persistent-cpython-v4.1-checkpoint'


class PiParityPierAgent(PiSkeinPtcPierAgent):
    """Fresh matched reference; keeps historical v4.1 runs unchanged."""

    parity_loop = 'pi'

    def version(self) -> str:
        return 'pi-loop+shared-v4.1-contract-parity-v1'


class SkeinParityPierAgent(PiSkeinPtcPierAgent):
    """ADK owns the loop; the reference owns the shared PTC/transport contract."""

    parity_loop = 'adk'

    @staticmethod
    def name() -> str:
        return 'skein-pi-parity'

    def version(self) -> str:
        return 'adk-loop+shared-v4.1-contract-parity-v1'
