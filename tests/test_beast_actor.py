"""Tests for Beast Actor — honest health checks, real corpus maintenance, entity maintenance.

Covers:
- Instantiation with all constructor parameters (including project_store=None)
- run_health_check() performs real probes (no hardcoded values)
- run_corpus_maintenance() uses list_stale_vectors() and re-embeds
- run_corpus_maintenance() falls back to global mode without project_store
- run_entity_maintenance() with and without entity_store
- run_cycle() cadence method
- Event emission via EventStore
- Graceful degradation when stores are unavailable
- Backward compatibility with old constructor signatures
"""

from unittest.mock import AsyncMock

import pytest

from aip.foundation.protocols import (
    CanonicalStore,
    EmbeddingProvider,
    EntityStore,
    EventStore,
    ProjectStore,
    VectorStore,
)
from aip.foundation.schemas import BeastCadenceConfig
from aip.orchestration.actors.beast import Beast


def _make_beast(
    *,
    config: BeastCadenceConfig | None = None,
    vs_health: dict | None = None,
    vs_count: int = 0,
    vs_stale: list[dict] | None = None,
    embed_side_effect: object = None,
    projects: list[dict] | None = None,
    event_store: EventStore | None = None,
    entity_store: EntityStore | None = None,
    canonical_store: CanonicalStore | None = None,
    project_store: ProjectStore | None | object = "_default",  # sentinel
) -> Beast:
    """Factory for Beast instances with sensible async mocks."""
    cfg = config or BeastCadenceConfig()
    vs = AsyncMock(spec=VectorStore)
    vs.health_check.return_value = vs_health or {"connected": True, "latency_ms": 3}
    vs.count.return_value = vs_count
    vs.list_stale_vectors.return_value = vs_stale or []

    ep = AsyncMock(spec=EmbeddingProvider)
    if embed_side_effect:
        ep.embed.side_effect = embed_side_effect
    else:
        ep.embed.return_value = [0.1] * 768

    # Handle project_store: default creates a mock, None means no store
    if project_store == "_default":
        ps = AsyncMock(spec=ProjectStore)
        ps.list_projects.return_value = projects or []
    else:
        ps = project_store  # None or user-provided mock

    return Beast(
        config=cfg,
        vector_store=vs,
        embedding_provider=ep,
        project_store=ps,
        event_store=event_store,
        entity_store=entity_store,
        canonical_store=canonical_store,
    )


# -----------------------------------------------------------------------
# Instantiation
# -----------------------------------------------------------------------


class TestBeastInstantiation:
    def test_default_config(self):
        b = _make_beast()
        assert b._config.corpus_reindex_interval_seconds == 3600
        assert b._config.max_reindex_batch_size == 1000

    def test_custom_config(self):
        cfg = BeastCadenceConfig(
            corpus_reindex_interval_seconds=7200,
            max_reindex_batch_size=50,
        )
        b = _make_beast(config=cfg)
        assert b._config.corpus_reindex_interval_seconds == 7200
        assert b._config.max_reindex_batch_size == 50

    def test_entity_store_injected_via_constructor(self):
        es = AsyncMock(spec=EntityStore)
        b = _make_beast(entity_store=es)
        assert b._entity_store is es

    def test_entity_store_none_by_default(self):
        b = _make_beast()
        assert b._entity_store is None

    def test_canonical_store_injected_via_constructor(self):
        cs = AsyncMock(spec=CanonicalStore)
        b = _make_beast(canonical_store=cs)
        assert b._canonical_store is cs

    def test_project_store_none_by_default(self):
        b = _make_beast(project_store=None)
        assert b._projects is None

    def test_project_store_injected_via_constructor(self):
        ps = AsyncMock(spec=ProjectStore)
        b = _make_beast(project_store=ps)
        assert b._projects is ps

    def test_last_cycle_time_initially_none(self):
        b = _make_beast()
        assert b._last_cycle_time is None


# -----------------------------------------------------------------------
# run_health_check()
# -----------------------------------------------------------------------


class TestRunHealthCheck:
    @pytest.mark.asyncio
    async def test_all_systems_healthy(self):
        es = AsyncMock(spec=EntityStore)
        es.list_entities.return_value = []
        cs = AsyncMock(spec=CanonicalStore)
        cs.list_canonical.return_value = []

        b = _make_beast(entity_store=es, canonical_store=cs)
        health = await b.run_health_check()

        assert health["overall"] == "ok"
        assert health["vector_backend"]["connected"] is True
        assert health["embedding_provider"]["connected"] is True
        assert health["entity_store"]["connected"] is True
        assert health["canonical_store"]["connected"] is True

    @pytest.mark.asyncio
    async def test_vector_store_unhealthy(self):
        b = _make_beast(vs_health={"connected": False, "error": "timeout"})
        health = await b.run_health_check()

        assert health["overall"] == "degraded"
        assert health["vector_backend"]["connected"] is False
        assert "error" in health["vector_backend"]

    @pytest.mark.asyncio
    async def test_vector_store_health_check_raises(self):
        vs = AsyncMock(spec=VectorStore)
        vs.health_check.side_effect = ConnectionError("unreachable")
        ep = AsyncMock(spec=EmbeddingProvider)
        ep.embed.return_value = [0.1] * 768

        b = Beast(config=BeastCadenceConfig(), vector_store=vs, embedding_provider=ep, project_store=None)
        health = await b.run_health_check()

        assert health["overall"] == "degraded"
        assert health["vector_backend"]["connected"] is False
        assert "error" in health["vector_backend"]

    @pytest.mark.asyncio
    async def test_embedding_provider_unhealthy(self):
        b = _make_beast(embed_side_effect=ConnectionError("Ollama not running"))
        health = await b.run_health_check()

        assert health["overall"] == "degraded"
        assert health["embedding_provider"]["connected"] is False
        assert "error" in health["embedding_provider"]

    @pytest.mark.asyncio
    async def test_embedding_provider_latency_reported(self):
        b = _make_beast()
        health = await b.run_health_check()

        assert health["embedding_provider"]["connected"] is True
        assert "latency_ms" in health["embedding_provider"]
        assert isinstance(health["embedding_provider"]["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_no_hardcoded_ollama_status(self):
        """The old Beast hardcoded ollama: {connected: True, latency_ms: 5}.
        Verify we no longer have a hardcoded 'ollama' key."""
        b = _make_beast()
        health = await b.run_health_check()

        # Should use 'embedding_provider' not 'ollama'
        assert "ollama" not in health
        assert "embedding_provider" in health

    @pytest.mark.asyncio
    async def test_entity_store_not_configured(self):
        b = _make_beast()  # no entity_store
        health = await b.run_health_check()

        assert health["entity_store"]["connected"] is False
        assert health["entity_store"].get("status") == "not_configured"

    @pytest.mark.asyncio
    async def test_canonical_store_not_configured(self):
        b = _make_beast()  # no canonical_store
        health = await b.run_health_check()

        assert health["canonical_store"]["connected"] is False

    @pytest.mark.asyncio
    async def test_project_store_not_configured(self):
        b = _make_beast(project_store=None)
        health = await b.run_health_check()

        assert health["project_store"]["connected"] is False
        assert health["project_store"].get("status") == "not_configured"

    @pytest.mark.asyncio
    async def test_project_store_healthy(self):
        ps = AsyncMock(spec=ProjectStore)
        ps.list_projects.return_value = []
        b = _make_beast(project_store=ps)
        health = await b.run_health_check()

        assert health["project_store"]["connected"] is True
        assert "latency_ms" in health["project_store"]

    @pytest.mark.asyncio
    async def test_event_emitted_on_health_check(self):
        ev = AsyncMock(spec=EventStore)
        b = _make_beast(event_store=ev)
        await b.run_health_check()

        ev.write_event.assert_called_once()
        call_kwargs = ev.write_event.call_args
        assert call_kwargs.kwargs["event_type"] == "beast_health_check"
        assert call_kwargs.kwargs["actor"] == "beast"

    @pytest.mark.asyncio
    async def test_no_event_emitted_when_no_event_store(self):
        b = _make_beast()  # no event_store
        # Should not raise
        health = await b.run_health_check()
        assert health["overall"] in ("ok", "degraded")


# -----------------------------------------------------------------------
# run_corpus_maintenance()
# -----------------------------------------------------------------------


class TestRunCorpusMaintenance:
    @pytest.mark.asyncio
    async def test_uses_list_stale_vectors(self):
        """Corpus maintenance must call list_stale_vectors, not just count()."""
        stale = [
            {"id": "v1", "domain": "p1", "metadata": {"content": "hello"}},
            {"id": "v2", "domain": "p1", "metadata": {"content": "world"}},
        ]
        b = _make_beast(
            projects=[{"project_id": "p1"}],
            vs_stale=stale,
        )
        result = await b.run_corpus_maintenance()

        b._vector.list_stale_vectors.assert_called()
        assert result["stale_vectors_found"] == 2

    @pytest.mark.asyncio
    async def test_reembeds_stale_vectors(self):
        """Each stale vector should be re-embedded and upserted."""
        stale = [
            {"id": "v1", "domain": "p1", "metadata": {"content": "hello"}},
        ]
        b = _make_beast(
            projects=[{"project_id": "p1"}],
            vs_stale=stale,
        )
        result = await b.run_corpus_maintenance()

        b._embed.embed.assert_called_with("hello")
        b._vector.upsert.assert_called_once()
        assert result["vectors_reembedded"] == 1
        assert result["vectors_failed"] == 0

    @pytest.mark.asyncio
    async def test_handles_embed_failure_gracefully(self):
        """If embedding fails for one vector, others should still succeed."""
        stale = [
            {"id": "v1", "domain": "p1", "metadata": {"content": "good"}},
            {"id": "v2", "domain": "p1", "metadata": {"content": "bad"}},
        ]

        async def embed_side_effect(text):
            if text == "bad":
                raise RuntimeError("Embedding failed")
            return [0.1] * 768

        b = _make_beast(
            projects=[{"project_id": "p1"}],
            vs_stale=stale,
            embed_side_effect=embed_side_effect,
        )
        result = await b.run_corpus_maintenance()

        assert result["vectors_reembedded"] == 1
        assert result["vectors_failed"] == 1
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_skips_vectors_without_content(self):
        """Vectors missing content or id should be counted as failed."""
        stale = [
            {"id": "v1", "domain": "p1", "metadata": {}},  # no content
            {"id": "", "domain": "p1", "metadata": {"content": "orphan"}},  # no id
        ]
        b = _make_beast(
            projects=[{"project_id": "p1"}],
            vs_stale=stale,
        )
        result = await b.run_corpus_maintenance()

        assert result["vectors_failed"] == 2
        assert result["vectors_reembedded"] == 0

    @pytest.mark.asyncio
    async def test_no_projects(self):
        b = _make_beast(projects=[])
        result = await b.run_corpus_maintenance()

        assert result["projects_checked"] == 0
        assert result["stale_vectors_found"] == 0

    @pytest.mark.asyncio
    async def test_project_listing_failure(self):
        ps = AsyncMock(spec=ProjectStore)
        ps.list_projects.side_effect = RuntimeError("DB down")

        b = _make_beast(project_store=ps)

        with pytest.raises(RuntimeError, match="DB down"):
            await b.run_corpus_maintenance()

    @pytest.mark.asyncio
    async def test_event_emitted_on_maintenance(self):
        ev = AsyncMock(spec=EventStore)
        b = _make_beast(event_store=ev, projects=[{"project_id": "p1"}])
        await b.run_corpus_maintenance()

        ev.write_event.assert_called_once()
        call_kwargs = ev.write_event.call_args
        assert call_kwargs.kwargs["event_type"] == "beast_corpus_maintenance"

    @pytest.mark.asyncio
    async def test_does_not_call_health_check_in_loop(self):
        """The old Beast incorrectly called health_check() in the re-index loop.
        Verify it's not called during corpus maintenance."""
        b = _make_beast(projects=[{"project_id": "p1"}])
        await b.run_corpus_maintenance()

        b._vector.health_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_configurable_threshold(self):
        """Threshold days should derive from corpus_reindex_interval_seconds."""
        cfg = BeastCadenceConfig(corpus_reindex_interval_seconds=86400 * 7)  # 7 days
        b = _make_beast(config=cfg, projects=[{"project_id": "p1"}])
        await b.run_corpus_maintenance()

        call_args = b._vector.list_stale_vectors.call_args
        assert call_args.kwargs["threshold_days"] == 7


# -----------------------------------------------------------------------
# run_corpus_maintenance() — global mode (no project_store)
# -----------------------------------------------------------------------


class TestCorpusMaintenanceGlobal:
    @pytest.mark.asyncio
    async def test_global_mode_without_project_store(self):
        """When project_store is None, corpus maintenance runs in global mode."""
        b = _make_beast(
            project_store=None,
            vs_stale=[
                {"id": "v1", "metadata": {"content": "hello"}},
            ],
        )
        result = await b.run_corpus_maintenance()

        assert result["mode"] == "global_no_project_store"
        assert result["projects_checked"] == 0
        assert result["stale_vectors_found"] == 1
        assert result["vectors_reembedded"] == 1

    @pytest.mark.asyncio
    async def test_global_mode_calls_list_stale_without_domain(self):
        """Global mode should call list_stale_vectors with domain=None."""
        b = _make_beast(project_store=None)
        await b.run_corpus_maintenance()

        call_args = b._vector.list_stale_vectors.call_args
        assert call_args.kwargs["domain"] is None

    @pytest.mark.asyncio
    async def test_global_mode_handles_failure(self):
        """Global mode should handle list_stale_vectors failure gracefully."""
        vs = AsyncMock(spec=VectorStore)
        vs.list_stale_vectors.side_effect = RuntimeError("Vector store down")
        ep = AsyncMock(spec=EmbeddingProvider)
        ep.embed.return_value = [0.1] * 768

        b = Beast(config=BeastCadenceConfig(), vector_store=vs, embedding_provider=ep, project_store=None)
        result = await b.run_corpus_maintenance()

        assert result["errors"] == 1
        assert result["mode"] == "global_no_project_store"


# -----------------------------------------------------------------------
# run_entity_maintenance()
# -----------------------------------------------------------------------


class TestRunEntityMaintenance:
    @pytest.mark.asyncio
    async def test_no_entity_store_returns_skip(self):
        b = _make_beast()  # no entity_store
        result = await b.run_entity_maintenance()

        assert result["entities_checked"] == 0
        assert result["skipped_reason"] == "entity_store_not_configured"

    @pytest.mark.asyncio
    async def test_detects_stale_entities(self):
        es = AsyncMock(spec=EntityStore)
        es.list_entities.return_value = [
            {"entity_id": "e1", "entity_type": "concept"},
            {"entity_id": "e2", "entity_type": "concept"},
        ]
        es.get_entity.side_effect = [
            {"entity_id": "e1", "entity_type": "concept", "name": "foo", "updated_since_canonical": True},
            {"entity_id": "e2", "entity_type": "concept", "name": "bar", "updated_since_canonical": False},
        ]

        b = _make_beast(entity_store=es)
        result = await b.run_entity_maintenance()

        assert result["entities_checked"] == 2
        assert len(result["stale_entities"]) == 1
        assert result["stale_entities"][0]["entity_id"] == "e1"
        assert result["stale_entities"][0]["reason"] == "updated_since_canonical"

    @pytest.mark.asyncio
    async def test_no_stale_entities(self):
        es = AsyncMock(spec=EntityStore)
        es.list_entities.return_value = [
            {"entity_id": "e1", "entity_type": "concept"},
        ]
        es.get_entity.return_value = {
            "entity_id": "e1",
            "entity_type": "concept",
            "updated_since_canonical": False,
        }

        b = _make_beast(entity_store=es)
        result = await b.run_entity_maintenance()

        assert result["stale_entities"] == []

    @pytest.mark.asyncio
    async def test_entity_get_failure_counted(self):
        es = AsyncMock(spec=EntityStore)
        es.list_entities.return_value = [
            {"entity_id": "e1"},
        ]
        es.get_entity.side_effect = RuntimeError("DB error")

        b = _make_beast(entity_store=es)
        result = await b.run_entity_maintenance()

        assert result["consistency_errors"] == 1

    @pytest.mark.asyncio
    async def test_stale_entity_event_emitted(self):
        es = AsyncMock(spec=EntityStore)
        ev = AsyncMock(spec=EventStore)
        es.list_entities.return_value = [
            {"entity_id": "e1"},
        ]
        es.get_entity.return_value = {
            "entity_id": "e1",
            "entity_type": "concept",
            "updated_since_canonical": True,
        }

        b = _make_beast(entity_store=es, event_store=ev)
        await b.run_entity_maintenance()

        stale_calls = [
            c for c in ev.write_event.call_args_list if c.kwargs.get("event_type") == "beast_entity_stale_detected"
        ]
        assert len(stale_calls) == 1
        assert stale_calls[0].kwargs["actor"] == "beast"

    @pytest.mark.asyncio
    async def test_no_stale_event_when_all_fresh(self):
        es = AsyncMock(spec=EntityStore)
        ev = AsyncMock(spec=EventStore)
        es.list_entities.return_value = [
            {"entity_id": "e1"},
        ]
        es.get_entity.return_value = {
            "entity_id": "e1",
            "updated_since_canonical": False,
        }

        b = _make_beast(entity_store=es, event_store=ev)
        await b.run_entity_maintenance()

        stale_calls = [
            c for c in ev.write_event.call_args_list if c.kwargs.get("event_type") == "beast_entity_stale_detected"
        ]
        assert len(stale_calls) == 0


# -----------------------------------------------------------------------
# run_cycle() — cadence method
# -----------------------------------------------------------------------


class TestRunCycle:
    """Tests for Beast run_cycle() after ADR-011 restructuring.

    Per ADR-011, run_cycle() is a lightweight heartbeat that runs:
    1. Health check
    2. Lightweight heartbeat (budget check, stale artifact flagging)
    3. Context advisory (conditional domain summaries — only if beast_provider
       is configured and corpus has changed)

    Maintenance operations (corpus, entity, tagging, embedding, graph) have
    moved to Sexton. The "corpus" and "entity" keys are no longer in the
    cycle summary.
    """

    @pytest.mark.asyncio
    async def test_run_cycle_returns_summary(self):
        """After ADR-011, run_cycle returns heartbeat-style summary."""
        b = _make_beast(projects=[])
        summary = await b.run_cycle()

        # ADR-011: run_cycle is lightweight heartbeat, not full maintenance
        assert "health_overall" in summary
        assert "heartbeat" in summary
        assert "context_advisory" in summary
        assert "cycle_elapsed_seconds" in summary
        assert summary["cycle_elapsed_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_run_cycle_updates_last_cycle_time(self):
        b = _make_beast(projects=[])
        assert b._last_cycle_time is None

        await b.run_cycle()
        assert b._last_cycle_time is not None

    @pytest.mark.asyncio
    async def test_run_cycle_emits_heartbeat_event(self):
        """After ADR-011, run_cycle emits 'beast_heartbeat' (not cycle_complete)."""
        ev = AsyncMock(spec=EventStore)
        b = _make_beast(event_store=ev, projects=[])
        await b.run_cycle()

        # ADR-011: heartbeat event, not the old cycle_complete
        heartbeat_events = [c for c in ev.write_event.call_args_list if c.kwargs.get("event_type") == "beast_heartbeat"]
        assert len(heartbeat_events) >= 1

    @pytest.mark.asyncio
    async def test_run_cycle_emits_health_check_event(self):
        """run_cycle also triggers health_check which emits its own event."""
        ev = AsyncMock(spec=EventStore)
        b = _make_beast(event_store=ev, projects=[])
        await b.run_cycle()

        health_events = [c for c in ev.write_event.call_args_list if c.kwargs.get("event_type") == "beast_health_check"]
        assert len(health_events) == 1
        assert health_events[0].kwargs["actor"] == "beast"

    @pytest.mark.asyncio
    async def test_run_cycle_context_advisory_no_llm(self):
        """Without beast_provider, context_advisory indicates heartbeat-only."""
        b = _make_beast(project_store=None)
        summary = await b.run_cycle()

        assert summary["health_overall"] in ("ok", "degraded")
        # No LLM configured → heartbeat-only advisory
        assert summary["context_advisory"]["note"] == "no_llm_heartbeat_only"

    @pytest.mark.asyncio
    async def test_run_cycle_heartbeat_structure(self):
        """Verify heartbeat sub-dict has expected fields after ADR-011."""
        b = _make_beast(projects=[])
        summary = await b.run_cycle()

        hb = summary["heartbeat"]
        assert "budget_ok" in hb
        assert "stale_generated_flagged" in hb
        assert "heartbeat_written" in hb


# -----------------------------------------------------------------------
# Event emission helper
# -----------------------------------------------------------------------


class TestEventEmission:
    @pytest.mark.asyncio
    async def test_emit_event_no_event_store(self):
        b = _make_beast()
        # Should not raise
        await b._emit_event("test_event", "test_artifact")

    @pytest.mark.asyncio
    async def test_emit_event_failure_does_not_raise(self):
        ev = AsyncMock(spec=EventStore)
        ev.write_event.side_effect = RuntimeError("Write failed")
        b = _make_beast(event_store=ev)

        # Should not raise even when write_event fails
        await b._emit_event("test_event", "test_artifact")

    @pytest.mark.asyncio
    async def test_emit_event_with_metadata(self):
        ev = AsyncMock(spec=EventStore)
        b = _make_beast(event_store=ev)
        await b._emit_event("test_event", "art1", metadata={"key": "value"})

        ev.write_event.assert_called_once_with(
            event_type="test_event",
            actor="beast",
            artifact_id="art1",
            from_state=None,
            to_state=None,
            key="value",
        )


# -----------------------------------------------------------------------
# Backward compatibility
# -----------------------------------------------------------------------


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_old_constructor_still_works_with_positional_project_store(self):
        """Ensure callers that pass project_store positionally still work."""
        cfg = BeastCadenceConfig()
        vs = AsyncMock(spec=VectorStore)
        vs.health_check.return_value = {"connected": True}
        ep = AsyncMock(spec=EmbeddingProvider)
        ep.embed.return_value = [0.1] * 768
        ps = AsyncMock(spec=ProjectStore)
        ps.list_projects.return_value = []

        # Old-style: project_store as 4th positional arg
        b = Beast(cfg, vs, ep, ps)
        health = await b.run_health_check()
        assert health["overall"] in ("ok", "degraded")

    @pytest.mark.asyncio
    async def test_constructor_without_project_store(self):
        """New-style: omit project_store entirely (defaults to None)."""
        cfg = BeastCadenceConfig()
        vs = AsyncMock(spec=VectorStore)
        vs.health_check.return_value = {"connected": True}
        ep = AsyncMock(spec=EmbeddingProvider)
        ep.embed.return_value = [0.1] * 768

        b = Beast(config=cfg, vector_store=vs, embedding_provider=ep)
        assert b._projects is None
        health = await b.run_health_check()
        assert health["project_store"]["connected"] is False

    @pytest.mark.asyncio
    async def test_admin_route_compatible_return_shape(self):
        """The admin route expects 'overall' key in health check result."""
        b = _make_beast()
        health = await b.run_health_check()
        assert "overall" in health
        assert health["overall"] in ("ok", "degraded")

    @pytest.mark.asyncio
    async def test_corpus_maintenance_return_has_projects_checked(self):
        """Admin surface reads 'projects_checked' from result."""
        b = _make_beast(projects=[{"project_id": "p1"}])
        result = await b.run_corpus_maintenance()
        assert "projects_checked" in result
        assert result["projects_checked"] == 1
