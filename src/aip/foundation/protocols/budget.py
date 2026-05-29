"""Budget store Protocol definition.

Token budget tracking across session, project, and daily scopes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BudgetStore(Protocol):
    """Budget and autonomy tracking.

    Supports consume/remaining/reset for simple budgets and
    get_budget/record_usage/check_limit for scoped budgets.
    """

    async def consume(self, amount: int, budget_id: str = "default") -> bool:
        """Consume amount from the named budget. Return True if successful."""
        ...

    async def remaining(self, budget_id: str = "default") -> int:
        """Return remaining budget units for the named budget."""
        ...

    async def reset(self, budget_id: str = "default", amount: int | None = None) -> None:
        """Reset or initialize the named budget."""
        ...

    async def get_budget(self, scope: "BudgetScope", scope_id: str) -> dict:
        """Get current budget status.

        Args:
            scope: Budget scope (session/project/daily).
            scope_id: Scope identifier (session_id, project_id, or date string).

        Returns:
            dict with consumed, remaining, limit, warning_threshold.
        """
        ...

    async def record_usage(
        self,
        scope: "BudgetScope",
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        """Record token consumption after a model call.

        Called by the workflow engine after each agent node completes.
        Writes to budget_ledger table in state.db.
        """
        ...

    async def check_limit(self, scope: "BudgetScope", scope_id: str) -> bool:
        """Check whether budget has remaining capacity.

        Returns True if budget is not exhausted, False if at/past limit.
        Used by workflow engine before dispatching model calls.
        When budget_hard_stop is True, returning False blocks the call.
        """
        ...


__all__ = [
    "BudgetStore",
]
