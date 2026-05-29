"""Trajectory regulation and session context types.

L4 trajectory detection signals and multi-turn session state
used by the trajectory regulator and context reset logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Type alias for trajectory signal types
TrajectorySignalType = Literal["loop", "anxiety", "failure_streak"]


@dataclass
class TrajectorySignal:
    """A single detection from an L4 trajectory detector.

    Loop detection → D, anxiety → F, failure streak → E.
    Every L4 trigger must carry model_gen_assumption.
    D/E/F are the L4 failure type codes.
    """
    signal_type: TrajectorySignalType
    session_id: str
    artifact_id: str | None = None
    failure_type: Literal["D", "E", "F"] = "D"
    confidence: float = 0.0
    detail: str = ""
    detected_at: str = ""  # REQUIRED — ISO 8601
    model_gen_assumption: str | None = None


@dataclass
class SessionContext:
    """State of a multi-turn session for L4 and context management.

    Tracks turn count, context window usage, artifacts produced,
    and when the last reset occurred.
    """
    session_id: str
    project_id: str
    turn_count: int = 0
    context_tokens_estimate: int = 0
    context_window_limit: int = 128000
    artifacts_produced: list[str] = field(default_factory=list)
    last_reset_at: str | None = None


__all__ = [
    "TrajectorySignalType",
    "TrajectorySignal",
    "SessionContext",
]
