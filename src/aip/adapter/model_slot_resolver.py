"""Model Slot Resolver — Phase 3 real model wiring (CHUNK-5.0b).

Resolves named slots to concrete provider + model + base_url.
Supports ci_mode for fully deterministic tests (no network).
"""
from __future__ import annotations

from typing import Any

from aip.foundation.protocols import ModelProvider
from aip.foundation.schemas import ModelSlotConfig


class ModelSlotResolver(ModelProvider):
    """Resolves model slots and executes calls (with ci_mode support)."""

    def __init__(self, config: dict | Any) -> None:
        if hasattr(config, "model_dump"):
            self._cfg = config.model_dump()
        elif isinstance(config, dict):
            self._cfg = config
        else:
            self._cfg = {}

        self._models = self._cfg.get("models", {})
        self._ci_mode = self._models.get("ci_mode", True)

    def resolve(self, slot_name: str) -> ModelSlotConfig:
        if slot_name not in self._models:
            raise ValueError(f"Unknown model slot: {slot_name}")

        slot_cfg = self._models[slot_name]
        # Filter: only process dict values (skip non-dict like ci_mode flag)
        if not isinstance(slot_cfg, dict):
            raise ValueError(f"Unknown model slot: {slot_name}")
        return ModelSlotConfig(
            slot_name=slot_name,
            provider=slot_cfg.get("provider", "stub"),
            model=slot_cfg.get("model", f"<{slot_name}>"),
            base_url=slot_cfg.get("base_url"),
            fallback_provider=slot_cfg.get("fallback_provider"),
            fallback_model=slot_cfg.get("fallback_model"),
            dimensions=slot_cfg.get("dimensions"),
        )

    def list_slots(self) -> list[str]:
        return [k for k, v in self._models.items() if isinstance(v, dict)]

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        """Execute a model call for the given slot.

        In ci_mode: returns a deterministic fixture.
        Otherwise: dispatches to the appropriate provider (stub for now).
        """
        slot = self.resolve(slot_name)

        if self._ci_mode:
            # Deterministic fixture — content is a hash of the input for reproducibility
            prompt = messages[-1]["content"] if messages else ""
            content = f"[CI-FIXTURE for {slot_name}] {prompt[:80]}..."
            return {
                "content": content,
                "model": slot.model,
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 50},
                "latency_ms": 5,
                "cost_usd": 0.0,
            }

        # Real dispatch would go here (Ollama, OpenAI-compatible, Anthropic).
        # For the initial 5.0b implementation we keep it as a clear extension point.
        raise NotImplementedError(
            f"Real model call for slot {slot_name} not yet wired (provider={slot.provider}). "
            "Set models.ci_mode=true for deterministic CI, or implement the provider clients."
        )
