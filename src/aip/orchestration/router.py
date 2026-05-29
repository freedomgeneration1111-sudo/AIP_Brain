"""Adaptive Router.

Transparent wrapper around ModelSlotResolver (does not replace it).
Integrates BudgetManager (from 7.0b) for centralized enforcement on all three scopes
before any model call. Implements exploration/exploitation + Sexton role for
recommend_exploration_weight. Records to routing_outcomes and consumption.

Placed at orchestration/router.py per spec. All storage access via injected
Protocols (ModelSlotResolver + BudgetManager). No direct adapter imports.

update_weights() uses real routing outcome history.
recommend_exploration_weight() derives from actual domain sample counts.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import ModelProvider
from aip.foundation.schemas import RoutingWeight
from aip.orchestration.budget import BudgetManager

logger = logging.getLogger(__name__)


class AdaptiveRouter:
    """Adaptive Router.

    Maintains a per-(slot, domain) weight table that is updated from
    routing outcome history. Exploration vs exploitation is driven by
    the weight table rather than pure random chance.
    """

    def __init__(
        self,
        model_resolver: ModelProvider,
        budget_manager: BudgetManager,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._resolver = model_resolver
        self._budget = budget_manager
        self._config = config or {}
        self._weights: dict[tuple[str, str], RoutingWeight] = {}
        # Routing outcome history: list of (slot, domain, success: bool, timestamp, latency_ms)
        self._outcome_history: list[tuple[str, str, bool, str, float]] = []
        self._max_history = self._config.get("max_outcome_history", 1000)
        # Decay factor for older outcomes (0-1, higher = slower decay)
        self._decay_factor = self._config.get("weight_decay_factor", 0.95)

    def _record_outcome(self, slot_name: str, domain: str, success: bool, latency_ms: float) -> None:
        """Record a routing outcome for future weight computation."""
        now = datetime.now(timezone.utc).isoformat()
        self._outcome_history.append((slot_name, domain, success, now, latency_ms))
        # Trim history if it exceeds max
        if len(self._outcome_history) > self._max_history:
            self._outcome_history = self._outcome_history[-self._max_history :]

    async def resolve_with_routing(self, slot_name: str, domain: str, messages: list[dict], **kwargs) -> dict:
        """Primary entry point. Budget check + exploration/exploitation."""
        # Centralized budget enforcement on all three scopes (per 7.4 prose)
        for scope in ("session", "project", "daily"):
            scope_id = kwargs.get(f"{scope}_id", "default")
            estimated = kwargs.get("estimated_tokens", 1000)
            if not await self._budget.check_before_call(scope, scope_id, estimated):
                return {"error": "budget_exceeded", "scope": scope}

        # Exploration vs exploitation using weights
        exp_w = await self.recommend_exploration_weight(domain)
        if random.random() < exp_w:
            # Exploration: pick a non-optimal slot based on weight table
            resolved_slot = self._pick_non_optimal(slot_name, domain)
            logger.debug(
                "Router: exploration path for slot=%s domain=%s -> %s (exp_w=%.3f)",
                slot_name,
                domain,
                resolved_slot,
                exp_w,
            )
        else:
            resolved_slot = slot_name  # Exploitation (highest weight assumed to be the named slot)

        # Call the resolved slot
        result = await self._resolver.call(resolved_slot, messages, **kwargs)

        # Record outcome for future weight updates
        success = "error" not in result
        latency_ms = result.get("latency_ms", 0.0)
        self._record_outcome(resolved_slot, domain, success, latency_ms)

        # Record consumption (via budget)
        await self._budget.record_consumption(
            "session",
            "default",
            result.get("usage", {}).get("total_tokens", 0),
            0.0,
            resolved_slot,
        )

        return result

    async def update_weights(self) -> None:
        """Recompute weights from routing outcome history.

        For each (slot, domain) pair, computes a success rate weighted by
        recency (exponential decay). Higher success rate = higher weight.
        Latency is also factored in (lower latency = slight bonus).
        """
        if not self._outcome_history:
            logger.debug("Router: update_weights called but no outcome history yet")
            return

        # Group outcomes by (slot, domain)
        groups: dict[tuple[str, str], list[tuple[bool, str, float]]] = {}
        for slot, domain, success, ts, latency in self._outcome_history:
            key = (slot, domain)
            if key not in groups:
                groups[key] = []
            groups[key].append((success, ts, latency))

        # Compute weights for each group
        new_weights: dict[tuple[str, str], RoutingWeight] = {}
        for (slot, domain), outcomes in groups.items():
            n = len(outcomes)
            # Weighted success rate: more recent outcomes count more
            weighted_success = 0.0
            total_weight = 0.0
            total_latency = 0.0
            for i, (success, _ts, latency) in enumerate(outcomes):
                # Exponential decay: most recent = highest weight
                recency_weight = self._decay_factor ** (n - 1 - i)
                weighted_success += recency_weight * (1.0 if success else 0.0)
                total_weight += recency_weight
                total_latency += latency

            success_rate = weighted_success / total_weight if total_weight > 0 else 0.5
            avg_latency = total_latency / n if n > 0 else 0.0

            # Latency bonus: normalize to 0-1 range (lower is better)
            # Assume 5000ms is "bad", 100ms is "good"
            latency_score = max(0.0, min(1.0, 1.0 - (avg_latency - 100) / 4900))

            # Combined weight: 70% success rate + 30% latency score
            weight_value = 0.7 * success_rate + 0.3 * latency_score

            from datetime import datetime as _dt

            new_weights[(slot, domain)] = RoutingWeight(
                model_slot=slot,
                domain=domain,
                weight=weight_value,
                exploration_weight=await self.recommend_exploration_weight(domain),
                sample_count=n,
                updated_at=_dt.now(timezone.utc).isoformat(),
            )

        self._weights = new_weights
        logger.info(
            "Router: updated weights for %d (slot, domain) pairs from %d outcomes",
            len(new_weights),
            len(self._outcome_history),
        )

    async def get_routing_weights(self, domain: str | None = None) -> list[RoutingWeight]:
        if domain:
            return [w for w in self._weights.values() if w.domain == domain]
        return list(self._weights.values())

    async def recommend_exploration_weight(self, domain: str) -> float:
        """Sexton role: higher for sparse domains, lower for stable dense domains.

        Uses actual outcome history to determine domain density.
        - Domains with few outcomes (< min_sample_count) get high exploration
        - Domains with many outcomes and high success get low exploration
        - Domains with many outcomes but low success get moderate exploration
        """
        min_sample = self._config.get("min_sample_count", 10)
        default_w = self._config.get("default_exploration_weight", 0.10)

        # Count actual outcomes for this domain
        domain_outcomes = [
            (slot, d, success, ts, lat) for slot, d, success, ts, lat in self._outcome_history if d == domain
        ]
        count = len(domain_outcomes)

        # Sparse domain: high exploration
        if count < min_sample:
            return 0.25

        # Dense domain: compute success rate to determine exploration
        successes = sum(1 for _, _, s, _, _ in domain_outcomes if s)
        success_rate = successes / count

        # Very successful domain: low exploration
        if count > 100 and success_rate > 0.85:
            return 0.05

        # Unsuccessful domain: moderate exploration (try alternatives)
        if success_rate < 0.5:
            return 0.20

        # Default for well-sampled domains
        return default_w

    def _pick_non_optimal(self, slot_name: str, domain: str) -> str:
        """Pick an alternative slot for exploration.

        If weight data is available, prefer the best-performing alternative.
        Otherwise, fall back to the hardcoded list.
        """
        # Look for alternative slots with weight data for this domain
        alternatives = []
        for (slot, d), weight in self._weights.items():
            if d == domain and slot != slot_name:
                alternatives.append((slot, weight.weight))

        if alternatives:
            # Pick the best-performing alternative
            alternatives.sort(key=lambda x: x[1], reverse=True)
            best_alt = alternatives[0][0]
            logger.debug("Router: exploration picked best alternative %s (w=%.3f)", best_alt, alternatives[0][1])
            return best_alt

        # No weight data: fall back to simple list
        fallbacks = [s for s in ("synthesis", "evaluation", "sexton") if s != slot_name]
        return fallbacks[0] if fallbacks else slot_name
