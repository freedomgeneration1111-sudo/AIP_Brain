"""Adaptive Budget Tuning — data-driven per-channel budget adjustments.

Sprint 5.12: Uses channel contribution data from recent retrieval traces
to suggest (or automatically apply) adjustments to per-channel budgets
in ``OrchestratorConfig``.

Heuristic:
  - If a channel consistently contributes very few hits over many queries,
    reduce its budget (it's wasting dispatch time without adding value).
  - If a channel contributes high-value hits (hits that survive quality gate
    and appear in top results), protect or increase its budget.
  - Never auto-disable a channel entirely — minimum budget is always 1.
  - Adjustments are capped at 30% change per tuning cycle for stability.

This module is intentionally conservative and configurable. It produces
``BudgetAdjustment`` objects that can be reviewed before being applied.

Layer: orchestration.  May import foundation, stdlib.  May NOT import
adapter directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    from aip.logging import get_logger
    logger = get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budget adjustment types
# ---------------------------------------------------------------------------

@dataclass
class BudgetAdjustment:
    """A single per-channel budget adjustment suggestion.

    Attributes:
        channel_name: The channel being adjusted (e.g. "fts", "graph").
        current_budget: Current per-channel hit limit (0 = unlimited).
        suggested_budget: Suggested new per-channel hit limit.
        reason: Human-readable explanation for the adjustment.
        confidence: Confidence in this adjustment (0.0-1.0).
    """

    channel_name: str
    current_budget: int
    suggested_budget: int
    reason: str = ""
    confidence: float = 0.5


@dataclass
class BudgetTuningResult:
    """Result of an adaptive budget tuning cycle.

    Attributes:
        adjustments: List of BudgetAdjustment suggestions.
        applied: Whether the adjustments were auto-applied.
        summary: Human-readable summary of the tuning result.
    """

    adjustments: list[BudgetAdjustment] = field(default_factory=list)
    applied: bool = False
    summary: str = ""


# ---------------------------------------------------------------------------
# Adaptive budget tuner
# ---------------------------------------------------------------------------

class AdaptiveBudgetTuner:
    """Data-driven per-channel budget tuner.

    Analyzes channel contribution data from recent retrieval traces and
    suggests budget adjustments.  The tuner is intentionally conservative:

    - Maximum budget change per cycle: 30%
    - Minimum budget: 1 (never auto-disable a channel)
    - Only suggests changes when there is sufficient data (min_samples)
    - High-value channels (those contributing hits in top results) are
      protected from budget reductions

    Usage::

        tuner = AdaptiveBudgetTuner()
        result = tuner.tune(
            config=current_config,
            channel_contributions={"fts": 150, "vector": 80, "graph": 12, "wiki": 5},
            total_queries=20,
        )
        for adj in result.adjustments:
            print(f"{adj.channel_name}: {adj.current_budget} -> {adj.suggested_budget} ({adj.reason})")

        # Optionally auto-apply:
        tuner.apply(result, config)
    """

    # Tuning constants
    MAX_CHANGE_FRACTION = 0.30  # Max 30% change per cycle
    MIN_BUDGET = 1  # Never reduce below 1
    MIN_SAMPLES = 5  # Need at least this many queries to tune
    LOW_CONTRIBUTION_THRESHOLD = 0.05  # < 5% of total hits = "low contribution"
    HIGH_VALUE_THRESHOLD = 0.15  # > 15% of total hits = "high value"
    REDUCTION_FACTOR = 0.7  # Reduce low-contribution channels to 70% of current
    INCREASE_FACTOR = 1.2  # Increase high-value channels by 20%

    def __init__(
        self,
        max_change_fraction: float = 0.30,
        min_budget: int = 1,
        min_samples: int = 5,
        auto_apply: bool = False,
    ) -> None:
        """Initialize the adaptive budget tuner.

        Args:
            max_change_fraction: Maximum fractional change per cycle (0-1).
            min_budget: Minimum per-channel budget (never go below this).
            min_samples: Minimum number of queries needed to produce suggestions.
            auto_apply: Whether to automatically apply suggestions.
        """
        self._max_change_fraction = max_change_fraction
        self._min_budget = min_budget
        self._min_samples = min_samples
        self._auto_apply = auto_apply

    def tune(
        self,
        config: Any,  # OrchestratorConfig
        channel_contributions: dict[str, int],
        total_queries: int = 0,
    ) -> BudgetTuningResult:
        """Analyze channel contributions and produce budget adjustment suggestions.

        Args:
            config: Current OrchestratorConfig (read for current budgets).
            channel_contributions: Aggregated channel contribution data
                (channel_name -> total_hit_count) from recent traces.
            total_queries: Number of queries represented in the contribution data.

        Returns:
            BudgetTuningResult with adjustment suggestions.
        """
        result = BudgetTuningResult()

        if total_queries < self._min_samples:
            result.summary = (
                f"Insufficient data for tuning ({total_queries} queries, "
                f"need {self._min_samples}). Skipping."
            )
            return result

        if not channel_contributions:
            result.summary = "No channel contribution data available. Skipping."
            return result

        total_hits = sum(channel_contributions.values())
        if total_hits == 0:
            result.summary = "Zero total hits in contribution data. Skipping."
            return result

        adjustments: list[BudgetAdjustment] = []

        # Known channels from config
        channel_budget_map = {
            "fts": config.fts_max_hits,
            "vector": config.vector_max_hits,
            "graph": config.graph_max_hits,
            "wiki": config.wiki_max_hits,
            "procedural": config.procedural_max_hits,
            "corpus": config.corpus_max_hits,
        }

        for channel_name, current_budget in channel_budget_map.items():
            ch_hits = channel_contributions.get(channel_name, 0)
            contribution_pct = ch_hits / total_hits if total_hits > 0 else 0

            # Skip unlimited budgets (0) — no point tuning
            if current_budget == 0:
                continue

            suggested = current_budget
            reason = ""
            confidence = 0.5

            # Low contribution channel: consistently few hits
            if contribution_pct < self.LOW_CONTRIBUTION_THRESHOLD and ch_hits > 0:
                # Channel contributes but very little — consider reduction
                reduction = int(current_budget * self.REDUCTION_FACTOR)
                max_reduction = int(current_budget * (1 - self._max_change_fraction))
                suggested = max(reduction, max_reduction, self._min_budget)
                reason = (
                    f"Low contribution ({contribution_pct:.1%} of hits, "
                    f"{ch_hits} total over {total_queries} queries). "
                    f"Reducing budget to free capacity for higher-value channels."
                )
                confidence = 0.6

            elif contribution_pct >= self.HIGH_VALUE_THRESHOLD:
                # High-value channel: protect or increase
                increase = int(current_budget * self.INCREASE_FACTOR)
                max_increase = int(current_budget * (1 + self._max_change_fraction))
                suggested = min(increase, max_increase)
                reason = (
                    f"High-value channel ({contribution_pct:.1%} of hits, "
                    f"{ch_hits} total over {total_queries} queries). "
                    f"Increasing budget to allow more high-quality hits."
                )
                confidence = 0.7

            elif ch_hits == 0:
                # Zero hits — but don't auto-disable, just note it
                reason = (
                    f"Zero hits contributed over {total_queries} queries. "
                    f"Consider reviewing channel configuration, but not reducing budget."
                )
                confidence = 0.3

            if suggested != current_budget:
                adjustments.append(BudgetAdjustment(
                    channel_name=channel_name,
                    current_budget=current_budget,
                    suggested_budget=suggested,
                    reason=reason,
                    confidence=round(confidence, 2),
                ))

        result.adjustments = adjustments
        result.summary = (
            f"Tuning analysis on {total_queries} queries, "
            f"{total_hits} total hits. "
            f"{len(adjustments)} adjustments suggested."
        )

        # Auto-apply if configured
        if self._auto_apply and adjustments:
            self.apply(result, config)
            result.applied = True

        return result

    def apply(
        self,
        tuning_result: BudgetTuningResult,
        config: Any,  # OrchestratorConfig
    ) -> None:
        """Apply budget adjustments to an OrchestratorConfig.

        Only applies adjustments from the tuning result.  Modifies the
        config in-place.

        Args:
            tuning_result: The BudgetTuningResult containing adjustments.
            config: The OrchestratorConfig to modify.
        """
        budget_field_map = {
            "fts": "fts_max_hits",
            "vector": "vector_max_hits",
            "graph": "graph_max_hits",
            "wiki": "wiki_max_hits",
            "procedural": "procedural_max_hits",
            "corpus": "corpus_max_hits",
        }

        for adj in tuning_result.adjustments:
            field_name = budget_field_map.get(adj.channel_name)
            if field_name and hasattr(config, field_name):
                old_val = getattr(config, field_name)
                setattr(config, field_name, adj.suggested_budget)
                logger.info(
                    "budget_adjusted",
                    channel=adj.channel_name,
                    old_budget=old_val,
                    new_budget=adj.suggested_budget,
                    reason=adj.reason,
                )
