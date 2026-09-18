"""Host-side Pier adapter for the isolated Pi Code Tool comparison."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from harness.adapters.pier import HarborWorkspaceEnvironment, _AsyncBridge
from harness.execution.environment import sha256_bytes
from harness.ptc.repl import PersistentPythonWorker
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


class _PierPtcBroker:
    """Expose Pier workspace operations to Skein's resident CPython worker."""

    def __init__(self, environment: BaseEnvironment, bridge: _AsyncBridge,
                 files: HarborWorkspaceEnvironment, workspace: str):
        self.environment = environment
        self.bridge = bridge
        self.files = files
        self.workspace = workspace

    def read(self, path: str, offset: int = 1, limit: int = 400) -> dict:
        content = self.files.read_bytes(path)
        lines = content.decode('utf-8', errors='replace').splitlines(keepends=True)
        text = ''.join(lines[max(offset - 1, 0):max(offset - 1, 0) + limit])
        return {'status': 'ok', 'data': {'path': path, 'text': text,
                'sha256': sha256_bytes(content), 'offset': offset,
                'returned_lines': len(text.splitlines())}}

    def write(self, path: str, content: str, expected_sha256: str | None = None,
              expected_absent: bool = False) -> dict:
        result = self.files.atomic_write(path, content.encode(),
            expected_sha256=expected_sha256, expected_absent=expected_absent)
        return {'status': 'ok', 'data': {'path': result.path, 'changed': result.changed,
                'sha256': result.after_sha256, 'diff': result.diff}}

    def edit(self, path: str, old_text: str, new_text: str,
             expected_sha256: str | None = None) -> dict:
        result = self.files.replace_text(path, old_text, new_text,
            expected_sha256=expected_sha256)
        return {'status': 'ok', 'data': {'path': result.path, 'changed': result.changed,
                'sha256': result.after_sha256, 'diff': result.diff}}

    def bash(self, command: str, timeout_seconds: int = 120) -> dict:
        result = self.bridge.call(self.environment.exec(
            command, cwd=self.workspace, timeout_sec=timeout_seconds))
        return {'status': 'ok' if result.return_code == 0 else 'error',
                'data': {'exit_code': result.return_code, 'stdout': result.stdout or '',
                         'stderr': result.stderr or ''}}

    def call(self, capability: str, arguments: dict) -> dict:
        raise ValueError(f'Unsupported capability: {capability}')

    def parallel(self, operations: list[dict]) -> list[dict]:
        results = []
        for item in operations:
            operation = str(item['operation'])
            method = {'fs.read': self.read, 'fs.write': self.write, 'fs.edit': self.edit,
                      'shell.run': self.bash}.get(operation)
            if method is None:
                raise ValueError(f'Unsupported parallel operation: {operation}')
            results.append(method(**dict(item.get('arguments') or {})))
        return results

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


class PiCodeToolPierAgent(BaseAgent):
    """Run Pi on the host; route the code tool's effects into Pier."""

    def __init__(self, *args, model_name: str | None = 'openai/gpt-5.6-luna',
                 reasoning: str = 'max', max_output_tokens: int | None = None, **kwargs):
        super().__init__(*args, model_name=model_name, **kwargs)
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
        worker = PersistentPythonWorker(max_output_bytes=16_000) if self.uses_skein_ptc else None
        broker = _PierPtcBroker(environment, bridge, files, self.workspace)

        async def dispatch(name: str, input_: dict) -> str:
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
                result = await asyncio.to_thread(worker.execute, str(input_['code']), broker,
                    120, cell_id=uuid4().hex)
                output = result.stdout
                if result.value_repr:
                    output += ('\n' if output else '') + result.value_repr
                if result.stderr:
                    output += ('\n' if output else '') + result.stderr
                if result.error_message:
                    output += ('\n' if output else '') + f'{result.error_type}: {result.error_message}'
                return json.dumps({'status': result.status, 'model_text': output,
                    'state_count': result.state_count, 'state_delta': list(result.state_delta),
                    'state_preserved': result.state_preserved,
                    'failure_stage': result.failure_stage})
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

        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        agent_dir = self.logs_dir / 'pi-state'
        agent_dir.mkdir(parents=True, exist_ok=True)
        if self.max_output_tokens is not None:
            (agent_dir / 'models.json').write_text(json.dumps({'providers': {'openrouter': {
                'modelOverrides': {self.model_name: {'maxTokens': self.max_output_tokens}}
            }}}, sort_keys=True))
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
        env = {**os.environ, 'OPENROUTER_API_KEY': _openrouter_key(),
               'HOME': str(agent_dir) if self.package_env == 'PI_CODEMODE_PACKAGE'
                    else os.environ.get('HOME', str(Path.home())),
               'PI_CODING_AGENT_DIR': str(agent_dir),
               self.package_env: str(self.package),
               'PI_HARBOR_BRIDGE_URL': f'http://127.0.0.1:{port}/tool'}
        argv = ['node', str(PI_CLI), '-e', str(self.extension), '--no-builtin-tools',
                '--no-session', '--print', '--mode', 'json', '--provider', 'openrouter',
                '--model', self.model_name or 'openai/gpt-5.6-luna',
                '--thinking', self.reasoning, '--', instruction]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*argv, cwd=str(pi_cwd), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=6900)
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            if worker is not None:
                await asyncio.to_thread(worker.close)
            server.close()
            await server.wait_closed()
            shutil.rmtree(pi_cwd)
        (self.logs_dir / 'pi-events.jsonl').write_bytes(stdout)
        (self.logs_dir / 'pi-stderr.log').write_bytes(stderr)
        usage = {'input': 0, 'cache': 0, 'cache_write': 0,
                 'output': 0, 'cost': 0.0, 'steps': 0}
        for line in stdout.splitlines():
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
                usage['cost'] += float(data.get('cost', {}).get('total', 0))
                usage['steps'] += 1
        context.n_input_tokens = usage['input'] + usage['cache'] + usage['cache_write']
        context.n_cache_tokens = usage['cache']
        context.n_output_tokens = usage['output']
        context.cost_usd = usage['cost']
        context.n_agent_steps = usage['steps']
        context.metadata = {'pi_code_tool': {'exit_code': process.returncode, 'usage': usage,
                            'model': self.model_name, 'reasoning': self.reasoning,
                            'max_output_tokens': self.max_output_tokens}}
        if process.returncode:
            raise RuntimeError(f'Pi exited {process.returncode}; see pi-stderr.log')


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
        return 'pi-local+skein-persistent-cpython'
