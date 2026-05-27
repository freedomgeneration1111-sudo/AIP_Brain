"""Context Anxiety Detector (Type F) — CHUNK-5.3.

Detects declining output length / quality in recent synthesis turns (proxy for context anxiety / window collapse).
Emits TrajectorySignal with failure_type="F".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas import TrajectorySignal


class ContextAnxietyDetector:
    """Context anxiety / output collapse detector (Type F).

    Uses a simple heuristic on recent output lengths (provided by the caller
    or extracted from trace events) to detect when the model is producing
    progressively shorter or lower-quality responses.
    """

    def __init__(self, min_length_drop: float = 0.4, window: int = 5):
        self.min_length_drop = min_length_drop
        self.window = window

    async def detect(
        self,
        session_id: str,
        recent_outputs: list[str] | None = None,
        model_gen_assumption: str | None = None,
    ) -> TrajectorySignal | None:
        """Detect context anxiety if recent outputs show significant length decline."""
        if not recent_outputs or len(recent_outputs) < 2:
            return None

        lengths = [len(o or "") for o in recent_outputs[-self.window :]]
        if len(lengths) < 2:
            return None

        # Check for consistent downward trend
        drops = 0
        for i in range(1, len(lengths)):
            if lengths[i] < lengths[i-1] * (1 - self.min_length_drop / 2):
                drops += 1

        if drops >= 2:
            confidence = min(0.6 + drops * 0.08, 0.92)
            return TrajectorySignal(
                signal_type="anxiety",
                session_id=session_id,
                failure_type="F",
                confidence=confidence,
                detail=f"Output length declining over last {len(lengths)} turns (drops={drops}). Possible context anxiety / window collapse.",
                detected_at=datetime.now(timezone.utc).isoformat(),
                model_gen_assumption=model_gen_assumption or "current models suffer output-length collapse and loss of coherence as context window fills in multi-turn sessions",
            )

        return None
