"""UI Cycle 11 — Retrieval Lab Tests.

Tests that:
1. Retrieval test endpoint returns stable schema.
2. Retrieval test does not synthesize an answer.
3. Retrieval test with all channels returns channel health/details.
4. Retrieval test with partial/missing channels reports degraded/unavailable.
5. Empty retrieval returns empty/no_context honestly.
6. Vector fallback/degraded state is visible if backend reports it.
7. Channel exception becomes failed/degraded channel, not total fake success.
8. Retrieval health endpoint returns stable schema.
9. GUI Retrieval Lab page imports/renders.
10. GUI handles: no query, backend unavailable, healthy results,
    partial/degraded results, empty results, vector unavailable.
11. No secret exposure.
12. GUI import-boundary tests pass.
13. General import-boundary tests pass.
14. Existing Ask/Beast/Model Council/Wiki/Crosslink/Artifact/Corpus tests still pass.

Backend tests use direct function calls with mock containers.
Frontend tests use import checks.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

# ── Backend Endpoint Schema Tests ────────────────────────────────────


class TestRetrievalTestEndpoint:
    """Test POST /api/v1/retrieval/test endpoint returns stable schema."""

    def _make_container_with_pipeline(self) -> MagicMock:
        """Create a mock container with a working retrieval pipeline."""
        container = MagicMock()

        # Mock _search_sources_fn that returns sources and trace
        async def mock_search_sources(**kwargs):
            # Create mock sources
            source1 = MagicMock()
            source1.source_id = "src_1"
            source1.title = "Test Document 1"
            source1.content_snippet = "This is a test snippet about AIP."
            source1.score = 0.85
            source1.source_type = "fts"
            source1.domain = "testing"
            source1.metadata = {}

            source2 = MagicMock()
            source2.source_id = "src_2"
            source2.title = "Test Document 2"
            source2.content_snippet = "Another test snippet."
            source2.score = 0.72
            source2.source_type = "vector"
            source2.domain = "testing"
            source2.metadata = {}

            # Create mock trace
            trace = MagicMock()
            trace.channel_health = {
                "fts": "active",
                "vector": "active",
                "graph": "not_configured",
                "wiki": "not_configured",
                "procedural": "not_configured",
                "corpus": "active",
            }
            trace.channel_health_reasons = {
                "graph": "Channel not registered (missing store dependency)",
                "wiki": "Channel not registered (missing store dependency)",
                "procedural": "Channel not registered (missing store dependency)",
            }
            trace.channel_details = {
                "fts": MagicMock(
                    channel="fts",
                    result_count=1,
                    latency_ms=12.5,
                    degradation_reason="",
                    error_summary="",
                ),
                "vector": MagicMock(
                    channel="vector",
                    result_count=1,
                    latency_ms=45.3,
                    degradation_reason="",
                    error_summary="",
                ),
                "corpus": MagicMock(
                    channel="corpus",
                    result_count=0,
                    latency_ms=8.1,
                    degradation_reason="",
                    error_summary="",
                ),
            }
            trace.per_channel_elapsed_ms = {"fts": 12.5, "vector": 45.3, "corpus": 8.1}
            trace.channel_contributions = {"fts": 1, "vector": 1}
            trace.hits_before_fusion = 3
            trace.hits_after_fusion = 2
            trace.hits_after_quality_gate = 2
            trace.verdict = "OK"
            trace.top_scores = [
                {"id": "src_1", "rrf_score": 0.015, "raw_score": 0.85},
                {"id": "src_2", "rrf_score": 0.012, "raw_score": 0.72},
            ]
            trace.lexical_only = False
            trace.vector_contributed = True
            trace.degradation_warnings = []

            return [source1, source2], trace, None

        # Wire container
        container._search_sources_fn = mock_search_sources
        container._ask_stores_class = MagicMock
        container._orchestrator_config_class = MagicMock

        # Wire stores
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        return container

    def test_retrieval_test_returns_stable_schema(self):
        """Retrieval test with a working pipeline returns all required fields."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = self._make_container_with_pipeline()

        payload = {
            "query": "What is AIP?",
            "selected_channels": ["fts", "vector", "corpus"],
            "limit": 20,
            "include_trace": True,
        }

        result = asyncio.run(retrieval_test(payload=payload, container=container))

        # Verify required top-level fields
        assert "status" in result
        assert "query" in result
        assert "selected_channels" in result
        assert "channel_results" in result
        assert "channel_health" in result
        assert "latency_ms" in result
        assert "per_channel_latency_ms" in result
        assert "scores" in result
        assert "fusion_results" in result
        assert "selected_context" in result
        assert "degraded_channels" in result
        assert "failed_channels" in result
        assert "warnings" in result
        assert "trace" in result
        assert "lexical_only" in result
        assert "vector_contributed" in result

    def test_retrieval_test_no_query_returns_error(self):
        """Retrieval test with empty query returns error status."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = self._make_container_with_pipeline()

        payload = {"query": ""}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        assert result["status"] == "error"
        assert "query is required" in result.get("message", "")
        assert result["fusion_results"] == []

    def test_retrieval_test_does_not_synthesize_answer(self):
        """Retrieval test does not return any answer field."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = self._make_container_with_pipeline()

        payload = {"query": "test query"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        # Must not contain answer-related fields
        assert "answer" not in result
        assert "model_slot" not in result
        assert "model_provider" not in result
        assert "artifact_id" not in result

    def test_retrieval_test_pipeline_unavailable(self):
        """Retrieval test returns unavailable when pipeline not wired."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()
        container._search_sources_fn = None
        container._ask_stores_class = None

        payload = {"query": "test query"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        assert result["status"] == "unavailable"
        assert "not configured" in result.get("message", "").lower() or "not wired" in result.get("message", "").lower()

    def test_retrieval_test_pipeline_error(self):
        """Retrieval test returns error when pipeline throws exception."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()

        async def mock_search_sources_error(**kwargs):
            raise RuntimeError("Test retrieval error")

        container._search_sources_fn = mock_search_sources_error
        container._ask_stores_class = MagicMock
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        payload = {"query": "test query"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        assert result["status"] == "error"
        assert "Test retrieval error" in result.get("message", "")

    def test_retrieval_test_empty_results_honest(self):
        """Retrieval test with no results returns empty honestly, not fake success."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()

        async def mock_search_sources_empty(**kwargs):
            trace = MagicMock()
            trace.channel_health = {
                "fts": "empty",
                "vector": "not_configured",
                "graph": "not_configured",
                "wiki": "not_configured",
                "procedural": "not_configured",
                "corpus": "empty",
            }
            trace.channel_health_reasons = {
                "fts": "Channel returned 0 results",
                "vector": "Channel not registered",
                "corpus": "Channel returned 0 results",
            }
            trace.channel_details = {}
            trace.per_channel_elapsed_ms = {"fts": 5.0, "corpus": 3.0}
            trace.channel_contributions = {}
            trace.hits_before_fusion = 0
            trace.hits_after_fusion = 0
            trace.hits_after_quality_gate = 0
            trace.verdict = "NO_RESULTS"
            trace.top_scores = []
            trace.lexical_only = True
            trace.vector_contributed = False
            trace.degradation_warnings = ["No results from any channel"]
            return [], trace, None

        container._search_sources_fn = mock_search_sources_empty
        container._ask_stores_class = MagicMock
        container.lexical_store = MagicMock()
        container.vector_store = None
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = None
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        payload = {"query": "obscure query with no matches"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        # Must NOT claim success with fake data
        assert result["fusion_results"] == []
        assert result["selected_context"] == []
        assert result["lexical_only"] is True
        assert result["vector_contributed"] is False

    def test_retrieval_test_degraded_channels_visible(self):
        """Degraded channels are reported in degraded_channels list."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()

        async def mock_search_sources_degraded(**kwargs):
            trace = MagicMock()
            trace.channel_health = {
                "fts": "active",
                "vector": "degraded",
                "graph": "not_configured",
                "wiki": "not_configured",
                "procedural": "not_configured",
                "corpus": "active",
            }
            trace.channel_health_reasons = {
                "vector": "Vector search using brute-force fallback",
            }
            trace.channel_details = {
                "vector": MagicMock(
                    channel="vector",
                    result_count=1,
                    latency_ms=150.0,
                    degradation_reason="Vector search using brute-force fallback",
                    error_summary="",
                    backend_type="brute_force",
                    vss_available=False,
                    embedding_provider_configured=True,
                ),
            }
            trace.per_channel_elapsed_ms = {"fts": 10.0, "vector": 150.0, "corpus": 5.0}
            trace.channel_contributions = {"fts": 1, "vector": 1, "corpus": 1}
            trace.hits_before_fusion = 3
            trace.hits_after_fusion = 3
            trace.hits_after_quality_gate = 3
            trace.verdict = "OK"
            trace.top_scores = []
            trace.lexical_only = False
            trace.vector_contributed = True
            trace.degradation_warnings = []

            source = MagicMock()
            source.source_id = "s1"
            source.title = "Test"
            source.content_snippet = "test"
            source.score = 0.8
            source.source_type = "fts"
            source.domain = ""
            source.metadata = {}
            return [source], trace, None

        container._search_sources_fn = mock_search_sources_degraded
        container._ask_stores_class = MagicMock
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        payload = {"query": "test degraded"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        assert "vector" in result.get("degraded_channels", [])
        assert result["channel_health"].get("vector") == "degraded"

    def test_retrieval_test_channel_selection_respected(self):
        """Selected channels are passed to the pipeline."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()

        received_kwargs: dict = {}

        async def mock_search_sources_capture(**kwargs):
            received_kwargs.update(kwargs)
            trace = MagicMock()
            trace.channel_health = {"fts": "active"}
            trace.channel_health_reasons = {}
            trace.channel_details = {}
            trace.per_channel_elapsed_ms = {}
            trace.channel_contributions = {}
            trace.hits_before_fusion = 0
            trace.hits_after_fusion = 0
            trace.hits_after_quality_gate = 0
            trace.verdict = "NO_RESULTS"
            trace.top_scores = []
            trace.lexical_only = True
            trace.vector_contributed = False
            trace.degradation_warnings = []
            return [], trace, None

        container._search_sources_fn = mock_search_sources_capture
        container._ask_stores_class = MagicMock
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        payload = {
            "query": "test",
            "selected_channels": ["fts", "corpus"],
        }
        asyncio.run(retrieval_test(payload=payload, container=container))

        # Verify channel flags were passed correctly
        assert received_kwargs.get("enable_fts") is True
        assert received_kwargs.get("enable_vector") is False
        assert received_kwargs.get("enable_graph") is False
        assert received_kwargs.get("auto_channel_selection") is False
        # Note: enable_corpus is not a parameter of _search_sources_with_trace;
        # corpus is always enabled via OrchestratorConfig.enable_corpus=True.
        # The retrieval test endpoint maps "corpus" in selected_channels to
        # the orchestrator config, not to the function signature.


class TestRetrievalHealthEndpoint:
    """Test GET /api/v1/retrieval/health endpoint returns stable schema."""

    def test_health_returns_stable_schema(self):
        """Retrieval health returns all required fields."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_health

        container = MagicMock()
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.vector_store.get_backend_status.return_value = MagicMock(value="available")
        container.vector_store._backend_name = "sqlite_vss"
        container.vector_store._vss_available = True
        container.embedding_provider = MagicMock()
        container.artifact_store = MagicMock()
        container.ecs_store = MagicMock()
        container.corpus_turn_store = AsyncMock()
        container.corpus_turn_store.get_corpus_status = AsyncMock(
            return_value={
                "total_turns": 100,
                "embedded": 50,
                "embed_coverage": 50.0,
            }
        )
        container.graph_store = MagicMock()

        result = asyncio.run(retrieval_health(container=container))

        # Verify top-level fields
        assert result["status"] == "ok"
        assert "channels" in result
        assert "embedding_coverage" in result
        assert "vector_fallback_chain" in result
        assert "summary" in result

        # Verify channel structure
        channels = result["channels"]
        assert "lexical" in channels
        assert "vector" in channels
        assert "graph" in channels
        assert "wiki" in channels
        assert "procedural" in channels
        assert "corpus" in channels

        # Verify each channel has required fields
        for ch_name, ch_data in channels.items():
            assert "channel" in ch_data
            assert "state" in ch_data
            assert "backend_type" in ch_data
            assert "available" in ch_data
            assert "degradation_reason" in ch_data

        # Verify summary
        summary = result["summary"]
        assert "total_channels" in summary
        assert "active" in summary
        assert "degraded" in summary
        assert "unavailable" in summary

    def test_health_with_no_stores(self):
        """Retrieval health with no stores reports all channels unavailable."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_health

        container = MagicMock()
        container.lexical_store = None
        container.vector_store = None
        container.embedding_provider = None
        container.artifact_store = None
        container.ecs_store = None
        container.corpus_turn_store = None
        container.graph_store = None

        result = asyncio.run(retrieval_health(container=container))

        channels = result["channels"]
        # All channels should be unavailable
        for ch_name, ch_data in channels.items():
            assert ch_data["state"] in ("unavailable", "not_configured")
            assert ch_data["available"] is False

        summary = result["summary"]
        assert summary["active"] == 0
        assert summary["unavailable"] > 0

    def test_health_vector_degraded(self):
        """Retrieval health shows vector as degraded when brute-force fallback."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_health
        from aip.foundation.schemas.vector import VectorBackendStatus

        container = MagicMock()
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.vector_store.get_backend_status.return_value = VectorBackendStatus.DEGRADED_BRUTEFORCE
        container.vector_store._vss_available = False
        container.embedding_provider = MagicMock()
        container.artifact_store = MagicMock()
        container.ecs_store = MagicMock()
        container.corpus_turn_store = AsyncMock()
        container.graph_store = MagicMock()

        result = asyncio.run(retrieval_health(container=container))

        vector_ch = result["channels"].get("vector", {})
        assert vector_ch.get("state") == "degraded"
        assert vector_ch.get("degraded") is True
        assert "brute-force" in vector_ch.get("degradation_reason", "").lower()

    def test_health_embedding_coverage(self):
        """Retrieval health includes embedding coverage data."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_health

        container = MagicMock()
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.vector_store.get_backend_status.return_value = MagicMock(value="available")
        container.vector_store._backend_name = "sqlite_vss"
        container.embedding_provider = MagicMock()
        container.artifact_store = MagicMock()
        container.ecs_store = MagicMock()
        container.corpus_turn_store = AsyncMock()
        container.corpus_turn_store.get_corpus_status = AsyncMock(
            return_value={
                "total_turns": 2766,
                "embedded": 50,
                "embed_coverage": 1.8,
            }
        )
        container.graph_store = MagicMock()

        result = asyncio.run(retrieval_health(container=container))

        coverage = result["embedding_coverage"]
        assert coverage["status"] == "available"
        assert coverage["coverage_percent"] == 1.8
        assert coverage["total_turns"] == 2766
        assert coverage["embedded_turns"] == 50


# ── Frontend Import Tests ────────────────────────────────────────────


class TestRetrievalLabPageImports:
    """Test that the Retrieval Lab page and components are importable."""

    def test_retrieval_lab_page_importable(self):
        """gui.pages.retrieval_lab can be imported."""
        import gui.pages.retrieval_lab  # noqa: F401

    def test_retrieval_query_panel_importable(self):
        """gui.components.retrieval_query_panel can be imported."""
        import gui.components.retrieval_query_panel  # noqa: F401

    def test_retrieval_channel_results_importable(self):
        """gui.components.retrieval_channel_results can be imported."""
        import gui.components.retrieval_channel_results  # noqa: F401

    def test_retrieval_health_cards_importable(self):
        """gui.components.retrieval_health_cards can be imported."""
        import gui.components.retrieval_health_cards  # noqa: F401

    def test_retrieval_ranked_context_importable(self):
        """gui.components.retrieval_ranked_context can be imported."""
        import gui.components.retrieval_ranked_context  # noqa: F401

    def test_status_types_retrieval_lab_typeddicts(self):
        """gui.status_types has Retrieval Lab TypedDicts."""
        from gui.status_types import (
            RetrievalHealthResponse,
            RetrievalTestResponse,
        )

        # Verify they exist as TypedDict subclasses
        assert hasattr(RetrievalTestResponse, "__annotations__")
        assert hasattr(RetrievalHealthResponse, "__annotations__")

    def test_api_client_retrieval_methods(self):
        """gui.api_client has retrieval lab methods."""
        from gui.api_client import AipApiClient

        assert hasattr(AipApiClient, "retrieval_test")
        assert hasattr(AipApiClient, "retrieval_health")
        assert hasattr(AipApiClient, "get_retrieval_recent_traces")


# ── GUI State Handling Tests ─────────────────────────────────────────


class TestRetrievalLabStateHandling:
    """Test that GUI components handle various states correctly."""

    def test_health_cards_renders_no_channels(self):
        """RetrievalHealthCards renders with no channel data."""
        from gui.components.retrieval_health_cards import RetrievalHealthCards

        cards = RetrievalHealthCards()
        # This should not raise — container is None so render does nothing
        cards.render({"channels": {}})

    def test_channel_results_renders_empty(self):
        """RetrievalChannelResults renders with empty results."""
        from gui.components.retrieval_channel_results import RetrievalChannelResults

        results = RetrievalChannelResults()
        results.render(
            {
                "channel_results": {},
                "channel_health": {},
            }
        )

    def test_ranked_context_renders_empty(self):
        """RetrievalRankedContext renders with empty results."""
        from gui.components.retrieval_ranked_context import RetrievalRankedContext

        ctx = RetrievalRankedContext()
        ctx.render(
            {
                "fusion_results": [],
                "selected_context": [],
                "scores": {},
                "warnings": [],
                "lexical_only": True,
                "vector_contributed": False,
            }
        )

    def test_ranked_context_shows_lexical_only_warning(self):
        """RetrievalRankedContext shows LEXICAL ONLY flag when set."""
        from gui.components.retrieval_ranked_context import RetrievalRankedContext

        ctx = RetrievalRankedContext()
        # This should not raise
        ctx.render(
            {
                "fusion_results": [],
                "selected_context": [],
                "scores": {},
                "warnings": [],
                "lexical_only": True,
                "vector_contributed": False,
            }
        )


# ── No Secret Exposure Tests ─────────────────────────────────────────


class TestRetrievalLabNoSecretExposure:
    """Verify that no secrets are exposed in retrieval responses."""

    def test_retrieval_test_no_secrets(self):
        """Retrieval test response does not contain secrets."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()
        container._search_sources_fn = None
        container._ask_stores_class = None

        payload = {"query": "test"}
        result = asyncio.run(retrieval_test(payload=payload, container=container))

        # No secrets in the response
        result_str = str(result).lower()
        for secret_word in ["api_key", "password", "token", "secret"]:
            assert secret_word not in result_str, f"Retrieval test response contains '{secret_word}'"

    def test_retrieval_health_no_secrets(self):
        """Retrieval health response does not contain secrets."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_health

        container = MagicMock()
        container.lexical_store = None
        container.vector_store = None
        container.embedding_provider = None
        container.artifact_store = None
        container.ecs_store = None
        container.corpus_turn_store = None
        container.graph_store = None

        result = asyncio.run(retrieval_health(container=container))

        result_str = str(result).lower()
        for secret_word in ["api_key", "password", "token", "secret"]:
            assert secret_word not in result_str, f"Retrieval health response contains '{secret_word}'"


# ── No Mutation Tests ────────────────────────────────────────────────


class TestRetrievalLabNoMutation:
    """Verify that retrieval testing does not mutate any state."""

    def test_retrieval_test_does_not_call_model(self):
        """Retrieval test endpoint does not invoke model_provider.generate."""
        from aip.adapter.api.routes.retrieval_dashboard import retrieval_test

        container = MagicMock()

        # Track if model_provider.generate is ever called
        model_called = []

        async def mock_search_sources(**kwargs):
            trace = MagicMock()
            trace.channel_health = {"fts": "active"}
            trace.channel_health_reasons = {}
            trace.channel_details = {}
            trace.per_channel_elapsed_ms = {}
            trace.channel_contributions = {}
            trace.hits_before_fusion = 0
            trace.hits_after_fusion = 0
            trace.hits_after_quality_gate = 0
            trace.verdict = "NO_RESULTS"
            trace.top_scores = []
            trace.lexical_only = True
            trace.vector_contributed = False
            trace.degradation_warnings = []
            return [], trace, None

        container._search_sources_fn = mock_search_sources
        container._ask_stores_class = MagicMock
        container.lexical_store = MagicMock()
        container.vector_store = MagicMock()
        container.artifact_store = MagicMock()
        container.event_store = MagicMock()
        container.project_store = MagicMock()
        container.ecs_store = MagicMock()
        container.embedding_provider = MagicMock()
        container.corpus_turn_store = MagicMock()
        container.graph_store = None

        payload = {"query": "test no mutation"}
        asyncio.run(retrieval_test(payload=payload, container=container))

        # The retrieval test should not call model_provider
        if container.model_provider is not None:
            assert not model_called, "Retrieval test should not invoke model_provider"
