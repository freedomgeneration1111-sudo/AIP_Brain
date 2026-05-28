"""Tests for multi-turn session context manager."""
import pytest

from aip.foundation.schemas import SessionContext, TrajectorySignal
from aip.orchestration.session import SessionManager


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None, **kwargs):
        pass

    async def query_events(self, session_id, node_type=None, limit=100):
        return [e for e in self._events if e.get("session_id") == session_id][-limit:]


class FakeArtifactStore:
    async def write(self, id, content, metadata):
        pass

    async def read(self, id, version=None):
        return ""

    async def list_versions(self, id):
        return []


class FakeEventStore:
    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        pass

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class FakeEcsStore:
    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        pass

    async def current_state(self, artifact_id):
        return None


@pytest.fixture
def manager():
    return SessionManager()


def test_create_session(manager):
    ctx = manager.create_session("s1", "p1")
    assert ctx.session_id == "s1"
    assert ctx.turn_count == 0
    assert ctx.context_tokens_estimate == 0


@pytest.mark.asyncio
async def test_advance_turn(manager):
    ctx = manager.create_session("s1", "p1")
    ctx = await manager.advance_turn(ctx, output_tokens=2000)
    assert ctx.turn_count == 1
    assert ctx.context_tokens_estimate == 2000
    ctx = await manager.advance_turn(ctx, output_tokens=3000)
    assert ctx.turn_count == 2
    assert ctx.context_tokens_estimate == 5000


def test_context_utilization(manager):
    ctx = manager.create_session("s1", "p1")
    ctx = SessionContext(
        session_id="s1", project_id="p1",
        context_tokens_estimate=64000,
        context_window_limit=128000,
    )
    assert manager.context_utilization(ctx) == 0.5


@pytest.mark.asyncio
async def test_check_trajectory_returns_signals_and_flag(manager):
    ctx = manager.create_session("s1", "p1")
    store = FakeTraceStore()
    signals, intervene = await manager.check_trajectory(ctx, store)
    assert isinstance(signals, list)
    assert isinstance(intervene, bool)


@pytest.mark.asyncio
async def test_handle_intervention_type_d_triggers_reset(manager):
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=10)
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    result = await manager.handle_intervention(
        ctx, signals, FakeArtifactStore(), FakeTraceStore(),
        FakeEventStore(), FakeEcsStore(),
    )
    # Full reset: turn_count should be 0
    assert result.turn_count == 0
    assert result.last_reset_at is not None


@pytest.mark.asyncio
async def test_handle_intervention_type_e_recovery_only(manager):
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=5)
    signals = [
        TrajectorySignal(
            signal_type="failure_streak", session_id="s1", failure_type="E",
            confidence=0.7, detail="streak", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    result = await manager.handle_intervention(
        ctx, signals, FakeArtifactStore(), FakeTraceStore(),
        FakeEventStore(), FakeEcsStore(),
    )
    # Recovery only: turn_count unchanged (no reset)
    assert result.turn_count == 5
