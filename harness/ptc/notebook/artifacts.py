"""Bound rich notebook outputs with content-addressed artifact references."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def _encoded(value: Any) -> tuple[bytes, Any]:
    if isinstance(value, bytes):
        return value, base64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        return value.encode(), value
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return content, value


def put_artifact(root: Path, content: bytes) -> str:
    """Store immutable bytes once and return their content-addressed URI."""

    digest = hashlib.sha256(content).hexdigest()
    target = root / digest
    if target.exists():
        return f"artifact://sha256/{digest}"
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{digest}.", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(FileExistsError):
            os.link(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
    return f"artifact://sha256/{digest}"


def externalize_mime_bundle(
    bundle: dict[str, Any],
    *,
    artifact_root: Path,
    max_inline_bytes: int,
) -> tuple[dict[str, Any], list[str]]:
    """Return an nbformat-safe bundle and externalize oversized members."""

    rendered: dict[str, Any] = {}
    externalized: list[dict[str, Any]] = []
    refs: list[str] = []
    for media_type, value in sorted(bundle.items()):
        content, inline = _encoded(value)
        if len(content) <= max_inline_bytes:
            rendered[media_type] = inline
            continue
        uri = put_artifact(artifact_root, content)
        refs.append(uri)
        externalized.append(
            {"artifact_uri": uri, "byte_size": len(content), "media_type": media_type}
        )
    if externalized:
        rendered["application/vnd.agent.artifact+json"] = externalized
    return rendered, refs


__all__ = ["externalize_mime_bundle", "put_artifact"]
