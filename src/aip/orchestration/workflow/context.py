"""
Workflow execution context (foundation).

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
        """: Delegates to injected BudgetStore (now with correct bool return).

        Fixed the optimistic-always-True path recorded in the 3.11 foundation stub.
        When no running event loop (all current tests, sync entrypoints, examples),
        uses asyncio.run to obtain the authoritative decision from the store.
        When inside a running loop (real async workflow execution), schedules the
        store mutation while using the local budget_remaining shadow for the
        immediate sync return value (eventual consistency for this foundation layer).
        Future L6 chunks may introduce a fully-async consume_budget variant.
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
                    return asyncio.run(coro)
                else:
                    # Inside running loop (async runner path): schedule mutation,
                    # decide immediately via local shadow counter below.
                    asyncio.create_task(coro)  # type: ignore[arg-type]
            except Exception:
                pass

        # Fallback / shadow counter (existing Phase 2 behavior, preserved exactly)
        if self.budget_remaining is None:
            return True
        if amount > self.budget_remaining:
            return False
        self.budget_remaining -= amount
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
        """
        return WorkflowContext(
            variables=self.variables.copy(),
            budget_remaining=self.budget_remaining,
            protocols=self.protocols,  # shared protocols
            events=self.events,        # shared event log for now
            metadata={"parent": id(self)},
        )
