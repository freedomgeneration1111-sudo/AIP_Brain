"""
Tests for L4 Workflow Activation + DEFINER Surface.

Verifies that the L4 coordinator can be invoked from within a WorkflowContext
(as done in the reference Workflow 0.1 pipeline) and that a recommendation
correctly causes emit_event("l4_reset_recommended") with the required payload
including model_gen_assumption (per §1.8).

Deterministic, zero-token, no network, no LLM.
"""

import pytest

from aip.orchestration.l4.monitor import TrajectoryMonitor
from aip.orchestration.l4.reset import (
    L4ResetCoordinator,
    ResetRecommendation,
    check_l4_and_surface_if_needed,
    run_l4_and_sexton_check,
)
from aip.orchestration.workflow.context import WorkflowContext


class FakeTraceStoreForIntegration:
    """Minimal fake supporting the L4 protocol surface for integration tests."""

    def __init__(self):
        self._events = []
        self.writes = []

    async def write_event(self, session_id, node_type=None, failure_type=None, outcome=None, detail=None, **kw):
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

    async def get_recent_events(self, session_id: str, limit: int = 100):
        matching = [e for e in reversed(self._events) if e.get("session_id") == session_id]
        return matching[:limit]

    async def get_unclassified_failures(self, limit: int = 100):
        # Sexton additive compat
        unclassified = [
            e for e in reversed(self._events) if e.get("failure_type") is None and e.get("outcome") == "failure"
        ]
        return unclassified[:limit]


@pytest.mark.asyncio
async def test_l4_helper_emits_dialog_event_when_signals_present():
    """
    When the coordinator (pre-seeded with D/F signals) is in the context protocols,
    calling the L4 helper causes an "l4_reset_recommended" event to be
    emitted with the correct structure and model_gen_assumption.
    """
    trace = FakeTraceStoreForIntegration()
    # Seed signals so detection fires (D + F = combined_2of3)
    await trace.write_event(session_id="test-sess", node_type="L4", failure_type="D", outcome="failure")
    await trace.write_event(session_id="test-sess", node_type="L4", failure_type="F", outcome="failure")

    monitor = TrajectoryMonitor(trace_store=trace)
    coordinator = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace)

    ctx = WorkflowContext(protocols={"l4_coordinator": coordinator})

    recs = await check_l4_and_surface_if_needed(ctx, session_id="test-sess")

    assert len(recs) == 1
    assert isinstance(recs[0], ResetRecommendation)
    assert recs[0].model_gen_assumption is not None

    # Verify the surface event was emitted (this is what reaches the DEFINER dialog)
    events = [e for e in ctx.events if e.get("type") == "l4_reset_recommended"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["session_id"] == "test-sess"
    assert payload["action"] == "context_reset"
    assert len(payload["recommendations"]) >= 1
    rec_payload = payload["recommendations"][0]
    assert "model_gen_assumption" in rec_payload
    assert rec_payload["model_gen_assumption"] is not None
    assert "§10.2" in rec_payload["model_gen_assumption"] or "§1.8" in rec_payload["model_gen_assumption"]


@pytest.mark.asyncio
async def test_l4_helper_no_event_on_clean_session():
    """Clean session (no L4 signals) produces no recommendation and no surface event."""
    trace = FakeTraceStoreForIntegration()
    await trace.write_event(session_id="clean", node_type="L5", outcome="success")

    monitor = TrajectoryMonitor(trace_store=trace)
    coordinator = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace)

    ctx = WorkflowContext(protocols={"l4_coordinator": coordinator})

    recs = await check_l4_and_surface_if_needed(ctx, session_id="clean")
    assert recs == []
    l4_events = [e for e in ctx.events if e.get("type") == "l4_reset_recommended"]
    assert l4_events == []


def test_layering_sanity_for_helper():
    """Cheap double-check that the helper lives in the allowed import boundary."""
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

    # The helper imports WorkflowContext (same layer) and foundation — allowed.
    bad = [imp for imp in imports if imp in ("adapter",) and not str(imp).startswith("orchestration.l4")]
    assert not bad, f"Unexpected cross-layer import in L4 helper: {bad}"


@pytest.mark.asyncio
async def test_l4_and_sexton_check_integration():
    """
    The thin runtime helper can be called from a 'node' context
    (simulated here) and triggers both L4 recommendation emission and Sexton
    classification in one call (node-level integration pattern).
    """
    trace = FakeTraceStoreForIntegration()
    # Seed an L4-style unclassified failure so both L4 and Sexton have work
    await trace.write_event(
        session_id="node_level",
        node_type="L4",
        failure_type=None,
        outcome="failure",
        detail="Context pressure high, output shortening perhaps maybe I think",
    )
    # Add some token decline for L4b
    await trace.write_event(session_id="node_level", node_type="L5", outcome="success", token_count_out=800)
    await trace.write_event(session_id="node_level", node_type="L5", outcome="success", token_count_out=450)

    monitor = TrajectoryMonitor(trace)
    coordinator = L4ResetCoordinator(trajectory_monitor=monitor, trace_store=trace)

    ctx = WorkflowContext(protocols={"l4_coordinator": coordinator, "trace_store": trace})

    # This is the 3.6 node-level integration call
    result = await run_l4_and_sexton_check(ctx, session_id="node_level", also_run_sexton=True)

    # L4 side should have produced a recommendation (due to hedging + length decline + pressure)
    assert result["l4_recommendations"] or any(e.get("type") == "l4_reset_recommended" for e in ctx.events)

    # Sexton side should have run (may return [] or classifications)
    assert isinstance(result["sexton_classifications"], list)
