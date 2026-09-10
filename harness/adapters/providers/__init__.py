"""Provider adapters that preserve Google ADK as the model runtime."""

from .contracts import AdkModelProvider, AdkModelProviderRegistry
from .providers import (
    ClosedAdkModelProviderRegistry,
    GoogleAdkModelProvider,
    OpenAiCodexModelProvider,
    OpenRouterModelProvider,
    default_adk_model_provider_registry,
)

__all__ = [
    "AdkModelProvider",
    "AdkModelProviderRegistry",
    "ClosedAdkModelProviderRegistry",
    "GoogleAdkModelProvider",
    "OpenAiCodexModelProvider",
    "OpenRouterModelProvider",
    "default_adk_model_provider_registry",
]
