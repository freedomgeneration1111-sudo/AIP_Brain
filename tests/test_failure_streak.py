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


@pytest.mark.asyncio
async def test_default_substance_score_below_threshold():
    """Type E detection can fire when substance_score is missing from outcomes.

    The default_substance_score (0.3) is below substance_threshold (0.4),
    so outcomes without substance_score should count toward the streak.
    This was previously broken with default 0.5 >= 0.4 threshold.
    """
    detector = FailureStreakDetector(streak_threshold=3)
    # No substance_score key — should use default 0.3 (below 0.4 threshold)
    outcomes = [
        {"claimed_done": True},
        {"claimed_done": True},
        {"claimed_done": True},
    ]
    signal = await detector.detect("s1", recent_outcomes=outcomes)
    assert signal is not None
    assert signal.failure_type == "E"


@pytest.mark.asyncio
async def test_configurable_substance_threshold():
    """Substance threshold is configurable via constructor."""
    detector = FailureStreakDetector(streak_threshold=3, substance_threshold=0.5)
    # Scores between 0.4 and 0.5 should now trigger with higher threshold
    outcomes = [
        {"claimed_done": True, "substance_score": 0.45},
        {"claimed_done": True, "substance_score": 0.42},
        {"claimed_done": True, "substance_score": 0.48},
    ]
    signal = await detector.detect("s1", recent_outcomes=outcomes)
    assert signal is not None
    assert signal.failure_type == "E"


@pytest.mark.asyncio
async def test_configurable_default_substance_score():
    """Default substance score is configurable via constructor."""
    detector = FailureStreakDetector(
        streak_threshold=3,
        default_substance_score=0.6,  # Above default threshold
    )
    # No substance_score key — uses default 0.6 (above 0.4 threshold)
    outcomes = [
        {"claimed_done": True},
        {"claimed_done": True},
        {"claimed_done": True},
    ]
    signal = await detector.detect("s1", recent_outcomes=outcomes)
    # With default 0.6 >= 0.4, these should NOT trigger
    assert signal is None
