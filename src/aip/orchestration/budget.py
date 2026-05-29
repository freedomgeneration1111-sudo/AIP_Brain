"""
Budget and Autonomy Tracking.

In-memory budget tracking with consumption on agent nodes,
parallel inheritance, and a two-phase autonomy gate.
Persistence is handled by the SqliteBudgetStore adapter;
this module provides the in-memory default used when no
persistent store is configured.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import AutonomyGate, BudgetStore


class InMemoryBudgetStore(BudgetStore):
    """In-memory BudgetStore used when no persistent store is configured."""

    def __init__(self, initial_budget: int | None = None):
        self._budgets: dict[str, int] = {}
        self._consumed: dict[str, int] = {}
        if initial_budget is not None:
            self._budgets["default"] = initial_budget

    async def consume(self, amount: int, budget_id: str = "default") -> bool:
        if budget_id not in self._budgets:
            self._budgets[budget_id] = 0
        if self._budgets[budget_id] < amount:
            return False
        self._budgets[budget_id] -= amount
        return True

    async def remaining(self, budget_id: str = "default") -> int:
        return self._budgets.get(budget_id, 0)

    async def reset(self, budget_id: str = "default", amount: int | None = None) -> None:
        if amount is not None:
            self._budgets[budget_id] = amount
        else:
            self._budgets.pop(budget_id, None)

    # --- Extended BudgetStore Protocol support ---
    async def get_budget(self, scope: str, scope_id: str) -> dict:
        """Track consumed tokens per scope_id."""
        key = f"{scope}:{scope_id}"
        consumed = self._consumed.get(key, 0) if hasattr(self, "_consumed") else 0
        return {"consumed_tokens": consumed, "consumed_cost": 0.0}

    async def record_usage(self, scope: str, scope_id: str, tokens_used: int, cost_usd: float, model_slot: str) -> None:
        """Track usage in-memory; SqliteBudgetStore provides persistent tracking."""
        if not hasattr(self, "_consumed"):
            self._consumed = {}
        key = f"{scope}:{scope_id}"
        self._consumed[key] = self._consumed.get(key, 0) + tokens_used

    async def check_limit(self, scope: str, scope_id: str) -> bool:
        """No in-memory limit checking; BudgetManager enforces configured limits."""
        return True


class SimpleAutonomyGate(AutonomyGate):
    """Two-phase autonomy gate (in-memory default)."""

    async def request_autonomy(self, level: int, context: dict[str, Any]) -> bool:
        # Low autonomy levels are always permitted
        if level <= 1:
            return True
        # Higher levels require DEFINER or policy approval
        return False

    async def record_autonomy_use(self, level: int, context: dict[str, Any]) -> None:
        # No-op in the in-memory default
        pass


# --- BudgetManager ---

from aip.foundation.protocols import BudgetStore, EventStore  # noqa: E402 -- lazy import after class definitions
from aip.foundation.schemas import BudgetConfig, BudgetScope  # noqa: E402 -- lazy import after class definitions


class BudgetManager:
    """Manages token budget enforcement across sessions, projects, and daily limits.

    Composes BudgetStore (persistence) and BudgetConfig (limits).
    Optionally writes warning events to EventStore when thresholds are crossed.
    See architecture spec.
    """

    def __init__(
        self,
        config: BudgetConfig,
        budget_store: BudgetStore,
        event_store: EventStore | None = None,
    ) -> None:
        self._config = config
        self._store = budget_store
        self._event_store = event_store

    def _get_limit(self, scope: BudgetScope) -> int:
        limits = {
            "session": self._config.session_token_limit,
            "project": self._config.project_token_limit,
            "daily": self._config.daily_token_limit,
        }
        return limits[scope]

    async def check_before_call(
        self,
        scope: BudgetScope,
        scope_id: str,
        estimated_tokens: int,
    ) -> bool:
        """Check whether a model call can proceed within budget.

        Returns False if budget_hard_stop=True and the call would exceed the limit.
        Emits warning event if threshold is crossed.
        """
        budget = await self._store.get_budget(scope, scope_id)
        consumed = budget.get("consumed_tokens", 0)
        limit = self._get_limit(scope)
        _remaining = limit - consumed

        # Check warning threshold
        if limit > 0 and consumed / limit >= self._config.budget_warning_threshold:
            if self._event_store:
                await self._event_store.write_event(
                    "budget_warning",
                    "budget_manager",
                    "",
                    from_state=None,
                    to_state=None,
                    detail=f"Budget warning: {scope}/{scope_id} at {consumed}/{limit} ({consumed / limit:.0%})",
                )

        # Check hard stop
        if self._config.budget_hard_stop and (consumed + estimated_tokens) > limit:
            return False

        return True

    async def record_consumption(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        """Record token consumption after a model call."""
        await self._store.record_usage(scope, scope_id, tokens_used, cost_usd, model_slot)

    async def get_status(self, scope: BudgetScope, scope_id: str) -> dict:
        """Get comprehensive budget status for a scope."""
        budget = await self._store.get_budget(scope, scope_id)
        consumed = budget.get("consumed_tokens", 0)
        limit = self._get_limit(scope)
        return {
            "scope": scope,
            "scope_id": scope_id,
            "consumed_tokens": consumed,
            "consumed_cost": budget.get("consumed_cost", 0.0),
            "limit": limit,
            "remaining": limit - consumed,
            "fraction_used": consumed / limit if limit > 0 else 1.0,
            "warning_threshold": self._config.budget_warning_threshold,
            "hard_stop": self._config.budget_hard_stop,
        }

    def is_hard_stop(self) -> bool:
        """Return whether budget is configured to block calls when limit is reached."""
        return self._config.budget_hard_stop
