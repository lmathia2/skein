"""Offline differential test: Pi and ADK consume identical scripted model turns."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PI_ROOT = Path(os.environ.get("SKEIN_PI_ROOT", "/Users/mathiasl/src/pi"))


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()
                if key not in {"timestamp", "duration_ms"}}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


@pytest.mark.skipif(not (PI_ROOT / "packages/agent/dist/agent.js").exists(),
                    reason="Differential integration requires the pinned Pi build")
@pytest.mark.parametrize("wire", [False, True], ids=["replayed-messages", "provider-wire"])
@pytest.mark.parametrize("needs_review", [False, True], ids=["verified", "review"])
def test_pi_and_adk_model_contexts_match_through_tools_errors_paging_and_review(tmp_path, wire, needs_review):
    requests = []
    provider_requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["content-length"])))
            if self.path.endswith('/chat/completions'):
                message = fixture["messages"][len(provider_requests) % len(fixture["messages"])]
                provider_requests.append(request)
                content = message["content"]
                calls = [part for part in content if part['type'] == 'toolCall']
                delta = {"role": "assistant", "content": ''.join(
                    part['text'] for part in content if part['type'] == 'text')}
                if calls:
                    delta['tool_calls'] = [{"index": i, "id": part['id'], "type": "function",
                        "function": {"name": part['name'], "arguments": json.dumps(part['arguments'])}}
                        for i, part in enumerate(calls)]
                chunks = [{"id": "response", "object": "chat.completion.chunk", "created": 0,
                    "model": "fixture", "choices": [{"index": 0, "delta": delta,
                    "finish_reason": None}]},
                    {"id": "response", "object": "chat.completion.chunk", "created": 0,
                    "model": "fixture", "choices": [{"index": 0, "delta": {},
                    "finish_reason": 'tool_calls' if calls else 'stop'}]}]
                body = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks)
                        + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('content-type', 'text/event-stream')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            requests.append(request)
            if request["name"] == "describe_contract":
                value = "read(path: str, offset: int = 1, limit: int = 400) -> text"
            elif request["name"] == "read_result":
                value = {"text": "retained λ evidence\n[end of result]", "details": {}}
            else:
                code = request["input"]["code"]
                operation = "verify" if code == "check" else "edit"
                status = "error" if code == "check" and needs_review else "ok"
                value = {"text": "failure: λ\n[exit 1]" if status == "error" else "changed",
                         "details": {"broker_outcomes": [
                             {"operation": operation, "status": status, "changed": True}]}}
            body = json.dumps({"value": value}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def call(id, arguments, name="code"):
        return {"type": "toolCall", "id": id, "name": name, "arguments": arguments}

    def assistant(content):
        return {"role": "assistant", "api": "openai-completions", "provider": "openrouter",
                "model": "fixture", "content": content,
                "stopReason": "toolUse" if any(x["type"] == "toolCall" for x in content) else "stop",
                "timestamp": 0, "usage": {"input": 1, "output": 1, "cacheRead": 0,
                "cacheWrite": 0, "totalTokens": 2,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}}}

    fixture = {"model": {"id": "fixture", "provider": "openrouter", "api": "openai-completions",
                         "contextWindow": 1000000, "maxTokens": 32768, "reasoning": True},
               "messages": [
                   assistant([{"type": "thinking", "thinking": "private", "thinkingSignature": "opaque"},
                              call("c1", {"code": "edit"}), call("c2", {"code": "check"})]),
                   assistant([call("c3", {"more": "r_1", "offset": 0, "limit": 51200})]),
                   assistant([call("c4", {"code": "never", "more": "invalid"})]),
                   assistant([call("c5", {}, name="unknown")]),
                   assistant([{"type": "text", "text": "Known gap: failing probe."}]),
                   assistant([{"type": "text", "text": "Finished review; no known gaps."}]),
               ]}
    if not needs_review:
        fixture['messages'] = [*fixture['messages'][:4], assistant([
            {"type": "text", "text": "No known gaps."}])]
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    traces = []
    try:
        for arm in ("pi", "adk"):
            logs = tmp_path / arm
            logs.mkdir()
            env = {**os.environ, "SKEIN_PI_ROOT": str(PI_ROOT),
                   "PI_HARBOR_BRIDGE_URL": f"http://127.0.0.1:{server.server_port}",
                   "PI_CODING_AGENT_DIR": str(logs), "SKEIN_PARITY_LOGS": str(logs),
                   "SKEIN_PARITY_WORKSPACE": "/app", "SKEIN_PARITY_TASK": "Implement the task.",
                   "SKEIN_PARITY_PROVIDER": "openrouter", "SKEIN_PARITY_MODEL": "fixture",
                   "SKEIN_PARITY_REASONING": "xhigh", "SKEIN_PARITY_MAX_TOKENS": "32768",
                   "SKEIN_PARITY_REPLAY": str(fixture_path)}
            if wire:
                env.pop('SKEIN_PARITY_REPLAY')
                (logs / 'models.json').write_text(json.dumps({"providers": {"openrouter": {
                    "api": "openai-completions", "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                    "apiKey": "test", "models": [{"id": "fixture", "name": "fixture",
                    "reasoning": True, "contextWindow": 1000000, "maxTokens": 32768,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}))
            command = (["node", str(ROOT / "scripts/pi_parity_transport.mjs"), "--pi-loop"]
                       if arm == "pi" else [sys.executable, "-m", "scripts.skein_pi_parity"])
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
            assert result.returncode == 0, result.stderr
            traces.append([normalize(json.loads(line)) for line in
                           (logs / "model-contexts.jsonl").read_text().splitlines()])
        assert len(traces[0]) == len(fixture["messages"])
        if not wire:
            assert traces[0] == traces[1]
        else:
            count = len(fixture['messages'])
            assert len(provider_requests) == count * 2
            assert provider_requests[:count] == provider_requests[count:]
        assert json.loads((tmp_path / "pi/parity-contract.json").read_text()) == json.loads(
            (tmp_path / "adk/parity-contract.json").read_text())
        reminders = [message for message in traces[1][-1]['messages']
                     if message['role'] == 'user' and
                     message['content'][0]['text'].startswith('Before finalizing')]
        assert len(reminders) == int(needs_review)
        assert len([x for x in requests if x["name"] == "code"]) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
