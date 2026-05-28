"""Tests for CHUNK-5.2 Loop Detector (Type D)."""
import pytest

from aip.foundation.protocols import TraceStore
from aip.orchestration.l4.loop_detector import LoopDetector


class FakeTraceStore(TraceStore):
    def __init__(self, events):
        self._events = events

    async def query_events(self, session_id, node_type=None, limit=100):
        filtered = [e for e in self._events if e.get("session_id") == session_id]
        if node_type:
            filtered = [e for e in filtered if e.get("node_type") == node_type]
        return filtered[-limit:]


@pytest.mark.asyncio
async def test_loop_detector_detects_repeating_pattern():
    events = [
        {"session_id": "s1", "node_type": "retrieve"},
        {"session_id": "s1", "node_type": "synthesize"},
        {"session_id": "s1", "node_type": "retrieve"},
        {"session_id": "s1", "node_type": "synthesize"},
        {"session_id": "s1", "node_type": "retrieve"},
        {"session_id": "s1", "node_type": "synthesize"},
    ]
    store = FakeTraceStore(events)
    detector = LoopDetector(store, window_size=10, min_repeats=3)

    signal = await detector.detect("s1")
    assert signal is not None
    assert signal.signal_type == "loop"
    assert signal.failure_type == "D"
    assert signal.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_loop_detector_returns_none_for_no_loop():
    events = [
        {"session_id": "s1", "node_type": "retrieve"},
        {"session_id": "s1", "node_type": "synthesize"},
        {"session_id": "s1", "node_type": "validate"},
    ]
    store = FakeTraceStore(events)
    detector = LoopDetector(store, window_size=10, min_repeats=3)

    signal = await detector.detect("s1")
    assert signal is None
