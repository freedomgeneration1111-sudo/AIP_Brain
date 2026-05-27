"""
L4 Trajectory Monitor Foundation (CHUNK-3.1 spec delta)

Implements the minimal detection capability from Architecture Rev 5.2 §10.1:
- Queries TraceStore for recent events in a session window.
- Detects basic signals for failure_types D (Session Drift / Loop) and F (Context Anxiety).
- Applies the "2 of 3 signals" rule using deterministic heuristics on trace data.
- Emits TrajectorySignal objects tagged with model_gen_assumption per §1.8 / gate [31].

This is deliberately the smallest useful foundation:
- Pure query + count/trend heuristics (zero tokens, no model calls).
- No execution of the full Context Reset Protocol (§10.2).
- No Sexton classification logic.
- Injected via WorkflowContext.protocols["trajectory_monitor"] (or direct construction in tests).

All heuristics that encode assumptions about model trajectory behavior under
context pressure carry an explicit model_gen_assumption field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aip.foundation.protocols import TraceStore


@dataclass
class TrajectorySignal:
    """
    A detected trajectory problem signal.

    Per Architecture §10.1 and Appendix E:
    - signal_type: "loop_d", "context_anxiety_f", "combined_2of3", etc.
    - evidence: supporting recent trace events (truncated for logging).
    - model_gen_assumption: Non-null when the heuristic compensates for
      documented model limitations (e.g., tendency to degenerate or rush
      under context pressure). Required for §1.8 auditability.
    """

    signal_type: str
    session_id: str
    confidence: float  # 0.0–1.0 rough heuristic strength
    evidence: list[dict[str, Any]] = field(default_factory=list)
    model_gen_assumption: str | None = (
        "L4 2-of-3 heuristic encodes assumptions about model trajectory "
        "degeneration (repetitive/generic output, shortening, hedging) under "
        "growing context pressure or loop formation. See Architecture Rev 5.2 "
        "§10.1, Appendix E (Types D/F), and §1.8 Harness Evolution Principle."
    )


class TrajectoryMonitor:
    """
    Deterministic L4 foundation monitor.

    Usage (typical):
        monitor = TrajectoryMonitor(trace_store=real_or_fake_trace_store)
        signals = monitor.detect(session_id="sess_123")

    The monitor never constructs a TraceStore itself — it is always injected
    (directly or via WorkflowContext.protocols).
    """

    def __init__(self, trace_store: TraceStore, window_limit: int = 50) -> None:
        self._trace_store = trace_store
        self._window_limit = window_limit

    async def detect(self, session_id: str) -> list[TrajectorySignal]:
        """
        Run the basic 2-of-3 trajectory signal detection for the given session.

        Current minimal heuristics (will be extended in later L4 chunks):
        - D signal: presence of recent events with failure_type == "D"
        - F signal: presence of recent events with failure_type == "F"
        - Combined: if both D and F (or multiple of one type) appear in the window,
          emit a combined_2of3 signal (proxy for the "2 of 3" rule when only two
          primary L4 types are observable in the current trace producers).

        Returns [] when no concerning signals are detected.
        Always safe to call; never raises on empty trace.
        """
        if not session_id:
            return []

        # Query via injected protocol only (layering + injection invariant)
        try:
            events = await self._trace_store.get_recent_events(
                session_id=session_id, limit=self._window_limit
            )
        except Exception:
            # Defensive: a misbehaving store must not break the caller
            return []

        if not events:
            return []

        # Count signals present in the window
        d_events: list[dict[str, Any]] = []
        f_events: list[dict[str, Any]] = []

        for ev in events:
            ft = ev.get("failure_type")
            if ft == "D":
                d_events.append(ev)
            elif ft == "F":
                f_events.append(ev)

        signals: list[TrajectorySignal] = []

        if d_events:
            signals.append(
                TrajectorySignal(
                    signal_type="loop_d",
                    session_id=session_id,
                    confidence=min(1.0, len(d_events) / 3.0),
                    evidence=d_events[:3],  # keep small for logs
                )
            )

        if f_events:
            signals.append(
                TrajectorySignal(
                    signal_type="context_anxiety_f",
                    session_id=session_id,
                    confidence=min(1.0, len(f_events) / 3.0),
                    evidence=f_events[:3],
                )
            )

        # Basic "2 of 3" proxy: when we see both primary L4 failure types (D and F)
        # in the recent window, emit the combined signal. Real 2-of-3 (including E streaks)
        # will be strengthened once more trace producers (tool nodes) exist.
        if d_events and f_events:
            signals.append(
                TrajectorySignal(
                    signal_type="combined_2of3",
                    session_id=session_id,
                    confidence=0.85,
                    evidence=(d_events[:2] + f_events[:2]),
                    model_gen_assumption=(
                        "Combined D+F observation treated as proxy for the §10.1 "
                        "'2 of 3 signals' rule. Encodes the assumption that "
                        "co-occurrence of drift and context anxiety within a short "
                        "window indicates actionable trajectory degeneration "
                        "requiring intervention. See Architecture §10.1."
                    ),
                )
            )

        return signals
