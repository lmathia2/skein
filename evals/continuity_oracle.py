"""Host-owned fixture verdict through the existing managed command boundary."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.execution.sandbox import CommandSandbox, SandboxRequest, SandboxResult

ORACLE_COMMAND = "python -m unittest discover -s __skein_host_oracle__"


class HostOracleSandbox:
    """One exact virtual test target; all other commands retain their sandbox.

    No oracle file or guessable expected-answer hash is published to the worker.
    Both model-requested checks and outer verification use this same adapter.
    This is an evaluation backend, not a replacement for production verification.
    """

    def __init__(self, delegate: CommandSandbox, expected: Any,
                 evidence_check: Callable[[str], bool] | None = None) -> None:
        self.workspace: Path = delegate.workspace
        self._delegate = delegate
        self._expected = json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._evidence_check = evidence_check

    def execute(self, request: SandboxRequest) -> SandboxResult:
        if request.command != ORACLE_COMMAND:
            return self._delegate.execute(request)
        started = time.monotonic()
        answer = self.workspace / "answer.json"
        passed = False
        evidence_missing = False
        raw = b""
        try:
            if answer.is_file() and not answer.is_symlink() and answer.stat().st_size < 4096:
                raw = answer.read_bytes()
                actual = json.loads(raw)
                passed = json.dumps(actual, sort_keys=True, separators=(",", ":"), allow_nan=False) == self._expected
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        # Corrupt/unreadable evidence is an infrastructure error, not an ordinary
        # wrong-answer verdict that a model should try to repair by guessing.
        if passed and self._evidence_check is not None:
            evidence_missing = not self._evidence_check(hashlib.sha256(raw).hexdigest())
            passed = not evidence_missing
        return SandboxResult(
            status="ok" if passed else "error", exit_code=0 if passed else 1,
            stdout="Host-owned answer check passed." if passed else "",
            stderr="" if passed else (
                "Required source evidence was not established before this managed answer write. "
                "Acquire the missing or current source ranges, then write the answer again before verification."
                if evidence_missing else "Answer is missing, invalid, or does not match the requested source evidence."),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
