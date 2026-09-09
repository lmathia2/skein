"""Bounded synchronous host for the vendored Prime JSON-lines REPL protocol."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

RUNTIME = "harness.repl.prime_runtime.repl"
REVISION = "bf8894afa55832f7cfa2094c8a0d041bc680a691"
KINDS = {"ready", "stdout", "stderr", "result", "display", "host_request", "error", "done"}


class PrimeRuntime:
    """One process and namespace; callers own transcript and snapshot receipts.

    Native Python has OS permissions. Neither this transport nor dill is a sandbox.
    """

    def __init__(self, workspace: Path, *, max_output_bytes: int = 16000) -> None:
        self.workspace = workspace.resolve()
        self.max_output_bytes = max_output_bytes
        self.epoch = uuid4().hex
        self.process: subprocess.Popen[bytes] | None = None
        self._frames: queue.Queue[dict[str, Any] | Exception] = queue.Queue(maxsize=128)
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    def _read(self, process: subprocess.Popen[bytes], frames: queue.Queue) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = process.stdout.readline(2_000_001)
                if not line:
                    raise RuntimeError("Prime REPL protocol closed")
                if len(line) > 2_000_000 or not line.endswith(b"\n"):
                    raise RuntimeError("Prime REPL frame exceeds transport bound")
                frame = json.loads(line)
                if not isinstance(frame, dict) or frame.get("event") not in KINDS:
                    raise RuntimeError("Invalid Prime REPL frame")
                while process.poll() is None:
                    try:
                        frames.put(frame, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except Exception as error:
            with suppress(queue.Full):
                frames.put(error, timeout=0.1)

    def _send(self, request: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(request, allow_nan=False) + "\n").encode())
        self.process.stdin.flush()

    def _next(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Prime REPL deadline exceeded")
        try:
            frame = self._frames.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("Prime REPL deadline exceeded") from error
        if isinstance(frame, Exception):
            raise frame
        return frame

    def start(self) -> None:
        if self.process is not None:
            return
        self.epoch = uuid4().hex
        self._frames = queue.Queue(maxsize=128)
        # Provider credentials stay in the parent. Project tools may obtain their own
        # credentials through the explicitly trusted user's normal OS environment.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
        package_root = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join((str(package_root / "harness/_vendor"), str(package_root)))
        env["PRIME_AGENT_KERNEL_OWNER_PID"] = str(os.getpid())
        env["PRIME_AGENT_BASH_SHELL"] = "/bin/bash"
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", RUNTIME], cwd=self.workspace, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._reader = threading.Thread(target=self._read, args=(self.process, self._frames), daemon=True)
        self._reader.start()
        try:
            ready = self._next(time.monotonic() + 30)
            if ready.get("event") != "ready" or ready.get("protocol") != 3:
                raise RuntimeError("Prime REPL protocol version mismatch")
            bootstrap = self.request("execute", code=(
                "import asyncio\n"
                "from harness.repl.prime_runtime.bash import bash\n"
                "from rlm.repl import emit\n"
            ))
            if bootstrap.get("status") != "ok":
                raise RuntimeError("Prime REPL bootstrap failed")
        except BaseException:
            self.close()
            raise

    def request(self, kind: str, *, timeout: float = 120, **fields: Any) -> dict[str, Any]:
        """Execute one protocol request; background output is separately attributed."""
        with self._lock:
            rid = str(fields.pop("id", uuid4().hex))
            self._send({"type": kind, "id": rid, **fields})
            deadline = time.monotonic() + timeout
            output: dict[str, Any] = {"stdout": "", "stderr": "", "background": "", "displays": []}
            remaining = self.max_output_bytes
            try:
                while True:
                    frame = self._next(deadline)
                    event = frame["event"]
                    if event in {"done", "host_request"} and not isinstance(frame.get("id"), str):
                        raise RuntimeError("Prime REPL frame missing request identity")
                    if event == "host_request":
                        self._send({"type": "host_reply", "id": frame["id"], "data": {
                            "status": "error", "error": "Host capability is not implemented in this profile",
                        }})
                        continue
                    if event == "done" and frame.get("id") == rid:
                        return {**output, **frame, "runtime_epoch": self.epoch}
                    if event == "ready" or (event == "done" and frame.get("id") != rid):
                        raise RuntimeError("Unexpected Prime REPL protocol boundary")
                    if event not in {"stdout", "stderr", "result", "error", "display"}:
                        continue
                    payload = json.dumps(frame, ensure_ascii=False).encode()
                    if event == "display":
                        if len(payload) <= remaining:
                            output["displays"].append(frame)
                            remaining -= len(payload)
                        else:
                            output["truncated"] = True
                        continue
                    key = event if event in {"stdout", "stderr"} else "result" if event == "result" else "error"
                    if frame.get("id") != rid:
                        key = "background"
                    value = str(frame.get("text", frame.get("evalue", "")))
                    encoded = value.encode()
                    selected = encoded[:remaining].decode("utf-8", errors="ignore")
                    remaining -= len(selected.encode())
                    output[key] = output.get(key, "") + selected
                    if len(encoded) > len(selected.encode()):
                        output["truncated"] = True
            except BaseException:
                self.close()
                raise

    def interrupt(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self._send({"type": "interrupt"})

    def snapshot(self, directory: Path) -> dict[str, Any]:
        """Publish immutable payload and metadata; the caller publishes the receipt last."""
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{uuid4().hex}.dill"
        manifest = path.with_suffix(".json")
        result = self.request("snapshot", path=str(path), manifest_path=str(manifest), timeout=30)
        if result.get("status") != "ok":
            raise RuntimeError(f"Prime snapshot failed: {result.get('reason', 'unknown error')}")
        return {
            "path": str(path), "manifest_path": str(manifest),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "runtime_revision": REVISION, "python": list(sys.version_info[:3]),
            "workspace": str(self.workspace), "runtime_epoch": self.epoch,
            "saved": result.get("saved", []), "skipped": result.get("skipped", []),
        }

    def restore(self, receipt: dict[str, Any], directory: Path) -> dict[str, Any]:
        if (receipt.get("runtime_revision") != REVISION or receipt.get("python") != list(sys.version_info[:3])
                or receipt.get("workspace") != str(self.workspace)):
            raise RuntimeError("Prime snapshot environment mismatch")
        for field, digest in (("path", "sha256"), ("manifest_path", "manifest_sha256")):
            path = Path(receipt[field])
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("Prime snapshot escapes owned state directory")
            if path.stat().st_size > (256 * 1024 * 1024 if field == "path" else 16 * 1024 * 1024):
                raise RuntimeError("Prime snapshot exceeds restore size bound")
            if hashlib.sha256(path.read_bytes()).hexdigest() != receipt[digest]:
                raise RuntimeError("Prime snapshot integrity mismatch")
        return self.request("restore", path=receipt["path"], timeout=30)

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write(b'{"type":"shutdown","id":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        # Descendants can outlive the parent even after a clean shutdown.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
