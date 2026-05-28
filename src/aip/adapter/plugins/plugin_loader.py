"""PluginLoader — discovers, loads, and manages YAML-driven model plugins.

Pure adapter-layer. Respects sandbox_mode (errors do not crash AIP).
Registers loaded providers with the DI container (via callable hook or direct assignment).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import yaml

from aip.foundation.protocols import PluginProvider
from aip.adapter.plugins.yaml_plugin_provider import YamlPluginProvider


class PluginLoader:
    """Scans a directory for plugin YAML files and manages their lifecycle."""

    def __init__(self, config: "PluginConfig") -> None:  # type: ignore  # forward from 10.0a
        self.config = config
        self._loaded: dict[str, PluginProvider] = {}
        self._plugins_dir = Path(config.plugins_dir)

    def discover_plugins(self) -> list[dict]:
        """Return metadata for all discoverable YAML plugin configs."""
        if not self.config.auto_discover or not self._plugins_dir.exists():
            return []
        results: list[dict] = []
        for yml in self._plugins_dir.glob("*.yaml"):
            try:
                with open(yml, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                results.append(
                    {
                        "path": str(yml),
                        "slot_name": data.get("slot_name"),
                        "provider_name": data.get("provider_name"),
                    }
                )
            except Exception:
                if self.config.sandbox_mode:
                    continue
                raise
        return results

    def load_plugin(self, plugin_config_path: str) -> PluginProvider | None:
        """Load a single plugin. Returns None on error when sandbox_mode=True."""
        try:
            provider = YamlPluginProvider(plugin_config_path)
            slot = provider.get_slot_name()
            self._loaded[slot] = provider
            return provider
        except Exception:
            if self.config.sandbox_mode:
                return None
            raise

    def list_loaded_plugins(self) -> list[dict]:
        return [
            {
                "slot_name": p.get_slot_name(),
                "provider_name": p.get_provider_name(),
            }
            for p in self._loaded.values()
        ]

    def unload_plugin(self, slot_name: str) -> None:
        self._loaded.pop(slot_name, None)

    # Optional hook for DI registration (called by higher layers or tests)
    def register_with_container(
        self, container: Any, register_fn: Callable[[str, PluginProvider], None] | None = None
    ) -> None:
        for slot, provider in self._loaded.items():
            if register_fn:
                register_fn(slot, provider)
            else:
                # Fallback: set attribute on container if it has the right shape
                setattr(container, f"plugin_{slot}", provider)
