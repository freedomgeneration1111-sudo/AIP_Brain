"""Tests for retriever channel modules: per-channel registration, safe_retriever,
ChannelFailure, and the dogfood gate (custom channel registration).

Each channel is tested in isolation with fake stores, verifying:
1. Registration succeeds when dependencies are present
2. Registration returns ChannelFailure when dependencies are missing
3. The retriever callable returns correct RetrievalHit objects
4. safe_retriever wraps exceptions into structured ChannelFailures
5. A new custom channel can be added without editing ask_pipeline.py
"""

from __future__ import annotations

import asyncio
import pytest

from aip.foundation.schemas.retrieval import Chunk, RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, ChannelResult, safe_retriever
from aip.orchestration.channels.registry import (
    register_all_channels,
    register_custom_channel,
    clear_custom_channels,
)
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorConfig,
    RetrievalOrchestrator,
)


# ---------------------------------------------------------------------------
# Fake stores for testing
# ---------------------------------------------------------------------------


class FakeLexicalStore:
    """Fake LexicalStore with in-memory search."""

    def __init__(self, documents=None):
        self.documents = documents or []

    async def search(self, query, domain=None, limit=10):
        results = []
        query_lower = query.lower()
        for doc in self.documents:
            content = doc.get("content", "").lower()
            # Simple token matching
            if any(t in content for t in query_lower.split() if len(t) >= 2):
                if domain is None or doc.get("domain") == domain:
                    results.append(Chunk(
                        id=doc["doc_id"],
                        content=doc["content"],
                        score=0.8,
                        metadata=doc.get("metadata", {}),
                        domain=doc.get("domain"),
                    ))
        return results[:limit]


class FakeVectorStore:
    """Fake VectorStore with in-memory search."""

    def __init__(self, chunks=None):
        self.chunks = chunks or []

    async def retrieve(self, query_vector, domain=None, top_k=10):
        return self.chunks[:top_k]

    async def upsert(self, *a, **kw): pass
    async def delete(self, *a, **kw): pass
    async def count(self, domain=None): return len(self.chunks)
    async def store(self, chunk): return chunk.id
    async def health_check(self): return {"connected": True}
    async def list_stale_vectors(self, **kw): return []
    async def list_all_ids(self, **kw): return []


class FakeEmbeddingProvider:
    """Deterministic fake embedding provider."""

    async def embed(self, text: str) -> list[float]:
        return [0.1] * 768


class FakeArtifactStore:
    """Fake ArtifactStore for wiki/procedural channels."""

    def __init__(self, artifacts=None):
        self.artifacts = artifacts or []

    async def list_artifacts_by_metadata(self, key, value, limit=50):
        results = []
        for art in self.artifacts:
            meta = art.get("metadata", {})
            if meta.get(key) == value:
                results.append(art)
        return results[:limit]

    async def write(self, *a, **kw): pass
    async def read(self, *a, **kw): return ""
    async def list_versions(self, *a): return [1]


class FakeEcsStore:
    """Fake EcsStore that tracks states."""

    def __init__(self, states=None):
        self._states = states or {}

    async def current_state(self, artifact_id):
        return self._states.get(artifact_id)

    async def transition(self, *a, **kw): pass


class FakeCorpusTurnStore:
    """Fake CorpusTurnStore for corpus channel."""

    def __init__(self, turns=None):
        self.turns = turns or []

    async def search(self, query, primary_domain=None, limit=30):
        return self.turns[:limit]


class FakeProjectStore:
    """Fake ProjectStore."""

    async def list_projects(self, **kw): return []


class FakeEventStore:
    """Fake EventStore."""

    async def write_event(self, **kw): pass
    async def query(self, **kw): return []


class FakeModelProvider:
    """Fake ModelProvider for graph channel LLM wiring."""

    async def call(self, *a, **kw):
        return {"content": "test", "model": "test-model"}


class FakeGraphStore:
    """Fake GraphStore for graph channel."""

    async def get_all_nodes(self, min_confidence=0.4):
        return []

    async def get_all_edges(self, min_confidence=0.4):
        return []

    async def get_neighbors(self, node_id, min_confidence=0.4):
        return []


# ---------------------------------------------------------------------------
# Minimal AskStores helper
# ---------------------------------------------------------------------------


class _FakeAskStores:
    """Minimal AskStores-like container for channel testing."""

    def __init__(self, **kwargs):
        self.lexical_store = kwargs.get("lexical_store")
        self.vector_store = kwargs.get("vector_store")
        self.embedding_provider = kwargs.get("embedding_provider")
        self.corpus_turn_store = kwargs.get("corpus_turn_store")
        self.artifact_store = kwargs.get("artifact_store")
        self.ecs_store = kwargs.get("ecs_store")
        self.model_provider = kwargs.get("model_provider")
        self.graph_store = kwargs.get("graph_store")


# ---------------------------------------------------------------------------
# Test: ChannelFailure and ChannelResult types
# ---------------------------------------------------------------------------


class TestChannelFailure:
    """Tests for ChannelFailure structured error type."""

    def test_to_dict(self):
        failure = ChannelFailure(
            channel="vector",
            error_type="store_unavailable",
            message="Missing embedding_provider",
            exception_type="RuntimeError",
        )
        d = failure.to_dict()
        assert d["channel"] == "vector"
        assert d["error_type"] == "store_unavailable"
        assert d["message"] == "Missing embedding_provider"
        assert d["exception_type"] == "RuntimeError"
        assert d["is_fatal"] is False

    def test_defaults(self):
        failure = ChannelFailure(channel="test", error_type="test_error", message="msg")
        assert failure.exception_type == ""
        assert failure.is_fatal is False


class TestChannelResult:
    """Tests for ChannelResult wrapper."""

    def test_succeeded_with_hits(self):
        result = ChannelResult(hits=[RetrievalHit(id="1", content="test")])
        assert result.succeeded is True
        assert result.failed is False

    def test_failed_with_failure(self):
        result = ChannelResult(
            failure=ChannelFailure(channel="test", error_type="test", message="msg")
        )
        assert result.succeeded is False
        assert result.failed is True

    def test_to_dict(self):
        result = ChannelResult(
            hits=[RetrievalHit(id="1")],
            elapsed_ms=42.5,
        )
        d = result.to_dict()
        assert d["hit_count"] == 1
        assert d["elapsed_ms"] == 42.5
        assert d["succeeded"] is True
        assert "failure" not in d


# ---------------------------------------------------------------------------
# Test: safe_retriever wrapper
# ---------------------------------------------------------------------------


class TestSafeRetriever:
    """Tests for the safe_retriever error-capturing wrapper."""

    async def test_success_returns_hits(self):
        async def _good_retriever(query: str) -> list[RetrievalHit]:
            return [RetrievalHit(id="1", content="test", score=0.9)]

        wrapped = safe_retriever("test", _good_retriever)
        hits = await wrapped("hello")
        assert len(hits) == 1
        assert hits[0].id == "1"

    async def test_exception_returns_empty(self):
        async def _bad_retriever(query: str) -> list[RetrievalHit]:
            raise RuntimeError("Store is down")

        wrapped = safe_retriever("test", _bad_retriever, log_level="debug")
        hits = await wrapped("hello")
        assert hits == []
        # The wrapper should record the last failure
        failure = wrapped.get_last_failure()
        assert failure is not None
        assert failure.channel == "test"
        assert failure.error_type == "store_error"
        assert "Store is down" in failure.message

    async def test_empty_result_stamps_no_results_failure(self):
        async def _empty_retriever(query: str) -> list[RetrievalHit]:
            return []

        wrapped = safe_retriever("test", _empty_retriever)
        hits = await wrapped("hello")
        assert hits == []
        failure = wrapped.get_last_failure()
        assert failure is not None
        assert failure.error_type == "no_results"


# ---------------------------------------------------------------------------
# Test: Lexical (FTS) channel
# ---------------------------------------------------------------------------


class TestLexicalChannel:
    """Tests for the FTS5 lexical channel."""

    async def test_register_and_retrieve(self):
        from aip.orchestration.channels.lexical_channel import register, CHANNEL_NAME

        lexical = FakeLexicalStore(documents=[
            {"doc_id": "doc:1", "content": "AIP uses a three-layer architecture", "domain": "test", "metadata": {"type": "project_artifact"}},
        ])
        stores = _FakeAskStores(lexical_store=lexical)
        orch = RetrievalOrchestrator()
        register(orch, stores)

        assert orch.is_registered(CHANNEL_NAME)
        hits, trace = await orch.retrieve("AIP architecture")
        assert len(hits) > 0
        assert any(h.source_channel == CHANNEL_NAME for h in hits)

    async def test_no_duplicate_registration(self):
        from aip.orchestration.channels.lexical_channel import register

        stores = _FakeAskStores(lexical_store=FakeLexicalStore())
        orch = RetrievalOrchestrator()
        register(orch, stores)
        register(orch, stores)  # should be idempotent
        assert len(orch.channel_names) == 1


class TestSanitizeFtsQuery:
    """Tests for _sanitize_fts_query utility."""

    def test_basic_query(self):
        from aip.orchestration.channels.lexical_channel import _sanitize_fts_query
        result = _sanitize_fts_query("What is the AIP architecture?")
        assert "AIP" in result
        assert "architecture" in result
        # Stop words should be removed
        assert "the" not in result.split()
        assert "is" not in result.split()

    def test_special_characters_removed(self):
        from aip.orchestration.channels.lexical_channel import _sanitize_fts_query
        result = _sanitize_fts_query("What?! How does *this* work?")
        assert "?" not in result
        assert "!" not in result
        assert "*" not in result

    def test_empty_query_returns_original(self):
        from aip.orchestration.channels.lexical_channel import _sanitize_fts_query
        result = _sanitize_fts_query("?!.")
        # Should return the original query when no meaningful tokens
        assert result == "?!."


# ---------------------------------------------------------------------------
# Test: Vector channel
# ---------------------------------------------------------------------------


class TestVectorChannel:
    """Tests for the vector (semantic) channel."""

    async def test_register_with_deps(self):
        from aip.orchestration.channels.vector_channel import register, CHANNEL_NAME

        vec_store = FakeVectorStore(chunks=[
            Chunk(id="vec:1", content="Vector content", score=0.9, metadata={}),
        ])
        stores = _FakeAskStores(
            vector_store=vec_store,
            embedding_provider=FakeEmbeddingProvider(),
        )
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 0

    async def test_register_without_deps_returns_failure(self):
        from aip.orchestration.channels.vector_channel import register, CHANNEL_NAME

        stores = _FakeAskStores(vector_store=None, embedding_provider=None)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert not orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 1
        assert failures[0].channel == CHANNEL_NAME
        assert failures[0].error_type == "store_unavailable"

    async def test_retrieve_with_vector(self):
        from aip.orchestration.channels.vector_channel import register

        vec_store = FakeVectorStore(chunks=[
            Chunk(id="vec:1", content="Vector search content", score=0.9, metadata={}, domain="test"),
        ])
        stores = _FakeAskStores(
            vector_store=vec_store,
            embedding_provider=FakeEmbeddingProvider(),
            lexical_store=FakeLexicalStore(),
        )
        orch = RetrievalOrchestrator()
        register(orch, stores)

        config = OrchestratorConfig(enable_vector=True)
        hits, trace = await orch.retrieve("search content", config=config)
        assert any(h.source_channel == "vector" for h in hits)


# ---------------------------------------------------------------------------
# Test: Corpus channel
# ---------------------------------------------------------------------------


class TestCorpusChannel:
    """Tests for the corpus turn channel."""

    async def test_register_with_deps(self):
        from aip.orchestration.channels.corpus_channel import register, CHANNEL_NAME

        # Create a simple turn-like object
        class FakeTurn:
            turn_id = "turn:1"
            searchable_text = "How does vector search work?"
            primary_domain = "test"
            conversation_id = "conv:1"
            importance = 0.5

        stores = _FakeAskStores(corpus_turn_store=FakeCorpusTurnStore(turns=[FakeTurn()]))
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 0

    async def test_register_without_deps_returns_failure(self):
        from aip.orchestration.channels.corpus_channel import register, CHANNEL_NAME

        stores = _FakeAskStores(corpus_turn_store=None)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert not orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 1
        assert failures[0].channel == CHANNEL_NAME
        assert failures[0].error_type == "store_unavailable"


# ---------------------------------------------------------------------------
# Test: Wiki channel
# ---------------------------------------------------------------------------


class TestWikiChannel:
    """Tests for the wiki article channel."""

    async def test_register_with_deps(self):
        from aip.orchestration.channels.wiki_channel import register, CHANNEL_NAME

        artifact_store = FakeArtifactStore(artifacts=[
            {
                "id": "wiki:1",
                "content": "AIP is a knowledge engine for retrieval-augmented generation.",
                "metadata": {"artifact_type": "beast_wiki", "domain": "test", "overview_text": "Overview of AIP"},
            },
        ])
        ecs_store = FakeEcsStore(states={"wiki:1": "APPROVED"})
        stores = _FakeAskStores(artifact_store=artifact_store, ecs_store=ecs_store)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 0

    async def test_register_without_deps_returns_failure(self):
        from aip.orchestration.channels.wiki_channel import register, CHANNEL_NAME

        stores = _FakeAskStores(artifact_store=None, ecs_store=None)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert not orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 1
        assert failures[0].channel == CHANNEL_NAME
        assert failures[0].error_type == "store_unavailable"

    async def test_retrieve_approved_wiki_articles(self):
        from aip.orchestration.channels.wiki_channel import register

        artifact_store = FakeArtifactStore(artifacts=[
            {
                "id": "wiki:approved",
                "content": "AIP knowledge engine architecture overview.",
                "metadata": {"artifact_type": "beast_wiki", "domain": "aip", "overview_text": "AIP architecture"},
            },
        ])
        ecs_store = FakeEcsStore(states={"wiki:approved": "APPROVED"})
        stores = _FakeAskStores(
            artifact_store=artifact_store,
            ecs_store=ecs_store,
            lexical_store=FakeLexicalStore(),
        )
        orch = RetrievalOrchestrator()
        register(orch, stores)

        config = OrchestratorConfig(enable_wiki=True, enable_fts=False)
        hits, trace = await orch.retrieve("AIP architecture", config=config)
        # Should find the wiki article
        wiki_hits = [h for h in hits if h.source_channel == "wiki"]
        assert len(wiki_hits) > 0


# ---------------------------------------------------------------------------
# Test: Procedural channel
# ---------------------------------------------------------------------------


class TestProceduralChannel:
    """Tests for the procedural guide channel."""

    async def test_register_with_deps(self):
        from aip.orchestration.channels.procedural_channel import register, CHANNEL_NAME

        artifact_store = FakeArtifactStore(artifacts=[
            {
                "id": "proc:1",
                "content": "Step by step guide to configure AIP. First, install dependencies.",
                "metadata": {"artifact_type": "procedural_guide", "domain": "test"},
            },
        ])
        stores = _FakeAskStores(artifact_store=artifact_store)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 0

    async def test_register_without_deps_returns_failure(self):
        from aip.orchestration.channels.procedural_channel import register, CHANNEL_NAME

        stores = _FakeAskStores(artifact_store=None)
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        assert not orch.is_registered(CHANNEL_NAME)
        assert len(failures) == 1
        assert failures[0].channel == CHANNEL_NAME
        assert failures[0].error_type == "store_unavailable"

    async def test_retrieve_procedural_guides(self):
        from aip.orchestration.channels.procedural_channel import register

        artifact_store = FakeArtifactStore(artifacts=[
            {
                "id": "proc:1",
                "content": "Step by step guide: how to configure AIP for retrieval. Follow these instructions.",
                "metadata": {"artifact_type": "procedural_guide", "domain": "aip"},
            },
        ])
        stores = _FakeAskStores(
            artifact_store=artifact_store,
            lexical_store=FakeLexicalStore(),
        )
        orch = RetrievalOrchestrator()
        register(orch, stores)

        config = OrchestratorConfig(enable_procedural=True, enable_fts=False)
        hits, trace = await orch.retrieve("configure AIP", config=config)
        proc_hits = [h for h in hits if h.source_channel == "procedural"]
        assert len(proc_hits) > 0


# ---------------------------------------------------------------------------
# Test: Graph channel
# ---------------------------------------------------------------------------


class TestGraphChannel:
    """Tests for the graph retrieval channel."""

    async def test_register_with_graph_store(self):
        from aip.orchestration.channels.graph_channel import register, CHANNEL_NAME

        stores = _FakeAskStores(graph_store=FakeGraphStore())
        orch = RetrievalOrchestrator()
        failures = register(orch, stores)

        # Graph channel always registers (it creates GraphStore as fallback)
        assert orch.is_registered(CHANNEL_NAME)

    async def test_retrieve_with_empty_graph(self):
        from aip.orchestration.channels.graph_channel import register

        stores = _FakeAskStores(
            graph_store=FakeGraphStore(),
            lexical_store=FakeLexicalStore(),
        )
        orch = RetrievalOrchestrator()
        register(orch, stores)

        config = OrchestratorConfig(enable_graph=True, enable_fts=False)
        hits, trace = await orch.retrieve("test query", config=config)
        # Empty graph should return no graph hits
        graph_hits = [h for h in hits if h.source_channel == "graph"]
        assert len(graph_hits) == 0


# ---------------------------------------------------------------------------
# Test: Channel registry (auto-discovery)
# ---------------------------------------------------------------------------


class TestChannelRegistry:
    """Tests for the channel registry auto-discovery."""

    async def test_register_all_channels(self):
        lexical = FakeLexicalStore()
        stores = _FakeAskStores(
            lexical_store=lexical,
            vector_store=FakeVectorStore(),
            embedding_provider=FakeEmbeddingProvider(),
        )
        orch = RetrievalOrchestrator()
        failures = register_all_channels(orch, stores)

        # FTS should always be registered
        assert orch.is_registered("fts")
        # Vector should be registered (deps present)
        assert orch.is_registered("vector")

    async def test_register_all_returns_failures_for_missing_deps(self):
        stores = _FakeAskStores(
            lexical_store=FakeLexicalStore(),
            # No vector_store or embedding_provider
            # No corpus_turn_store
            # No artifact_store (wiki, procedural)
        )
        orch = RetrievalOrchestrator()
        failures = register_all_channels(orch, stores)

        # FTS is always available
        assert orch.is_registered("fts")
        # Vector should have a failure
        vector_failures = [f for f in failures if f.channel == "vector"]
        assert len(vector_failures) > 0
        assert vector_failures[0].error_type == "store_unavailable"
        # Corpus should have a failure
        corpus_failures = [f for f in failures if f.channel == "corpus"]
        assert len(corpus_failures) > 0

    async def test_custom_channel_registration(self):
        """Dogfood gate: a new channel can be added without editing ask_pipeline.py."""
        try:
            custom_called = False

            def register_custom(orch, stores, config):
                nonlocal custom_called
                custom_called = True

                async def _custom_retriever(query: str) -> list[RetrievalHit]:
                    return [RetrievalHit(
                        id="custom:1",
                        content=f"Custom result for: {query}",
                        score=0.95,
                        source_channel="custom",
                    )]

                orch.register_channel("custom", _custom_retriever)
                return []

            register_custom_channel("custom", register_custom)

            stores = _FakeAskStores(lexical_store=FakeLexicalStore())
            orch = RetrievalOrchestrator()
            register_all_channels(orch, stores)

            assert custom_called, "Custom channel register function was not called"
            assert orch.is_registered("custom")

            # The custom channel should return results
            config = OrchestratorConfig(enable_all_registered=True)
            hits, trace = await orch.retrieve("test query", config=config)
            custom_hits = [h for h in hits if h.source_channel == "custom"]
            assert len(custom_hits) > 0
            assert "Custom result" in custom_hits[0].content

        finally:
            clear_custom_channels()

    async def test_custom_channel_failure_is_structured(self):
        """Custom channel registration failure produces a ChannelFailure."""
        try:
            def _failing_register(orch, stores, config):
                raise ImportError("Custom backend not installed")

            register_custom_channel("failing_custom", _failing_register)

            stores = _FakeAskStores(lexical_store=FakeLexicalStore())
            orch = RetrievalOrchestrator()
            failures = register_all_channels(orch, stores)

            custom_failures = [f for f in failures if f.channel == "failing_custom"]
            assert len(custom_failures) == 1
            assert custom_failures[0].error_type == "initialization"
            assert "Custom backend not installed" in custom_failures[0].message

        finally:
            clear_custom_channels()


# ---------------------------------------------------------------------------
# Test: Integration — register_all_channels produces working orchestrator
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Integration tests for the full channel registry pipeline."""

    async def test_full_pipeline_with_fakes(self):
        """Verify that register_all_channels produces a working orchestrator
        with FTS + Vector channels that can retrieve results."""
        lexical = FakeLexicalStore(documents=[
            {"doc_id": "doc:1", "content": "AIP architecture overview", "domain": "test", "metadata": {"type": "project_artifact"}},
        ])
        vec = FakeVectorStore(chunks=[
            Chunk(id="vec:1", content="Vector search architecture", score=0.9, metadata={}, domain="test"),
        ])
        stores = _FakeAskStores(
            lexical_store=lexical,
            vector_store=vec,
            embedding_provider=FakeEmbeddingProvider(),
        )
        orch = RetrievalOrchestrator()
        failures = register_all_channels(orch, stores)

        # Both channels should be registered
        assert orch.is_registered("fts")
        assert orch.is_registered("vector")

        # Retrieve should return hits from both channels
        config = OrchestratorConfig(enable_fts=True, enable_vector=True)
        hits, trace = await orch.retrieve("AIP architecture", config=config)

        assert len(hits) > 0
        assert "fts" in trace.channels_queried
        assert "vector" in trace.channels_queried
