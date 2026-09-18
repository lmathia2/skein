"""Contract check for the isolated Pi-to-Harbor MCP shim."""

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from scripts import pi_harbor_mcp


def test_stdio_discovery() -> None:
    script = Path(__file__).resolve().parents[1] / 'scripts/pi_harbor_mcp.py'
    requests = '\n'.join(json.dumps({'jsonrpc': '2.0', 'id': i,
                                     'method': method}) for i, method in
                         [(1, 'initialize'), (2, 'tools/list')]) + '\n'
    process = subprocess.run([sys.executable, str(script)], input=requests,
                             text=True, capture_output=True, check=True)
    rows = [json.loads(line) for line in process.stdout.splitlines()]
    assert rows[0]['result']['serverInfo']['name'] == 'harbor-bridge'
    assert {tool['name'] for tool in rows[1]['result']['tools']} == {
        'read', 'bash', 'edit', 'write'}
    assert all(tool['inputSchema']['required'] for tool in
               rows[1]['result']['tools'])


def test_independent_calls_run_together(monkeypatch) -> None:
    barrier = threading.Barrier(2)

    def fake_call(name: str, args: dict) -> str:
        barrier.wait(timeout=2)
        return name

    requests = '\n'.join(json.dumps({'jsonrpc': '2.0', 'id': i,
                                     'method': 'tools/call',
                                     'params': {'name': name, 'arguments': {}}})
                         for i, name in [(1, 'read'), (2, 'bash')]) + '\n'
    output = io.StringIO()
    monkeypatch.setattr(pi_harbor_mcp, 'call', fake_call)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(requests))
    monkeypatch.setattr(sys, 'stdout', output)
    pi_harbor_mcp.main()
    deadline = time.monotonic() + 3
    while len(output.getvalue().splitlines()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {row['id'] for row in rows} == {1, 2}
    assert not any(row['result'].get('isError') for row in rows)
