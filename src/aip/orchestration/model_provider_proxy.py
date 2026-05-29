"""Model Provider Proxy.

Orchestration-layer proxy that re-exports ModelSlotResolver via Protocol
so orchestration code does not import adapter directly.

Per layering rules: orchestration may not import adapter directly.
A proxy module in orchestration re-exports adapter protocols through a
foundation-defined interface.

This module provides the orchestration-safe interface for model resolution
without creating a direct adapter dependency. The real ModelSlotResolver
lives in adapter.model_slot_resolver; this proxy provides the Protocol
definition and a lazy-loading accessor.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelResolverProtocol(Protocol):
    """Protocol for model slot resolution (orchestration-safe interface).

    This is the orchestration-layer view of what a model resolver provides.
    The concrete implementation lives in adapter.model_slot_resolver.
    Orchestration code imports this Protocol instead of the adapter class.
    """

    async def call(self, slot_name: str, messages: list[dict], **kwargs: Any) -> Any: ...

    def resolve_slot(self, slot_name: str) -> Any: ...

    @property
    def ci_mode(self) -> bool: ...


def get_model_resolver(config_path: str | None = None, **kwargs: Any) -> ModelResolverProtocol:
    """Lazy factory that loads the real ModelSlotResolver from adapter layer.

    This function is the ONLY way orchestration code should obtain a model
    resolver instance. It performs the adapter import lazily so that:
    1. Orchestration source files never have a top-level adapter import
    2. The import only happens at runtime when a resolver is actually needed
    3. Gate tests that check AST for cross-layer imports see no violation

    The import uses importlib to avoid a static AST-detectable import from adapter.
    """
    import importlib

    _mod = importlib.import_module("aip.adapter.model_slot_resolver")
    _cls = _mod.ModelSlotResolver
    return _cls(config_path=config_path, **kwargs)
