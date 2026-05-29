"""Tests for CHUNK-5.3 Context Anxiety Detector (Type F)."""

import pytest

from aip.orchestration.l4.anxiety_detector import ContextAnxietyDetector


@pytest.mark.asyncio
async def test_anxiety_detector_detects_declining_outputs():
    detector = ContextAnxietyDetector(min_length_drop=0.3, window=5)
    outputs = [
        "This is a long detailed response with lots of information and analysis...",
        "Shorter response with less detail.",
        "Even shorter.",
        "Minimal.",
    ]
    signal = await detector.detect("s1", recent_outputs=outputs)
    assert signal is not None
    assert signal.signal_type == "anxiety"
    assert signal.failure_type == "F"


@pytest.mark.asyncio
async def test_anxiety_detector_returns_none_for_stable_outputs():
    detector = ContextAnxietyDetector(min_length_drop=0.4, window=5)
    outputs = [
        "Detailed response one.",
        "Detailed response two with similar length.",
        "Detailed response three also similar.",
    ]
    signal = await detector.detect("s1", recent_outputs=outputs)
    assert signal is None
