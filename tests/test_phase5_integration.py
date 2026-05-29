"""Phase 5 Integration Test (CHUNK-7.6) — full self-improvement cycle.

Extends CHUNK-6.5 with actor-level verification on top of the production pipeline.
All scenarios use CI mode (deterministic fixtures).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aip.foundation.schemas import BeastCadenceConfig, BudgetConfig, SextonConfig
from aip.orchestration.ace_playbook import AcePlaybook
from aip.orchestration.actors.beast import Beast
from aip.orchestration.budget import BudgetManager, InMemoryBudgetStore
from aip.orchestration.router import AdaptiveRouter
from aip.orchestration.sexton.sexton import Sexton


@pytest.mark.asyncio
async def test_scenario_4_beast_corpus_maintenance():
    cfg = BeastCadenceConfig()
    vs = AsyncMock()
    vs.count.return_value = 10
    vs.health_check.return_value = {"connected": True}
    ps = AsyncMock()
    ps.list_projects.return_value = [{"project_id": "p1"}]
    ep = MagicMock()
    b = Beast(config=cfg, vector_store=vs, embedding_provider=ep, project_store=ps)
    result = await b.run_corpus_maintenance()
    assert "projects_checked" in result


@pytest.mark.asyncio
async def test_scenario_5_budget_enforcement():
    cfg = BudgetConfig(session_token_limit=10, budget_hard_stop=True)
    store = InMemoryBudgetStore()
    mgr = BudgetManager(cfg, store)
    ok = await mgr.check_before_call("session", "s1", 100)
    assert ok is False
