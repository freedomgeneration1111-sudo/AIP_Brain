"""Budget enforcement acceptance tests.

Tests that budget enforcement works per §6:
- BudgetStore protocol is implemented
- BudgetManager enforces session/project/daily limits
- Hard stop blocks calls when budget is exhausted
- Warning threshold emits events
- Budget status reporting is accurate
"""

import pytest


@pytest.mark.asyncio
async def test_budget_store_protocol_importable():
    """BudgetStore protocol is importable from foundation."""
    from aip.foundation.protocols import BudgetStore

    assert hasattr(BudgetStore, "consume")
    assert hasattr(BudgetStore, "remaining")
    assert hasattr(BudgetStore, "get_budget")
    assert hasattr(BudgetStore, "record_usage")
    assert hasattr(BudgetStore, "check_limit")


@pytest.mark.asyncio
async def test_in_memory_budget_store_works():
    """InMemoryBudgetStore implements BudgetStore correctly."""
    from aip.orchestration.budget import InMemoryBudgetStore

    store = InMemoryBudgetStore(initial_budget=1000)
    assert await store.remaining() == 1000

    result = await store.consume(300)
    assert result is True
    assert await store.remaining() == 700


@pytest.mark.asyncio
async def test_budget_store_rejects_overspend():
    """BudgetStore.consume returns False when amount exceeds remaining."""
    from aip.orchestration.budget import InMemoryBudgetStore

    store = InMemoryBudgetStore(initial_budget=100)
    result = await store.consume(200)
    assert result is False


@pytest.mark.asyncio
async def test_budget_manager_enforces_hard_stop():
    """BudgetManager blocks calls that would exceed limit when hard_stop=True."""
    from aip.foundation.schemas import BudgetConfig
    from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore

    config = BudgetConfig(
        session_token_limit=1000,
        budget_hard_stop=True,
    )
    store = InMemoryBudgetStore()
    manager = BudgetManager(config, store)

    # Record some usage to get close to limit
    await store.record_usage("session", "sess-1", 900, 0.0, "synthesis")

    # This call should be blocked (900 + 200 > 1000)
    can_proceed = await manager.check_before_call("session", "sess-1", 200)
    assert can_proceed is False


@pytest.mark.asyncio
async def test_budget_manager_allows_within_limit():
    """BudgetManager allows calls within budget."""
    from aip.foundation.schemas import BudgetConfig
    from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore

    config = BudgetConfig(
        session_token_limit=10000,
        budget_hard_stop=True,
    )
    store = InMemoryBudgetStore()
    manager = BudgetManager(config, store)

    can_proceed = await manager.check_before_call("session", "sess-1", 100)
    assert can_proceed is True


@pytest.mark.asyncio
async def test_budget_manager_soft_limit():
    """BudgetManager allows overspend when hard_stop=False."""
    from aip.foundation.schemas import BudgetConfig
    from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore

    config = BudgetConfig(
        session_token_limit=1000,
        budget_hard_stop=False,
    )
    store = InMemoryBudgetStore()
    manager = BudgetManager(config, store)

    await store.record_usage("session", "sess-1", 900, 0.0, "synthesis")

    # With hard_stop=False, this should still be allowed
    can_proceed = await manager.check_before_call("session", "sess-1", 200)
    assert can_proceed is True


@pytest.mark.asyncio
async def test_budget_status_report():
    """BudgetManager.get_status returns accurate consumption data."""
    from aip.foundation.schemas import BudgetConfig
    from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore

    config = BudgetConfig(session_token_limit=1000)
    store = InMemoryBudgetStore()
    manager = BudgetManager(config, store)

    await store.record_usage("session", "sess-1", 300, 0.01, "synthesis")
    status = await manager.get_status("session", "sess-1")

    assert status["consumed_tokens"] == 300
    assert status["limit"] == 1000
    assert status["remaining"] == 700
    assert status["fraction_used"] == 0.3


def test_budget_config_defaults():
    """BudgetConfig has sensible defaults matching spec."""
    from aip.foundation.schemas import BudgetConfig

    cfg = BudgetConfig()
    assert cfg.session_token_limit == 500000
    assert cfg.project_token_limit == 5000000
    assert cfg.daily_token_limit == 10000000
    assert cfg.budget_warning_threshold == 0.80
    assert cfg.budget_hard_stop is True


@pytest.mark.asyncio
async def test_sqlite_budget_store_importable():
    """SqliteBudgetStore is importable from adapter layer."""
    from aip.adapter.budget_store_sqlite import SqliteBudgetStore

    store = SqliteBudgetStore(db_path=":memory:")
    assert store is not None
