"""
L4 — Trajectory Regulation and Context Reset (Foundation)

Per Architecture Rev 5.2 §10.
This package provides deterministic, zero-token components for detecting
session drift, loops, and context anxiety from the trace archive.

CHUNK-3.1: Basic monitor foundation only. Full context reset protocol
and Sexton integration are deferred to later L4 chunks.
"""

from aip.orchestration.l4.monitor import TrajectoryMonitor, TrajectorySignal

__all__ = ["TrajectoryMonitor", "TrajectorySignal"]
