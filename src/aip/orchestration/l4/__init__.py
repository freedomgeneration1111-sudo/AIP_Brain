"""
L4 — Trajectory Regulation and Context Reset (Foundation)

Per Architecture Rev 5.2.
This package provides deterministic, zero-token components for detecting
session drift, loops, and context anxiety from the trace archive, plus the
response path of the Context Reset Protocol.

Basic monitor foundation (detection only).
Context Reset Protocol foundation (recommendation + intervention
logging). Full Sexton integration and L4b advanced metrics deferred.
"""

from aip.orchestration.l4.monitor import TrajectoryMonitor
from aip.foundation.schemas import TrajectorySignal  # issue 18: import from schemas
from aip.orchestration.l4.reset import L4ResetCoordinator, ResetRecommendation

__all__ = ["TrajectoryMonitor", "TrajectorySignal", "L4ResetCoordinator", "ResetRecommendation"]
