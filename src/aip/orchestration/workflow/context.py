"""
Workflow execution context (foundation).

Provides the shared state, budget tracking, protocol injection,
and event emission mechanism that all nodes use.

Budget behavior (Campaign 1 fix):
- budget_remaining defaults to DEFAULT_WORKFLOW_BUDGET (500,000 tokens),
  matching BudgetConfig.session_token_limit. This prevents workflows from
  running with infinite budget by default.
- consume_budget() enforces the limit: returns False when budget is exhausted.
- Explicit budget_remaining=None is still supported for test fixtures and
  deliberate unlimited-budget scenarios, but logs a warning on first use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default workflow budget matches BudgetConfig.session_token_limit (500k tokens).
# This ensures workflows have a finite, enforceable budget unless explicitly
# overridden with budget_remaining=None (e.g. for test fixtures).
DEFAULT_WORKFLOW_BUDGET: int = 500_000


@dataclass
class WorkflowContext:
    """
    Execution context passed to every node.

    Responsibilities:
    - Variable / state store for the workflow run
    - Budget tracking (especially important for parallel nodes)
    - Access to injected protocols (stores, etc.) — never direct implementations
    - Simple event emission (critical for dialog nodes)

    Budget defaults:
    - budget_remaining defaults to DEFAULT_WORKFLOW_BUDGET (500k) — not None.
    - Set budget_remaining=None explicitly only for test fixtures or when
      you deliberately want unlimited budget (a warning is logged).
    """

    variables: dict[str, Any] = field(default_factory=dict)
    budget_remaining: Optional[int] = DEFAULT_WORKFLOW_BUDGET   # tokens or abstract units
    protocols: dict[str, Any] = field(default_factory=dict)  # name -> protocol instance
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Internal flag to track whether we've already warned about infinite budget
    _warned_infinite_budget: bool = field(default=False, repr=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get_protocol(self, name: str) -> Any:
        """Retrieve an injected protocol (e.g. 'artifact_store', 'event_store')."""
        return self.protocols.get(name)

    def emit_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Emit an event (used by dialog nodes and the engine itself)."""
        self.events.append({
            "type": event_type,
            "payload": payload or {},
        })

    def consume_budget(self, amount: int) -> bool:
        """Consume budget and enforce limits.

        Delegates to injected BudgetStore when available (with correct bool return).
        Falls back to local budget_remaining counter.

        Budget enforcement:
        - If budget_remaining is None (explicitly set for test fixtures), always
          returns True but logs a warning on first use.
        - If budget_remaining is a finite number, returns False when the amount
          would exceed the remaining budget.
        - Logs consumption at DEBUG level and budget exhaustion at WARNING level.

        Returns:
            True if the budget consumption was allowed, False if denied.
        """
        budget_store = self.get_protocol("budget_store")
        if budget_store is not None:
            try:
                coro = budget_store.consume(amount, "default")
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is None or not loop.is_running():
                    # Safe to block — covers pytest sync tests and typical example runs
                    result = asyncio.run(coro)
                    if not result:
                        logger.warning(
                            "Budget denied by store: %d tokens requested, store refused "
                            "(budget_remaining shadow=%s).",
                            amount, self.budget_remaining,
                        )
                    else:
                        # Sync the local shadow counter with the store's decision
                        if self.budget_remaining is not None:
                            self.budget_remaining -= amount
                        logger.debug(
                            "Budget consumed: %d tokens (remaining shadow=%s).",
                            amount, self.budget_remaining,
                        )
                    return result
                else:
                    # Inside running loop (async runner path): schedule mutation,
                    # decide immediately via local shadow counter below.
                    asyncio.create_task(coro)  # type: ignore[arg-type]
            except Exception as exc:
                logger.warning(
                    "Budget store call failed (%s). Falling back to local counter.",
                    exc,
                )

        # Fallback / shadow counter (existing Phase 2 behavior, preserved exactly)
        if self.budget_remaining is None:
            if not self._warned_infinite_budget:
                logger.warning(
                    "WorkflowContext has budget_remaining=None (infinite budget). "
                    "This allows unlimited token consumption — set an explicit budget "
                    "for production workflows. This warning will not repeat."
                )
                # We can't mutate a dataclass field from a method if it's defined
                # with field(default=False), but we can use object.__setattr__
                # since this is an internal bookkeeping flag.
                try:
                    self._warned_infinite_budget = True
                except Exception:
                    pass
            return True
        if amount > self.budget_remaining:
            logger.warning(
                "Budget exhausted: %d tokens requested but only %d remaining. "
                "Consumption denied.",
                amount, self.budget_remaining,
            )
            return False
        self.budget_remaining -= amount
        logger.debug(
            "Budget consumed: %d tokens (remaining=%d).",
            amount, self.budget_remaining,
        )
        return True

    def request_autonomy(self, level: int, context: dict[str, Any] | None = None) -> bool:
        """: Delegates to injected AutonomyGate if present (L6 wiring).

        Provides the minimal integration surface for the two-phase gate delivered
        as a stub in 3.11. Mirrors SimpleAutonomyGate default behavior when no
        gate is injected. Uses the same async-compat pattern as the fixed
        consume_budget path so it works from both sync tests and async runners.
        """
        gate = self.get_protocol("autonomy_gate")
        if gate is not None:
            ctx = context or {}
            try:
                coro = gate.request_autonomy(level, ctx)
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is None or not loop.is_running():
                    return asyncio.run(coro)
                else:
                    asyncio.create_task(coro)  # type: ignore[arg-type]
                    return level <= 1  # optimistic for async foundation; decision scheduled
            except Exception:
                pass

        # Default foundation behavior (matches SimpleAutonomyGate when absent)
        return level <= 1

    def fork_for_parallel(self) -> "WorkflowContext":
        """
        Create a child context for a parallel branch.
        Inherits current variables and budget (per Architecture invariant).

        Note: budget_remaining is copied by value at fork time. Each parallel
        branch consumes from its own copy. If you need shared budget enforcement
        across parallel branches, inject a BudgetStore protocol instead.
        """
        return WorkflowContext(
            variables=self.variables.copy(),
            budget_remaining=self.budget_remaining,
            protocols=self.protocols,  # shared protocols
            events=self.events,        # shared event log for now
            metadata={"parent": id(self)},
        )
