"""Plugin provider Protocol definition.

Abstraction for plugin-provided model providers, enabling
extensibility without hardcoding model names.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PluginProvider(Protocol):
    """Abstraction for a plugin-provided model provider.

    Plugins extend model slots without hardcoding names.
    This is the extensible variant of ModelProvider.
    """

    async def call_model(self, prompt: str, config: dict) -> str:
        """Send prompt to the plugin's model and return the response text."""
        ...

    async def health_check(self) -> dict:
        """Verify the plugin's model is accessible. Returns status dict."""
        ...

    def get_slot_name(self) -> str:
        """Return the model slot name this plugin binds to."""
        ...

    def get_provider_name(self) -> str:
        """Return the concrete provider name (e.g. plugin YAML key)."""
        ...


__all__ = [
    "PluginProvider",
]
