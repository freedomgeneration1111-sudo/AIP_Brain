"""
Budget and Autonomy Tracking (foundation).

Minimal deterministic implementation per Architecture L6 / Budget sections
and the existing BudgetStore + AutonomyGate protocol stubs.

For foundation: in-memory tracking, simple consumption on agent nodes,
parallel inheritance, and a two-phase autonomy gate stub.
Real persistence and complex L6 logic in later chunks.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import AutonomyGate, BudgetStore


class InMemoryBudgetStore(BudgetStore):
    """Minimal in-memory BudgetStore for foundation."""

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

    # --- Extended BudgetStore Protocol support (for CI) ---
    async def get_budget(self, scope: str, scope_id: str) -> dict:
        """Support extended Protocol (track consumed per scope_id for CI tests)."""
        key = f"{scope}:{scope_id}"
        consumed = self._consumed.get(key, 0) if hasattr(self, "_consumed") else 0
        return {"consumed_tokens": consumed, "consumed_cost": 0.0}

    async def record_usage(self, scope: str, scope_id: str, tokens_used: int, cost_usd: float, model_slot: str) -> None:
        """Track usage in-memory for CI (real persistence in Sqlite impl)."""
        if not hasattr(self, "_consumed"):
            self._consumed = {}
        key = f"{scope}:{scope_id}"
        self._consumed[key] = self._consumed.get(key, 0) + tokens_used

    async def check_limit(self, scope: str, scope_id: str) -> bool:
        """In-memory foundation: always allow (limits enforced in BudgetManager with config)."""
        return True


class SimpleAutonomyGate(AutonomyGate):
    """Two-phase autonomy gate stub (foundation)."""

    async def request_autonomy(self, level: int, context: dict[str, Any]) -> bool:
        # Always allow low autonomy levels (foundation default)
        if level <= 1:
            return True
        # Stub — would check DEFINER or policy in real implementation
        return False

    async def record_autonomy_use(self, level: int, context: dict[str, Any]) -> None:
        # No-op in foundation
        pass


# --- BudgetManager (extension, preserves existing code) ---

from aip.foundation.protocols import BudgetStore, EventStore
from aip.foundation.schemas import BudgetConfig, BudgetScope


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
        remaining = limit - consumed

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
