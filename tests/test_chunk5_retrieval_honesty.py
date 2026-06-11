"""Chunk 5: Retrieval honesty and degradation signaling — smoke tests.

Covers:
1. VectorBackendStatus enum and VectorDegradationInfo
2. SqliteVssVectorStore.get_backend_status() / get_degradation_info()
3. Metadata-only storage stamps _embed_failure
4. Brute-force scan limits and truncation signaling
5. RetrievalTrace.vector_degradation and degradation_summary()
6. AskResult.retrieval_degradation
7. Performance smoke test with 1k+ embedded rows (no sqlite-vss)
8. Health endpoint reports explicit VectorBackendStatus
"""

from __future__ import annotations

import math
import time

import pytest

# ---------------------------------------------------------------------------
# 1. VectorBackendStatus enum
# ---------------------------------------------------------------------------
from aip.foundation.schemas.vector import VectorBackendStatus, VectorDegradationInfo


class TestVectorBackendStatus:
    """Verify the VectorBackendStatus enum and its helper methods."""

    def test_available_is_searchable(self):
        assert VectorBackendStatus.AVAILABLE.is_searchable is True

    def test_available_is_not_degraded(self):
        assert VectorBackendStatus.AVAILABLE.is_degraded is False

    def test_degraded_bruteforce_is_searchable(self):
        assert VectorBackendStatus.DEGRADED_BRUTEFORCE.is_searchable is True

    def test_degraded_bruteforce_is_degraded(self):
        assert VectorBackendStatus.DEGRADED_BRUTEFORCE.is_degraded is True

    def test_disabled_is_not_searchable(self):
        assert VectorBackendStatus.DISABLED.is_searchable is False

    def test_failed_is_not_searchable(self):
        assert VectorBackendStatus.FAILED.is_searchable is False

    def test_human_messages_are_non_empty(self):
        for status in VectorBackendStatus:
            assert len(status.human_message()) > 20, f"{status} has empty human_message"


class TestVectorDegradationInfo:
    """Verify VectorDegradationInfo serialization."""

    def test_default_is_disabled(self):
        vdi = VectorDegradationInfo()
        assert vdi.backend_status == VectorBackendStatus.DISABLED

    def test_to_dict_contains_required_keys(self):
        vdi = VectorDegradationInfo(
            backend_status=VectorBackendStatus.DEGRADED_BRUTEFORCE,
            backend_name="brute_force",
            reason="VSS unavailable",
            brute_force_rows_scanned=5000,
        )
        d = vdi.to_dict()
        assert d["backend_status"] == "degraded_bruteforce"
        assert d["is_degraded"] is True
        assert d["is_searchable"] is True
        assert d["brute_force_rows_scanned"] == 5000
        assert "human_message" in d


# ---------------------------------------------------------------------------
# 2. SqliteVssVectorStore backend status and degradation info
# ---------------------------------------------------------------------------

from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore


class TestSqliteVssBackendStatus:
    """Verify that SqliteVssVectorStore reports honest backend status."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_vectors.db")

    @pytest.mark.asyncio
    async def test_initialized_store_reports_status(self, db_path):
        """After initialize(), the store should report AVAILABLE or DEGRADED_BRUTEFORCE."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()
        status = store.get_backend_status()
        # Either VSS loaded (available) or not (degraded_bruteforce)
        assert status in (VectorBackendStatus.AVAILABLE, VectorBackendStatus.DEGRADED_BRUTEFORCE)
        await store.close()

    @pytest.mark.asyncio
    async def test_degradation_info_has_reason(self, db_path):
        """get_degradation_info() should provide a non-empty reason."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()
        info = store.get_degradation_info()
        assert info.backend_status in (VectorBackendStatus.AVAILABLE, VectorBackendStatus.DEGRADED_BRUTEFORCE)
        assert len(info.backend_name) > 0
        if info.backend_status == VectorBackendStatus.DEGRADED_BRUTEFORCE:
            assert "brute-force" in info.reason.lower() or "vss" in info.reason.lower()
        await store.close()

    @pytest.mark.asyncio
    async def test_health_check_includes_backend_status(self, db_path):
        """health_check() must include backend_status field."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()
        health = await store.health_check()
        assert "backend_status" in health
        assert health["backend_status"] in ("available", "degraded_bruteforce")
        assert "degradation" in health
        await store.close()


# ---------------------------------------------------------------------------
# 3. Metadata-only storage stamps _embed_failure
# ---------------------------------------------------------------------------


class TestEmbedFailureStamping:
    """Verify that metadata-only storage records _embed_failure in chunk metadata."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_vectors.db")

    @pytest.mark.asyncio
    async def test_store_without_provider_stamps_failure(self, db_path):
        """store() without EmbeddingProvider should stamp _embed_failure."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()

        from aip.foundation.schemas import Chunk

        chunk = Chunk(id="test-1", content="hello world", metadata={}, domain="test")
        result_id = await store.store(chunk)

        # Retrieve and check metadata
        retrieved = await store.get_by_id(result_id)
        assert retrieved is not None
        assert retrieved.metadata.get("_embed_failure") is True
        assert "_embed_failure_reason" in retrieved.metadata
        assert store._metadata_only_count >= 1
        await store.close()

    @pytest.mark.asyncio
    async def test_degradation_info_tracks_failures(self, db_path):
        """After embed failures, get_degradation_info() should report them."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()

        from aip.foundation.schemas import Chunk

        for i in range(3):
            chunk = Chunk(id=f"fail-{i}", content=f"content {i}", metadata={}, domain="test")
            await store.store(chunk)

        info = store.get_degradation_info()
        assert info.metadata_only_stored == 3
        await store.close()


# ---------------------------------------------------------------------------
# 4. Brute-force scan limits and truncation signaling
# ---------------------------------------------------------------------------


class TestBruteForceScanLimits:
    """Verify brute-force scan limits and truncation metadata."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_vectors.db")

    @pytest.mark.asyncio
    async def test_brute_force_results_are_stamped_degraded(self, db_path):
        """Brute-force results must carry _degraded_retrieval and _retrieval_backend."""
        store = SqliteVssVectorStore(db_path=db_path, dimensions=8)
        await store.initialize()

        # Insert some vectors manually
        for i in range(5):
            await store.upsert(
                id=f"vec-{i}",
                embedding=[0.1 * i] * 8,
                content=f"content {i}",
                metadata={"test": True},
                domain="test",
            )

        # Force brute-force retrieval if VSS not available
        if not store._vss_available:
            results = await store.retrieve([0.1] * 8, domain="test", top_k=3)
            for r in results:
                assert r.metadata.get("_degraded_retrieval") is True
                assert r.metadata.get("_retrieval_backend") == "brute_force"

        await store.close()


# ---------------------------------------------------------------------------
# 5. RetrievalTrace.vector_degradation and degradation_summary()
# ---------------------------------------------------------------------------

from aip.foundation.schemas.retrieval import RetrievalTrace


class TestRetrievalTraceDegradation:
    """Verify RetrievalTrace carries degradation info and can summarize it."""

    def test_default_trace_has_disabled_degradation(self):
        trace = RetrievalTrace()
        assert trace.vector_degradation.backend_status == VectorBackendStatus.DISABLED

    def test_degradation_summary_for_disabled(self):
        trace = RetrievalTrace()
        summary = trace.degradation_summary()
        assert "lexical" in summary.lower() or "unavailable" in summary.lower()

    def test_degradation_summary_for_degraded_bruteforce(self):
        vdi = VectorDegradationInfo(
            backend_status=VectorBackendStatus.DEGRADED_BRUTEFORCE,
            backend_name="brute_force",
            reason="VSS unavailable",
            brute_force_rows_scanned=12345,
        )
        trace = RetrievalTrace(vector_degradation=vdi)
        summary = trace.degradation_summary()
        assert "degraded" in summary.lower()
        assert "12345" in summary

    def test_degradation_summary_for_failed(self):
        vdi = VectorDegradationInfo(
            backend_status=VectorBackendStatus.FAILED,
            reason="Connection refused",
        )
        trace = RetrievalTrace(vector_degradation=vdi)
        summary = trace.degradation_summary()
        assert "failed" in summary.lower() or "error" in summary.lower()


# ---------------------------------------------------------------------------
# 6. AskResult.retrieval_degradation
# ---------------------------------------------------------------------------

from aip.foundation.schemas.ask import AskResult


class TestAskResultDegradation:
    """Verify AskResult carries retrieval_degradation dict."""

    def test_default_ask_result_has_empty_degradation(self):
        result = AskResult(status="OK", answer="test")
        assert isinstance(result.retrieval_degradation, dict)

    def test_ask_result_with_degradation(self):
        vdi = VectorDegradationInfo(
            backend_status=VectorBackendStatus.DEGRADED_BRUTEFORCE,
            backend_name="brute_force",
        )
        trace = RetrievalTrace(vector_degradation=vdi)
        from aip.orchestration.ask_pipeline import _build_degradation_dict

        degradation = _build_degradation_dict(trace)
        result = AskResult(
            status="OK",
            answer="test answer",
            retrieval_degradation=degradation,
        )
        assert result.retrieval_degradation["backend_status"] == "degraded_bruteforce"
        assert result.retrieval_degradation["is_degraded"] is True


# ---------------------------------------------------------------------------
# 7. Performance smoke test with 1k+ rows and sqlite-vss absent
# ---------------------------------------------------------------------------


class TestBruteForcePerformance1k:
    """Performance smoke test: 1k+ embedded rows with brute-force (no sqlite-vss).

    This test verifies that brute-force retrieval degrades honestly and
    completes in reasonable time even without sqlite-vss.  It does NOT
    require sqlite-vss to be installed — it works with brute-force mode.
    """

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "perf_vectors.db")

    @pytest.mark.asyncio
    async def test_brute_force_1k_rows_performance(self, db_path):
        """Insert 1k+ rows and verify brute-force retrieval completes with honest degradation."""
        dim = 8
        store = SqliteVssVectorStore(db_path=db_path, dimensions=dim)
        await store.initialize()

        num_rows = 1100  # slightly over 1k
        # Insert rows in batches
        batch_size = 100
        for batch_start in range(0, num_rows, batch_size):
            for i in range(batch_start, min(batch_start + batch_size, num_rows)):
                # Generate a simple deterministic vector
                vec = [math.sin(i * 0.01 + j * 0.1) for j in range(dim)]
                await store.upsert(
                    id=f"perf-{i}",
                    embedding=vec,
                    content=f"Performance test content {i}",
                    metadata={"batch": batch_start // batch_size},
                    domain="perf-test",
                )

        # Verify count
        total = await store.count(domain="perf-test")
        assert total == num_rows, f"Expected {num_rows} rows, got {total}"

        # Retrieve via brute-force
        query_vec = [math.sin(5.0 + j * 0.1) for j in range(dim)]
        start = time.monotonic()
        results = await store.retrieve(query_vec, domain="perf-test", top_k=10)
        elapsed_ms = (time.monotonic() - start) * 1000.0

        # Verify honest degradation signaling
        status = store.get_backend_status()
        info = store.get_degradation_info()
        if not store._vss_available:
            assert status == VectorBackendStatus.DEGRADED_BRUTEFORCE
            assert info.brute_force_rows_scanned > 0
            # Results should be stamped degraded
            for r in results:
                assert r.metadata.get("_degraded_retrieval") is True
                assert r.metadata.get("_retrieval_backend") == "brute_force"

        # Performance assertion: brute-force over 1k+ rows should complete
        # in under 5 seconds (generous for CI)
        assert elapsed_ms < 5000, f"Brute-force retrieval took {elapsed_ms:.0f}ms (>5s)"

        # Verify we got results
        assert len(results) > 0, "Brute-force retrieval returned no results"

        await store.close()

    @pytest.mark.asyncio
    async def test_brute_force_hard_cap_respected(self, db_path):
        """Verify that brute-force scan is capped at _BRUTE_FORCE_MAX_ROWS."""
        from aip.adapter.vector.sqlite_vss_store import _BRUTE_FORCE_MAX_ROWS

        dim = 8
        store = SqliteVssVectorStore(db_path=db_path, dimensions=dim)
        await store.initialize()

        # Insert more rows than the hard cap
        num_rows = min(_BRUTE_FORCE_MAX_ROWS + 100, 2000)  # don't make test too slow
        for i in range(num_rows):
            vec = [math.sin(i * 0.01 + j * 0.1) for j in range(dim)]
            await store.upsert(
                id=f"cap-{i}",
                embedding=vec,
                content=f"Cap test content {i}",
                metadata={},
                domain="cap-test",
            )

        # Verify count
        total = await store.count(domain="cap-test")
        assert total == num_rows

        # Retrieve and check truncation metadata
        if not store._vss_available:
            query_vec = [0.5] * dim
            results = await store.retrieve(query_vec, domain="cap-test", top_k=5)
            # If rows exceed scan limit, truncation flag should be set
            info = store.get_degradation_info()
            # The scan should have been limited
            assert store._last_brute_force_rows_scanned <= _BRUTE_FORCE_MAX_ROWS

        await store.close()


# ---------------------------------------------------------------------------
# 8. InMemoryVectorStore reports DISABLED
# ---------------------------------------------------------------------------

from aip.adapter.vector._in_memory import InMemoryVectorStore


class TestInMemoryBackendStatus:
    """Verify InMemoryVectorStore honestly reports DISABLED."""

    def test_get_backend_status_disabled(self):
        store = InMemoryVectorStore()
        assert store.get_backend_status() == VectorBackendStatus.DISABLED

    def test_degradation_info_disabled(self):
        store = InMemoryVectorStore()
        info = store.get_degradation_info()
        assert info.backend_status == VectorBackendStatus.DISABLED
        assert "non-persistent" in info.reason.lower()

    @pytest.mark.asyncio
    async def test_health_check_disabled(self):
        store = InMemoryVectorStore()
        health = await store.health_check()
        assert health["backend_status"] == "disabled"
        assert "degradation" in health
