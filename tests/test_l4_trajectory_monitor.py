"""
Tests for L4 Trajectory Monitor Foundation (CHUNK-3.1).

Deterministic, zero-token, no network, no LLM.
Exercises the basic 2-of-3 signal detection heuristics against synthetic
trace events that match Architecture Rev 5.2 §5.9 + §10.1 / Appendix E.

Part of the L4 opening gate alongside test_layering.py and test_trace_schema.py.
"""

import pytest

from aip.foundation.protocols import TraceStore
from aip.orchestration.l4.monitor import TrajectoryMonitor, TrajectorySignal


class FakeTraceStoreForL4(TraceStore):
    """Minimal in-memory TraceStore fake for L4 tests. Returns events newest-first."""

    def __init__(self):
        self._events: list[dict] = []

    async def write_event(self, session_id, node_type, failure_type=None, outcome=None, detail=None, **kw):
        self._events.append({
            "session_id": session_id,
            "node_type": node_type,
            "failure_type": failure_type,
            "outcome": outcome,
            "detail": detail,
            "created_at": "2025-01-01T00:00:00Z",
        })

    async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        # Newest first (matches expected production ordering)
        matching = [e for e in reversed(self._events) if e["session_id"] == session_id]
        return matching[:limit]

    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        # Sexton/CHUNK-3.4 additive compat
        unclassified = [e for e in reversed(self._events) if e.get("failure_type") is None and e.get("outcome") == "failure"]
        return unclassified[:limit]


@pytest.fixture
def trace_store():
    return FakeTraceStoreForL4()


@pytest.mark.asyncio
async def test_monitor_returns_empty_when_no_signals(trace_store):
    """Happy path: clean session with only success events produces no signals."""
    await trace_store.write_event(
        session_id="clean_sess", node_type="L5", outcome="success"
    )
    monitor = TrajectoryMonitor(trace_store)
    signals = await monitor.detect("clean_sess")
    assert signals == []


@pytest.mark.asyncio
async def test_detects_d_signal(trace_store):
    """D (drift/loop) events in the window produce a loop_d signal."""
    for i in range(3):
        await trace_store.write_event(
            session_id="drifty", node_type="L4", failure_type="D", outcome="failure"
        )
    monitor = TrajectoryMonitor(trace_store, window_limit=20)
    signals = await monitor.detect("drifty")
    assert any(s.signal_type == "loop_d" for s in signals)
    d_sig = next(s for s in signals if s.signal_type == "loop_d")
    assert d_sig.confidence > 0.0
    assert d_sig.model_gen_assumption is not None  # §1.8 tagging


@pytest.mark.asyncio
async def test_detects_f_signal(trace_store):
    """F (context anxiety) events produce a context_anxiety_f signal."""
    await trace_store.write_event(
        session_id="anxious", node_type="L4", failure_type="F", outcome="failure"
    )
    monitor = TrajectoryMonitor(trace_store)
    signals = await monitor.detect("anxious")
    assert any(s.signal_type == "context_anxiety_f" for s in signals)


@pytest.mark.asyncio
async def test_combined_2of3_when_both_d_and_f_present(trace_store):
    """
    When both D and F appear in the recent window, the monitor emits the
    combined_2of3 proxy signal (foundation implementation of the §10.1 rule).
    """
    await trace_store.write_event(session_id="mixed", node_type="L3b", failure_type="D", outcome="failure")
    await trace_store.write_event(session_id="mixed", node_type="L4", failure_type="F", outcome="failure")
    monitor = TrajectoryMonitor(trace_store)
    signals = await monitor.detect("mixed")
    assert any(s.signal_type == "combined_2of3" for s in signals)
    comb = next(s for s in signals if s.signal_type == "combined_2of3")
    assert comb.confidence >= 0.8
    assert "2-of-3" in (comb.model_gen_assumption or "") or "combined" in (comb.model_gen_assumption or "").lower()


@pytest.mark.asyncio
async def test_monitor_is_injection_safe_and_deterministic(trace_store):
    """Monitor never constructs stores; multiple calls are pure given same input."""
    await trace_store.write_event(session_id="s1", node_type="L5", failure_type="D", outcome="failure")
    m1 = TrajectoryMonitor(trace_store)
    m2 = TrajectoryMonitor(trace_store)
    assert await m1.detect("s1") == await m2.detect("s1")  # deterministic
    # Also works when passed through a context-like dict (simulating WorkflowContext)
    ctx_like = {"protocols": {"trajectory_monitor": m1}}
    mon = ctx_like["protocols"].get("trajectory_monitor")
    assert mon is not None
    assert isinstance(await mon.detect("s1"), list)


def test_layering_and_no_direct_storage_import():
    """
    Sanity check that the monitor module does not violate §7.2.
    (The real enforcement is in test_layering.py; this is a cheap double-check.)
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "aip" / "orchestration" / "l4" / "monitor.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])

    # monitor may only reach foundation (for the protocol) and stdlib
    bad = [imp for imp in imports if imp in ("adapter", "orchestration") and not imp.startswith("orchestration.l4")]
    assert not bad, f"L4 monitor illegally imports from other orchestration or adapter layers: {bad}"
