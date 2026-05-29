"""Failure Streak Detector (Type E).

Detects consecutive "false success" claims (model says it is done but the output
is incomplete or low-substance). Emits TrajectorySignal with failure_type="E".

The substance_score default (0.3) is intentionally BELOW the detection threshold
(0.4 by default) so that Type E signals can fire when outcomes lack real
substance scores. Previously, the default was 0.5 which was always >= 0.4,
making Type E detection completely non-functional.

Both the default_substance_score and substance_threshold are configurable via
constructor parameters, allowing callers to tune detection sensitivity.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas import TrajectorySignal

logger = logging.getLogger(__name__)


class FailureStreakDetector:
    """Failure streak / false success detector (Type E).

    Looks for a run of recent outcomes that look like "claimed completion but low value".

    Args:
        streak_threshold: Number of consecutive low-substance outcomes to trigger detection.
        default_substance_score: Default substance_score when outcome dict lacks the key.
            Must be below substance_threshold so detection can fire on missing data.
        substance_threshold: Score below which an outcome is considered "low substance".
            Outcomes with substance_score < substance_threshold count toward the streak.
    """

    def __init__(
        self,
        streak_threshold: int = 3,
        default_substance_score: float = 0.3,
        substance_threshold: float = 0.4,
    ):
        self.streak_threshold = streak_threshold
        self.default_substance_score = default_substance_score
        self.substance_threshold = substance_threshold

    async def detect(
        self,
        session_id: str,
        recent_outcomes: list[dict] | None = None,
        model_gen_assumption: str | None = None,
    ) -> TrajectorySignal | None:
        """Detect a failure streak if we see too many low-substance 'success' claims in a row.

        An outcome is counted toward the streak when:
          1. It claims completion (claimed_done=True or "done" in outcome string), AND
          2. Its substance_score is below the substance_threshold.

        When substance_score is missing from an outcome dict, default_substance_score
        is used (0.3 by default — below the 0.4 threshold so Type E can fire).
        """
        if not recent_outcomes or len(recent_outcomes) < self.streak_threshold:
            return None

        streak = 0
        for outcome in recent_outcomes[-self.streak_threshold :]:
            claimed_done = outcome.get("claimed_done", False) or "done" in str(outcome.get("outcome", "")).lower()
            substance = outcome.get("substance_score", self.default_substance_score)
            if claimed_done and substance < self.substance_threshold:
                streak += 1
            else:
                streak = 0

        if streak >= self.streak_threshold:
            logger.info(
                "Type E failure streak detected: %d consecutive low-substance completions (session=%s)",
                streak,
                session_id,
            )
            return TrajectorySignal(
                signal_type="failure_streak",
                session_id=session_id,
                failure_type="E",
                confidence=min(0.55 + (streak - self.streak_threshold) * 0.12, 0.93),
                detail=f"Detected {streak} consecutive low-substance 'completion' claims.",
                detected_at=datetime.now(timezone.utc).isoformat(),
                model_gen_assumption=model_gen_assumption
                or (
                    "current models frequently claim completion while producing "
                    "incomplete or low-substance outputs (false success)"
                ),
            )

        return None
