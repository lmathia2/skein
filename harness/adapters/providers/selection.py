"""Safe model identity and a single private default shared by CLI and server."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from harness.core.config import ModelConfig


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    reasoning: str | None = Field(default=None, max_length=32, pattern=r"^[a-z0-9_-]+$")
    client_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")

    @classmethod
    def from_config(cls, model: ModelConfig) -> ModelChoice:
        return cls.model_validate(model.model_dump(include=set(cls.model_fields)))

    def apply(self, base: ModelConfig) -> ModelConfig:
        payload = base.model_dump()
        if self.provider != base.provider:
            payload.update(base_url=None, api_key=None)
        return ModelConfig.model_validate({**payload, **self.model_dump()})


def model_default_path(state_root: Path) -> Path:
    return state_root.expanduser().resolve() / "auth/model-selection.json"


def load_model_default(state_root: Path) -> ModelChoice | None:
    path = model_default_path(state_root)
    if not path.exists():
        # Read-only compatibility with earlier CLI/Go installations.
        path = path.with_name("openai-codex-selection.json")
        try:
            value = json.loads(path.read_text())
            return ModelChoice(provider="openai_codex", name=value["model"],
                               reasoning=value.get("reasoning"), client_version=value.get("client_version"))
        except (OSError, ValueError, KeyError, TypeError):
            return None
    try:
        return ModelChoice.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None


def save_model_default(state_root: Path, choice: ModelChoice) -> Path:
    path = model_default_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".model-selection.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(choice.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
    return path
