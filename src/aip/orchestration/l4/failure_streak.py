"""Failure Streak Detector (Type E) — CHUNK-5.4.

Detects consecutive "false success" claims (model says it is done but the output is incomplete or low-substance).
Emits TrajectorySignal with failure_type="E".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas import TrajectorySignal


class FailureStreakDetector:
    """Failure streak / false success detector (Type E).

    Looks for a run of recent outcomes that look like "claimed completion but low value".
    """

    def __init__(self, streak_threshold: int = 3):
        self.streak_threshold = streak_threshold

    async def detect(
        self,
        session_id: str,
        recent_outcomes: list[dict] | None = None,
        model_gen_assumption: str | None = None,
    ) -> TrajectorySignal | None:
        """Detect a failure streak if we see too many low-substance 'success' claims in a row."""
        if not recent_outcomes or len(recent_outcomes) < self.streak_threshold:
            return None

        streak = 0
        for outcome in recent_outcomes[-self.streak_threshold :]:
            claimed_done = outcome.get("claimed_done", False) or "done" in str(outcome.get("outcome", "")).lower()
            substance = outcome.get("substance_score", 0.5)  # 0.0–1.0, higher is better
            if claimed_done and substance < 0.4:
                streak += 1
            else:
                streak = 0

        if streak >= self.streak_threshold:
            return TrajectorySignal(
                signal_type="failure_streak",
                session_id=session_id,
                failure_type="E",
                confidence=min(0.55 + (streak - self.streak_threshold) * 0.12, 0.93),
                detail=f"Detected {streak} consecutive low-substance 'completion' claims.",
                detected_at=datetime.now(timezone.utc).isoformat(),
                model_gen_assumption=model_gen_assumption or "current models frequently claim completion while producing incomplete or low-substance outputs (false success)",
            )

        return None
