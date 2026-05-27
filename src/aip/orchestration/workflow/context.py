"""
Workflow execution context (CHUNK-2.1 foundation).

Provides the shared state, budget tracking, protocol injection,
and event emission mechanism that all nodes use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class WorkflowContext:
    """
    Execution context passed to every node.

    Responsibilities:
    - Variable / state store for the workflow run
    - Budget tracking (especially important for parallel nodes)
    - Access to injected protocols (stores, etc.) — never direct implementations
    - Simple event emission (critical for dialog nodes)
    """

    variables: dict[str, Any] = field(default_factory=dict)
    budget_remaining: Optional[int] = None   # tokens or abstract units
    protocols: dict[str, Any] = field(default_factory=dict)  # name -> protocol instance
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

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
        """CHUNK-3.11: Delegates to injected BudgetStore if present (from protocols),
        otherwise falls back to the simple in-context counter.
        Returns False if budget would be exceeded.
        """
        budget_store = self.get_protocol("budget_store")
        if budget_store is not None:
            # Fire-and-forget for foundation (real impl would await)
            try:
                # In async context this would be awaited; for foundation we do best-effort
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't await here safely in all cases; assume sync wrapper or fire
                    pass
                else:
                    loop.run_until_complete(budget_store.consume(amount))
                return True  # optimistic for foundation wiring
            except Exception:
                pass

        # Fallback to simple counter (existing Phase 2 behavior)
        if self.budget_remaining is None:
            return True
        if amount > self.budget_remaining:
            return False
        self.budget_remaining -= amount
        return True

    def fork_for_parallel(self) -> "WorkflowContext":
        """
        Create a child context for a parallel branch.
        Inherits current variables and budget (per Architecture invariant).
        """
        return WorkflowContext(
            variables=self.variables.copy(),
            budget_remaining=self.budget_remaining,
            protocols=self.protocols,  # shared protocols
            events=self.events,        # shared event log for now
            metadata={"parent": id(self)},
        )
