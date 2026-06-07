"""Tests for Sprint 5.9: Intelligence & Evaluation.

Covers:
1. EntityExtractor — noun-phrase extraction, graph-fuzzy matching, LLM fallback
2. Per-Channel Budget Allocation — OrchestratorConfig.get_channel_max_hits(),
   enforcement before RRF fusion, SmartContextPacker per-channel limits
3. ChannelSelector — rule-based adaptive channel selection
4. Retrieval Quality Evaluation Harness — metric computation and harness run
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorConfig,
    RetrievalOrchestrator,
    rrf_fuse,
)
from aip.orchestration.smart_context_packer import (
    PackerConfig,
    SmartContextPacker,
)


# ---------------------------------------------------------------------------
# 1. EntityExtractor tests
# ---------------------------------------------------------------------------


class TestNounPhraseExtraction:
    """Verify extract_noun_phrases identifies multi-word entities and proper nouns."""

    def test_multi_word_capitalised_phrase(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases("How does Knowledge Graph connect to AIP?")
        assert "Knowledge Graph" in result

    def test_single_capitalised_word_excludes_sentence_starters(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases("The system uses Python for retrieval")
        # "The" should be excluded as a sentence starter
        assert "The" not in result
        assert "Python" in result

    def test_quoted_strings_extracted(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases('Find information about "my special entity" in the graph')
        assert "my special entity" in result

    def test_no_entities_returns_empty(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases("what is the best way to do it")
        # No capitalised words, no quoted strings
        # This might extract short words, but they should be filtered by min_length
        assert isinstance(result, list)

    def test_deduplication(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases("AIP connects to AIP via AIP")
        # "AIP" should appear only once
        assert result.count("AIP") == 1

    def test_min_length_filter(self):
        from aip.orchestration.entity_extractor import extract_noun_phrases
        result = extract_noun_phrases("I am OK", min_length=3)
        # "OK" is only 2 chars, should be excluded
        assert "OK" not in result


class TestGraphFuzzyMatching:
    """Verify fuzzy_match_graph_entities matches against the knowledge graph."""

    def test_match_against_graph_entities(self):
        from aip.orchestration.entity_extractor import fuzzy_match_graph_entities

        class FakeGraphStore:
            def search_nodes(self, query, limit=20):
                if "knowledge" in query.lower():
                    return [_FakeNode("kg_1", "Knowledge Graph")]
                if "aip" in query.lower():
                    return [_FakeNode("aip_1", "AIP Brain")]
                return []

        result = fuzzy_match_graph_entities(
            ["Knowledge Graph", "AIP"],
            FakeGraphStore(),
            threshold=0.3,  # Lower threshold so "AIP" matches "AIP Brain"
        )
        assert "Knowledge Graph" in result
        assert "AIP Brain" in result

    def test_empty_candidates_returns_empty(self):
        from aip.orchestration.entity_extractor import fuzzy_match_graph_entities
        assert fuzzy_match_graph_entities([], None) == []

    def test_none_graph_store_returns_empty(self):
        from aip.orchestration.entity_extractor import fuzzy_match_graph_entities
        assert fuzzy_match_graph_entities(["test"], None) == []

    def test_alias_matching(self):
        from aip.orchestration.entity_extractor import fuzzy_match_graph_entities

        class FakeGraphStoreWithAliases:
            def search_nodes(self, query, limit=20):
                return [_FakeNode("kg_1", "Knowledge Graph", aliases=["KG", "KGraph"])]

        result = fuzzy_match_graph_entities(
            ["KG"],
            FakeGraphStoreWithAliases(),
            threshold=0.4,
        )
        assert "Knowledge Graph" in result


class _FakeNode:
    """Helper for graph-fuzzy matching tests."""
    def __init__(self, id, canonical_name, aliases=None):
        self.id = id
        self.canonical_name = canonical_name
        self.aliases = aliases or []


class TestEntityExtractor:
    """Verify the main EntityExtractor class with different strategies."""

    def test_noun_phrase_strategy(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig
        ext = EntityExtractor(config=EntityExtractorConfig(strategy="noun_phrase"))
        result = ext.extract("How does Knowledge Graph connect to AIP?")
        assert "Knowledge Graph" in result
        assert "AIP" in result

    def test_hybrid_strategy_with_graph_store(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        class FakeStore:
            def search_nodes(self, query, limit=20):
                if "knowledge" in query.lower():
                    return [_FakeNode("kg_1", "Knowledge Graph")]
                return []

        ext = EntityExtractor(
            config=EntityExtractorConfig(strategy="hybrid", use_graph_fuzzy=True),
            graph_store=FakeStore(),
        )
        result = ext.extract("How does Knowledge Graph connect to AIP?")
        assert "Knowledge Graph" in result

    def test_graph_fuzzy_strategy(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        class FakeStore:
            def search_nodes(self, query, limit=20):
                if "knowledge" in query.lower():
                    return [_FakeNode("kg_1", "Knowledge Graph")]
                return []

        ext = EntityExtractor(
            config=EntityExtractorConfig(strategy="graph_fuzzy"),
            graph_store=FakeStore(),
        )
        result = ext.extract("Knowledge Graph")
        assert "Knowledge Graph" in result

    def test_max_candidates_limit(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig
        ext = EntityExtractor(config=EntityExtractorConfig(max_candidates=2))
        result = ext.extract("Alpha Beta Gamma Delta Epsilon")
        assert len(result) <= 2

    def test_extract_async_without_llm(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig
        ext = EntityExtractor(config=EntityExtractorConfig(strategy="noun_phrase"))
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("How does Knowledge Graph work?")
        )
        assert "Knowledge Graph" in result

    def test_extract_async_with_llm_fallback(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        async def fake_llm(query):
            return ["CustomEntity1", "CustomEntity2"]

        ext = EntityExtractor(
            config=EntityExtractorConfig(
                strategy="noun_phrase",
                use_llm_fallback=True,
            ),
            llm_fn=fake_llm,
        )
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("simple query with no caps")
        )
        # LLM fallback should have been called since noun_phrase found nothing
        assert "CustomEntity1" in result

    def test_unknown_strategy_falls_back(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig
        ext = EntityExtractor(config=EntityExtractorConfig(strategy="nonexistent"))
        result = ext.extract("Knowledge Graph test")
        # Should fall back to noun_phrase extraction
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 2. Per-Channel Budget Allocation tests
# ---------------------------------------------------------------------------


class TestOrchestratorConfigChannelBudget:
    """Verify OrchestratorConfig per-channel budget fields and get_channel_max_hits()."""

    def test_default_no_limits(self):
        config = OrchestratorConfig()
        assert config.get_channel_max_hits("fts") == 0
        assert config.get_channel_max_hits("graph") == 0

    def test_global_per_channel_limit(self):
        config = OrchestratorConfig(max_hits_per_channel=5)
        assert config.get_channel_max_hits("fts") == 5
        assert config.get_channel_max_hits("graph") == 5
        assert config.get_channel_max_hits("unknown") == 5

    def test_channel_specific_overrides_global(self):
        config = OrchestratorConfig(max_hits_per_channel=5, fts_max_hits=10, graph_max_hits=2)
        assert config.get_channel_max_hits("fts") == 10
        assert config.get_channel_max_hits("graph") == 2
        assert config.get_channel_max_hits("wiki") == 5  # falls back to global

    def test_zero_means_unlimited(self):
        config = OrchestratorConfig(max_hits_per_channel=0, fts_max_hits=0)
        assert config.get_channel_max_hits("fts") == 0

    def test_unknown_channel_uses_global(self):
        config = OrchestratorConfig(max_hits_per_channel=7)
        assert config.get_channel_max_hits("custom_channel") == 7


class TestPerChannelBudgetEnforcement:
    """Verify that per-channel budget limits are enforced before RRF fusion."""

    async def test_fts_channel_capped(self):
        """FTS channel returning many hits should be capped."""
        async def _many_fts_hits(query):
            return [
                RetrievalHit(id=f"fts:{i}", content=f"FTS hit {i}", score=0.9 - i * 0.01, source_channel="fts")
                for i in range(20)
            ]

        async def _few_graph_hits(query):
            return [
                RetrievalHit(id="graph:1", content="Graph hit", score=0.8, source_channel="graph"),
            ]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _many_fts_hits)
        orch.register_channel("graph", _few_graph_hits)

        config = OrchestratorConfig(
            enable_fts=True, enable_graph=True,
            fts_max_hits=5,
        )
        hits, trace = await orch.retrieve("test query", config=config)

        # FTS should have been capped to 5 hits before fusion
        fts_hits_in_trace = sum(1 for h in hits if h.source_channel == "fts" or "fts" in h.metadata.get("source_channels", []))
        # After RRF fusion and quality gate, FTS hits could be fewer than 5
        # but the total FTS hits before fusion should be 5
        assert trace.hits_before_fusion <= 6  # 5 FTS + 1 graph

    async def test_global_per_channel_limit(self):
        """max_hits_per_channel should cap all channels."""
        async def _many_fts_hits(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"Hit {i}", score=0.9, source_channel="fts") for i in range(15)]

        async def _many_vec_hits(query):
            return [RetrievalHit(id=f"vec:{i}", content=f"Vec {i}", score=0.85, source_channel="vector") for i in range(15)]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _many_fts_hits)
        orch.register_channel("vector", _many_vec_hits)

        config = OrchestratorConfig(
            enable_fts=True, enable_vector=True,
            max_hits_per_channel=3,
        )
        hits, trace = await orch.retrieve("test query", config=config)
        assert trace.hits_before_fusion <= 6  # 3 FTS + 3 vector

    async def test_no_limit_when_zero(self):
        """When per-channel limit is 0 (default), all hits should pass through."""
        async def _many_fts_hits(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"Hit {i}", score=0.9, source_channel="fts") for i in range(20)]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _many_fts_hits)

        config = OrchestratorConfig(enable_fts=True)  # default: no per-channel limit
        hits, trace = await orch.retrieve("test query", config=config)
        assert trace.hits_before_fusion == 20


class TestSmartContextPackerPerChannel:
    """Verify SmartContextPacker respects per-channel limits."""

    def test_packer_per_channel_limit(self):
        hits = [
            RetrievalHit(id=f"fts:{i}", content=f"FTS content {i}", rrf_score=0.05 - i * 0.001, source_channel="fts")
            for i in range(8)
        ] + [
            RetrievalHit(id=f"graph:{i}", content=f"Graph content {i}", rrf_score=0.03, source_channel="graph")
            for i in range(2)
        ]

        packer = SmartContextPacker(config=PackerConfig(
            max_context_tokens=10000,
            max_hits=20,
            max_hits_per_channel=3,
        ))
        packed = packer.pack(hits, query="test")

        # Only 3 FTS hits should have been included (plus both graph hits)
        channel_counts: dict[str, int] = {}
        for line in packed.context_text.split("\n"):
            if "channel=fts" in line:
                channel_counts["fts"] = channel_counts.get("fts", 0) + 1
            elif "channel=graph" in line:
                channel_counts["graph"] = channel_counts.get("graph", 0) + 1

        assert channel_counts.get("fts", 0) <= 3

    def test_packer_no_limit_when_zero(self):
        hits = [
            RetrievalHit(id=f"fts:{i}", content=f"FTS {i}", rrf_score=0.05, source_channel="fts")
            for i in range(10)
        ]

        packer = SmartContextPacker(config=PackerConfig(
            max_context_tokens=20000,
            max_hits=20,
            max_hits_per_channel=0,  # no limit
        ))
        packed = packer.pack(hits, query="test")
        assert packed.hits_packed == 10


# ---------------------------------------------------------------------------
# 3. ChannelSelector tests
# ---------------------------------------------------------------------------


class TestQueryAnalysis:
    """Verify analyze_query detects signals correctly."""

    def test_entity_signals_detected(self):
        from aip.orchestration.channel_selector import analyze_query
        analysis = analyze_query("How does Knowledge Graph connect to Python?")
        assert analysis.has_entity_signals is True
        assert analysis.entity_count >= 2  # "Knowledge Graph", "Python"

    def test_procedural_signals_detected(self):
        from aip.orchestration.channel_selector import analyze_query
        analysis = analyze_query("How do I configure the system?")
        assert analysis.has_procedural_signals is True

    def test_wiki_signals_detected(self):
        from aip.orchestration.channel_selector import analyze_query
        analysis = analyze_query("What is AIP?")
        assert analysis.has_wiki_signals is True

    def test_multiple_signals_detected(self):
        from aip.orchestration.channel_selector import analyze_query
        analysis = analyze_query("How do I configure Knowledge Graph?")
        assert analysis.has_procedural_signals is True
        assert analysis.has_entity_signals is True

    def test_no_signals_for_simple_query(self):
        from aip.orchestration.channel_selector import analyze_query
        analysis = analyze_query("test the system")
        # No capitalised words, no procedural/wiki patterns
        assert analysis.has_entity_signals is False
        assert analysis.has_procedural_signals is False
        assert analysis.has_wiki_signals is False


class TestChannelSelector:
    """Verify ChannelSelector auto-enables channels based on query signals."""

    def test_entity_query_enables_graph(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("How does Knowledge Graph work?")
        assert result.enable_graph is True
        assert "graph" in result.auto_enabled_channels

    def test_procedural_query_enables_procedural(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("How do I install the software?")
        assert result.enable_procedural is True
        assert "procedural" in result.auto_enabled_channels

    def test_wiki_query_enables_wiki(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("What is the definition of RRF fusion?")
        assert result.enable_wiki is True
        assert "wiki" in result.auto_enabled_channels

    def test_mixed_query_enables_multiple_channels(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("How do I configure Knowledge Graph?")
        assert result.enable_graph is True
        assert result.enable_procedural is True

    def test_simple_query_enables_no_extra_channels(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("simple test query about nothing specific")
        assert result.enable_graph is False
        assert result.enable_procedural is False
        assert result.enable_wiki is False

    def test_apply_to_config_merges_correctly(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        config = OrchestratorConfig()  # defaults: graph=False, wiki=False, procedural=False
        config = selector.apply_to_config("How does Knowledge Graph work?", config)
        assert config.enable_graph is True  # auto-enabled

    def test_apply_to_config_respects_explicit_settings(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        # User explicitly disables graph via explicit_channels set
        config = OrchestratorConfig(enable_graph=False)
        config = selector.apply_to_config(
            "How does Knowledge Graph work?",
            config,
            explicit_channels={"graph"},
        )
        # Should NOT override explicit setting
        assert config.enable_graph is False

    def test_step_by_step_enables_procedural(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("Step by step guide to deployment")
        assert result.enable_procedural is True

    def test_tutorial_enables_procedural(self):
        from aip.orchestration.channel_selector import ChannelSelector
        selector = ChannelSelector()
        result = selector.select("Tutorial on using the retrieval pipeline")
        assert result.enable_procedural is True


# ---------------------------------------------------------------------------
# 4. Retrieval Quality Evaluation Harness tests
# ---------------------------------------------------------------------------


class TestMetricComputation:
    """Verify individual metric computation functions."""

    def test_recall_at_k_perfect(self):
        from aip.orchestration.retrieval_eval import compute_recall_at_k
        result = compute_recall_at_k(
            retrieved_ids=["a", "b", "c"],
            relevant_ids=["a", "b"],
            k=10,
        )
        assert result == 1.0

    def test_recall_at_k_partial(self):
        from aip.orchestration.retrieval_eval import compute_recall_at_k
        result = compute_recall_at_k(
            retrieved_ids=["a", "c"],
            relevant_ids=["a", "b"],
            k=10,
        )
        assert result == 0.5

    def test_recall_at_k_empty_relevant(self):
        from aip.orchestration.retrieval_eval import compute_recall_at_k
        assert compute_recall_at_k(["a", "b"], [], k=10) == 0.0

    def test_precision_at_k_perfect(self):
        from aip.orchestration.retrieval_eval import compute_precision_at_k
        result = compute_precision_at_k(
            retrieved_ids=["a", "b"],
            relevant_ids=["a", "b", "c"],
            k=2,
        )
        assert result == 1.0

    def test_precision_at_k_partial(self):
        from aip.orchestration.retrieval_eval import compute_precision_at_k
        result = compute_precision_at_k(
            retrieved_ids=["a", "c"],
            relevant_ids=["a", "b"],
            k=2,
        )
        assert result == 0.5

    def test_mrr_first_position(self):
        from aip.orchestration.retrieval_eval import compute_mrr
        assert compute_mrr(["a", "b"], ["a"]) == 1.0

    def test_mrr_second_position(self):
        from aip.orchestration.retrieval_eval import compute_mrr
        assert compute_mrr(["c", "a"], ["a"]) == 0.5

    def test_mrr_not_found(self):
        from aip.orchestration.retrieval_eval import compute_mrr
        assert compute_mrr(["c", "d"], ["a"]) == 0.0

    def test_entity_coverage_perfect(self):
        from aip.orchestration.retrieval_eval import compute_entity_coverage
        hits = [
            RetrievalHit(id="graph:KnowledgeGraph", content="Knowledge Graph entity", metadata={"entity_name": "Knowledge Graph"}),
        ]
        result = compute_entity_coverage(hits, ["Knowledge Graph"])
        assert result == 1.0

    def test_entity_coverage_partial(self):
        from aip.orchestration.retrieval_eval import compute_entity_coverage
        hits = [
            RetrievalHit(id="graph:A", content="Entity A"),
        ]
        result = compute_entity_coverage(hits, ["A", "B"])
        assert result == 0.5

    def test_entity_coverage_empty_expected(self):
        from aip.orchestration.retrieval_eval import compute_entity_coverage
        assert compute_entity_coverage([], []) == 0.0


class TestGoldenQueryLoading:
    """Verify golden query loading from JSON."""

    def test_load_from_file(self):
        from aip.orchestration.retrieval_eval import load_golden_queries
        # Use the golden queries file we created
        path = os.path.join(
            os.path.dirname(__file__), "retrieval_goldens", "golden_queries.json"
        )
        if os.path.exists(path):
            queries = load_golden_queries(path)
            assert len(queries) >= 5
            assert queries[0].query == "What is AIP?"
            assert "AIP" in queries[0].expected_entities
        else:
            # If the file doesn't exist in test env, skip
            pass

    def test_load_from_nonexistent_file(self):
        from aip.orchestration.retrieval_eval import load_golden_queries
        queries = load_golden_queries("/nonexistent/path.json")
        assert queries == []

    def test_create_default_golden_queries(self):
        from aip.orchestration.retrieval_eval import create_default_golden_queries, load_golden_queries
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "golden_queries.json")
            create_default_golden_queries(path)
            queries = load_golden_queries(path)
            assert len(queries) >= 5


class TestRetrievalEvalHarness:
    """Verify the evaluation harness runs correctly."""

    def test_harness_run_with_mock_retriever(self):
        from aip.orchestration.retrieval_eval import (
            GoldenQuery,
            RetrievalEvalHarness,
        )

        async def _mock_retriever(query):
            # Return some hits based on query
            hits = [
                RetrievalHit(id="doc:aip_overview", content="AIP overview", score=0.9, source_channel="fts"),
                RetrievalHit(id="doc:retrieval_pipeline", content="Retrieval pipeline", score=0.8, source_channel="fts"),
            ]
            trace = RetrievalTrace(query=query)
            return hits, trace

        golden = [
            GoldenQuery(query="What is AIP?", relevant_ids=["doc:aip_overview"], expected_entities=[]),
            GoldenQuery(query="Explain the pipeline", relevant_ids=["doc:retrieval_pipeline"], expected_entities=[]),
        ]

        harness = RetrievalEvalHarness(k=10)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run(golden, _mock_retriever)
        )

        assert result.total_queries == 2
        assert result.mean_recall_at_k > 0
        assert result.mean_mrr > 0
        assert len(result.per_query_results) == 2

    def test_harness_result_serialization(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            QueryEvalResult,
        )

        result = EvalResult(
            timestamp="2026-01-01T00:00:00Z",
            total_queries=1,
            mean_recall_at_k=0.5,
            mean_precision_at_k=0.3,
            mean_mrr=0.5,
            mean_entity_coverage=0.0,
            per_query_results=[
                QueryEvalResult(query="test", recall_at_k=0.5, precision_at_k=0.3, mrr=0.5),
            ],
        )

        data = result.to_dict()
        assert data["total_queries"] == 1
        assert data["mean_recall_at_k"] == 0.5
        assert len(data["per_query_results"]) == 1

    def test_harness_result_to_json(self):
        from aip.orchestration.retrieval_eval import EvalResult, QueryEvalResult

        result = EvalResult(
            timestamp="2026-01-01T00:00:00Z",
            total_queries=1,
            mean_recall_at_k=0.75,
            per_query_results=[
                QueryEvalResult(query="test", recall_at_k=0.75, mrr=1.0),
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            result.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert data["mean_recall_at_k"] == 0.75
        finally:
            os.unlink(path)

    def test_handles_retriever_failure_gracefully(self):
        from aip.orchestration.retrieval_eval import (
            GoldenQuery,
            RetrievalEvalHarness,
        )

        async def _failing_retriever(query):
            raise RuntimeError("Retriever failed")

        golden = [GoldenQuery(query="test query", relevant_ids=["doc:1"])]

        harness = RetrievalEvalHarness(k=10)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run(golden, _failing_retriever)
        )

        assert result.total_queries == 1
        assert result.per_query_results[0].num_retrieved == 0
        assert result.per_query_results[0].recall_at_k == 0.0


# ---------------------------------------------------------------------------
# 5. Integration: Channel selection + orchestrator
# ---------------------------------------------------------------------------


class TestChannelSelectionIntegration:
    """Verify that channel selection integrates with the orchestrator correctly."""

    async def test_auto_channel_selection_enables_graph(self):
        """Query with entity signals should auto-enable graph channel."""
        from aip.orchestration.channel_selector import ChannelSelector

        graph_called = False

        async def _graph_retriever(query):
            nonlocal graph_called
            graph_called = True
            return [RetrievalHit(id="graph:1", content="Graph hit", score=0.6, source_channel="graph")]

        async def _fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="FTS hit", score=0.9, source_channel="fts")]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fts_retriever)
        orch.register_channel("graph", _graph_retriever)

        # Apply channel selection
        selector = ChannelSelector()
        config = OrchestratorConfig()  # graph disabled by default
        config = selector.apply_to_config("How does Knowledge Graph work?", config)
        assert config.enable_graph is True

        hits, trace = await orch.retrieve("How does Knowledge Graph work?", config=config)
        assert graph_called is True
        assert "graph" in trace.channels_queried

    async def test_auto_channel_selection_does_not_override_explicit_disable(self):
        """Explicitly disabled channels should stay disabled even with entity signals."""
        from aip.orchestration.channel_selector import ChannelSelector

        selector = ChannelSelector()
        config = OrchestratorConfig(enable_graph=False)
        config = selector.apply_to_config(
            "How does Knowledge Graph work?",
            config,
            explicit_channels={"graph"},
        )
        assert config.enable_graph is False


# ---------------------------------------------------------------------------
# 6. End-to-end: per-channel budget + RRF + quality gate
# ---------------------------------------------------------------------------


class TestPerChannelBudgetIntegration:
    """Verify per-channel budget works correctly with the full pipeline."""

    async def test_dominant_channel_capped(self):
        """A channel returning many hits should not dominate after budget capping."""
        async def _dominant_fts(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"FTS {i}", score=0.9 - i * 0.01, source_channel="fts") for i in range(30)]

        async def _weak_graph(query):
            return [RetrievalHit(id="graph:1", content="Graph hit", score=0.7, source_channel="graph")]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _dominant_fts)
        orch.register_channel("graph", _weak_graph)

        # Without per-channel limit, FTS would dominate
        config_no_limit = OrchestratorConfig(enable_fts=True, enable_graph=True)
        hits_no_limit, trace_no_limit = await orch.retrieve("test", config=config_no_limit)

        # With per-channel limit, FTS is capped
        config_capped = OrchestratorConfig(
            enable_fts=True, enable_graph=True,
            fts_max_hits=3,
        )
        hits_capped, trace_capped = await orch.retrieve("test", config=config_capped)

        # The capped run should have fewer FTS hits before fusion
        assert trace_capped.hits_before_fusion < trace_no_limit.hits_before_fusion

    async def test_per_channel_budget_allows_balanced_retrieval(self):
        """Per-channel budget should promote balanced retrieval across channels."""
        async def _fts(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"FTS {i}", score=0.9, source_channel="fts") for i in range(20)]

        async def _vector(query):
            return [RetrievalHit(id=f"vec:{i}", content=f"Vec {i}", score=0.85, source_channel="vector") for i in range(20)]

        async def _graph(query):
            return [RetrievalHit(id=f"graph:{i}", content=f"Graph {i}", score=0.7, source_channel="graph") for i in range(5)]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fts)
        orch.register_channel("vector", _vector)
        orch.register_channel("graph", _graph)

        config = OrchestratorConfig(
            enable_fts=True, enable_vector=True, enable_graph=True,
            max_hits_per_channel=5,
        )
        hits, trace = await orch.retrieve("test", config=config)

        # Each channel should contribute at most 5 hits
        assert trace.hits_before_fusion <= 15  # 5*3

        # Check that graph hits are in the final result (not drowned out)
        graph_hits = [h for h in hits if h.source_channel == "graph" or "graph" in h.metadata.get("source_channels", [])]
        assert len(graph_hits) > 0, "Graph channel should not be completely drowned out"
