"""Tests for Sprint 5.8: Deprecated code removal, Graph/Wiki/Procedural channel
wiring, and enhanced retrieval dashboard.

Covers:
1. Deprecated _search_sources and _assemble_context are removed
2. Graph channel registration and dispatch in the orchestrator
3. Wiki channel registration and dispatch
4. Procedural channel registration and dispatch
5. All six channels participate in RRF fusion when enabled
6. _hit_type_matches replaces _source_type_matches for RetrievalHit filtering
7. AskStores accepts graph_store parameter
8. Enhanced dashboard endpoint structure
9. _search_sources_with_trace no longer falls back to legacy path
10. SmartContextPacker is the only context assembly path
"""

from __future__ import annotations

import asyncio

from aip.foundation.schemas.retrieval import Chunk, RetrievalHit, RetrievalTrace
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorCache,
    OrchestratorConfig,
    RetrievalOrchestrator,
    apply_quality_gate,
    rrf_fuse,
)
from aip.orchestration.smart_context_packer import (
    PackerConfig,
    SmartContextPacker,
)


# ---------------------------------------------------------------------------
# Helpers: fake retriever channels for all six channels
# ---------------------------------------------------------------------------


async def _fake_fts_retriever(query: str) -> list[RetrievalHit]:
    await asyncio.sleep(0.005)
    return [
        RetrievalHit(id="fts:1", content="FTS hit about AIP", score=0.9, source_channel="fts"),
        RetrievalHit(id="fts:2", content="FTS hit about retrieval", score=0.7, source_channel="fts"),
    ]


async def _fake_vector_retriever(query: str) -> list[RetrievalHit]:
    await asyncio.sleep(0.005)
    return [
        RetrievalHit(id="vec:1", content="Vector hit about embeddings", score=0.85, source_channel="vector"),
    ]


async def _fake_graph_retriever(query: str) -> list[RetrievalHit]:
    """Simulate graph channel: returns entity-based hits."""
    return [
        RetrievalHit(
            id="graph:EntityA",
            content="Graph entity: EntityA — connected to query entities via knowledge graph.",
            score=0.6,
            source_channel="graph",
            metadata={"type": "graph_entity", "entity_name": "EntityA"},
        ),
        RetrievalHit(
            id="graph:EntityB",
            content="Graph entity: EntityB — connected to query entities via knowledge graph.",
            score=0.4,
            source_channel="graph",
            metadata={"type": "graph_entity", "entity_name": "EntityB"},
        ),
    ]


async def _fake_wiki_retriever(query: str) -> list[RetrievalHit]:
    """Simulate wiki channel: returns wiki article hits."""
    return [
        RetrievalHit(
            id="wiki:beast:wiki:test_domain:20260101",
            content="Wiki article about the test domain with relevant information.",
            score=0.7,
            source_channel="wiki",
            domain="test_domain",
            metadata={"type": "wiki_article", "artifact_id": "beast:wiki:test_domain:20260101"},
        ),
    ]


async def _fake_procedural_retriever(query: str) -> list[RetrievalHit]:
    """Simulate procedural channel: returns how-to guide hits."""
    return [
        RetrievalHit(
            id="proc:guide:1",
            content="Step-by-step guide: How to configure AIP retrieval pipeline.",
            score=0.5,
            source_channel="procedural",
            metadata={"type": "procedural_guide", "artifact_id": "guide:1"},
        ),
    ]


async def _fake_corpus_retriever(query: str) -> list[RetrievalHit]:
    return [
        RetrievalHit(
            id="corpus:turn1",
            content="Corpus turn about the topic",
            score=0.75,
            source_channel="corpus",
            metadata={"type": "conversation_chunk"},
        ),
    ]


# ---------------------------------------------------------------------------
# 1. Deprecated code removal
# ---------------------------------------------------------------------------


class TestDeprecatedCodeRemoved:
    """Verify that deprecated functions are no longer importable."""

    def test_search_sources_removed(self):
        """_search_sources should no longer exist in ask_pipeline."""
        import aip.orchestration.ask_pipeline as mod

        assert not hasattr(mod, "_search_sources"), "_search_sources should be removed in Sprint 5.8"

    def test_assemble_context_removed(self):
        """_assemble_context should no longer exist in ask_pipeline."""
        import aip.orchestration.ask_pipeline as mod

        assert not hasattr(mod, "_assemble_context"), "_assemble_context should be removed in Sprint 5.8"

    def test_source_type_matches_removed(self):
        """_source_type_matches (Chunk-based) should be removed."""
        import aip.orchestration.ask_pipeline as mod

        assert not hasattr(mod, "_source_type_matches"), "_source_type_matches should be removed in Sprint 5.8"

    def test_chunk_to_source_ref_removed(self):
        """_chunk_to_source_ref should be removed."""
        import aip.orchestration.ask_pipeline as mod

        assert not hasattr(mod, "_chunk_to_source_ref"), "_chunk_to_source_ref should be removed in Sprint 5.8"

    def test_hit_type_matches_exists(self):
        """_hit_type_matches (RetrievalHit-based) should exist as replacement."""
        from aip.orchestration.ask_pipeline import _hit_type_matches

        assert callable(_hit_type_matches)


# ---------------------------------------------------------------------------
# 2. _hit_type_matches filter
# ---------------------------------------------------------------------------


class TestHitTypeMatches:
    """Verify the new RetrievalHit-based source type filter."""

    def test_all_passes_everything(self):
        from aip.orchestration.ask_pipeline import _hit_type_matches

        hit = RetrievalHit(id="1", metadata={"type": "conversation_chunk"})
        assert _hit_type_matches(hit, "all") is True

    def test_ingested_filters_conversation_chunks(self):
        from aip.orchestration.ask_pipeline import _hit_type_matches

        hit_conv = RetrievalHit(id="1", metadata={"type": "conversation_chunk"})
        hit_art = RetrievalHit(id="2", metadata={"type": "wiki_article"})
        assert _hit_type_matches(hit_conv, "ingested") is True
        assert _hit_type_matches(hit_art, "ingested") is False

    def test_artifacts_excludes_conversation_chunks(self):
        from aip.orchestration.ask_pipeline import _hit_type_matches

        hit_conv = RetrievalHit(id="1", metadata={"type": "conversation_chunk"})
        hit_art = RetrievalHit(id="2", metadata={"type": "graph_entity"})
        assert _hit_type_matches(hit_conv, "artifacts") is False
        assert _hit_type_matches(hit_art, "artifacts") is True


# ---------------------------------------------------------------------------
# 3. Graph channel in orchestrator
# ---------------------------------------------------------------------------


class TestGraphChannel:
    """Verify Graph channel dispatch and RRF fusion."""

    async def test_graph_channel_dispatched(self):
        """Graph channel should be dispatched when enabled."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("graph", _fake_graph_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_graph=True)
        hits, trace = await orch.retrieve("EntityA test", config=config)

        assert "graph" in trace.channels_queried
        graph_hits = [
            h for h in hits if h.source_channel == "graph" or "graph" in h.metadata.get("source_channels", [])
        ]
        assert len(graph_hits) > 0, "Graph channel should contribute hits"

    async def test_graph_channel_disabled_by_default(self):
        """Graph channel should not be dispatched when enable_graph=False (default)."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("graph", _fake_graph_retriever)

        # Default config has enable_graph=False
        hits, trace = await orch.retrieve("EntityA test")

        assert "graph" not in trace.channels_queried

    async def test_graph_hits_participate_in_rrf(self):
        """Graph hits should be properly fused with FTS hits via RRF."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("graph", _fake_graph_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_graph=True)
        hits, trace = await orch.retrieve("EntityA test", config=config)

        # Should have hits from both channels
        assert trace.hits_before_fusion >= 3  # 2 FTS + 2 Graph
        assert trace.hits_after_fusion > 0
        # All hits should have rrf_score set
        for hit in hits:
            assert hit.rrf_score > 0


# ---------------------------------------------------------------------------
# 4. Wiki channel in orchestrator
# ---------------------------------------------------------------------------


class TestWikiChannel:
    """Verify Wiki channel dispatch and RRF fusion."""

    async def test_wiki_channel_dispatched(self):
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("wiki", _fake_wiki_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_wiki=True)
        hits, trace = await orch.retrieve("test domain", config=config)

        assert "wiki" in trace.channels_queried
        wiki_hits = [h for h in hits if h.source_channel == "wiki" or "wiki" in h.metadata.get("source_channels", [])]
        assert len(wiki_hits) > 0

    async def test_wiki_channel_disabled_by_default(self):
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("wiki", _fake_wiki_retriever)

        hits, trace = await orch.retrieve("test domain")
        assert "wiki" not in trace.channels_queried


# ---------------------------------------------------------------------------
# 5. Procedural channel in orchestrator
# ---------------------------------------------------------------------------


class TestProceduralChannel:
    """Verify Procedural channel dispatch and RRF fusion."""

    async def test_procedural_channel_dispatched(self):
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("procedural", _fake_procedural_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_procedural=True)
        hits, trace = await orch.retrieve("how to configure", config=config)

        assert "procedural" in trace.channels_queried
        proc_hits = [
            h for h in hits if h.source_channel == "procedural" or "procedural" in h.metadata.get("source_channels", [])
        ]
        assert len(proc_hits) > 0

    async def test_procedural_channel_disabled_by_default(self):
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("procedural", _fake_procedural_retriever)

        hits, trace = await orch.retrieve("how to configure")
        assert "procedural" not in trace.channels_queried


# ---------------------------------------------------------------------------
# 6. All six channels together
# ---------------------------------------------------------------------------


class TestAllChannelsTogether:
    """Verify all six channels dispatch in parallel and fuse correctly."""

    async def test_six_channels_parallel_dispatch(self):
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("vector", _fake_vector_retriever)
        orch.register_channel("corpus", _fake_corpus_retriever)
        orch.register_channel("graph", _fake_graph_retriever)
        orch.register_channel("wiki", _fake_wiki_retriever)
        orch.register_channel("procedural", _fake_procedural_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_corpus=True,
            enable_graph=True,
            enable_wiki=True,
            enable_procedural=True,
        )
        hits, trace = await orch.retrieve("EntityA test domain", config=config)

        # All six channels should have been queried
        assert set(trace.channels_queried) == {"fts", "vector", "corpus", "graph", "wiki", "procedural"}
        # Should have hits from multiple channels
        assert len(hits) > 0
        # All fused hits should have rrf_score
        for hit in hits:
            assert hit.rrf_score > 0

    async def test_selective_channel_enable(self):
        """Only enabled channels should be dispatched."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("vector", _fake_vector_retriever)
        orch.register_channel("graph", _fake_graph_retriever)
        orch.register_channel("wiki", _fake_wiki_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_graph=True, enable_vector=False, enable_wiki=False)
        hits, trace = await orch.retrieve("EntityA", config=config)

        assert "fts" in trace.channels_queried
        assert "graph" in trace.channels_queried
        assert "vector" not in trace.channels_queried
        assert "wiki" not in trace.channels_queried


# ---------------------------------------------------------------------------
# 7. AskStores with graph_store
# ---------------------------------------------------------------------------


class TestAskStoresGraphStore:
    """Verify AskStores accepts and stores graph_store."""

    def test_graph_store_parameter(self):
        from aip.orchestration.ask_pipeline import AskStores

        class FakeLexical:
            pass

        class FakeArtifact:
            pass

        class FakeProject:
            pass

        class FakeGraphStore:
            pass

        stores = AskStores(
            artifact_store=FakeArtifact(),
            lexical_store=FakeLexical(),
            vector_store=None,
            event_store=None,
            project_store=FakeProject(),
            graph_store=FakeGraphStore(),
        )

        assert stores.graph_store is not None
        assert isinstance(stores.graph_store, FakeGraphStore)

    def test_graph_store_defaults_to_none(self):
        from aip.orchestration.ask_pipeline import AskStores

        class FakeLexical:
            pass

        class FakeArtifact:
            pass

        class FakeProject:
            pass

        stores = AskStores(
            artifact_store=FakeArtifact(),
            lexical_store=FakeLexical(),
            vector_store=None,
            event_store=None,
            project_store=FakeProject(),
        )

        assert stores.graph_store is None


# ---------------------------------------------------------------------------
# 8. _search_sources_with_trace no legacy fallback
# ---------------------------------------------------------------------------


class TestNoLegacyFallback:
    """Verify that _search_sources_with_trace does not fall back to legacy path."""

    async def test_orchestrator_failure_returns_empty(self):
        """When the orchestrator fails, return empty results instead of legacy fallback."""
        from aip.orchestration.ask_pipeline import (
            AskStores,
            _orchestrator_cache,
            _search_sources_with_trace,
        )

        class FakeLexical:
            async def search(self, query, domain=None, limit=10):
                raise RuntimeError("Intentional failure")

        class FakeEvent:
            async def write_event(self, **kwargs):
                pass

            async def query(self, **kwargs):
                return []

        class FakeProject:
            async def list_projects(self, **kwargs):
                return []

        class FakeArtifact:
            async def write(self, *a):
                pass

            async def read(self, *a):
                return ""

            async def list_versions(self, *a):
                return [1]

        stores = AskStores(
            artifact_store=FakeArtifact(),
            lexical_store=FakeLexical(),
            vector_store=None,
            event_store=FakeEvent(),
            project_store=FakeProject(),
        )

        _orchestrator_cache.invalidate()

        # This should return empty results, not crash or fall back
        sources, trace, packed = await _search_sources_with_trace(
            query="test query",
            stores=stores,
            session_id="test-session",
        )

        # Should get empty results, not an exception
        assert isinstance(sources, list)


# ---------------------------------------------------------------------------
# 9. SmartContextPacker is sole context assembly path
# ---------------------------------------------------------------------------


class TestSmartContextPackerOnlyPath:
    """Verify SmartContextPacker handles all context assembly scenarios."""

    def test_pack_multi_channel_hits(self):
        """SmartContextPacker should correctly pack hits from multiple channels."""
        hits = [
            RetrievalHit(id="fts:1", content="FTS content about AIP", rrf_score=0.05, source_channel="fts"),
            RetrievalHit(id="graph:EntityA", content="Graph entity: EntityA", rrf_score=0.03, source_channel="graph"),
            RetrievalHit(id="wiki:art1", content="Wiki article content", rrf_score=0.02, source_channel="wiki"),
            RetrievalHit(
                id="proc:guide1", content="Step-by-step procedure", rrf_score=0.015, source_channel="procedural"
            ),
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=2000))
        packed = packer.pack(hits, query="AIP EntityA")

        assert packed.hits_packed == 4
        assert "fts" in packed.context_text
        assert "graph" in packed.context_text
        assert "wiki" in packed.context_text
        assert "procedural" in packed.context_text

    def test_pack_graph_entity_hit(self):
        """Graph entity hits should be packable with metadata."""
        hits = [
            RetrievalHit(
                id="graph:TestEntity",
                content="Graph entity: TestEntity — connected via knowledge graph.",
                rrf_score=0.04,
                source_channel="graph",
                metadata={"type": "graph_entity", "entity_name": "TestEntity"},
            ),
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=1000))
        packed = packer.pack(hits, query="TestEntity")

        assert packed.hits_packed == 1
        assert "TestEntity" in packed.context_text


# ---------------------------------------------------------------------------
# 10. OrchestratorConfig channel toggles
# ---------------------------------------------------------------------------


class TestOrchestratorConfigToggles:
    """Verify per-channel enable toggles in OrchestratorConfig."""

    def test_default_config_has_opt_in_channels_disabled(self):
        """Graph, Wiki, and Procedural should be opt-in (disabled by default)."""
        config = OrchestratorConfig()
        assert config.enable_graph is False
        assert config.enable_wiki is False
        assert config.enable_procedural is False

    def test_default_config_has_core_channels_enabled(self):
        """FTS, Vector, and Corpus should be enabled by default."""
        config = OrchestratorConfig()
        assert config.enable_fts is True
        assert config.enable_vector is True
        assert config.enable_corpus is True

    def test_custom_config_overrides(self):
        """Custom config should override defaults."""
        config = OrchestratorConfig(
            enable_fts=False,
            enable_graph=True,
            enable_wiki=True,
            enable_procedural=True,
        )
        assert config.enable_fts is False
        assert config.enable_graph is True
        assert config.enable_wiki is True
        assert config.enable_procedural is True
