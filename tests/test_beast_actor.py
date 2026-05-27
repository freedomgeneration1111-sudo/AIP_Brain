"""Tests for CHUNK-7.5 Beast Actor (per Phase 5 ANNEX + prose)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from aip.foundation.schemas import BeastCadenceConfig
from aip.foundation.protocols import VectorStore, EmbeddingProvider, ProjectStore
from aip.orchestration.actors.beast import Beast


def test_beast_instantiation():
    cfg = BeastCadenceConfig()
    vs = MagicMock(spec=VectorStore)
    ep = MagicMock(spec=EmbeddingProvider)
    ps = MagicMock(spec=ProjectStore)

    b = Beast(config=cfg, vector_store=vs, embedding_provider=ep, project_store=ps)
    assert b._config.corpus_reindex_interval_seconds == 3600


@pytest.mark.asyncio
async def test_run_corpus_maintenance_uses_list_projects_and_count():
    cfg = BeastCadenceConfig(max_reindex_batch_size=5)
    vs = AsyncMock(spec=VectorStore)
    vs.count.return_value = 42
    ps = AsyncMock(spec=ProjectStore)
    ps.list_projects.return_value = [{"project_id": "p1"}, {"project_id": "p2"}]
    ep = MagicMock(spec=EmbeddingProvider)

    b = Beast(config=cfg, vector_store=vs, embedding_provider=ep, project_store=ps)
    result = await b.run_corpus_maintenance()

    assert result["projects_checked"] == 2
    assert "vectors_reindexed" in result


@pytest.mark.asyncio
async def test_run_health_check_uses_vector_health_check():
    cfg = BeastCadenceConfig()
    vs = AsyncMock(spec=VectorStore)
    vs.health_check.return_value = {"connected": True, "latency_ms": 3}
    ps = AsyncMock(spec=ProjectStore)
    ps.list_projects.return_value = []
    ep = MagicMock(spec=EmbeddingProvider)

    b = Beast(config=cfg, vector_store=vs, embedding_provider=ep, project_store=ps)
    health = await b.run_health_check()

    assert health["overall"] in ("ok", "degraded")
    assert "vector_backend" in health
