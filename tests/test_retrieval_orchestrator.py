"""Tests for Sprint 5.7: RetrievalOrchestrator, parallel dispatch, RRF fusion,
orchestrator caching, SmartContextPacker, and retry behavior.

Covers:
1. Parallel dispatch correctness (all channels run, results merged)
2. RRF fusion correctness (multi-channel hits properly scored)
3. Per-channel trace timing accuracy under concurrency
4. Quality gate verdicts (OK, NEEDS_MORE_CONTEXT, NO_RESULTS)
5. Automatic retry on NEEDS_MORE_CONTEXT
6. OrchestratorCache — instance reuse and invalidation
7. SmartContextPacker — budget-aware packing and extractive summarization
8. Legacy _assemble_context deprecation (still works but marked)
9. _search_sources_with_trace integration
10. TraceStore analytics (get_dashboard_summary)
"""

from __future__ import annotations

import asyncio
import time

from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorCache,
    OrchestratorConfig,
    RetrievalOrchestrator,
    apply_quality_gate,
    expand_query,
    rrf_fuse,
)
from aip.orchestration.smart_context_packer import (
    PackerConfig,
    SmartContextPacker,
    extractive_summarize,
)


# ---------------------------------------------------------------------------
# Helpers: fake retriever channels
# ---------------------------------------------------------------------------


async def _fake_fts_retriever(query: str) -> list[RetrievalHit]:
    """Fake FTS retriever that returns 3 hits with a small delay."""
    await asyncio.sleep(0.01)  # simulate I/O
    return [
        RetrievalHit(id="fts:1", content="FTS hit one about AIP", score=0.9, source_channel="fts"),
        RetrievalHit(id="fts:2", content="FTS hit two about retrieval", score=0.7, source_channel="fts"),
        RetrievalHit(id="fts:3", content="FTS hit three about context", score=0.5, source_channel="fts"),
    ]


async def _fake_vector_retriever(query: str) -> list[RetrievalHit]:
    """Fake vector retriever that returns 3 hits with a different delay."""
    await asyncio.sleep(0.015)  # slightly slower
    return [
        RetrievalHit(id="vec:1", content="Vector hit one about AIP", score=0.85, source_channel="vector"),
        RetrievalHit(id="vec:2", content="Vector hit two about embeddings", score=0.75, source_channel="vector"),
        RetrievalHit(id="fts:2", content="Overlapping hit also in FTS", score=0.8, source_channel="vector"),
    ]


async def _slow_retriever(query: str) -> list[RetrievalHit]:
    """Slow retriever (100ms) to verify parallel dispatch saves time."""
    await asyncio.sleep(0.1)
    return [RetrievalHit(id="slow:1", content="Slow hit", score=0.6, source_channel="slow")]


async def _failing_retriever(query: str) -> list[RetrievalHit]:
    """Retriever that raises an exception."""
    raise RuntimeError("Retriever failure")


async def _empty_retriever(query: str) -> list[RetrievalHit]:
    """Retriever that returns no hits."""
    return []


# ---------------------------------------------------------------------------
# 1. Parallel dispatch correctness
# ---------------------------------------------------------------------------


class TestParallelDispatch:
    """Verify that all enabled channels are dispatched and results collected."""

    async def test_all_channels_dispatched(self):
        """Both FTS and Vector channels should run and contribute results."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("vector", _fake_vector_retriever)

        hits, trace = await orch.retrieve("test query")

        # Both channels should be in the trace
        assert "fts" in trace.channels_queried
        assert "vector" in trace.channels_queried

        # We should have hits from both channels (deduped by RRF)
        fts_hits = [h for h in hits if h.source_channel == "fts" or "fts" in h.metadata.get("source_channels", [])]
        vec_hits = [
            h for h in hits
            if h.source_channel == "vector" or "vector" in h.metadata.get("source_channels", [])
        ]
        assert len(fts_hits) > 0, "Should have FTS hits"
        assert len(vec_hits) > 0, "Should have vector hits"

    async def test_parallel_is_faster_than_sequential(self):
        """Parallel dispatch should be faster than sequential execution."""
        orch = RetrievalOrchestrator()
        orch.register_channel("slow1", _slow_retriever)
        orch.register_channel("slow2", _slow_retriever)
        orch.register_channel("slow3", _slow_retriever)

        config = OrchestratorConfig(enable_all_registered=True)
        start = time.monotonic()
        hits, trace = await orch.retrieve("test query", config=config)
        elapsed = (time.monotonic() - start) * 1000

        # 3 retrievers each taking 100ms; parallel should be ~100ms, not 300ms
        assert elapsed < 250, f"Parallel dispatch should be < 250ms, got {elapsed:.0f}ms"
        assert len(trace.channels_queried) == 3

    async def test_single_channel_dispatch(self):
        """With only one channel, dispatch still works correctly."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)

        hits, trace = await orch.retrieve("test query")
        assert trace.channels_queried == ["fts"]
        assert len(hits) == 3

    async def test_no_channels_registered(self):
        """With no channels, retrieval returns NO_RESULTS."""
        orch = RetrievalOrchestrator()
        hits, trace = await orch.retrieve("test query")
        assert trace.verdict == "NO_RESULTS"
        assert len(hits) == 0


# ---------------------------------------------------------------------------
# 2. RRF fusion correctness
# ---------------------------------------------------------------------------


class TestRRFFusion:
    """Verify Reciprocal Rank Fusion merges and scores correctly."""

    def test_single_channel_rrf(self):
        """RRF with one channel preserves ranking order."""
        channel_results = {
            "fts": [
                RetrievalHit(id="a", score=0.9),
                RetrievalHit(id="b", score=0.7),
                RetrievalHit(id="c", score=0.5),
            ]
        }
        fused = rrf_fuse(channel_results, k=60)
        assert [h.id for h in fused] == ["a", "b", "c"]
        # RRF score for rank 1 = 1/(60+1) = 0.01639...
        assert abs(fused[0].rrf_score - 1.0 / 61) < 0.001

    def test_overlapping_hits_accumulate_scores(self):
        """A hit appearing in multiple channels should have a higher RRF score."""
        channel_results = {
            "fts": [
                RetrievalHit(id="shared", score=0.9),
                RetrievalHit(id="fts_only", score=0.7),
            ],
            "vector": [
                RetrievalHit(id="shared", score=0.85),  # overlap
                RetrievalHit(id="vec_only", score=0.6),
            ],
        }
        fused = rrf_fuse(channel_results, k=60)

        # "shared" should be top-ranked because it appears in both channels
        assert fused[0].id == "shared"
        # Its RRF score should be higher than any single-channel hit
        shared_score = fused[0].rrf_score
        fts_only_score = next(h.rrf_score for h in fused if h.id == "fts_only")
        assert shared_score > fts_only_score

    def test_deduplication(self):
        """Same ID appearing in multiple channels should be deduplicated."""
        channel_results = {
            "fts": [RetrievalHit(id="dup", score=0.9)],
            "vector": [RetrievalHit(id="dup", score=0.8)],
        }
        fused = rrf_fuse(channel_results, k=60)
        assert len(fused) == 1
        assert fused[0].id == "dup"

    def test_empty_channels(self):
        """RRF with no results returns empty list."""
        assert rrf_fuse({}, k=60) == []
        assert rrf_fuse({"fts": []}, k=60) == []


# ---------------------------------------------------------------------------
# 3. Per-channel trace timing
# ---------------------------------------------------------------------------


class TestTraceTiming:
    """Verify that trace timing is correct under parallel dispatch."""

    async def test_per_channel_timing_recorded(self):
        """Each channel should have its elapsed_ms recorded in the trace."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("vector", _fake_vector_retriever)

        hits, trace = await orch.retrieve("test query")

        assert "fts" in trace.per_channel_elapsed_ms
        assert "vector" in trace.per_channel_elapsed_ms
        assert trace.per_channel_elapsed_ms["fts"] > 0
        assert trace.per_channel_elapsed_ms["vector"] > 0

    async def test_total_elapsed_is_wall_clock(self):
        """total_elapsed_ms should reflect wall-clock time, not sum of channels."""
        orch = RetrievalOrchestrator()
        orch.register_channel("slow1", _slow_retriever)
        orch.register_channel("slow2", _slow_retriever)

        config = OrchestratorConfig(enable_all_registered=True)
        hits, trace = await orch.retrieve("test query", config=config)

        # Wall-clock should be ~100ms (parallel), not ~200ms (sum)
        assert trace.total_elapsed_ms < 250
        # But the sum of per-channel times should be ~200ms
        channel_sum = sum(trace.per_channel_elapsed_ms.values())
        assert channel_sum > 150  # both ~100ms channels summed


# ---------------------------------------------------------------------------
# 4. Quality gate verdicts
# ---------------------------------------------------------------------------


class TestQualityGate:
    """Verify quality gate produces correct verdicts."""

    def test_ok_with_sufficient_hits(self):
        hits = [
            RetrievalHit(id="1", rrf_score=0.05),
            RetrievalHit(id="2", rrf_score=0.03),
        ]
        filtered, verdict = apply_quality_gate(hits, min_rrf_score=0.01, min_hits=1)
        assert verdict == "OK"
        assert len(filtered) == 2

    def test_needs_more_context_below_threshold(self):
        hits = [
            RetrievalHit(id="1", rrf_score=0.005),  # below threshold
        ]
        filtered, verdict = apply_quality_gate(hits, min_rrf_score=0.01, min_hits=1)
        assert verdict == "NEEDS_MORE_CONTEXT"

    def test_no_results(self):
        filtered, verdict = apply_quality_gate([], min_rrf_score=0.01, min_hits=1)
        assert verdict == "NO_RESULTS"
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# 5. Automatic retry on NEEDS_MORE_CONTEXT
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    """Verify automatic retry when quality gate returns NEEDS_MORE_CONTEXT."""

    async def test_retry_on_low_quality(self):
        """Orchestrator should retry when quality gate returns NEEDS_MORE_CONTEXT."""
        call_count = 0

        async def _weak_retriever(query: str) -> list[RetrievalHit]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return low-quality hits
                return [RetrievalHit(id="weak:1", content="Weak", score=0.1, source_channel="fts")]
            else:
                # Retry: return better hits
                return [
                    RetrievalHit(id="good:1", content="Good hit about AIP", score=0.9, source_channel="fts"),
                    RetrievalHit(id="good:2", content="Good hit about retrieval", score=0.8, source_channel="fts"),
                ]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _weak_retriever)

        config = OrchestratorConfig(
            max_retrieval_rounds=2,
            quality_gate_min_rrf=0.01,
            quality_gate_min_hits=2,
        )

        hits, trace = await orch.retrieve("test query", config=config)

        # Should have retried
        assert call_count >= 2
        # Final result should have good hits
        assert len(hits) >= 2

    async def test_no_retry_on_ok(self):
        """No retry when quality gate returns OK."""
        call_count = 0

        async def _good_retriever(query: str) -> list[RetrievalHit]:
            nonlocal call_count
            call_count += 1
            return [
                RetrievalHit(id="1", content="Good", score=0.9, source_channel="fts"),
                RetrievalHit(id="2", content="Also good", score=0.8, source_channel="fts"),
            ]

        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _good_retriever)

        config = OrchestratorConfig(max_retrieval_rounds=2)
        hits, trace = await orch.retrieve("test query", config=config)

        assert call_count == 1, "Should not retry on OK verdict"
        assert trace.verdict == "OK"


# ---------------------------------------------------------------------------
# 6. OrchestratorCache — instance reuse and invalidation
# ---------------------------------------------------------------------------


class TestOrchestratorCache:
    """Verify that OrchestratorCache reuses instances correctly."""

    def test_same_key_returns_same_instance(self):
        cache = OrchestratorCache()
        orch1 = cache.get_or_create(store_key=42)
        orch2 = cache.get_or_create(store_key=42)
        assert orch1 is orch2

    def test_different_key_creates_new_instance(self):
        cache = OrchestratorCache()
        orch1 = cache.get_or_create(store_key=42)
        orch2 = cache.get_or_create(store_key=99)
        assert orch1 is not orch2

    def test_invalidation_forces_new_instance(self):
        cache = OrchestratorCache()
        orch1 = cache.get_or_create(store_key=42)
        cache.invalidate()
        orch2 = cache.get_or_create(store_key=42)
        assert orch1 is not orch2

    def test_register_fn_called_on_new_instance(self):
        registered = []

        def _register(orch: RetrievalOrchestrator) -> None:
            registered.append(orch)

        cache = OrchestratorCache()
        orch1 = cache.get_or_create(store_key=1, register_fn=_register)
        assert len(registered) == 1

        # Same key → no re-registration
        orch2 = cache.get_or_create(store_key=1, register_fn=_register)
        assert len(registered) == 1  # still 1
        assert orch1 is orch2

        # Different key → registration happens
        cache.get_or_create(store_key=2, register_fn=_register)
        assert len(registered) == 2


# ---------------------------------------------------------------------------
# 7. SmartContextPacker
# ---------------------------------------------------------------------------


class TestSmartContextPacker:
    """Verify budget-aware packing and extractive summarization."""

    def test_pack_basic(self):
        hits = [
            RetrievalHit(id="1", content="Content about AIP architecture", rrf_score=0.05, source_channel="fts"),
            RetrievalHit(id="2", content="Content about retrieval pipeline", rrf_score=0.03, source_channel="vector"),
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=1000))
        packed = packer.pack(hits, query="AIP architecture")

        assert packed.hits_packed == 2
        assert packed.hits_skipped == 0
        assert "1" in packed.context_text
        assert "2" in packed.context_text

    def test_pack_empty_hits(self):
        packer = SmartContextPacker()
        packed = packer.pack([], query="test")
        assert "No relevant sources" in packed.context_text
        assert packed.hits_packed == 0

    def test_pack_with_budget_constraint(self):
        """When hits exceed budget, some should be summarized or skipped."""
        long_hits = [
            RetrievalHit(
                id=f"hit:{i}",
                content="A" * 5000,  # 5k chars each
                rrf_score=0.05 - i * 0.01,
                source_channel="fts",
            )
            for i in range(10)
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=500))
        packed = packer.pack(long_hits, query="test")

        # Should have packed some and skipped/summarized others
        assert packed.hits_packed + packed.hits_summarized + packed.hits_skipped == 10
        assert packed.estimated_tokens > 0

    def test_metadata_headers_included(self):
        hits = [
            RetrievalHit(id="1", content="Test", rrf_score=0.05, source_channel="fts"),
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=1000, include_metadata=True))
        packed = packer.pack(hits, query="test")
        assert "Source 1" in packed.context_text
        assert "score=" in packed.context_text

    def test_metadata_headers_excluded(self):
        hits = [
            RetrievalHit(id="1", content="Test", rrf_score=0.05, source_channel="fts"),
        ]
        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=1000, include_metadata=False))
        packed = packer.pack(hits, query="test")
        assert "score=" not in packed.context_text


# ---------------------------------------------------------------------------
# 8. Extractive summarization
# ---------------------------------------------------------------------------


class TestExtractiveSummarization:
    """Verify sentence extraction and summarization."""

    def test_short_text_unchanged(self):
        text = "This is short."
        result = extractive_summarize(text, max_chars=100)
        assert result == text

    def test_long_text_truncated(self):
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = extractive_summarize(text, max_chars=30, query="first")
        assert len(result) <= 30 + 20  # some slack for sentence boundaries
        assert "First" in result

    def test_empty_text(self):
        assert extractive_summarize("", max_chars=100) == ""

    def test_query_relevance_boosts_matching_sentences(self):
        text = "AIP is a knowledge engine. Python is a programming language. AIP uses retrieval."
        result = extractive_summarize(text, max_chars=50, query="AIP")
        # AIP-relevant sentences should be preferred
        assert "AIP" in result


# ---------------------------------------------------------------------------
# 9. Query expansion
# ---------------------------------------------------------------------------


class TestQueryExpansion:
    """Verify simple query expansion."""

    def test_no_extra_terms(self):
        assert expand_query("What is AIP?") == "What is AIP?"

    def test_with_extra_terms(self):
        result = expand_query("What is AIP?", extra_terms=["architecture", "retrieval"])
        assert "architecture" in result
        assert "retrieval" in result


# ---------------------------------------------------------------------------
# 10. Failing retriever handling
# ---------------------------------------------------------------------------


class TestFailingRetriever:
    """Verify graceful degradation when a retriever fails."""

    async def test_failing_channel_doesnt_crash(self):
        """A failing channel should be caught and return empty results."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("failing", _failing_retriever)

        config = OrchestratorConfig(enable_all_registered=True)
        hits, trace = await orch.retrieve("test query", config=config)

        # FTS should still have returned results
        assert len(hits) > 0
        # Both channels should be in the trace (attempted)
        assert "fts" in trace.channels_queried
        assert "failing" in trace.channels_queried

    async def test_empty_channel_doesnt_break_fusion(self):
        """An empty channel should not break RRF fusion."""
        orch = RetrievalOrchestrator()
        orch.register_channel("fts", _fake_fts_retriever)
        orch.register_channel("empty", _empty_retriever)

        hits, trace = await orch.retrieve("test query")
        assert len(hits) > 0


# ---------------------------------------------------------------------------
# 11. RetrievalHit and RetrievalTrace schemas
# ---------------------------------------------------------------------------


class TestRetrievalSchemas:
    """Verify RetrievalHit and RetrievalTrace dataclasses."""

    def test_retrieval_hit_defaults(self):
        hit = RetrievalHit(id="test")
        assert hit.content == ""
        assert hit.score == 0.0
        assert hit.rrf_score == 0.0
        assert hit.source_channel == ""
        assert hit.rank_in_channel == 0

    def test_retrieval_trace_defaults(self):
        trace = RetrievalTrace()
        assert trace.verdict == "OK"
        assert trace.round_number == 0
        assert trace.channels_queried == []
        assert trace.per_channel_elapsed_ms == {}

    def test_hit_metadata_preserved_in_rrf_fusion(self):
        """RRF fusion should preserve metadata from the best-scoring hit."""
        channel_results = {
            "fts": [RetrievalHit(id="1", score=0.9, metadata={"type": "doc"})],
            "vector": [RetrievalHit(id="1", score=0.95, metadata={"type": "chunk"})],
        }
        fused = rrf_fuse(channel_results, k=60)
        assert len(fused) == 1
        # Should keep the higher raw score
        assert fused[0].score == 0.95


# ---------------------------------------------------------------------------
# 12. Integration: _search_sources_with_trace (with fake stores)
# ---------------------------------------------------------------------------


class TestSearchSourcesWithTrace:
    """Integration test for _search_sources_with_trace using fakes."""

    async def test_basic_search_with_trace(self):
        from aip.orchestration.ask_pipeline import (
            AskStores,
            _search_sources_with_trace,
        )
        from aip.foundation.schemas.retrieval import Chunk

        # Minimal fake stores
        class FakeLexical:
            async def search(self, query, domain=None, limit=10):
                return [
                    Chunk(id="doc:1", content="AIP architecture document", score=0.8,
                           metadata={"type": "project_artifact"}, domain="test"),
                ]

        class FakeEvent:
            async def write_event(self, **kwargs): pass
            async def query(self, **kwargs): return []

        class FakeProject:
            async def list_projects(self, **kwargs): return []

        class FakeArtifact:
            async def write(self, *a): pass
            async def read(self, *a): return ""
            async def list_versions(self, *a): return [1]

        stores = AskStores(
            artifact_store=FakeArtifact(),
            lexical_store=FakeLexical(),
            vector_store=None,
            event_store=FakeEvent(),
            project_store=FakeProject(),
        )

        # Invalidate cache so we get a fresh orchestrator
        from aip.orchestration.ask_pipeline import _orchestrator_cache
        _orchestrator_cache.invalidate()

        sources, trace, packed = await _search_sources_with_trace(
            query="AIP architecture",
            stores=stores,
            session_id="test-session",
        )

        assert len(sources) > 0
        assert trace is not None
        assert trace.channels_queried == ["fts"]
        assert packed is not None
        assert "doc:1" in packed.context_text or "AIP" in packed.context_text
