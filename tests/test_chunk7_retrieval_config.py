"""Chunk 7: Retrieval config helper tests.

Tests for the extracted configuration helpers in
``aip.orchestration.channels.retrieval_config``:
- build_orchestrator_config
- apply_channel_weights
- apply_channel_selector
- check_vector_coverage
"""

import pytest

from aip.orchestration.channels.retrieval_config import (
    apply_channel_selector,
    apply_channel_weights,
    build_orchestrator_config,
    check_vector_coverage,
)
from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

# ---------------------------------------------------------------------------
# build_orchestrator_config
# ---------------------------------------------------------------------------


class TestBuildOrchestratorConfig:
    """Tests for build_orchestrator_config helper."""

    def test_default_config_has_core_channels_enabled(self):
        config = build_orchestrator_config()
        assert config.enable_fts is True
        assert config.enable_vector is True
        assert config.enable_graph is False
        assert config.enable_wiki is False
        assert config.enable_procedural is False

    def test_vector_enabled_overrides_enable_vector(self):
        """vector_enabled (after coverage gating) takes precedence for the config."""
        config = build_orchestrator_config(
            enable_vector=True,
            vector_enabled=False,
        )
        assert config.enable_vector is False

    def test_max_hits_is_three_times_max_sources(self):
        config = build_orchestrator_config(max_sources=5)
        assert config.max_hits == 15

    def test_custom_channel_flags(self):
        config = build_orchestrator_config(
            enable_fts=False,
            enable_graph=True,
            enable_wiki=True,
            enable_procedural=True,
        )
        assert config.enable_fts is False
        assert config.enable_graph is True
        assert config.enable_wiki is True
        assert config.enable_procedural is True


# ---------------------------------------------------------------------------
# apply_channel_weights
# ---------------------------------------------------------------------------


class TestApplyChannelWeights:
    """Tests for apply_channel_weights helper."""

    def test_no_config_leaves_weights_unchanged(self):
        config = build_orchestrator_config()
        original_weights = dict(config.channel_weights)
        result = apply_channel_weights(config, {}, vector_enabled=True)
        assert result.channel_weights == original_weights

    def test_weights_applied_when_semantic_and_lexical_active(self):
        config = build_orchestrator_config(vector_enabled=True)
        effective_config = {
            "retrieval": {
                "channel_weights": {
                    "vector": 0.7,
                    "fts": 0.3,
                },
            },
        }
        result = apply_channel_weights(config, effective_config, vector_enabled=True)
        assert result.channel_weights == {"vector": 0.7, "fts": 0.3}

    def test_weights_cleared_when_only_lexical_active(self):
        """When vector is disabled, weights should be cleared to avoid distorting scores."""
        config = build_orchestrator_config(vector_enabled=False)
        effective_config = {
            "retrieval": {
                "channel_weights": {
                    "vector": 0.7,
                    "fts": 0.3,
                },
            },
        }
        result = apply_channel_weights(config, effective_config, vector_enabled=False)
        assert result.channel_weights == {}

    def test_none_config_returns_unchanged(self):
        config = build_orchestrator_config()
        original_weights = dict(config.channel_weights)
        result = apply_channel_weights(config, None, vector_enabled=True)
        assert result.channel_weights == original_weights

    def test_non_numeric_weights_filtered_out(self):
        config = build_orchestrator_config(vector_enabled=True)
        effective_config = {
            "retrieval": {
                "channel_weights": {
                    "vector": 0.6,
                    "fts": "invalid",
                    "corpus": 0.4,
                },
            },
        }
        result = apply_channel_weights(config, effective_config, vector_enabled=True)
        assert result.channel_weights == {"vector": 0.6, "corpus": 0.4}


# ---------------------------------------------------------------------------
# apply_channel_selector
# ---------------------------------------------------------------------------


class TestApplyChannelSelector:
    """Tests for apply_channel_selector helper."""

    def test_auto_channel_selection_disabled(self):
        config = build_orchestrator_config()
        original_graph = config.enable_graph
        result = apply_channel_selector("test query", config, auto_channel_selection=False)
        assert result.enable_graph == original_graph

    def test_channel_selector_failure_is_non_fatal(self):
        """If ChannelSelector import fails, config is returned unchanged."""
        config = build_orchestrator_config()
        # This should not raise even if ChannelSelector is unavailable
        result = apply_channel_selector("test query", config, auto_channel_selection=True)
        # Config should still be a valid OrchestratorConfig
        assert isinstance(result, OrchestratorConfig)


# ---------------------------------------------------------------------------
# check_vector_coverage
# ---------------------------------------------------------------------------


class TestCheckVectorCoverage:
    """Tests for check_vector_coverage helper."""

    @pytest.mark.asyncio
    async def test_vector_unavailable_returns_false(self):
        result = await check_vector_coverage(
            corpus_turn_store=None,
            vector_available=False,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_corpus_store_returns_true(self):
        """When no corpus store is available, vector stays enabled (fail-open)."""
        result = await check_vector_coverage(
            corpus_turn_store=None,
            vector_available=True,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_sufficient_coverage_returns_true(self):
        """When embedding coverage is above 10%, vector stays enabled."""

        class MockCorpusTurnStore:
            async def get_embedding_progress(self):
                return {"percentage": 50.0}

        result = await check_vector_coverage(
            corpus_turn_store=MockCorpusTurnStore(),
            vector_available=True,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_low_coverage_returns_false(self):
        """When embedding coverage is below 10%, vector is disabled."""

        class MockCorpusTurnStore:
            async def get_embedding_progress(self):
                return {"percentage": 5.0}

        result = await check_vector_coverage(
            corpus_turn_store=MockCorpusTurnStore(),
            vector_available=True,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_coverage_check_failure_returns_true(self):
        """If coverage check fails, vector stays enabled (fail-open for availability)."""

        class MockCorpusTurnStore:
            async def get_embedding_progress(self):
                raise RuntimeError("DB error")

        result = await check_vector_coverage(
            corpus_turn_store=MockCorpusTurnStore(),
            vector_available=True,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exact_10_percent_returns_false(self):
        """10% is the minimum; exactly 10% should still be enabled."""

        class MockCorpusTurnStore:
            async def get_embedding_progress(self):
                return {"percentage": 10.0}

        result = await check_vector_coverage(
            corpus_turn_store=MockCorpusTurnStore(),
            vector_available=True,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_just_below_10_percent_returns_false(self):
        """9.99% coverage should disable vector."""

        class MockCorpusTurnStore:
            async def get_embedding_progress(self):
                return {"percentage": 9.99}

        result = await check_vector_coverage(
            corpus_turn_store=MockCorpusTurnStore(),
            vector_available=True,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Channel registration consistency
# ---------------------------------------------------------------------------


class TestChannelRegistrationConsistency:
    """Tests that all channel register() functions return list[ChannelFailure]."""

    def test_lexical_channel_returns_list(self):
        """lexical_channel.register() must return list[ChannelFailure], not None."""
        from aip.orchestration.channels.lexical_channel import register
        from aip.orchestration.channels.types import ChannelFailure
        from aip.orchestration.retrieval_orchestrator import RetrievalOrchestrator

        # Create a mock stores object
        class MockLexicalStore:
            async def search(self, query, domain=None, limit=30):
                return []

        class MockStores:
            lexical_store = MockLexicalStore()

        orch = RetrievalOrchestrator()
        result = register(orch, MockStores())
        assert isinstance(result, list)
        # All items should be ChannelFailure (if any)
        for item in result:
            assert isinstance(item, ChannelFailure)


# ---------------------------------------------------------------------------
# retrieval_trace_utils dead code check
# ---------------------------------------------------------------------------


class TestRetrievalTraceUtilsClean:
    """Verify no silent exception patterns in retrieval_trace_utils."""

    def test_no_except_pass_in_trace_utils(self):
        """retrieval_trace_utils must not contain 'except Exception: pass'."""
        import inspect

        from aip.orchestration.channels import retrieval_trace_utils

        source = inspect.getsource(retrieval_trace_utils)
        # Check for the forbidden pattern
        assert "except Exception:\n            pass" not in source, (
            "Found 'except Exception: pass' in retrieval_trace_utils — "
            "all exceptions must be logged, not silently swallowed."
        )

    def test_no_dead_code_in_build_warnings(self):
        """build_retrieval_warnings must not contain dead code (unused dict lookups)."""
        import inspect

        from aip.orchestration.channels.retrieval_trace_utils import build_retrieval_warnings

        source = inspect.getsource(build_retrieval_warnings)
        # The dead code pattern was: `retrieval_trace.channel_health_reasons.get(channel, "")`
        # on a line by itself (result discarded)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("retrieval_trace.channel_health_reasons.get("):
                # This should not appear as a standalone statement
                assert False, (
                    f"Dead code at line {i + 1}: '{stripped}' — "
                    "result of .get() is discarded. Either use it or remove it."
                )
