"""Side-effect-free YAML loading for portable declarative composition."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .models import (
    AppConfig,
    FrozenModel,
    HarnessCapability,
    HarnessComposition,
    HarnessSelectionConfig,
    PersistenceConfig,
    ServerConfig,
    SkeinConfig,
)

DEFAULT_COMPOSITION_PATH = Path(__file__).with_name("default.yaml")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mappings."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _HarnessSelectionEnvelope(FrozenModel):
    implementation: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    api_version: int = Field(default=1, ge=1, le=1_000)
    required_capabilities: tuple[HarnessCapability, ...] = (
        "streaming",
        "steering",
        "tool_events",
        "state_snapshots",
        "artifacts",
    )
    config: dict[str, Any]


class _CompositionEnvelope(FrozenModel):
    schema_version: Literal[1]
    app: AppConfig
    harness: _HarnessSelectionEnvelope
    persistence: PersistenceConfig = PersistenceConfig()
    server: ServerConfig = ServerConfig()


DEFAULT_HARNESS_CONFIG_MODELS: Mapping[str, type[BaseModel]] = {
    "skein_v1": SkeinConfig,
}


def parse_harness_composition(
    payload: dict[str, Any],
    *,
    config_models: Mapping[str, type[BaseModel]] = DEFAULT_HARNESS_CONFIG_MODELS,
) -> HarnessComposition:
    """Validate the generic envelope, then its registered implementation payload."""

    envelope = _CompositionEnvelope.model_validate(payload)
    try:
        config_model = config_models[envelope.harness.implementation]
    except KeyError as error:
        raise ValueError(
            "harness implementation has no registered configuration model: "
            f"{envelope.harness.implementation}"
        ) from error
    config = config_model.model_validate(envelope.harness.config)
    return HarnessComposition(
        schema_version=envelope.schema_version,
        app=envelope.app,
        harness=HarnessSelectionConfig(
            implementation=envelope.harness.implementation,
            api_version=envelope.harness.api_version,
            required_capabilities=envelope.harness.required_capabilities,
            config=config,
        ),
        persistence=envelope.persistence,
        server=envelope.server,
    )


def load_harness_composition(
    path: Path = DEFAULT_COMPOSITION_PATH,
    *,
    config_models: Mapping[str, type[BaseModel]] = DEFAULT_HARNESS_CONFIG_MODELS,
) -> HarnessComposition:
    source = path.expanduser().resolve()
    payload = yaml.load(
        source.read_text(encoding="utf-8"),
        Loader=_UniqueKeySafeLoader,
    )
    if not isinstance(payload, dict):
        raise ValueError("harness composition must be a YAML mapping")
    return parse_harness_composition(payload, config_models=config_models)


__all__ = [
    "DEFAULT_COMPOSITION_PATH",
    "DEFAULT_HARNESS_CONFIG_MODELS",
    "load_harness_composition",
    "parse_harness_composition",
]
