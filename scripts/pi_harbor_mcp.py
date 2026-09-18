"""Tiny stdio MCP bridge for Pi's host-side Harbor evaluation."""

import json
import os
import sys
import threading
import urllib.request

TOOLS = {
    name: {
        "name": name,
        "description": f"{name} in the isolated Harbor task workspace",
        "inputSchema": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in keys},
            "required": keys,
        },
    }
    for name, keys in {
        "read": ["path"], "bash": ["command"],
        "edit": ["path", "old_text", "new_text"],
        "write": ["path", "content"],
    }.items()
}


def call(name, args):
    request = urllib.request.Request(
        os.environ["PI_HARBOR_BRIDGE_URL"],
        data=json.dumps({"name": name, "input": args}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=130) as response:
        return json.load(response)["value"]


def main():
    output_lock = threading.Lock()

    def respond(request, result):
        with output_lock:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                         "result": result}) + "\n")
            sys.stdout.flush()

    def run_call(request):
        params = request["params"]
        try:
            value = call(params["name"], params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": value}]}
        except Exception as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
        respond(request, result)

    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "harbor-bridge", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": list(TOOLS.values())}
        elif method == "tools/call":
            threading.Thread(target=run_call, args=(request,), daemon=True).start()
            continue
        else:
            result = {}
        respond(request, result)


if __name__ == "__main__":
    main()
