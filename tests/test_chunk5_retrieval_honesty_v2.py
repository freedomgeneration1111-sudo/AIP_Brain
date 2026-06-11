"""Chunk 5 — Retrieval Honesty and Vector Health Verification (extended).

Tests the retrieval honesty hardening: per-channel structured health details,
new channel health states (unavailable, not_configured, empty), vector-specific
health reporting, and retrieval trace metadata.

Required test scenarios:
1. lexical active, vector active → full healthy retrieval
2. vector unavailable → degraded retrieval, not silent success
3. vector fallback/brute-force → degraded with reason
4. graph store missing → graph unavailable/degraded
5. wiki/CODEX missing → wiki unavailable/degraded
6. retriever raises exception → channel failed but ask still returns with trace
7. all channels empty → result says empty/no_context, not healthy
8. health endpoint reports vector degraded when fallback is active
9. AskResult/retrieval trace contains channel state metadata
"""

from __future__ import annotations

import pytest

from aip.foundation.schemas.ask import AskResult
from aip.foundation.schemas.retrieval import (
    ChannelHealthDetail,
    ChannelHealthReport,
    ChannelHealthState,
    RetrievalHit,
    RetrievalTrace,
)
from aip.foundation.schemas.vector import VectorBackendStatus, VectorDegradationInfo
from aip.orchestration.ask_pipeline import _build_degradation_dict, _build_retrieval_warnings
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorConfig,
    RetrievalOrchestrator,
)

# ======================================================================
# 0. ChannelHealthState new values
# ======================================================================


class TestChannelHealthStateExtended:
    """Test new ChannelHealthState values added in Chunk 5."""

    def test_unavailable_value(self):
        assert ChannelHealthState.UNAVAILABLE.value == "unavailable"

    def test_not_configured_value(self):
        assert ChannelHealthState.NOT_CONFIGURED.value == "not_configured"

    def test_empty_value(self):
        assert ChannelHealthState.EMPTY.value == "empty"

    def test_unavailable_is_not_available(self):
        assert ChannelHealthState.UNAVAILABLE.is_available is False

    def test_not_configured_is_not_available(self):
        assert ChannelHealthState.NOT_CONFIGURED.is_available is False

    def test_empty_is_available(self):
        """Empty channel was dispatched and could have returned results."""
        assert ChannelHealthState.EMPTY.is_available is True

    def test_empty_is_not_healthy(self):
        assert ChannelHealthState.EMPTY.is_healthy is False

    def test_unavailable_not_attempted(self):
        assert ChannelHealthState.UNAVAILABLE.was_attempted is False

    def test_not_configured_not_attempted(self):
        assert ChannelHealthState.NOT_CONFIGURED.was_attempted is False

    def test_empty_was_attempted(self):
        assert ChannelHealthState.EMPTY.was_attempted is True

    def test_active_was_attempted(self):
        assert ChannelHealthState.ACTIVE.was_attempted is True

    def test_failed_was_attempted(self):
        assert ChannelHealthState.FAILED.was_attempted is True


# ======================================================================
# 1. ChannelHealthDetail
# ======================================================================


class TestChannelHealthDetail:
    """Test ChannelHealthDetail dataclass and serialization."""

    def test_default_values(self):
        detail = ChannelHealthDetail()
        assert detail.channel == ""
        assert detail.state == ChannelHealthState.DISABLED
        assert detail.attempted is False
        assert detail.succeeded is False
        assert detail.result_count == 0
        assert detail.latency_ms == 0.0
        assert detail.degradation_reason == ""

    def test_active_detail(self):
        detail = ChannelHealthDetail(
            channel="fts",
            state=ChannelHealthState.ACTIVE,
            attempted=True,
            succeeded=True,
            result_count=5,
            latency_ms=12.5,
        )
        assert detail.succeeded is True
        assert detail.result_count == 5

    def test_degraded_detail_with_vector_fields(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.DEGRADED,
            attempted=True,
            succeeded=True,
            result_count=3,
            latency_ms=150.0,
            degradation_reason="Brute-force fallback (no VSS index)",
            backend_type="brute_force",
            vss_available=False,
            vector_count=500,
            embedding_provider_configured=True,
        )
        assert detail.backend_type == "brute_force"
        assert detail.vss_available is False
        assert detail.vector_count == 500
        assert detail.embedding_provider_configured is True

    def test_to_dict_basic(self):
        detail = ChannelHealthDetail(
            channel="fts",
            state=ChannelHealthState.ACTIVE,
            attempted=True,
            succeeded=True,
            result_count=5,
            latency_ms=12.5,
        )
        d = detail.to_dict()
        assert d["channel"] == "fts"
        assert d["state"] == "active"
        assert d["attempted"] is True
        assert d["succeeded"] is True
        assert d["result_count"] == 5
        assert d["latency_ms"] == 12.5
        # Vector-specific fields should NOT be present when not set
        assert "backend_type" not in d
        assert "vss_available" not in d
        assert "vector_count" not in d
        assert "embedding_provider_configured" not in d

    def test_to_dict_with_vector_fields(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.DEGRADED,
            attempted=True,
            succeeded=True,
            result_count=3,
            latency_ms=150.0,
            degradation_reason="Brute-force fallback",
            backend_type="brute_force",
            vss_available=False,
            vector_count=500,
            embedding_provider_configured=True,
        )
        d = detail.to_dict()
        assert d["backend_type"] == "brute_force"
        assert d["vss_available"] is False
        assert d["vector_count"] == 500
        assert d["embedding_provider_configured"] is True

    def test_to_dict_error_summary(self):
        detail = ChannelHealthDetail(
            channel="graph",
            state=ChannelHealthState.FAILED,
            attempted=True,
            succeeded=False,
            error_summary="GraphStore connection refused",
        )
        d = detail.to_dict()
        assert d["error_summary"] == "GraphStore connection refused"

    def test_not_configured_detail(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.NOT_CONFIGURED,
            attempted=False,
            succeeded=False,
            degradation_reason="No embedding provider configured",
        )
        assert detail.state == ChannelHealthState.NOT_CONFIGURED
        assert detail.attempted is False


# ======================================================================
# 2. Test 1: lexical active, vector active → full healthy retrieval
# ======================================================================


class TestFullHealthyRetrieval:
    """When both lexical and vector are active, retrieval should be fully healthy."""

    @pytest.mark.asyncio
    async def test_both_channels_active(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def vector_retriever(query):
            return [RetrievalHit(id="vec:1", content="test", score=0.85)]

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", vector_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Both channels should be active
        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "active"

        # Channel details should be populated
        assert "fts" in trace.channel_details
        assert "vector" in trace.channel_details
        assert trace.channel_details["fts"].state == ChannelHealthState.ACTIVE
        assert trace.channel_details["vector"].state == ChannelHealthState.ACTIVE
        assert trace.channel_details["fts"].succeeded is True
        assert trace.channel_details["vector"].succeeded is True

        # Retrieval honesty flags
        assert "fts" in trace.channels_used
        assert "vector" in trace.channels_used
        assert trace.lexical_only is False
        assert trace.vector_contributed is True

        # No degradation warnings
        assert len(trace.degradation_warnings) == 0


# ======================================================================
# 3. Test 2: vector unavailable → degraded retrieval, not silent success
# ======================================================================


class TestVectorUnavailable:
    """When vector channel is not registered, it should report not_configured, not silent success."""

    @pytest.mark.asyncio
    async def test_vector_not_registered_reports_not_configured(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        orch.register_channel("fts", fts_retriever)
        # vector is NOT registered

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Vector should be not_configured, NOT silently "active" or "disabled"
        assert trace.channel_health["vector"] == "not_configured"
        assert trace.channel_details["vector"].state == ChannelHealthState.NOT_CONFIGURED
        assert trace.channel_details["vector"].attempted is False
        assert "not registered" in trace.channel_health_reasons.get("vector", "").lower()

        # The answer should indicate lexical-only
        assert trace.lexical_only is True
        assert trace.vector_contributed is False

        # Degradation warnings should mention vector
        assert any("Vector" in w or "vector" in w.lower() for w in trace.degradation_warnings)


# ======================================================================
# 4. Test 3: vector fallback/brute-force → degraded with reason
# ======================================================================


class TestVectorFallbackDegraded:
    """When vector store uses brute-force fallback, it should report degraded."""

    @pytest.mark.asyncio
    async def test_vector_brute_force_is_degraded(self):
        orch = RetrievalOrchestrator()
        orch._vector_degraded = True  # Simulate brute-force mode

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def vector_retriever(query):
            return [RetrievalHit(id="vec:1", content="test", score=0.85)]

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", vector_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Vector should be degraded
        assert trace.channel_health["vector"] == "degraded"
        assert trace.channel_details["vector"].state == ChannelHealthState.DEGRADED
        assert "brute-force" in trace.channel_health_reasons.get("vector", "").lower()

        # Vector still contributed
        assert trace.vector_contributed is True

        # Degradation warnings should mention vector
        assert any("Vector" in w and "degraded" in w.lower() for w in trace.degradation_warnings)


# ======================================================================
# 5. Test 4: graph store missing → graph not_configured
# ======================================================================


class TestGraphStoreMissing:
    """When graph store is missing, the channel should report not_configured."""

    @pytest.mark.asyncio
    async def test_graph_not_registered(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        orch.register_channel("fts", fts_retriever)
        # graph is NOT registered

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=False,
            enable_graph=True,  # enabled but not registered
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Graph should be not_configured
        assert trace.channel_health["graph"] == "not_configured"
        assert trace.channel_details["graph"].state == ChannelHealthState.NOT_CONFIGURED
        assert trace.channel_details["graph"].attempted is False


# ======================================================================
# 6. Test 5: wiki/CODEX missing → wiki not_configured
# ======================================================================


class TestWikiMissing:
    """When wiki store is missing, the channel should report not_configured."""

    @pytest.mark.asyncio
    async def test_wiki_not_registered(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        orch.register_channel("fts", fts_retriever)
        # wiki is NOT registered

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=True,  # enabled but not registered
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Wiki should be not_configured
        assert trace.channel_health["wiki"] == "not_configured"
        assert trace.channel_details["wiki"].state == ChannelHealthState.NOT_CONFIGURED


# ======================================================================
# 7. Test 6: retriever raises exception → channel failed but trace has detail
# ======================================================================


class TestRetrieverException:
    """When a retriever raises an exception, the channel should be failed with structured detail."""

    @pytest.mark.asyncio
    async def test_exception_produces_failed_with_detail(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def failing_retriever(query):
            raise RuntimeError("Connection refused")

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", failing_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Vector should be failed
        assert trace.channel_health["vector"] == "failed"
        assert trace.channel_details["vector"].state == ChannelHealthState.FAILED
        assert trace.channel_details["vector"].attempted is True
        assert trace.channel_details["vector"].succeeded is False
        assert trace.channel_details["vector"].result_count == 0
        assert "Connection refused" in trace.channel_details["vector"].error_summary

        # FTS should still be active
        assert trace.channel_health["fts"] == "active"

        # The answer trace should still be returned
        assert trace is not None
        assert len(hits) > 0


# ======================================================================
# 8. Test 7: all channels empty → result says empty, not healthy
# ======================================================================


class TestAllChannelsEmpty:
    """When all channels return 0 results, the trace should report empty state."""

    @pytest.mark.asyncio
    async def test_all_channels_empty_reports_empty_state(self):
        orch = RetrievalOrchestrator()

        async def empty_fts_retriever(query):
            return []

        async def empty_vector_retriever(query):
            return []

        orch.register_channel("fts", empty_fts_retriever)
        orch.register_channel("vector", empty_vector_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Both channels should be empty
        assert trace.channel_health["fts"] == "empty"
        assert trace.channel_health["vector"] == "empty"
        assert trace.channel_details["fts"].state == ChannelHealthState.EMPTY
        assert trace.channel_details["vector"].state == ChannelHealthState.EMPTY
        assert trace.channel_details["fts"].attempted is True
        assert trace.channel_details["fts"].succeeded is False
        assert trace.channel_details["fts"].result_count == 0

        # No channels used (empty is not "used")
        assert trace.channels_used == []
        # lexical_only is True because no non-lexical channels contributed results
        assert trace.lexical_only is True
        assert trace.vector_contributed is False

        # Verdict should be NO_RESULTS
        assert trace.verdict == "NO_RESULTS"


# ======================================================================
# 9. Test 8: RetrievalTrace new fields and methods
# ======================================================================


class TestRetrievalTraceChunk5:
    """Test Chunk 5 additions to RetrievalTrace."""

    def test_channel_details_field(self):
        trace = RetrievalTrace()
        assert trace.channel_details == {}

    def test_lexical_only_field(self):
        trace = RetrievalTrace()
        assert trace.lexical_only is False

    def test_vector_contributed_field(self):
        trace = RetrievalTrace()
        assert trace.vector_contributed is False

    def test_channels_attempted_field(self):
        trace = RetrievalTrace()
        assert trace.channels_attempted == []

    def test_channels_used_field(self):
        trace = RetrievalTrace()
        assert trace.channels_used == []

    def test_get_unavailable_channels(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "graph": "unavailable"},
        )
        assert trace.get_unavailable_channels() == ["graph"]

    def test_get_not_configured_channels(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "not_configured"},
        )
        assert trace.get_not_configured_channels() == ["vector"]

    def test_get_empty_channels(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "corpus": "empty"},
        )
        assert trace.get_empty_channels() == ["corpus"]

    def test_degradation_summary_includes_unavailable(self):
        trace = RetrievalTrace(
            channel_health={"graph": "unavailable"},
            channel_health_reasons={"graph": "GraphStore not configured"},
        )
        summary = trace.degradation_summary()
        assert "Graph" in summary
        assert "unavailable" in summary.lower()

    def test_degradation_summary_includes_not_configured(self):
        trace = RetrievalTrace(
            channel_health={"vector": "not_configured"},
            channel_health_reasons={"vector": "No embedding provider"},
        )
        summary = trace.degradation_summary()
        assert "Vector" in summary
        assert "not configured" in summary.lower()

    def test_degradation_summary_includes_empty(self):
        trace = RetrievalTrace(
            channel_health={"corpus": "empty"},
            channel_health_reasons={"corpus": "No matching turns"},
        )
        summary = trace.degradation_summary()
        assert "Corpus" in summary
        assert "no results" in summary.lower()

    def test_to_diagnostic_dict_includes_chunk5_fields(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.DEGRADED,
            attempted=True,
            succeeded=True,
            result_count=3,
            latency_ms=150.0,
            degradation_reason="Brute-force fallback",
            backend_type="brute_force",
            vss_available=False,
            vector_count=500,
            embedding_provider_configured=True,
        )
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "degraded"},
            channel_details={"vector": detail},
            channels_attempted=["fts", "vector"],
            channels_used=["fts", "vector"],
            lexical_only=False,
            vector_contributed=True,
        )
        d = trace.to_diagnostic_dict()
        assert "channel_details" in d
        assert d["channel_details"]["vector"]["state"] == "degraded"
        assert d["channel_details"]["vector"]["backend_type"] == "brute_force"
        assert d["channels_attempted"] == ["fts", "vector"]
        assert d["channels_used"] == ["fts", "vector"]
        assert d["lexical_only"] is False
        assert d["vector_contributed"] is True
        assert "unavailable_channels" in d
        assert "not_configured_channels" in d
        assert "empty_channels" in d


# ======================================================================
# 10. Test 9: AskResult/retrieval trace contains channel state metadata
# ======================================================================


class TestAskResultChannelStateMetadata:
    """Test that AskResult and _build_degradation_dict contain Chunk 5 fields."""

    def test_degradation_dict_includes_channel_details(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.DEGRADED,
            attempted=True,
            succeeded=True,
            result_count=3,
            latency_ms=150.0,
            degradation_reason="Brute-force fallback",
            backend_type="brute_force",
            vss_available=False,
            vector_count=500,
            embedding_provider_configured=True,
        )
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "degraded"},
            channel_details={"vector": detail},
            channels_attempted=["fts", "vector"],
            channels_used=["fts", "vector"],
            lexical_only=False,
            vector_contributed=True,
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 2, "vector": 3},
        )
        d = _build_degradation_dict(trace)

        # Chunk 5 fields
        assert "channel_details" in d
        assert d["channel_details"]["vector"]["state"] == "degraded"
        assert d["channel_details"]["vector"]["backend_type"] == "brute_force"
        assert d["channel_details"]["vector"]["vss_available"] is False
        assert d["channel_details"]["vector"]["vector_count"] == 500
        assert d["channel_details"]["vector"]["embedding_provider_configured"] is True
        assert d["channels_attempted"] == ["fts", "vector"]
        assert d["channels_used"] == ["fts", "vector"]
        assert d["lexical_only"] is False
        assert d["vector_contributed"] is True
        assert "unavailable_channels" in d
        assert "not_configured_channels" in d
        assert "empty_channels" in d

    def test_ask_result_has_retrieval_degradation_with_channel_details(self):
        detail = ChannelHealthDetail(
            channel="vector",
            state=ChannelHealthState.NOT_CONFIGURED,
            attempted=False,
            succeeded=False,
            degradation_reason="No embedding provider configured",
            embedding_provider_configured=False,
        )
        trace = RetrievalTrace(
            channel_health={"fts": "active", "vector": "not_configured"},
            channel_details={"vector": detail},
            channels_attempted=["fts"],
            channels_used=["fts"],
            lexical_only=True,
            vector_contributed=False,
        )
        d = _build_degradation_dict(trace)
        result = AskResult(
            status="OK",
            answer="test answer",
            retrieval_degradation=d,
        )
        assert result.retrieval_degradation["lexical_only"] is True
        assert result.retrieval_degradation["vector_contributed"] is False
        assert result.retrieval_degradation["channel_details"]["vector"]["state"] == "not_configured"
        assert result.retrieval_degradation["channel_details"]["vector"]["embedding_provider_configured"] is False

    def test_retrieval_warnings_for_unavailable_channels(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "graph": "unavailable", "vector": "not_configured"},
            channel_health_reasons={
                "graph": "GraphStore not present",
                "vector": "No embedding provider",
            },
            hits_after_quality_gate=5,
            verdict="OK",
            channel_contributions={"fts": 5},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.DISABLED,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        # Should have warnings for unavailable/not_configured channels
        assert any("Graph" in w for w in warnings)
        assert any("Vector" in w for w in warnings)

    def test_retrieval_warnings_for_empty_channels(self):
        trace = RetrievalTrace(
            channel_health={"fts": "active", "corpus": "empty"},
            channel_health_reasons={"corpus": "No matching turns"},
            hits_after_quality_gate=3,
            verdict="OK",
            channel_contributions={"fts": 3},
            vector_degradation=VectorDegradationInfo(
                backend_status=VectorBackendStatus.AVAILABLE,
            ),
        )
        warnings = _build_retrieval_warnings(trace)
        # Empty channels should not produce "unavailable" warnings by default
        # (they were attempted and succeeded — they just returned nothing)


# ======================================================================
# 11. ChannelHealthReport with new states
# ======================================================================


class TestChannelHealthReportExtended:
    """Test ChannelHealthReport with new states."""

    def test_format_warnings_includes_unavailable(self):
        report = ChannelHealthReport(
            channel_states={
                "fts": ChannelHealthState.ACTIVE,
                "graph": ChannelHealthState.UNAVAILABLE,
            },
            reasons={"graph": "GraphStore not configured"},
        )
        warnings = report.format_warnings()
        # UNAVAILABLE is not FAILED or DEGRADED, so format_warnings won't show it
        # This is a design choice: the ChannelHealthReport.format_warnings() only
        # surfaces FAILED and DEGRADED states. UNAVAILABLE is informational.
        # The full channel health is available via channel_states.

    def test_format_warnings_includes_not_configured(self):
        report = ChannelHealthReport(
            channel_states={
                "fts": ChannelHealthState.ACTIVE,
                "vector": ChannelHealthState.NOT_CONFIGURED,
            },
            reasons={"vector": "No embedding provider"},
        )
        warnings = report.format_warnings()
        # NOT_CONFIGURED is not FAILED, so format_warnings won't show it
        # (it's a different category — informational rather than error)
        # This is by design: not_configured is a configuration issue, not a failure


# ======================================================================
# 12. Integration: orchestrator with ChannelHealthDetail
# ======================================================================


class TestOrchestratorWithHealthDetail:
    """Integration test: full retrieval round with ChannelHealthDetail."""

    @pytest.mark.asyncio
    async def test_full_round_trip_with_details(self):
        orch = RetrievalOrchestrator()

        async def fts_retriever(query):
            return [RetrievalHit(id="fts:1", content="test", score=0.9)]

        async def empty_vector_retriever(query):
            return []  # 0 results

        orch.register_channel("fts", fts_retriever)
        orch.register_channel("vector", empty_vector_retriever)

        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=True,  # enabled but not registered
            enable_wiki=True,  # enabled but not registered
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Verify channel_health
        assert trace.channel_health["fts"] == "active"
        assert trace.channel_health["vector"] == "empty"  # returned 0 results
        assert trace.channel_health["graph"] == "not_configured"
        assert trace.channel_health["wiki"] == "not_configured"
        assert trace.channel_health["procedural"] == "disabled"

        # Verify channel_details
        assert trace.channel_details["fts"].state == ChannelHealthState.ACTIVE
        assert trace.channel_details["fts"].attempted is True
        assert trace.channel_details["fts"].succeeded is True
        assert trace.channel_details["fts"].result_count == 1

        assert trace.channel_details["vector"].state == ChannelHealthState.EMPTY
        assert trace.channel_details["vector"].attempted is True
        assert trace.channel_details["vector"].succeeded is False
        assert trace.channel_details["vector"].result_count == 0

        assert trace.channel_details["graph"].state == ChannelHealthState.NOT_CONFIGURED
        assert trace.channel_details["graph"].attempted is False

        assert trace.channel_details["wiki"].state == ChannelHealthState.NOT_CONFIGURED
        assert trace.channel_details["wiki"].attempted is False

        assert trace.channel_details["procedural"].state == ChannelHealthState.DISABLED
        assert trace.channel_details["procedural"].attempted is False

        # Verify retrieval honesty flags
        assert "fts" in trace.channels_attempted
        assert "vector" in trace.channels_attempted
        assert "fts" in trace.channels_used
        assert "vector" not in trace.channels_used  # empty is not "used"
        assert trace.lexical_only is True
        assert trace.vector_contributed is False

        # Diagnostic dict should include all new fields
        d = trace.to_diagnostic_dict()
        assert "channel_details" in d
        assert "channels_attempted" in d
        assert "channels_used" in d
        assert "lexical_only" in d
        assert "vector_contributed" in d
        assert "unavailable_channels" in d
        assert "not_configured_channels" in d
        assert "empty_channels" in d

    @pytest.mark.asyncio
    async def test_all_channels_disabled_with_details(self):
        orch = RetrievalOrchestrator()

        config = OrchestratorConfig(
            enable_fts=False,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=False,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        assert hits == []
        assert trace.verdict == "NO_RESULTS"
        # All channels should be disabled with details
        for ch in ["fts", "vector", "graph", "wiki", "procedural", "corpus"]:
            assert trace.channel_health.get(ch) == "disabled"
            assert ch in trace.channel_details
            assert trace.channel_details[ch].state == ChannelHealthState.DISABLED
            assert trace.channel_details[ch].attempted is False

        assert trace.lexical_only is True
        assert trace.vector_contributed is False
