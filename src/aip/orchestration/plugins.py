"""PluginManager — orchestration component for extensible model providers (CHUNK-10.2).

Per AIP_0_1_Phase8_BuildSpec_Rev1.0.md exact prose + box + ANNEX.
Orchestration-layer. Wraps 10.0b PluginLoader, registers with ModelSlotResolver
(Phase 5/3) and AdaptiveRouter (Phase 5) so plugins become transparent slot
providers. Sandbox mode (from 10.0a config) catches errors without crashing.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import PluginProvider
from aip.foundation.schemas import PluginConfig
from aip.adapter.plugins.plugin_loader import PluginLoader
from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.orchestration.router import AdaptiveRouter


class PluginManager:
    """Manages lifecycle of plugin-provided model providers.

    Composes the concrete PluginLoader (10.0b) and integrates it into the
    existing ModelSlotResolver + AdaptiveRouter so that plugin call_model
    becomes a drop-in replacement for any named slot.
    """

    def __init__(
        self,
        config: PluginConfig,
        plugin_loader: PluginLoader,
        model_slot_resolver: ModelSlotResolver,
        adaptive_router: AdaptiveRouter | None = None,
    ) -> None:
        self.config = config
        self.plugin_loader = plugin_loader
        self.model_slot_resolver = model_slot_resolver
        self.adaptive_router = adaptive_router
        self._registered: dict[str, PluginProvider] = {}

    def register_plugin(self, plugin: PluginProvider) -> None:
        """Register a loaded PluginProvider with resolver (and router if present)."""
        slot = plugin.get_slot_name()
        provider_name = plugin.get_provider_name()

        # Wrap as ModelProvider for the resolver
        # (PluginProvider already satisfies the call_model + health_check shape via Protocol)
        self.model_slot_resolver.register_provider(slot, plugin)  # type: ignore[attr-defined]

        if self.adaptive_router is not None:
            # Register as routing option (best-effort; router may accept providers)
            try:
                self.adaptive_router.register_provider(slot, plugin)  # type: ignore[attr-defined]
            except Exception:
                pass

        self._registered[f"{slot}:{provider_name}"] = plugin

    def unregister_plugin(self, slot_name: str, provider_name: str) -> None:
        key = f"{slot_name}:{provider_name}"
        self._registered.pop(key, None)
        # Resolver/router unregistration is best-effort in 0.1
        try:
            self.model_slot_resolver.unregister_provider(slot_name, provider_name)  # type: ignore[attr-defined]
        except Exception:
            pass

    def get_plugin(self, slot_name: str) -> PluginProvider | None:
        for k, p in self._registered.items():
            if k.startswith(f"{slot_name}:"):
                return p
        return None

    def list_plugins(self) -> list[dict]:
        return [
            {
                "slot_name": p.get_slot_name(),
                "provider_name": p.get_provider_name(),
            }
            for p in self._registered.values()
        ]

    async def health_check_all(self) -> dict:
        results: dict[str, Any] = {}
        for key, p in self._registered.items():
            try:
                results[key] = await p.health_check()
            except Exception as e:
                if self.config.sandbox_mode:
                    results[key] = {"status": "error", "error": str(e)}
                else:
                    raise
        return results

    def _sandbox_wrap(self, func):
        """Internal helper: wrap call_model with sandbox if enabled."""
        if not self.config.sandbox_mode:
            return func

        async def wrapped(*a, **kw):
            try:
                return await func(*a, **kw)
            except Exception as e:
                # Log to trace would happen via caller; here we just fail gracefully
                raise RuntimeError(f"Plugin error (sandbox): {e}") from e
        return wrapped
