"""Single-machine workspace ownership for the local server."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from pathlib import Path


class WorkspaceOwner:
    """An OS lock survives neither process death nor restart; no stale lease timer."""

    def __init__(self, workspace: Path) -> None:
        identity = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / f"skein-workspace-{os.getuid()}-{identity}.lock"
        self.descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self.descriptor)
            self.descriptor = -1
            raise ValueError("mutable workspace is owned by another server") from error

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
