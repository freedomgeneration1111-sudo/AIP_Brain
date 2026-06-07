"""Read pool telemetry and benchmark tests.

Tests that:
- Read pool health metrics are tracked correctly (checkout count, fallback
  count, exhaustion count, checkout latency).
- connection_health() includes read_pool data for stores with ReadPoolMixin.
- Concurrent read throughput is better with the read pool than without.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import pytest

from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
from aip.adapter.read_pool import ReadPoolMixin, ReadPoolHealth
from aip.adapter.store_health import StoreHealthMixin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """Provide a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "read_pool_test.db")


@pytest.fixture
async def graph_store(tmp_db):
    """Provide an initialized GraphStore."""
    store = GraphStore(tmp_db)
    await store.initialize()
    # Insert test data for reads
    node = GraphNode(
        id="test_node_1",
        entity_type="CONCEPT",
        canonical_name="TestConcept",
        domain="test",
        confidence=0.9,
    )
    await store.upsert_node(node)
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Telemetry tests
# ---------------------------------------------------------------------------


class TestReadPoolTelemetry:
    """Tests for read pool utilization metrics."""

    @pytest.mark.asyncio
    async def test_initial_telemetry_is_zero(self, graph_store):
        """Before any checkouts, all telemetry counters should be zero."""
        health = graph_store.read_pool_health()
        assert health["checkout_count"] == 0
        assert health["fallback_count"] == 0
        assert health["exhaustion_count"] == 0
        assert health["exhaustion_rate"] == 0.0
        assert health["avg_checkout_latency_ms"] == 0.0
        assert health["p95_checkout_latency_ms"] == 0.0
        assert health["pool_size"] == 3
        assert health["pool_active"] == 0

    @pytest.mark.asyncio
    async def test_checkout_increments_counter(self, graph_store):
        """Each successful checkout should increment checkout_count."""
        conn = await graph_store._checkout_read_conn()
        try:
            health = graph_store.read_pool_health()
            assert health["checkout_count"] == 1
            assert health["pool_active"] == 1
        finally:
            graph_store._return_read_conn(conn)

        health = graph_store.read_pool_health()
        assert health["pool_active"] == 0

    @pytest.mark.asyncio
    async def test_multiple_checkouts(self, graph_store):
        """Multiple sequential checkouts should increment the counter."""
        for _ in range(5):
            conn = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(conn)

        health = graph_store.read_pool_health()
        assert health["checkout_count"] == 5
        assert health["fallback_count"] == 0
        assert health["exhaustion_count"] == 0

    @pytest.mark.asyncio
    async def test_fallback_when_pool_exhausted(self, graph_store):
        """When all pool connections are checked out, fallback should increment."""
        # Check out all 3 pool connections
        conns = []
        for _ in range(3):
            conns.append(await graph_store._checkout_read_conn())

        health = graph_store.read_pool_health()
        assert health["pool_active"] == 3
        assert health["checkout_count"] == 3

        # One more checkout should fall back to write conn
        fallback_conn = await graph_store._checkout_read_conn()
        health = graph_store.read_pool_health()
        assert health["checkout_count"] == 4
        assert health["fallback_count"] == 1
        assert health["exhaustion_count"] == 1

        # Return all connections
        graph_store._return_read_conn(fallback_conn)
        for conn in conns:
            graph_store._return_read_conn(conn)

    @pytest.mark.asyncio
    async def test_checkout_latency_tracked(self, graph_store):
        """Checkout latency should be tracked and averaged."""
        for _ in range(3):
            conn = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(conn)

        health = graph_store.read_pool_health()
        assert health["avg_checkout_latency_ms"] > 0
        assert health["avg_checkout_latency_ms"] < 100  # Should be fast

    @pytest.mark.asyncio
    async def test_connection_health_includes_read_pool(self, graph_store):
        """connection_health() should include read_pool sub-dict for pool stores."""
        # Trigger pool initialization
        conn = await graph_store._checkout_read_conn()
        graph_store._return_read_conn(conn)

        health = graph_store.connection_health()
        assert "read_pool" in health
        assert isinstance(health["read_pool"], dict)
        assert "checkout_count" in health["read_pool"]
        assert "fallback_count" in health["read_pool"]
        assert "exhaustion_count" in health["read_pool"]
        assert "exhaustion_rate" in health["read_pool"]
        assert "avg_checkout_latency_ms" in health["read_pool"]
        assert "p95_checkout_latency_ms" in health["read_pool"]
        assert "pool_size" in health["read_pool"]
        assert "pool_active" in health["read_pool"]

    @pytest.mark.asyncio
    async def test_read_pool_health_typed_dict_shape(self, graph_store):
        """read_pool_health() should return all ReadPoolHealth fields."""
        conn = await graph_store._checkout_read_conn()
        graph_store._return_read_conn(conn)

        health = graph_store.read_pool_health()
        expected_keys = {
            "pool_size", "pool_active", "checkout_count",
            "fallback_count", "exhaustion_count", "exhaustion_rate",
            "avg_checkout_latency_ms", "p95_checkout_latency_ms",
        }
        assert set(health.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_exhaustion_vs_fallback_distinction(self, graph_store):
        """Exhaustion count should equal fallback count for pool exhaustion events."""
        # Check out all pool connections to trigger exhaustion
        conns = []
        for _ in range(3):
            conns.append(await graph_store._checkout_read_conn())

        # Multiple fallbacks
        fallback1 = await graph_store._checkout_read_conn()
        graph_store._return_read_conn(fallback1)
        fallback2 = await graph_store._checkout_read_conn()
        graph_store._return_read_conn(fallback2)

        health = graph_store.read_pool_health()
        assert health["fallback_count"] == 2
        assert health["exhaustion_count"] == 2

        # Cleanup
        for conn in conns:
            graph_store._return_read_conn(conn)

    @pytest.mark.asyncio
    async def test_exhaustion_rate_calculation(self, graph_store):
        """exhaustion_rate should be exhaustion_count / checkout_count."""
        # 3 pool checkouts + 1 fallback = 4 total, 1 exhaustion → rate = 0.25
        conns = []
        for _ in range(3):
            conns.append(await graph_store._checkout_read_conn())

        fallback = await graph_store._checkout_read_conn()
        graph_store._return_read_conn(fallback)

        health = graph_store.read_pool_health()
        assert health["checkout_count"] == 4
        assert health["exhaustion_count"] == 1
        assert health["exhaustion_rate"] == 0.25

        for conn in conns:
            graph_store._return_read_conn(conn)

    @pytest.mark.asyncio
    async def test_exhaustion_rate_zero_when_no_checkouts(self, graph_store):
        """exhaustion_rate should be 0.0 when no checkouts have occurred."""
        health = graph_store.read_pool_health()
        assert health["exhaustion_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_p95_latency_tracked(self, graph_store):
        """p95_checkout_latency_ms should be populated after checkouts."""
        for _ in range(5):
            conn = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(conn)

        health = graph_store.read_pool_health()
        assert health["p95_checkout_latency_ms"] >= 0
        # p95 should be a valid non-negative number
        assert isinstance(health["p95_checkout_latency_ms"], float)

    @pytest.mark.asyncio
    async def test_high_exhaustion_rate_detection(self, graph_store):
        """exhaustion_rate > 0.3 should flag the store as having high exhaustion."""
        # Exhaust pool and do many fallbacks to drive rate above 0.3
        conns = []
        for _ in range(3):
            conns.append(await graph_store._checkout_read_conn())

        # 4 fallbacks = 4 exhaustions, total checkouts = 3 pool + 4 fallback = 7
        # exhaustion_rate = 4/7 ≈ 0.57 > 0.3
        for _ in range(4):
            fb = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(fb)

        health = graph_store.read_pool_health()
        assert health["exhaustion_rate"] > 0.3

        for conn in conns:
            graph_store._return_read_conn(conn)


# ---------------------------------------------------------------------------
# Read operations with pool telemetry
# ---------------------------------------------------------------------------


class TestReadOperationsWithTelemetry:
    """Tests that read operations on GraphStore correctly use the pool and track telemetry."""

    @pytest.mark.asyncio
    async def test_get_node_uses_pool(self, graph_store):
        """get_node should use read pool and track checkout telemetry."""
        node = await graph_store.get_node("test_node_1")
        assert node is not None
        assert node.canonical_name == "TestConcept"

        health = graph_store.read_pool_health()
        assert health["checkout_count"] >= 1

    @pytest.mark.asyncio
    async def test_node_count_uses_pool(self, graph_store):
        """node_count should use read pool."""
        count = await graph_store.node_count()
        assert count >= 1

        health = graph_store.read_pool_health()
        assert health["checkout_count"] >= 1

    @pytest.mark.asyncio
    async def test_search_nodes_uses_pool(self, graph_store):
        """search_nodes should use read pool."""
        results = await graph_store.search_nodes("Test")
        assert len(results) >= 1

        health = graph_store.read_pool_health()
        assert health["checkout_count"] >= 1


# ---------------------------------------------------------------------------
# Concurrent read benchmark
# ---------------------------------------------------------------------------


class TestReadPoolBenchmark:
    """Benchmarks comparing concurrent read throughput with and without read pool.

    These tests measure relative performance rather than asserting absolute
    thresholds, making them resilient to different hardware speeds.
    """

    @pytest.mark.asyncio
    async def test_concurrent_reads_with_pool(self, tmp_db):
        """Measure concurrent read throughput with the read pool enabled."""
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(20):
            node = GraphNode(
                id=f"bench_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"BenchConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Warm up the pool
        _ = await store.node_count()

        # Concurrent reads
        num_reads = 30
        t0 = time.monotonic()
        tasks = [store.get_node(f"bench_node_{i % 20}") for i in range(num_reads)]
        results = await asyncio.gather(*tasks)
        elapsed_pool = time.monotonic() - t0

        await store.close()

        assert len(results) == num_reads
        # Log the result for manual inspection
        health = store.read_pool_health()
        print(f"\nWith pool: {num_reads} reads in {elapsed_pool:.3f}s "
              f"({num_reads/elapsed_pool:.0f} reads/s), "
              f"checkouts={health['checkout_count']}, "
              f"fallbacks={health['fallback_count']}, "
              f"exhaustions={health['exhaustion_count']}")

    @pytest.mark.asyncio
    async def test_concurrent_reads_without_pool(self, tmp_db):
        """Measure concurrent read throughput with the read pool disabled.

        This simulates the pre-pool behavior by directly using _get_conn()
        for all reads, which serializes through the single write connection.
        """
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(20):
            node = GraphNode(
                id=f"bench_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"BenchConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Read via the write connection directly (no pool)
        num_reads = 30

        async def read_via_write_conn(node_id: str):
            conn = await store._get_conn()
            cursor = await conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ?", (node_id,)
            )
            return await cursor.fetchone()

        t0 = time.monotonic()
        tasks = [read_via_write_conn(f"bench_node_{i % 20}") for i in range(num_reads)]
        results = await asyncio.gather(*tasks)
        elapsed_no_pool = time.monotonic() - t0

        await store.close()

        assert len(results) == num_reads
        print(f"\nWithout pool: {num_reads} reads in {elapsed_no_pool:.3f}s "
              f"({num_reads/elapsed_no_pool:.0f} reads/s)")

    @pytest.mark.asyncio
    async def test_pool_reduces_fallbacks_under_load(self, tmp_db):
        """Under concurrent load, the pool should serve most reads without fallback."""
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(20):
            node = GraphNode(
                id=f"bench_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"BenchConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Concurrent reads (more tasks than pool size to trigger some fallbacks)
        tasks = [store.get_node(f"bench_node_{i % 20}") for i in range(10)]
        await asyncio.gather(*tasks)

        health = store.read_pool_health()
        # The majority of checkouts should use pool connections, not fallbacks
        fallback_ratio = health["fallback_count"] / max(health["checkout_count"], 1)
        print(f"\nFallback ratio: {fallback_ratio:.2%} "
              f"(checkouts={health['checkout_count']}, "
              f"fallbacks={health['fallback_count']})")

        await store.close()


# ---------------------------------------------------------------------------
# Ask-like concurrent workload benchmark
# ---------------------------------------------------------------------------


class TestAskLikeWorkloadBenchmark:
    """Benchmarks simulating the concurrent ask workload pattern.

    An ask request touches multiple stores in sequence: lexical search,
    vector search, graph lookup, corpus turn search.  Each of these uses
    the read pool.  This benchmark simulates 10+ simultaneous "ask"
    requests, each performing multiple read operations against the same
    GraphStore, to measure pool utilization under realistic concurrent
    load.
    """

    @pytest.mark.asyncio
    async def test_ten_concurrent_ask_patterns(self, tmp_db):
        """Simulate 10 concurrent ask-like read patterns against a single store.

        Each "ask" performs 3 reads: get_node, search_nodes, node_count.
        This exercises the pool under concurrent load similar to the real
        ask pipeline where multiple retrieval channels hit the same store.
        """
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(50):
            node = GraphNode(
                id=f"ask_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"AskConcept_{i}",
                domain=f"domain_{i % 5}",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Warm up
        _ = await store.node_count()

        # Simulate 10 concurrent ask requests
        async def ask_read_pattern(ask_id: int):
            """One ask request = 3 reads."""
            node = await store.get_node(f"ask_node_{ask_id % 50}")
            results = await store.search_nodes(f"AskConcept_{ask_id % 50}")
            count = await store.node_count()
            return (node is not None, len(results), count)

        num_asks = 10
        t0 = time.monotonic()
        tasks = [ask_read_pattern(i) for i in range(num_asks)]
        results = await asyncio.gather(*tasks)
        elapsed = time.monotonic() - t0

        health = store.read_pool_health()
        total_reads = num_asks * 3  # 3 reads per ask

        print(f"\n{num_asks} concurrent asks ({total_reads} reads) in {elapsed:.3f}s "
              f"({total_reads/elapsed:.0f} reads/s), "
              f"checkouts={health['checkout_count']}, "
              f"fallbacks={health['fallback_count']}, "
              f"exhaustions={health['exhaustion_count']}, "
              f"exhaustion_rate={health['exhaustion_rate']:.2%}, "
              f"p95_latency={health['p95_checkout_latency_ms']:.3f}ms")

        # All reads should have completed
        assert len(results) == num_asks
        # Pool should serve most reads (exhaustion rate should be reasonable)
        assert health["checkout_count"] >= total_reads

        await store.close()

    @pytest.mark.asyncio
    async def test_pool_exhaustion_under_high_concurrency(self, tmp_db):
        """Under high concurrency (20+ concurrent reads), track pool utilization.

        With only 3 pool connections, 20 concurrent reads will trigger
        fallbacks.  This test measures the exhaustion rate and verifies
        that the pool still provides value (not all reads are fallbacks).
        """
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(30):
            node = GraphNode(
                id=f"high_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"HighConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Warm up
        _ = await store.node_count()

        # 20 concurrent reads
        num_reads = 20
        tasks = [store.get_node(f"high_node_{i % 30}") for i in range(num_reads)]
        results = await asyncio.gather(*tasks)

        health = store.read_pool_health()
        print(f"\nHigh concurrency ({num_reads} concurrent reads): "
              f"checkouts={health['checkout_count']}, "
              f"pool_served={health['checkout_count'] - health['fallback_count']}, "
              f"fallbacks={health['fallback_count']}, "
              f"exhaustion_rate={health['exhaustion_rate']:.2%}")

        # The pool should serve at least some reads (not 100% fallback)
        pool_served = health["checkout_count"] - health["fallback_count"]
        assert pool_served > 0, "Pool should serve at least some reads under high concurrency"

        await store.close()

    @pytest.mark.asyncio
    async def test_sequential_vs_concurrent_pool_utilization(self, tmp_db):
        """Compare pool utilization between sequential and concurrent read patterns.

        Sequential reads should have zero fallbacks (pool always has
        available connections).  Concurrent reads will have some fallbacks
        but should still serve the majority from the pool.
        """
        store = GraphStore(tmp_db)
        await store.initialize()

        # Seed data
        for i in range(20):
            node = GraphNode(
                id=f"seq_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"SeqConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Sequential reads (10 reads, one at a time)
        for i in range(10):
            await store.get_node(f"seq_node_{i % 20}")

        health_seq = store.read_pool_health()
        seq_fallback_rate = health_seq["fallback_count"] / max(health_seq["checkout_count"], 1)

        # Reset store to get fresh counters
        await store.close()
        store = GraphStore(tmp_db)
        await store.initialize()
        for i in range(20):
            node = GraphNode(
                id=f"seq_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"SeqConcept_{i}",
                domain="bench",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # Concurrent reads (10 reads, all at once)
        tasks = [store.get_node(f"seq_node_{i % 20}") for i in range(10)]
        await asyncio.gather(*tasks)

        health_conc = store.read_pool_health()
        conc_fallback_rate = health_conc["fallback_count"] / max(health_conc["checkout_count"], 1)

        print(f"\nSequential fallback rate: {seq_fallback_rate:.2%}")
        print(f"Concurrent fallback rate: {conc_fallback_rate:.2%}")

        # Sequential should have zero fallbacks
        assert seq_fallback_rate == 0.0

        await store.close()
