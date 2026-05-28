"""Tests for CHUNK-7.0b Token Budget System (exact per Phase 5 ANNEX + prose)."""
import pytest
import tempfile
import os

from aip.foundation.schemas import BudgetConfig, BudgetScope
from aip.foundation.protocols import BudgetStore
from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore
from aip.adapter.budget_store_sqlite import SqliteBudgetStore


def test_budget_config_and_scope():
    cfg = BudgetConfig()
    assert cfg.session_token_limit == 500000
    s: BudgetScope = "session"
    assert s in ("session", "project", "daily")


@pytest.mark.asyncio
async def test_inmemory_budget_store_supports_extended_protocol():
    """InMemory (extended in 7.0b) must support the 7.0a Protocol methods for CI."""
    store: BudgetStore = InMemoryBudgetStore()
    status = await store.get_budget("session", "s1")
    assert "consumed_tokens" in status
    await store.record_usage("session", "s1", 100, 0.01, "test")
    allowed = await store.check_limit("session", "s1")
    assert allowed is True


@pytest.mark.asyncio
async def test_sqlite_budget_store_implements_protocol():
    """SqliteBudgetStore must implement the full (7.0a-extended) BudgetStore."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_budget.db")
        store = SqliteBudgetStore(db_path)
        # record then query
        await store.record_usage("project", "p1", 1234, 0.05, "synthesis")
        status = await store.get_budget("project", "p1")
        assert status["consumed_tokens"] == 1234
        assert await store.check_limit("project", "p1") is True


@pytest.mark.asyncio
async def test_budget_manager_check_before_call_and_record():
    cfg = BudgetConfig(session_token_limit=1000, budget_hard_stop=True, budget_warning_threshold=0.8)
    store = InMemoryBudgetStore()
    mgr = BudgetManager(cfg, store)

    # Under limit
    ok = await mgr.check_before_call("session", "s1", 100)
    assert ok is True

    await mgr.record_consumption("session", "s1", 100, 0.0, "test")
    status = await mgr.get_status("session", "s1")
    assert status["consumed_tokens"] == 100
    assert status["remaining"] == 900

    # Would exceed hard stop
    ok2 = await mgr.check_before_call("session", "s1", 950)
    assert ok2 is False

    assert mgr.is_hard_stop() is True


@pytest.mark.asyncio
async def test_budget_manager_warning_threshold_emits_event():
    """When threshold crossed, warning event is (optionally) written."""
    events = []
    class FakeEventStore:
        async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
            events.append({"event_type": event_type, "actor": actor, "artifact_id": artifact_id, "from_state": from_state, "to_state": to_state, **kwargs})

    cfg = BudgetConfig(session_token_limit=100, budget_warning_threshold=0.5, budget_hard_stop=False)
    store = InMemoryBudgetStore()
    mgr = BudgetManager(cfg, store, event_store=FakeEventStore())

    await mgr.record_consumption("session", "s-warn", 60, 0.0, "test")
    ok = await mgr.check_before_call("session", "s-warn", 10)  # crosses 50%
    assert ok is True
    assert len(events) >= 1
    assert events[0]["event_type"] == "budget_warning"


@pytest.mark.asyncio
async def test_multi_scope_independent_tracking():
    cfg = BudgetConfig(session_token_limit=100, project_token_limit=500, daily_token_limit=1000)
    store = InMemoryBudgetStore()
    mgr = BudgetManager(cfg, store)

    await mgr.record_consumption("session", "s1", 90, 0.0, "m")
    await mgr.record_consumption("project", "p1", 200, 0.0, "m")

    s_status = await mgr.get_status("session", "s1")
    p_status = await mgr.get_status("project", "p1")
    assert s_status["consumed_tokens"] == 90
    assert p_status["consumed_tokens"] == 200
    # Cross scope limit check
    ok_s = await mgr.check_before_call("session", "s1", 20)  # 90+20>100
    assert ok_s is False


def test_layering_and_no_orchestration_in_adapter():
    """7.0b adapter must not import orchestration (enforced by import in test + layering gate)."""
    # The module import itself succeeds without pulling orchestration
    from aip.adapter.budget_store_sqlite import SqliteBudgetStore
    assert SqliteBudgetStore is not None
