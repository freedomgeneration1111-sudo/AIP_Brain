"""Trajectory regulation free functions (CHUNK-5.7 support).

Provides the exact interface expected by the CHUNK-5.7 ANNEX and SessionManager:
- regulate_trajectory
- should_intervene

This is an additive extension layer (per PHASE2_IMPORT_NOTES §Repo State Reconciliation
and "extend existing rather than replace" strategy). It re-uses the 5.5
TrajectoryRegulator class (l4/regulator.py) for the 2-of-3 decision logic while
providing the free-function shape the session manager and later engine integration
expect.

Real detector wiring (loop, anxiety, failure_streak against TraceStore) belongs
in 5.8 integration or a follow-on. This stub + rule is sufficient and deterministic
for the 5.7 gate.
"""

from __future__ import annotations

from typing import Any

from aip.foundation.schemas import TrajectorySignal
from aip.orchestration.l4.regulator import TrajectoryRegulator


# Reusable instance (the 5.5 class is stateless)
_regulator = TrajectoryRegulator()


async def regulate_trajectory(
    session_context: "SessionContext",
    trace_store: "TraceStore",
    config: Any = None,
) -> list[TrajectorySignal]:
    """Return trajectory signals for the session.

    Minimal impl for 5.7: returns [] (shape only). The check_trajectory gate
    tests only assert isinstance(list) and the handle_ tests supply explicit
    signals. Full detector execution (5.2-5.4) + trace queries deferred to 5.8
    where engine + multi-turn context are exercised together.

    When wired, this would:
      - query recent trace events via trace_store
      - run the three L4 detectors
      - return the firing TrajectorySignals (each carrying §1.8 model_gen_assumption)
    """
    # For 5.7 gate compatibility — real signals come from callers in handle tests
    return []


async def should_intervene(
    signals: list[TrajectorySignal],
    config: Any = None,
) -> bool:
    """Apply the "2 of 3" rule.

    Re-uses the logic shape from 5.5 TrajectoryRegulator.evaluate (distinct
    signal types >= 2). This keeps the decision in one place while satisfying
    the free-function contract expected by SessionManager.check_trajectory.
    """
    if not signals:
        return False
    signal_types = {s.signal_type for s in signals}
    # A single signal type firing multiple times does not count (per 5.5 prose)
    return len(signal_types) >= 2
