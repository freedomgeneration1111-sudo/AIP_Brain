"""Tests for Sprint 5.10: Evaluation-Driven Improvement.

Covers:
1. CLI eval command structure
2. LLM entity extraction integration (create_llm_entity_fn, config modes)
3. Evaluation regression protection (compare_against_baseline)
4. Channel contribution tracking in RetrievalTrace
5. EvalResult formatting and timestamped saving
6. LLM response parsing
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
)


# ---------------------------------------------------------------------------
# 1. Channel Contribution Tracking
# ---------------------------------------------------------------------------


class TestChannelContributions:
    """Verify that RetrievalTrace captures channel contribution data."""

    async def test_channel_contributions_populated(self):
        """Channel contributions should be recorded after retrieval."""
        async def _fts_retriever(query):
            return [
                RetrievalHit(id="fts:1", content="FTS hit 1", score=0.9, source_channel="fts"),
                RetrievalHit(id="fts:2", content="FTS hit 2", score=0.8, source_channel="fts"),
            ]

        async def _graph_retriever(query):
            return [
                RetrievalHit(id="graph:1", content="Graph hit 1", score=0.7, source_channel="graph"),
            ]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fts_retriever)
        orch.register_channel("graph", _graph_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_graph=True)
        hits, trace = await orch.retrieve("test query", config=config)

        # Channel contributions should be populated
        assert isinstance(trace.channel_contributions, dict)
        assert "fts" in trace.channel_contributions
        assert trace.channel_contributions["fts"] >= 1
        assert "graph" in trace.channel_contributions
        assert trace.channel_contributions["graph"] >= 1

    async def test_per_channel_hit_counts_populated(self):
        """Per-channel hit counts should be recorded before fusion."""
        async def _fts_retriever(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"Hit {i}", score=0.9, source_channel="fts") for i in range(5)]

        async def _vector_retriever(query):
            return [RetrievalHit(id=f"vec:{i}", content=f"Vec {i}", score=0.85, source_channel="vector") for i in range(3)]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fts_retriever)
        orch.register_channel("vector", _vector_retriever)

        config = OrchestratorConfig(enable_fts=True, enable_vector=True)
        hits, trace = await orch.retrieve("test query", config=config)

        assert isinstance(trace.per_channel_hit_counts, dict)
        assert trace.per_channel_hit_counts.get("fts") == 5
        assert trace.per_channel_hit_counts.get("vector") == 3

    async def test_channel_contributions_with_budget_limit(self):
        """Channel contributions should reflect budget-capped results."""
        async def _dominant_fts(query):
            return [RetrievalHit(id=f"fts:{i}", content=f"FTS {i}", score=0.9, source_channel="fts") for i in range(20)]

        async def _weak_graph(query):
            return [RetrievalHit(id="graph:1", content="Graph", score=0.5, source_channel="graph")]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _dominant_fts)
        orch.register_channel("graph", _weak_graph)

        config = OrchestratorConfig(
            enable_fts=True, enable_graph=True,
            fts_max_hits=3,
        )
        hits, trace = await orch.retrieve("test", config=config)

        # FTS should be capped at 3 before fusion
        assert trace.per_channel_hit_counts.get("fts", 0) <= 3
        assert trace.hits_before_fusion <= 4  # 3 FTS + 1 graph

    async def test_empty_channels_no_contributions(self):
        """When channels return no hits, contributions should be empty or zero."""
        async def _empty_fts(query):
            return []

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _empty_fts)

        config = OrchestratorConfig(enable_fts=True)
        hits, trace = await orch.retrieve("test", config=config)

        # With no hits, channel_contributions may be empty
        assert isinstance(trace.channel_contributions, dict)


# ---------------------------------------------------------------------------
# 2. LLM Entity Extraction Integration
# ---------------------------------------------------------------------------


class TestLLMEntityFn:
    """Verify create_llm_entity_fn creates a working LLM extraction callable."""

    def test_create_llm_entity_fn_with_mock_provider(self):
        from aip.orchestration.entity_extractor import create_llm_entity_fn

        class MockModelProvider:
            async def call(self, slot_name, messages, **kwargs):
                return {
                    "content": '["KnowledgeGraph", "AIP", "PersonalizedPageRank"]',
                    "model": "mock",
                }

        llm_fn = create_llm_entity_fn(MockModelProvider(), slot_name="fast")
        result = asyncio.get_event_loop().run_until_complete(
            llm_fn("How does Knowledge Graph connect to AIP?")
        )

        assert isinstance(result, list)
        assert "KnowledgeGraph" in result
        assert "AIP" in result

    def test_create_llm_entity_fn_graceful_degradation(self):
        """When model call fails, should return empty list."""
        from aip.orchestration.entity_extractor import create_llm_entity_fn

        class FailingModelProvider:
            async def call(self, slot_name, messages, **kwargs):
                raise RuntimeError("Model unavailable")

        llm_fn = create_llm_entity_fn(FailingModelProvider(), slot_name="fast")
        result = asyncio.get_event_loop().run_until_complete(
            llm_fn("test query")
        )

        assert result == []  # graceful degradation

    def test_create_llm_entity_fn_with_error_response(self):
        """When model returns error, should try fallback slot."""
        from aip.orchestration.entity_extractor import create_llm_entity_fn

        class PartialFailProvider:
            async def call(self, slot_name, messages, **kwargs):
                if slot_name == "fast":
                    return {"error": True, "error_message": "fast slot not configured"}
                return {
                    "content": '["Entity1", "Entity2"]',
                    "model": "synthesis",
                }

        llm_fn = create_llm_entity_fn(PartialFailProvider(), slot_name="fast")
        result = asyncio.get_event_loop().run_until_complete(
            llm_fn("test query")
        )

        assert isinstance(result, list)
        assert "Entity1" in result


class TestLLMResponseParsing:
    """Verify _parse_llm_entity_response handles various LLM output formats."""

    def test_parse_json_array(self):
        from aip.orchestration.entity_extractor import _parse_llm_entity_response
        result = _parse_llm_entity_response('["Knowledge Graph", "AIP"]')
        assert "Knowledge Graph" in result
        assert "AIP" in result

    def test_parse_markdown_json(self):
        from aip.orchestration.entity_extractor import _parse_llm_entity_response
        content = '```json\n["Entity1", "Entity2"]\n```'
        result = _parse_llm_entity_response(content)
        assert "Entity1" in result

    def test_parse_embedded_array(self):
        from aip.orchestration.entity_extractor import _parse_llm_entity_response
        content = 'Here are the entities: ["Alpha", "Beta"] and more text'
        result = _parse_llm_entity_response(content)
        assert "Alpha" in result

    def test_parse_comma_separated(self):
        from aip.orchestration.entity_extractor import _parse_llm_entity_response
        content = "Knowledge Graph, AIP, PageRank"
        result = _parse_llm_entity_response(content)
        assert "Knowledge Graph" in result

    def test_parse_empty_returns_empty(self):
        from aip.orchestration.entity_extractor import _parse_llm_entity_response
        result = _parse_llm_entity_response("")
        assert result == []


class TestEntityExtractorModes:
    """Verify EntityExtractor respects entity_extraction_mode config."""

    def test_hybrid_llm_mode_triggers_fallback(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        async def fake_llm(query):
            return ["LLMEntity1", "LLMEntity2"]

        # When mode is hybrid_llm and local extraction finds < threshold entities
        ext = EntityExtractor(
            config=EntityExtractorConfig(
                strategy="noun_phrase",
                entity_extraction_mode="hybrid_llm",
                llm_fallback_threshold=2,
            ),
            llm_fn=fake_llm,
        )
        # "simple query" has no capitalized words → local finds 0 entities
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("simple query with no caps")
        )
        assert "LLMEntity1" in result

    def test_llm_primary_mode_uses_llm_first(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        async def fake_llm(query):
            return ["PrimaryEntity1", "PrimaryEntity2"]

        ext = EntityExtractor(
            config=EntityExtractorConfig(
                entity_extraction_mode="llm_primary",
            ),
            llm_fn=fake_llm,
        )
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("How does Knowledge Graph work?")
        )
        # LLM primary should return LLM entities directly
        assert "PrimaryEntity1" in result

    def test_llm_primary_falls_back_to_local(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        async def failing_llm(query):
            raise RuntimeError("LLM unavailable")

        ext = EntityExtractor(
            config=EntityExtractorConfig(
                entity_extraction_mode="llm_primary",
                strategy="noun_phrase",
            ),
            llm_fn=failing_llm,
        )
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("How does Knowledge Graph work?")
        )
        # Should fall back to noun_phrase extraction
        assert "Knowledge Graph" in result

    def test_local_mode_never_calls_llm(self):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        llm_called = False

        async def tracking_llm(query):
            nonlocal llm_called
            llm_called = True
            return ["LLMEntity"]

        ext = EntityExtractor(
            config=EntityExtractorConfig(
                entity_extraction_mode="local",
            ),
            llm_fn=tracking_llm,
        )
        result = asyncio.get_event_loop().run_until_complete(
            ext.extract_async("simple query")
        )
        assert llm_called is False


# ---------------------------------------------------------------------------
# 3. Evaluation Regression Protection
# ---------------------------------------------------------------------------


class TestRegressionProtection:
    """Verify compare_against_baseline detects regressions."""

    def test_no_regression_when_improved(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            QueryEvalResult,
            compare_against_baseline,
        )

        current = EvalResult(
            timestamp="2026-06-07T00:00:00Z",
            total_queries=5,
            mean_recall_at_k=0.8,
            mean_mrr=0.7,
            mean_precision_at_k=0.6,
            mean_entity_coverage=0.5,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "mean_recall_at_k": 0.7,
                "mean_mrr": 0.6,
                "mean_precision_at_k": 0.5,
                "mean_entity_coverage": 0.4,
            }, f)
            baseline_path = f.name

        try:
            result = compare_against_baseline(current, baseline_path)
            assert result.passed is True
            assert len(result.failures) == 0
        finally:
            os.unlink(baseline_path)

    def test_warning_on_minor_regression(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            compare_against_baseline,
        )

        current = EvalResult(
            mean_recall_at_k=0.65,  # 7.1% drop from 0.7
            mean_mrr=0.7,
            mean_precision_at_k=0.6,
            mean_entity_coverage=0.5,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "mean_recall_at_k": 0.7,
                "mean_mrr": 0.7,
                "mean_precision_at_k": 0.6,
                "mean_entity_coverage": 0.5,
            }, f)
            baseline_path = f.name

        try:
            result = compare_against_baseline(current, baseline_path, warn_threshold=0.05)
            assert len(result.warnings) >= 1
            # 7% drop > 5% warn threshold but < 15% fail threshold
            assert result.passed is True
        finally:
            os.unlink(baseline_path)

    def test_failure_on_major_regression(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            compare_against_baseline,
        )

        current = EvalResult(
            mean_recall_at_k=0.5,  # 28.6% drop from 0.7
            mean_mrr=0.7,
            mean_precision_at_k=0.6,
            mean_entity_coverage=0.5,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "mean_recall_at_k": 0.7,
                "mean_mrr": 0.7,
                "mean_precision_at_k": 0.6,
                "mean_entity_coverage": 0.5,
            }, f)
            baseline_path = f.name

        try:
            result = compare_against_baseline(current, baseline_path, fail_threshold=0.15)
            assert result.passed is False
            assert len(result.failures) >= 1
        finally:
            os.unlink(baseline_path)

    def test_missing_baseline_gives_warning(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            compare_against_baseline,
        )

        current = EvalResult(mean_recall_at_k=0.5)
        result = compare_against_baseline(current, "/nonexistent/baseline.json")

        assert result.passed is True  # No regression since no baseline
        assert len(result.warnings) >= 1

    def test_regression_report_format(self):
        from aip.orchestration.retrieval_eval import (
            EvalResult,
            RegressionCheckResult,
            compare_against_baseline,
        )

        current = EvalResult(mean_recall_at_k=0.8, mean_mrr=0.6)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "mean_recall_at_k": 0.7,
                "mean_mrr": 0.7,
                "mean_precision_at_k": 0.5,
                "mean_entity_coverage": 0.4,
            }, f)
            baseline_path = f.name

        try:
            result = compare_against_baseline(current, baseline_path)
            report = result.format_report()
            assert "Regression Check Report" in report
            assert "mean_recall_at_k" in report
        finally:
            os.unlink(baseline_path)


# ---------------------------------------------------------------------------
# 4. EvalResult Formatting and Saving
# ---------------------------------------------------------------------------


class TestEvalResultFormatting:
    """Verify EvalResult.format_human_summary() produces readable output."""

    def test_format_human_summary_basic(self):
        from aip.orchestration.retrieval_eval import EvalResult, QueryEvalResult

        result = EvalResult(
            timestamp="2026-06-07T14:30:00Z",
            total_queries=3,
            mean_recall_at_k=0.75,
            mean_precision_at_k=0.6,
            mean_mrr=0.8,
            mean_entity_coverage=0.4,
            per_query_results=[
                QueryEvalResult(query="What is AIP?", recall_at_k=0.8, mrr=1.0),
            ],
            channel_contribution_summary={"fts": 8, "vector": 4, "graph": 2},
        )

        summary = result.format_human_summary()
        assert "Retrieval Quality Evaluation Report" in summary
        assert "0.7500" in summary
        assert "Channel Contribution Summary" in summary
        assert "fts" in summary
        assert "Per-Query Results" in summary

    def test_save_with_timestamp(self):
        from aip.orchestration.retrieval_eval import EvalResult

        result = EvalResult(
            timestamp="2026-06-07T14:30:00+00:00",
            total_queries=1,
            mean_recall_at_k=0.5,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            saved_path = result.save_with_timestamp(tmpdir)
            assert os.path.exists(saved_path)
            assert saved_path.endswith(".json")

            with open(saved_path) as f:
                data = json.load(f)
            assert data["total_queries"] == 1
            assert data["mean_recall_at_k"] == 0.5

    def test_channel_contributions_in_serialized_output(self):
        from aip.orchestration.retrieval_eval import EvalResult, QueryEvalResult

        result = EvalResult(
            timestamp="2026-06-07T14:30:00Z",
            total_queries=1,
            mean_recall_at_k=0.5,
            per_query_results=[
                QueryEvalResult(
                    query="test",
                    recall_at_k=0.5,
                    channel_contributions={"fts": 3, "graph": 1},
                ),
            ],
            channel_contribution_summary={"fts": 3, "graph": 1},
        )

        data = result.to_dict()
        assert data["channel_contribution_summary"]["fts"] == 3
        assert data["per_query_results"][0]["channel_contributions"]["graph"] == 1


# ---------------------------------------------------------------------------
# 5. Eval Harness with Channel Contributions
# ---------------------------------------------------------------------------


class TestEvalHarnessChannelContributions:
    """Verify the evaluation harness captures channel contributions from traces."""

    def test_harness_captures_channel_contributions(self):
        from aip.orchestration.retrieval_eval import (
            GoldenQuery,
            RetrievalEvalHarness,
        )

        async def _mock_retriever_with_trace(query):
            hits = [
                RetrievalHit(id="doc:1", content="Hit 1", score=0.9, source_channel="fts"),
                RetrievalHit(id="graph:1", content="Graph hit", score=0.7, source_channel="graph"),
            ]
            trace = RetrievalTrace(
                query=query,
                channel_contributions={"fts": 1, "graph": 1},
            )
            return hits, trace

        golden = [
            GoldenQuery(query="What is AIP?", relevant_ids=["doc:1"]),
        ]

        harness = RetrievalEvalHarness(k=10)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run(golden, _mock_retriever_with_trace)
        )

        assert result.channel_contribution_summary.get("fts", 0) >= 1
        assert result.channel_contribution_summary.get("graph", 0) >= 1
        assert result.per_query_results[0].channel_contributions.get("fts", 0) >= 1


# ---------------------------------------------------------------------------
# 6. CLI Eval Command
# ---------------------------------------------------------------------------


class TestCLIEvalCommand:
    """Verify the aip eval retrieval CLI command structure."""

    def test_eval_command_registered(self):
        """The eval command group should be registered."""
        from aip.cli.main import cli
        # Check that 'eval' is a registered subcommand
        eval_group = None
        for name, cmd in cli.commands.items():
            if name == "eval":
                eval_group = cmd
                break
        assert eval_group is not None, "eval command group should be registered"

    def test_retrieval_subcommand_registered(self):
        """The eval retrieval subcommand should be registered."""
        from aip.cli.eval import eval_cmd
        assert "retrieval" in eval_cmd.commands
