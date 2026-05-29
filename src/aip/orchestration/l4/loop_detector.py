"""Loop Detector (Type D) —.

Detects repeated patterns in a session's trace events (basic loop / session drift detection).
Emits TrajectorySignal with failure_type="D" when a loop is detected.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aip.foundation.protocols import TraceStore
from aip.foundation.schemas import TrajectorySignal


class LoopDetector:
    """Basic loop detector for L4 trajectory regulation (Type D).

    Looks for repeated sequences of node_types within a recent window of trace events
    for a given session.
    """

    def __init__(
        self,
        trace_store: TraceStore,
        window_size: int = 20,
        min_repeats: int = 3,
    ) -> None:
        self.trace_store = trace_store
        self.window_size = window_size
        self.min_repeats = min_repeats

    async def detect(
        self,
        session_id: str,
        model_gen_assumption: str | None = None,
    ) -> TrajectorySignal | None:
        """Scan recent trace events for a session and return a signal if a loop is found."""
        events = await self.trace_store.query_events(
            session_id=session_id,
            limit=self.window_size,
        )

        if len(events) < self.min_repeats:
            return None

        # Extract node types in order (most recent first from query, so reverse for sequence)
        node_sequence = [e.get("node_type", "") for e in reversed(events) if e.get("node_type")]

        # Very simple loop detection: look for a short repeated subsequence
        for length in range(2, min(6, len(node_sequence) // self.min_repeats + 1)):
            for start in range(len(node_sequence) - length * self.min_repeats + 1):
                pattern = tuple(node_sequence[start : start + length])
                repeats = 0
                i = start
                while i + length <= len(node_sequence) and tuple(node_sequence[i : i + length]) == pattern:
                    repeats += 1
                    i += length
                if repeats >= self.min_repeats:
                    return TrajectorySignal(
                        signal_type="loop",
                        session_id=session_id,
                        failure_type="D",
                        confidence=min(0.5 + (repeats - self.min_repeats) * 0.1, 0.95),
                        detail=f"Detected repeating pattern of length {length} ({repeats} times): {pattern}",
                        detected_at=datetime.now(timezone.utc).isoformat(),
                        model_gen_assumption=model_gen_assumption
                        or "current models can enter repetitive loops in multi-turn sessions",
                    )

        return None
