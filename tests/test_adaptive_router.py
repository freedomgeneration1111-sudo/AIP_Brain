"""Tests for CHUNK-7.4 Adaptive Router (per Phase 5 ANNEX + prose)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aip.adapter.model_slot_resolver import ModelSlotResolver
from aip.orchestration.budget import BudgetManager
from aip.orchestration.router import AdaptiveRouter


def test_router_instantiation_and_layering():
    resolver = MagicMock(spec=ModelSlotResolver)
    budget = MagicMock(spec=BudgetManager)
    router = AdaptiveRouter(model_resolver=resolver, budget_manager=budget)
    assert router._resolver is resolver


@pytest.mark.asyncio
async def test_resolve_with_routing_performs_budget_check_and_calls_resolver():
    resolver = MagicMock(spec=ModelSlotResolver)
    resolver.call = AsyncMock(return_value={"content": "ok", "usage": {"total_tokens": 123}})
    budget = AsyncMock(spec=BudgetManager)
    budget.check_before_call = AsyncMock(return_value=True)
    budget.record_consumption = AsyncMock()

    router = AdaptiveRouter(model_resolver=resolver, budget_manager=budget)
    result = await router.resolve_with_routing("synthesis", "test", [{"role": "user", "content": "hi"}])

    assert budget.check_before_call.called
    assert resolver.call.called
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_budget_exceeded_returns_error():
    resolver = MagicMock(spec=ModelSlotResolver)
    budget = AsyncMock(spec=BudgetManager)
    budget.check_before_call = AsyncMock(return_value=False)

    router = AdaptiveRouter(model_resolver=resolver, budget_manager=budget)
    result = await router.resolve_with_routing("synthesis", "test", [])

    assert "budget_exceeded" in result.get("error", "")
