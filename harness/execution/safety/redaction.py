"""Deterministic secret redaction for model-visible text and telemetry."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_REDACTED = "<redacted>"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,255})\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
        ),
    ),
    (
        "authorization",
        re.compile(
            r"(?im)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"
        ),
    ),
    (
        "credential-assignment",
        re.compile(
            r"(?im)\b(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
            r"password|passwd|secret)\b(\s*[:=]\s*)"
            r"(['\"]?)[^\s,'\";]{8,}\3"
        ),
    ),
)

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}


def _normalized_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    frequencies: dict[str, int] = {}
    for character in value:
        frequencies[character] = frequencies.get(character, 0) + 1
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in frequencies.values()
    )


def _looks_like_unlabelled_secret(value: str) -> bool:
    """Conservative fallback for long, high-entropy credential-like strings."""

    candidate = value.strip()
    if len(candidate) < 32 or len(candidate) > 512:
        return False
    if any(character.isspace() for character in candidate):
        return False
    allowed = sum(character.isalnum() or character in "-_./+=" for character in candidate)
    if allowed / len(candidate) < 0.95:
        return False
    classes = sum(
        bool(re.search(pattern, candidate))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[-_./+=]")
    )
    return classes >= 3 and _entropy(candidate) >= 4.2


@dataclass(slots=True)
class SecretRedactor:
    """Redact known secrets and common credential formats without an LLM."""

    known_secrets: Sequence[str] = field(default_factory=tuple)
    redact_high_entropy_values: bool = False

    def redact_text(self, text: str) -> str:
        result = text
        for secret in sorted(
            {value for value in self.known_secrets if len(value) >= 4},
            key=len,
            reverse=True,
        ):
            result = result.replace(secret, _REDACTED)

        for name, pattern in _PATTERNS:
            if name == "authorization":
                result = pattern.sub(r"\1<redacted>", result)
            elif name == "credential-assignment":
                result = pattern.sub(r"\1\2<redacted>", result)
            else:
                result = pattern.sub(_REDACTED, result)

        if self.redact_high_entropy_values:
            parts = re.split(r"(\s+)", result)
            result = "".join(
                _REDACTED if _looks_like_unlabelled_secret(part) else part
                for part in parts
            )
        return result

    def redact(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and _normalized_key(key) in _SENSITIVE_KEYS:
            return _REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                item_key: self.redact(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        return value


def redact_secrets(value: Any, known_secrets: Sequence[str] = ()) -> Any:
    return SecretRedactor(known_secrets=known_secrets).redact(value)
