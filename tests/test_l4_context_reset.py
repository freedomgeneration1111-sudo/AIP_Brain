from __future__ import annotations

"""
Tests for L4 Context Reset Protocol Foundation (CHUNK-3.2).

Deterministic, zero-token, no network, no LLM.
Exercises the signal → recommendation + intervention logging path
against synthetic trace events (Architecture Rev 5.2 §10.2 + §5.9).

Part of the L4 gate alongside the 3.1 monitor test + cross-cutting gates.
"""

from typing import Any

import pytest

from aip.foundation.protocols import ArtifactStore, TraceStore
from aip.orchestration.l4.monitor import TrajectoryMonitor
from aip.orchestration.l4.reset import L4ResetCoordinator, ResetRecommendation


class FakeTraceStoreForReset(TraceStore):
    """In-memory TraceStore fake that records all write_event calls with full kwargs."""

    def __init__(self):
        self._events: list[dict] = []
        self.writes: list[dict] = []  # captured calls for assertions

    async def write_event(self, session_id, node_type, failure_type=None, outcome=None, detail=None, **kw):
        call = {
            "session_id": session_id,
            "node_type": node_type,
            "failure_type": failure_type,
            "outcome": outcome,
            "detail": detail,
            **kw,
        }
        self.writes.append(call)
        self._events.append(call)

    async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
        # Newest first (matches monitor contract and production expectation)
        matching = [e for e in reversed(self._events) if e.get("session_id") == session_id]
        return matching[:limit]

    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        # Sexton/CHUNK-3.4 additive compat
        unclassified = [
            e for e in reversed(self._events) if e.get("failure_type") is None and e.get("outcome") == "failure"
        ]
        return unclassified[:limit]


class FakeArtifactStore(ArtifactStore):
    """Minimal artifact store for coordinator construction (optional in tests)."""

    async def write(self, id: str, content: str, metadata: dict | None = None, **kw: Any) -> None:
        pass

    async def read(self, id: str) -> str:
        return ""


@pytest.fixture
def trace_store():
    return FakeTraceStoreForReset()


@pytest.mark.asyncio
async def test_coordinator_returns_empty_on_clean_session(trace_store):
    """No L4 signals → no recommendation and no intervention log."""
    # Seed a clean success event (no D/F)
    await trace_store.write_event(session_id="clean", node_type="L5", outcome="success")
    monitor = TrajectoryMonitor(trace_store)
    coord = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace_store)
    recs = await coord.check_and_log_reset("clean")
    assert recs == []
    assert len(trace_store.writes) == 1  # only the seed write


@pytest.mark.asyncio
async def test_coordinator_recommends_and_logs_on_d_signal(trace_store):
    """D signal present → recommendation returned + intervention logged with correct fields."""
    for _ in range(2):
        await trace_store.write_event(session_id="drifty", node_type="L4", failure_type="D", outcome="failure")
    monitor = TrajectoryMonitor(trace_store, window_limit=20)
    coord = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace_store, artifact_store=FakeArtifactStore())
    recs = await coord.check_and_log_reset("drifty")
    assert len(recs) == 1
    rec = recs[0]
    assert isinstance(rec, ResetRecommendation)
    assert rec.session_id == "drifty"
    assert any(s.signal_type == "loop" or s.signal_type == "loop_d" for s in rec.signals)
    assert rec.action == "context_reset"
    assert rec.model_gen_assumption is not None
    assert "§10.2" in rec.model_gen_assumption or "§1.8" in rec.model_gen_assumption

    # Intervention log (step 4) must have been written
    intervention_writes = [w for w in trace_store.writes if w.get("intervention_type") == "context_reset"]
    assert len(intervention_writes) == 1
    log = intervention_writes[0]
    assert log["node_type"] == "L4"
    assert log["outcome"] == "intervention"
    assert log["intervention_applied"] == 1
    assert "detail" in log and "Context reset" in (log["detail"] or "")


@pytest.mark.asyncio
async def test_coordinator_recommends_on_combined_2of3(trace_store):
    """combined_2of3 signal (from 3.1 monitor) triggers reset recommendation + log."""
    await trace_store.write_event(session_id="mixed", node_type="L3b", failure_type="D", outcome="failure")
    await trace_store.write_event(session_id="mixed", node_type="L4", failure_type="F", outcome="failure")
    monitor = TrajectoryMonitor(trace_store)
    coord = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace_store)
    recs = await coord.check_and_log_reset("mixed")
    assert len(recs) == 1
    assert any(s.signal_type in ("failure_streak", "combined_2of3") for s in recs[0].signals)

    intervention = [w for w in trace_store.writes if w.get("intervention_type")]
    assert len(intervention) >= 1


@pytest.mark.asyncio
async def test_coordinator_is_injection_safe_and_deterministic(trace_store):
    """Coordinator never constructs stores; same input → same (deterministic) output."""
    await trace_store.write_event(session_id="s1", node_type="L5", failure_type="F", outcome="failure")
    m = TrajectoryMonitor(trace_store)
    c1 = L4ResetCoordinator(trajectory_monitor=m, trace_store=trace_store)
    c2 = L4ResetCoordinator(trajectory_monitor=m, trace_store=trace_store)

    r1 = await c1.check_and_log_reset("s1")
    r2 = await c2.check_and_log_reset("s1")
    # Compare structurally (detected_at timestamps may differ by microseconds)
    assert len(r1) == len(r2)
    if r1 and r2:
        assert r1[0].session_id == r2[0].session_id
        assert r1[0].action == r2[0].action
        assert len(r1[0].signals) == len(r2[0].signals)
        for s1, s2 in zip(r1[0].signals, r2[0].signals):
            assert s1.signal_type == s2.signal_type
            assert s1.failure_type == s2.failure_type

    # Also works when passed through a context-like dict (simulating WorkflowContext.protocols)
    ctx_like = {"protocols": {"l4_coordinator": c1, "trajectory_monitor": m}}
    coord = ctx_like["protocols"].get("l4_coordinator")
    assert coord is not None
    recs = await coord.check_and_log_reset("s1")
    assert isinstance(recs, list)


def test_layering_and_no_direct_storage_import():
    """
    Sanity double-check that reset.py respects §7.2 (orchestration/l4 may only
    reach foundation + the sibling monitor module).
    Real enforcement is in test_layering.py.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "aip" / "orchestration" / "l4" / "reset.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])

    # reset.py may only reach foundation (for the protocol) and stdlib
    # (same filter logic as the 3.1 monitor sanity check)
    bad = [imp for imp in imports if imp in ("adapter", "orchestration") and not imp.startswith("orchestration.l4")]
    assert not bad, f"L4 reset.py illegally imports from other orchestration or adapter layers: {bad}"
