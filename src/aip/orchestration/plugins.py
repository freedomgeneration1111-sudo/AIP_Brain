"""PluginManager — orchestration component for extensible model providers.

Per AIP_0_1_Phase8_BuildSpec_Rev1.0.md exact prose + box + ANNEX.
Orchestration-layer. Wraps 10.0b PluginLoader, registers with ModelSlotResolver
(Phase 5/3) and AdaptiveRouter (Phase 5) so plugins become transparent slot
providers. Sandbox mode (from 10.0a config) catches errors without crashing.

Issue 25: Fix _sandbox_wrap to catch exceptions, log to trace, disable plugin
gracefully, and fall back instead of raising RuntimeError.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.protocols import ModelProvider, PluginProvider, TraceStore
from aip.foundation.schemas import PluginConfig
from aip.orchestration.router import AdaptiveRouter

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages lifecycle of plugin-provided model providers.

    Composes the concrete PluginLoader (10.0b) and integrates it into the
    existing ModelSlotResolver + AdaptiveRouter so that plugin call_model
    becomes a drop-in replacement for any named slot.
    """

    def __init__(
        self,
        config: PluginConfig,
        plugin_loader: Any,  # PluginLoader — imported via DI, not directly
        model_slot_resolver: ModelProvider,
        adaptive_router: AdaptiveRouter | None = None,
        trace_store: TraceStore | None = None,
    ) -> None:
        self.config = config
        self.plugin_loader = plugin_loader
        self.model_slot_resolver = model_slot_resolver
        self.adaptive_router = adaptive_router
        self.trace_store = trace_store
        self._registered: dict[str, PluginProvider] = {}
        self._disabled: set[str] = set()  # Track disabled plugins

    def register_plugin(self, plugin: PluginProvider) -> None:
        """Register a loaded PluginProvider with resolver (and router if present)."""
        slot = plugin.get_slot_name()
        provider_name = plugin.get_provider_name()

        # Wrap as ModelProvider for the resolver
        # (PluginProvider already satisfies the call_model + health_check shape via Protocol)
        if hasattr(self.model_slot_resolver, "register_provider"):
            self.model_slot_resolver.register_provider(slot, plugin)  # type: ignore[attr-defined]

        if self.adaptive_router is not None:
            # AdaptiveRouter does not yet support register_provider(); skip silently.
            # When router gains provider registration, this will be wired here.
            pass

        self._registered[f"{slot}:{provider_name}"] = plugin

    def unregister_plugin(self, slot_name: str, provider_name: str) -> None:
        key = f"{slot_name}:{provider_name}"
        self._registered.pop(key, None)
        self._disabled.discard(key)
        # Resolver/router unregistration is best-effort in 0.1
        if hasattr(self.model_slot_resolver, "unregister_provider"):
            try:
                self.model_slot_resolver.unregister_provider(slot_name, provider_name)  # type: ignore[attr-defined]
            except Exception:
                pass

    def get_plugin(self, slot_name: str) -> PluginProvider | None:
        for k, p in self._registered.items():
            if k.startswith(f"{slot_name}:") and k not in self._disabled:
                return p
        return None

    def list_plugins(self) -> list[dict]:
        return [
            {
                "slot_name": p.get_slot_name(),
                "provider_name": p.get_provider_name(),
                "disabled": k in self._disabled,
            }
            for k, p in self._registered.items()
        ]

    async def health_check_all(self) -> dict:
        results: dict[str, Any] = {}
        for key, p in self._registered.items():
            if key in self._disabled:
                results[key] = {"status": "disabled"}
                continue
            try:
                results[key] = await p.health_check()
            except Exception as e:
                if self.config.sandbox_mode:
                    results[key] = {"status": "error", "error": str(e)}
                else:
                    raise
        return results

    def _sandbox_wrap(self, func):
        """Internal helper: wrap call_model with sandbox if enabled.

        Issue 25: Catch exceptions, log to trace, disable plugin gracefully,
        and fall back instead of raising RuntimeError.
        """
        if not self.config.sandbox_mode:
            return func

        manager = self  # capture reference

        async def wrapped(*a, **kw):
            try:
                return await func(*a, **kw)
            except Exception as e:
                # Log to trace store if available
                logger.warning(f"Plugin error caught by sandbox: {e}")
                if manager.trace_store is not None:
                    try:
                        await manager.trace_store.write_event(
                            session_id="plugin_manager",
                            node_type="plugin",
                            failure_type="",
                            outcome="plugin_error",
                            detail=f"Plugin error (sandbox): {e}",
                        )
                    except Exception:
                        pass  # trace logging failure must not break recovery

                # Disable the plugin gracefully
                for key, plugin in manager._registered.items():
                    if hasattr(plugin, "call_model") and getattr(plugin, "call_model", None) == func:
                        manager._disabled.add(key)
                        logger.warning(f"Plugin {key} disabled due to error: {e}")
                        break

                # Fall back gracefully — return a fallback response instead of raising
                return {
                    "content": f"[Plugin fallback: error was {type(e).__name__}: {str(e)[:100]}]",
                    "model": "plugin-fallback",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                    "latency_ms": 0,
                    "cost_usd": 0.0,
                }

        return wrapped
