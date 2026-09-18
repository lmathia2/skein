#!/usr/bin/env python3
"""Static replay only: never execute historical model code or effects.

Cross-cell names come from successful cells; cross-cell types/origins are unknown.
This audits the gate's coverage, not the runtime cost of hypothetical reruns.
"""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from harness.ptc.repl.preflight import helper_contract, validate
from harness.ptc.repl.worker import _RemoteOperation, _safe_builtins
from scripts.pi_code_tool_harbor import _PierPtcBroker


def replay(root: Path) -> dict:
    cells, findings = 0, []
    for path in sorted(root.glob('*/*/*/agent/pi-events.jsonl')):
        base = {'__builtins__': _safe_builtins(), 'agent': SimpleNamespace(fs=SimpleNamespace(**{n: _RemoteOperation(None, 'fs.' + n) for n in ('read', 'write', 'edit')}), shell=SimpleNamespace(run=_RemoteOperation(None, 'shell.run'))), 'json': object(),
                'math': object(), 're': object(),
                **{n: _RemoteOperation(None, n) for n in ('read', 'write', 'edit', 'bash')}}
        namespace, pending = dict(base), {}
        for line in path.open():
            event = json.loads(line)
            if event.get('type') == 'tool_execution_start' and event.get('toolName') == 'code':
                code = event.get('args', {}).get('code')
                if code is not None:
                    cells += 1
                    error = None
                    try:
                        validate(ast.parse(code), namespace, helper_contract(_PierPtcBroker))
                    except (ValueError, SyntaxError) as caught:
                        error = str(caught)
                    pending[event['toolCallId']] = (code, error)
            elif event.get('type') == 'tool_execution_end' and event['toolCallId'] in pending:
                code, error = pending.pop(event['toolCallId'])
                result = event.get('result', {})
                details = result.get('details', {})
                if error:
                    findings.append({'trace': str(path), 'call': event['toolCallId'], 'error': error,
                                     'original_status': details.get('status'), 'code': code})
                if details.get('status') == 'ok':
                    tree = ast.parse(code)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                            namespace[node.id] = object()
                        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                            namespace[node.name] = object()
                        elif isinstance(node, (ast.Import, ast.ImportFrom)):
                            for alias in node.names:
                                namespace[alias.asname or alias.name.split('.')[0]] = object()
                elif details.get('status') == 'timeout':
                    namespace = dict(base)
    return {'cells': cells, 'findings': findings,
            'limitations': 'Static name reconstruction; nested scopes overapproximate names. Cross-cell types unknown. No model code executed.'}


if __name__ == '__main__':
    print(json.dumps(replay(Path(sys.argv[1])), indent=2))
