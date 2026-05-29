"""Tests for CHUNK-5.5 Trajectory Regulator."""

import pytest

from aip.foundation.schemas import TrajectorySignal
from aip.orchestration.l4.regulator import TrajectoryRegulator


@pytest.mark.asyncio
async def test_regulator_fires_on_two_signals():
    regulator = TrajectoryRegulator()
    signals = [
        TrajectorySignal(signal_type="loop", session_id="s1", failure_type="D"),
        TrajectorySignal(signal_type="anxiety", session_id="s1", failure_type="F"),
    ]
    rec = await regulator.evaluate("s1", signals)
    assert rec is not None
    assert rec.action == "context_reset"
    assert "2-of-3" in rec.reason


@pytest.mark.asyncio
async def test_regulator_does_not_fire_on_one_signal():
    regulator = TrajectoryRegulator()
    signals = [
        TrajectorySignal(signal_type="loop", session_id="s1", failure_type="D"),
    ]
    rec = await regulator.evaluate("s1", signals)
    assert rec is None
