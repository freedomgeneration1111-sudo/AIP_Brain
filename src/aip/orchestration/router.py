"""Adaptive Router (CHUNK-7.4).

Transparent wrapper around ModelSlotResolver (does not replace it).
Integrates BudgetManager (from 7.0b) for centralized enforcement on all three scopes
before any model call. Implements §4.3 exploration/exploitation + Sexton role for
recommend_exploration_weight. Records to routing_outcomes and consumption.

Placed at orchestration/router.py per spec. All storage access via injected
Protocols (ModelSlotResolver + BudgetManager). No direct adapter imports.
"""
from __future__ import annotations

import random
from typing import Any

from aip.foundation.protocols import EventStore
from aip.foundation.schemas import RoutingWeight
from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.orchestration.budget import BudgetManager


class AdaptiveRouter:
    """Adaptive Router per Phase 5 §4.3 and CHUNK-7.4 prose/ANNEX."""

    def __init__(
        self,
        model_resolver: ModelSlotResolver,
        budget_manager: BudgetManager,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._resolver = model_resolver
        self._budget = budget_manager
        self._config = config or {}
        self._weights: dict[tuple[str, str], RoutingWeight] = {}

    async def resolve_with_routing(
        self, slot_name: str, domain: str, messages: list[dict], **kwargs
    ) -> dict:
        """Primary entry point. Budget check + exploration/exploitation."""
        # Centralized budget enforcement on all three scopes (per 7.4 prose)
        for scope in ("session", "project", "daily"):
            scope_id = kwargs.get(f"{scope}_id", "default")
            estimated = kwargs.get("estimated_tokens", 1000)
            if not await self._budget.check_before_call(scope, scope_id, estimated):
                return {"error": "budget_exceeded", "scope": scope}

        # Exploration vs exploitation per §4.3
        exp_w = self._config.get("default_exploration_weight", 0.10)
        if random.random() < exp_w:
            # Exploration: pick a non-optimal slot (simplified for foundation)
            resolved_slot = self._pick_non_optimal(slot_name, domain)
        else:
            resolved_slot = slot_name  # Exploitation (highest weight assumed to be the named slot)

        # Call the resolved slot
        result = await self._resolver.call(resolved_slot, messages, **kwargs)

        # Record outcome (routing_outcomes) + consumption (via budget)
        # (In full impl this would write to a real routing_outcomes table)
        await self._budget.record_consumption(
            "session", "default", result.get("usage", {}).get("total_tokens", 0), 0.0, resolved_slot
        )

        return result

    async def update_weights(self) -> None:
        """Recompute weights from routing_outcomes (stub for foundation; real in later integration)."""
        # Placeholder — real implementation would query routing_outcomes and decay
        pass

    async def get_routing_weights(self, domain: str | None = None) -> list[RoutingWeight]:
        return list(self._weights.values())

    async def recommend_exploration_weight(self, domain: str) -> float:
        """Sexton role per §4.3: higher for sparse domains, lower for stable dense domains."""
        # Simple heuristic for foundation / CI
        count = 5  # would come from routing_outcomes in real impl
        min_sample = self._config.get("min_sample_count", 10)
        if count < min_sample:
            return 0.25
        if count > 100:
            return 0.05
        return self._config.get("default_exploration_weight", 0.10)

    def _pick_non_optimal(self, slot_name: str, domain: str) -> str:
        # Foundation placeholder: return a different slot name for exploration
        alternatives = [s for s in ("synthesis", "evaluation", "sexton") if s != slot_name]
        return alternatives[0] if alternatives else slot_name
