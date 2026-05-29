"""Trajectory Regulator  — the "2 of 3" composer.

If two or more of the three L4 signals (loop D, anxiety F, failure streak E)
fire within the session window, it triggers a trajectory correction intervention."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas import TrajectorySignal
from aip.orchestration.l4.reset import ResetRecommendation  # reuse existing structure


class TrajectoryRegulator:
    """The "2 of 3" L4 regulator.

    Composes the three detectors and decides when an intervention
    (context reset / trajectory correction) is warranted.
    """

    def __init__(self):
        pass

    async def evaluate(
        self,
        session_id: str,
        recent_signals: list[TrajectorySignal],
        model_gen_assumption: str | None = None,
    ) -> ResetRecommendation | None:
        """Apply the 2-of-3 rule.

        If two or more distinct signal types are present in the recent window,
        return a ResetRecommendation.
        """
        if not recent_signals:
            return None

        signal_types = {s.signal_type for s in recent_signals}
        if len(signal_types) >= 2:
            return ResetRecommendation(
                session_id=session_id,
                signals=recent_signals,
                action="context_reset",
                reason=f"2-of-3 L4 rule fired: signals = {signal_types}",
                model_gen_assumption=model_gen_assumption
                or (
                    "L4 interventions are needed when current models enter "
                    "repetitive or low-value trajectories in multi-turn sessions"
                ),
            )

        return None
