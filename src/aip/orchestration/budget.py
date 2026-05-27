"""
Budget and Autonomy Tracking (CHUNK-3.11 foundation).

Minimal deterministic implementation per Architecture L6 / Budget sections
and the existing BudgetStore + AutonomyGate protocol stubs.

For foundation: in-memory tracking, simple consumption on agent nodes,
parallel inheritance, and a two-phase autonomy gate stub.
Real persistence and complex L6 logic in later chunks.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols import BudgetStore, AutonomyGate


class InMemoryBudgetStore(BudgetStore):
    """Minimal in-memory BudgetStore for foundation (CHUNK-3.11)."""

    def __init__(self, initial_budget: int | None = None):
        self._budgets: dict[str, int] = {}
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


class SimpleAutonomyGate(AutonomyGate):
    """Two-phase autonomy gate stub (CHUNK-3.11 foundation)."""

    async def request_autonomy(self, level: int, context: dict[str, Any]) -> bool:
        # Phase 1: always allow low levels in foundation
        if level <= 1:
            return True
        # Phase 2: stub — would check DEFINER or policy in real impl
        return False

    async def record_autonomy_use(self, level: int, context: dict[str, Any]) -> None:
        # No-op in foundation
        pass
