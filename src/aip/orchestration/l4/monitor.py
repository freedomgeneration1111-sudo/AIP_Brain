"""
L4 Trajectory Monitor Foundation (spec delta)

Implements the minimal detection capability from Architecture Rev 5.2:
- Queries TraceStore for recent events in a session window.
- Detects basic signals for failure_types D (Session Drift / Loop) and F (Context Anxiety).
- Applies the "2 of 3 signals" rule using deterministic heuristics on trace data.
- Emits TrajectorySignal objects tagged with model_gen_assumption.

This is deliberately the smallest useful foundation:
- Pure query + count/trend heuristics (zero tokens, no model calls).
- No execution of the full Context Reset Protocol.
- No Sexton classification logic.
- Injected via WorkflowContext.protocols["trajectory_monitor"] (or direct construction in tests).

All heuristics that encode assumptions about model trajectory behavior under
context pressure carry an explicit model_gen_assumption field.

Issue 17: Removed duplicate TrajectorySignal dataclass. Import from foundation.schemas instead.
Updated signal creation to use schema's field names (detail instead of evidence, detected_at, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import TraceStore
from aip.foundation.schemas import TrajectorySignal


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
        - F signal: presence of recent events with failure_type == "F"  (enhanced by L4b in 3.5)
        - Combined: if both D and F (or multiple of one type) appear in the window,
          emit a combined_2of3 signal (proxy for the "2 of 3" rule when only two
          primary L4 types are observable in the current trace producers).

        L4b extension : In addition to pre-labeled F events, the monitor
        now applies deterministic heuristics on raw event data to detect Type F
        (Context Anxiety) signals per Architecture Appendix E:
        - Hedging language in detail/failure_detail
        - Declining output length trends (via token_count_out where present)
        - High context pressure (event density + recent heavy nodes)

        These L4b indicators can emit or strengthen context_anxiety_f signals
        with higher confidence and richer evidence.

        Returns [] when no concerning signals are detected.
        Always safe to call; never raises on empty trace.
        """
        if not session_id:
            return []

        # Query via injected protocol only (layering + injection invariant)
        try:
            events = await self._trace_store.get_recent_events(session_id=session_id, limit=self._window_limit)
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
        now = datetime.now(timezone.utc).isoformat()

        if d_events:
            signals.append(
                TrajectorySignal(
                    signal_type="loop",
                    session_id=session_id,
                    failure_type="D",
                    confidence=min(1.0, len(d_events) / 3.0),
                    detail=f"Detected {len(d_events)} D-type events in session window",
                    detected_at=now,
                    model_gen_assumption=(
                        "L4 2-of-3 heuristic encodes assumptions about model trajectory "
                        "degeneration (repetitive/generic output, shortening, hedging) under "
                        "growing context pressure or loop formation. See Architecture Rev 5.2 "
                        "§10.1, Appendix E (Types D/F), and §1.8 Harness Evolution Principle."
                    ),
                ),
            )

        # L4b Context Anxiety heuristics  - Appendix E Type F
        l4b_f_events, l4b_confidence, l4b_evidence, l4b_assumption = self._run_l4b_context_anxiety_heuristics(
            events,
            session_id,
        )

        if l4b_f_events or l4b_confidence > 0.6:
            # If we have pre-labeled F or strong L4b indicators, emit/enhance the F signal
            combined_f = f_events + l4b_f_events
            final_conf = max(min(1.0, len(combined_f) / 3.0) if combined_f else 0.0, l4b_confidence)
            signals.append(
                TrajectorySignal(
                    signal_type="anxiety",
                    session_id=session_id,
                    failure_type="F",
                    confidence=final_conf,
                    detail=(
                        f"Context anxiety detected: {len(combined_f)} F-type events, "
                        f"L4b confidence {l4b_confidence:.2f}"
                    ),
                    detected_at=now,
                    model_gen_assumption=l4b_assumption,
                ),
            )
        elif f_events:
            # Fallback to basic pre-labeled only
            signals.append(
                TrajectorySignal(
                    signal_type="anxiety",
                    session_id=session_id,
                    failure_type="F",
                    confidence=min(1.0, len(f_events) / 3.0),
                    detail=f"Detected {len(f_events)} F-type events in session window",
                    detected_at=now,
                ),
            )

        # Basic "2 of 3" proxy: when we see both primary L4 failure types (D and F)
        # in the recent window, emit the combined signal. Real 2-of-3 (including E streaks)
        # will be strengthened once more trace producers (tool nodes) exist.
        if d_events and (f_events or l4b_confidence > 0.5):
            signals.append(
                TrajectorySignal(
                    signal_type="failure_streak",
                    session_id=session_id,
                    failure_type="D",
                    confidence=0.85,
                    detail="Combined D+F observation treated as proxy for 2-of-3 rule",
                    detected_at=now,
                    model_gen_assumption=(
                        "Combined D+F observation treated as proxy for the §10.1 "
                        "'2 of 3 signals' rule. Encodes the assumption that "
                        "co-occurrence of drift and context anxiety within a short "
                        "window indicates actionable trajectory degeneration "
                        "requiring intervention. See Architecture §10.1."
                    ),
                ),
            )

        return signals

    # --- L4b private helpers (, deterministic, zero-token) ---

    def _contains_hedging(self, text: str) -> bool:
        """Simple keyword heuristic for hedging language (Appendix E Type F signal)."""
        if not text:
            return False
        text_lower = text.lower()
        hedging = {
            "perhaps",
            "maybe",
            "possibly",
            "likely",
            "somewhat",
            "approximately",
            "i think",
            "it seems",
            "could be",
            "might",
            "probably",
            "potentially",
            "to some extent",
            "fairly",
            "rather",
            "quite",
        }
        return any(word in text_lower for word in hedging)

    def _compute_length_trend(self, events: list[dict[str, Any]]) -> float:
        """
        Simple declining length trend score (0.0-1.0).
        Looks for token_count_out decreasing in recent events (newest first).
        """
        lengths: list[int] = []
        for ev in events:
            tc = ev.get("token_count_out")
            if isinstance(tc, (int, float)) and tc > 0:
                lengths.append(int(tc))
        if len(lengths) < 2:
            return 0.0
        # Count how many consecutive pairs show decline
        declines = sum(1 for i in range(len(lengths) - 1) if lengths[i] < lengths[i + 1])
        return declines / (len(lengths) - 1)

    def _estimate_context_pressure(self, events: list[dict[str, Any]]) -> float:
        """
        Proxy for context pressure (0.0-1.0): high event density in window +
        presence of recent L2/L3/L4 or failure events.
        """
        if not events:
            return 0.0
        density = min(1.0, len(events) / max(1, self._window_limit))
        heavy_nodes = sum(
            1 for ev in events[:8] if ev.get("node_type") in ("L2", "L3a", "L3b", "L4") or ev.get("failure_type")
        )
        heavy_score = min(1.0, heavy_nodes / 5.0)
        return density * 0.6 + heavy_score * 0.4

    def _run_l4b_context_anxiety_heuristics(
        self,
        events: list[dict[str, Any]],
        session_id: str,
    ) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]], str | None]:
        """
        L4b Context Anxiety (Type F) detection per Architecture Appendix E.
        Returns (supporting_events, confidence, evidence, model_gen_assumption or None)
        """
        hedging_events: list[dict] = []
        for ev in events:
            text = str(ev.get("detail", "")) + " " + str(ev.get("failure_detail", ""))
            if self._contains_hedging(text):
                hedging_events.append(ev)

        length_decline = self._compute_length_trend(events)
        pressure = self._estimate_context_pressure(events)

        indicators = 0
        if hedging_events:
            indicators += 1
        if length_decline > 0.4:
            indicators += 1
        if pressure > 0.55:
            indicators += 1

        if indicators == 0:
            return [], 0.0, [], None

        confidence = min(0.95, 0.5 + (indicators - 1) * 0.2 + max(length_decline, pressure) * 0.15)

        evidence = hedging_events[:2]
        if length_decline > 0.4:
            # Add a couple of recent events as length trend evidence
            evidence.extend(events[:2])

        assumption = (
            "L4b heuristics for Type F (Context Anxiety) per Appendix E: "
            "hedging language in event details, declining output length trends "
            "(token_count_out), and high context pressure (event density + heavy recent nodes). "
            "These encode assumptions about model behavior under growing context load. "
            "See Architecture Rev 5.2 Appendix E and §1.8. Toggleable on model upgrade."
        )

        return hedging_events, round(confidence, 2), evidence[:3], assumption
