"""Tiny Unix-socket bridge for a persistent Prime runtime in a task container."""

# pyright: reportMissingImports=false
# The bundle installs the transport as prime_transport.py in Harbor.

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from prime_transport import PrimeRuntime


def _reply(connection: socket.socket, value: dict) -> None:
    connection.sendall(json.dumps(value, allow_nan=False).encode() + b"\n")


def serve(socket_path: Path, workspace: Path, state: Path, max_output_bytes: int) -> None:
    socket_path.unlink(missing_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    runtime = PrimeRuntime(
        workspace,
        max_output_bytes=max_output_bytes,
        runtime_module="rlm.repl",
        python_paths=(socket_path.parent,),
    )
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    listener.listen(1)
    try:
        while True:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as stream:
                request = json.loads(stream.readline())
                operation = request.pop("operation")
                try:
                    if operation == "start":
                        runtime.start()
                        result = {"status": "ok", "runtime_epoch": runtime.epoch}
                    elif operation == "request":
                        kind = request.pop("kind")
                        result = runtime.request(kind, **request)
                    elif operation == "snapshot":
                        result = runtime.snapshot(state)
                    elif operation == "restore":
                        result = runtime.restore(request["receipt"], state)
                    elif operation == "close":
                        runtime.close()
                        _reply(connection, {"status": "ok"})
                        break
                    else:
                        raise ValueError(f"unknown operation: {operation}")
                    _reply(connection, result)
                except BaseException as error:  # transport errors must reach the host
                    _reply(connection, {"bridge_error": type(error).__name__, "detail": str(error)})
    finally:
        runtime.close()
        listener.close()
        socket_path.unlink(missing_ok=True)


def request(socket_path: Path, payload: str) -> None:
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(str(socket_path))
    with connection:
        connection.sendall(payload.encode() + b"\n")
        response = connection.makefile("rb").readline()
    print(response.decode(), end="")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    server = subparsers.add_parser("serve")
    server.add_argument("socket", type=Path)
    server.add_argument("workspace", type=Path)
    server.add_argument("state", type=Path)
    server.add_argument("max_output_bytes", type=int)
    client = subparsers.add_parser("request")
    client.add_argument("socket", type=Path)
    client.add_argument("payload")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.socket, args.workspace, args.state, args.max_output_bytes)
    else:
        request(args.socket, args.payload)


if __name__ == "__main__":
    main()
