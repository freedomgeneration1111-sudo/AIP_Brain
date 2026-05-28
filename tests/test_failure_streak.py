"""Tests for CHUNK-5.4 Failure Streak Detector (Type E)."""
import pytest

from aip.orchestration.l4.failure_streak import FailureStreakDetector


@pytest.mark.asyncio
async def test_failure_streak_detects_consecutive_false_success():
    detector = FailureStreakDetector(streak_threshold=3)
    outcomes = [
        {"claimed_done": True, "substance_score": 0.2},
        {"claimed_done": True, "substance_score": 0.25},
        {"claimed_done": True, "substance_score": 0.15},
    ]
    signal = await detector.detect("s1", recent_outcomes=outcomes)
    assert signal is not None
    assert signal.signal_type == "failure_streak"
    assert signal.failure_type == "E"


@pytest.mark.asyncio
async def test_failure_streak_returns_none_when_substance_is_good():
    detector = FailureStreakDetector(streak_threshold=3)
    outcomes = [
        {"claimed_done": True, "substance_score": 0.8},
        {"claimed_done": True, "substance_score": 0.75},
        {"claimed_done": True, "substance_score": 0.7},
    ]
    signal = await detector.detect("s1", recent_outcomes=outcomes)
    assert signal is None
