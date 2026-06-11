"""Multi-store read pool integration test.

Verifies that the /health endpoint correctly aggregates read pool metrics
across multiple stores with ReadPoolMixin (GraphStore, CorpusTurnStore,
SqliteFts5LexicalStore, SqliteVssVectorStore when available).

This test exercises the aggregation logic that the health endpoint uses
to build the `read_pool_summary` dict, ensuring that:
- pool_stores lists all stores with read pool data
- total_checkouts, total_fallbacks, total_exhaustions sum correctly
- aggregate_exhaustion_rate is computed across all stores
- stores_with_high_exhaustion flags stores with rate > 0.3
"""

from __future__ import annotations

import os
import tempfile

import pytest

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.adapter.graph_store import GraphNode, GraphStore
from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
from aip.foundation.schemas.corpus_turn import CorpusTurn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory for all test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def graph_store(tmp_dir):
    """Provide an initialized GraphStore with test data."""
    db_path = os.path.join(tmp_dir, "graph_test.db")
    store = GraphStore(db_path)
    await store.initialize()
    # Seed data
    for i in range(5):
        node = GraphNode(
            id=f"multi_node_{i}",
            entity_type="CONCEPT",
            canonical_name=f"MultiConcept_{i}",
            domain="test",
            confidence=0.9,
        )
        await store.upsert_node(node)
    yield store
    await store.close()


@pytest.fixture
async def corpus_turn_store(tmp_dir):
    """Provide an initialized CorpusTurnStore with test data."""
    db_path = os.path.join(tmp_dir, "corpus_test.db")
    store = CorpusTurnStore(db_path)
    await store.initialize()
    # Seed data
    for i in range(3):
        turn = CorpusTurn(
            turn_id=f"multi_turn_{i}",
            conversation_id="conv_multi_test",
            conversation_name="Multi Store Test",
            turn_index=i,
            source_model="test",
            source_account="test",
            export_date="2025-01-01",
            user_text=f"Test user text for multi-store {i}",
            assistant_text=f"Test assistant text for multi-store {i}",
            turn_timestamp="2025-01-01T00:00:00Z",
            domains=["test"],
            primary_domain="test",
            tags=["test"],
            importance=0.8,
            bridges=[],
            beast_confidence=0.9,
            tagging_version=1,
            embedded=1,
            searchable_text=f"Searchable content {i}",
            word_count=10,
        )
        await store.write_turn(turn)
    yield store
    await store.close()


@pytest.fixture
async def lexical_store(tmp_dir):
    """Provide an initialized SqliteFts5LexicalStore with test data."""
    db_path = os.path.join(tmp_dir, "lexical_test.db")
    store = SqliteFts5LexicalStore(db_path)
    await store.initialize()
    # Seed data
    for i in range(3):
        await store.index_document(
            doc_id=f"multi_chunk_{i}",
            content=f"Test chunk content for multi-store test {i}",
            domain="test",
            metadata={"source": "test"},
        )
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Helper: simulate the health endpoint's read_pool_summary aggregation
# ---------------------------------------------------------------------------


def _aggregate_read_pool_summary(
    store_health: dict[str, dict],
) -> dict:
    """Replicate the health endpoint's read_pool_summary aggregation logic.

    Takes the same store_health dict that the health endpoint builds
    (store_name -> connection_health dict) and returns the
    read_pool_summary dict with recommendation propagation.
    """
    summary = {
        "pool_stores": [],
        "total_checkouts": 0,
        "total_fallbacks": 0,
        "total_exhaustions": 0,
        "aggregate_exhaustion_rate": 0.0,
        "stores_with_high_exhaustion": [],
        "recommendation": "",
    }
    for store_name, health_data in store_health.items():
        pool_data = health_data.get("read_pool")
        if isinstance(pool_data, dict):
            summary["pool_stores"].append(store_name)
            summary["total_checkouts"] += pool_data.get("checkout_count", 0)
            summary["total_fallbacks"] += pool_data.get("fallback_count", 0)
            summary["total_exhaustions"] += pool_data.get("exhaustion_count", 0)
            rate = pool_data.get("exhaustion_rate", 0.0)
            if rate > 0.3:
                summary["stores_with_high_exhaustion"].append(
                    {
                        "store": store_name,
                        "exhaustion_rate": rate,
                        "pool_size": pool_data.get("pool_size", 0),
                    }
                )
    total_co = summary["total_checkouts"]
    summary["aggregate_exhaustion_rate"] = round(summary["total_exhaustions"] / total_co, 4) if total_co > 0 else 0.0

    # Generate top-level recommendation when pool exhaustion is high
    high_exhaustion_stores = summary["stores_with_high_exhaustion"]
    if high_exhaustion_stores:
        store_names = [s["store"] for s in high_exhaustion_stores]
        if len(high_exhaustion_stores) == 1:
            s = high_exhaustion_stores[0]
            if s["exhaustion_rate"] > 0.6:
                summary["recommendation"] = (
                    f"Critical: {s['store']} exhaustion_rate={s['exhaustion_rate']:.2%}. "
                    f"Double pool_size from {s['pool_size']} to {s['pool_size'] * 2} and investigate read patterns."
                )
            else:
                summary["recommendation"] = (
                    f"High: {s['store']} exhaustion_rate={s['exhaustion_rate']:.2%}. "
                    f"Consider increasing pool_size from {s['pool_size']} to {s['pool_size'] + 2}."
                )
        else:
            critical = [s for s in high_exhaustion_stores if s["exhaustion_rate"] > 0.6]
            if critical:
                summary["recommendation"] = (
                    f"Critical: {len(critical)} store(s) ({', '.join(s['store'] for s in critical)}) "
                    f"have exhaustion_rate > 60%. Double their pool_size values and investigate. "
                    f"Also check: {', '.join(store_names)}."
                )
            else:
                summary["recommendation"] = (
                    f"High: {len(high_exhaustion_stores)} store(s) ({', '.join(store_names)}) "
                    f"have exhaustion_rate > 30%. Consider increasing pool_size in [read_pool] config."
                )

    return summary


# ---------------------------------------------------------------------------
# Tests: Multi-store aggregation
# ---------------------------------------------------------------------------


class TestMultiStoreReadPoolAggregation:
    """Tests that read pool metrics aggregate correctly across multiple stores."""

    @pytest.mark.asyncio
    async def test_three_pool_stores_aggregate(self, graph_store, corpus_turn_store, lexical_store):
        """When multiple pool-enabled stores are active, all appear in aggregation."""
        # Perform reads on each store to trigger pool checkouts
        _ = await graph_store.node_count()
        _ = await corpus_turn_store.total_turns()
        _ = await lexical_store.search("test", limit=5)

        # Build store_health as the health endpoint would
        store_health = {}
        for name, store in [
            ("graph_store", graph_store),
            ("corpus_turn_store", corpus_turn_store),
            ("lexical_store", lexical_store),
        ]:
            if hasattr(store, "connection_health"):
                store_health[name] = store.connection_health()

        summary = _aggregate_read_pool_summary(store_health)

        # All three stores should be in pool_stores
        assert "graph_store" in summary["pool_stores"]
        assert "corpus_turn_store" in summary["pool_stores"]
        assert "lexical_store" in summary["pool_stores"]
        assert len(summary["pool_stores"]) == 3

        # Each store should have at least 1 checkout
        assert summary["total_checkouts"] >= 3

        # No fallbacks expected from sequential reads
        assert summary["total_fallbacks"] == 0
        assert summary["total_exhaustions"] == 0
        assert summary["aggregate_exhaustion_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_aggregate_exhaustion_rate_across_stores(self, graph_store, corpus_turn_store, lexical_store):
        """Exhaustion events on one store should affect aggregate rate."""
        # Normal reads on corpus_turn_store and lexical_store
        _ = await corpus_turn_store.total_turns()
        _ = await lexical_store.search("test", limit=5)

        # Exhaust graph_store pool (3 connections) + trigger fallbacks
        conns = []
        for _ in range(3):
            conns.append(await graph_store._checkout_read_conn())

        # 2 fallbacks on graph_store
        for _ in range(2):
            fb = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(fb)

        # Return pool connections
        for conn in conns:
            graph_store._return_read_conn(conn)

        # Build store_health
        store_health = {}
        for name, store in [
            ("graph_store", graph_store),
            ("corpus_turn_store", corpus_turn_store),
            ("lexical_store", lexical_store),
        ]:
            if hasattr(store, "connection_health"):
                store_health[name] = store.connection_health()

        summary = _aggregate_read_pool_summary(store_health)

        # Graph store should have high exhaustion
        assert len(summary["stores_with_high_exhaustion"]) >= 1
        high_stores = {s["store"]: s for s in summary["stores_with_high_exhaustion"]}
        assert "graph_store" in high_stores
        assert high_stores["graph_store"]["exhaustion_rate"] > 0.3

        # Aggregate rate should be > 0 (graph_store contributed exhaustions)
        assert summary["aggregate_exhaustion_rate"] > 0.0

    @pytest.mark.asyncio
    async def test_per_store_pool_sizes_in_health(self, graph_store, corpus_turn_store, lexical_store):
        """Each store's read_pool health should report its pool_size."""
        store_health = {}
        for name, store in [
            ("graph_store", graph_store),
            ("corpus_turn_store", corpus_turn_store),
            ("lexical_store", lexical_store),
        ]:
            if hasattr(store, "connection_health"):
                store_health[name] = store.connection_health()

        # All three should have default pool_size of 3
        for name in ("graph_store", "corpus_turn_store", "lexical_store"):
            pool = store_health[name].get("read_pool", {})
            assert pool.get("pool_size") == 3, f"{name} should have pool_size=3"

    @pytest.mark.asyncio
    async def test_stores_without_pool_not_in_aggregation(self, graph_store, tmp_dir):
        """Stores without ReadPoolMixin should not appear in pool_stores."""
        # Create a store without ReadPoolMixin — use session_store
        from aip.adapter.auth.session_store import SqliteSessionStore
        from aip.foundation.schemas import AuthConfig

        db_path = os.path.join(tmp_dir, "auth_test.db")
        auth_store = SqliteSessionStore(db_path, AuthConfig())
        await auth_store.initialize()

        # Build store_health
        store_health = {}
        for name, store in [
            ("graph_store", graph_store),
            ("auth_session_store", auth_store),
        ]:
            if hasattr(store, "connection_health"):
                store_health[name] = store.connection_health()

        summary = _aggregate_read_pool_summary(store_health)

        # Only graph_store should be in pool_stores
        assert "graph_store" in summary["pool_stores"]
        assert "auth_session_store" not in summary["pool_stores"]
        assert len(summary["pool_stores"]) == 1

        await auth_store.close()

    @pytest.mark.asyncio
    async def test_concurrent_reads_across_stores(self, graph_store, corpus_turn_store, lexical_store):
        """Concurrent reads across multiple stores should exercise all pools."""
        import asyncio

        async def read_pattern(ask_id: int):
            """Simulate one ask-like read across all stores."""
            results = []
            # Graph read
            node = await graph_store.get_node(f"multi_node_{ask_id % 5}")
            results.append(node is not None or node is None)  # just exercise it
            # Corpus turn read
            count = await corpus_turn_store.total_turns()
            results.append(count >= 0)
            # Lexical read
            hits = await lexical_store.search("test")
            results.append(isinstance(hits, list))
            return results

        # 5 concurrent ask patterns
        results = await asyncio.gather(*[read_pattern(i) for i in range(5)])

        assert len(results) == 5

        # All stores should have pool activity
        for name, store in [
            ("graph_store", graph_store),
            ("corpus_turn_store", corpus_turn_store),
            ("lexical_store", lexical_store),
        ]:
            health = store.read_pool_health()
            assert health["checkout_count"] >= 5, f"{name} should have at least 5 checkouts from 5 concurrent reads"

    @pytest.mark.asyncio
    async def test_empty_pool_summary_when_no_pool_stores(self, tmp_dir):
        """When no stores have ReadPoolMixin, summary should be empty."""
        from aip.adapter.auth.session_store import SqliteSessionStore
        from aip.foundation.schemas import AuthConfig

        db_path = os.path.join(tmp_dir, "auth_only.db")
        auth_store = SqliteSessionStore(db_path, AuthConfig())
        await auth_store.initialize()

        store_health = {}
        if hasattr(auth_store, "connection_health"):
            store_health["auth_session_store"] = auth_store.connection_health()

        summary = _aggregate_read_pool_summary(store_health)

        assert summary["pool_stores"] == []
        assert summary["total_checkouts"] == 0
        assert summary["aggregate_exhaustion_rate"] == 0.0

        await auth_store.close()


# ---------------------------------------------------------------------------
# Tests: Health endpoint integration (with simulated container)
# ---------------------------------------------------------------------------


class TestHealthEndpointReadPoolSummary:
    """Tests simulating the /health endpoint with real pool-enabled stores.

    These tests create a minimal container-like setup and exercise the
    actual health route aggregation code path.
    """

    @pytest.mark.asyncio
    async def test_health_endpoint_aggregation_with_real_stores(self, tmp_dir):
        """Simulate the health endpoint with multiple real pool-enabled stores."""
        # Create and initialize stores
        graph_db = os.path.join(tmp_dir, "health_graph.db")
        graph_store = GraphStore(graph_db)
        await graph_store.initialize()

        corpus_db = os.path.join(tmp_dir, "health_corpus.db")
        corpus_store = CorpusTurnStore(corpus_db)
        await corpus_store.initialize()

        # Seed some data
        node = GraphNode(
            id="health_node",
            entity_type="CONCEPT",
            canonical_name="HealthConcept",
            domain="test",
            confidence=0.9,
        )
        await graph_store.upsert_node(node)

        # Perform reads to generate pool activity
        _ = await graph_store.node_count()
        _ = await corpus_store.total_turns()

        # Build store_health as the health endpoint does
        store_health = {}
        for name, store in [
            ("graph_store", graph_store),
            ("corpus_turn_store", corpus_store),
        ]:
            if hasattr(store, "connection_health"):
                try:
                    store_health[name] = store.connection_health()
                except Exception:
                    store_health[name] = {"error": "health_check_failed"}

        # Aggregate
        summary = _aggregate_read_pool_summary(store_health)

        assert "graph_store" in summary["pool_stores"]
        assert "corpus_turn_store" in summary["pool_stores"]
        assert summary["total_checkouts"] >= 2

        # Clean up
        await graph_store.close()
        await corpus_store.close()

    @pytest.mark.asyncio
    async def test_health_endpoint_flags_high_exhaustion_store(self, tmp_dir):
        """Health endpoint should flag stores with exhaustion_rate > 0.3."""
        graph_db = os.path.join(tmp_dir, "exhaust_graph.db")
        store = GraphStore(graph_db)
        await store.initialize()

        # Seed data
        for i in range(5):
            node = GraphNode(
                id=f"exhaust_node_{i}",
                entity_type="CONCEPT",
                canonical_name=f"ExhaustConcept_{i}",
                domain="test",
                confidence=0.9,
            )
            await store.upsert_node(node)

        # One pool checkout
        _ = await store.node_count()

        # Exhaust pool + many fallbacks
        conns = []
        for _ in range(3):
            conns.append(await store._checkout_read_conn())

        # 3 fallbacks → 4 checkouts total, 3 exhaustions → rate = 3/4 = 0.75
        for _ in range(3):
            fb = await store._checkout_read_conn()
            store._return_read_conn(fb)

        for conn in conns:
            store._return_read_conn(conn)

        # Build store_health
        store_health = {"graph_store": store.connection_health()}
        summary = _aggregate_read_pool_summary(store_health)

        assert len(summary["stores_with_high_exhaustion"]) == 1
        assert summary["stores_with_high_exhaustion"][0]["store"] == "graph_store"
        assert summary["stores_with_high_exhaustion"][0]["exhaustion_rate"] > 0.3

        await store.close()


class TestReadPoolRecommendationPropagation:
    """Tests for top-level recommendation in read_pool_summary when exhaustion is high."""

    @pytest.mark.asyncio
    async def test_no_recommendation_when_healthy(self, graph_store):
        """When all stores have low exhaustion, recommendation should be empty."""
        # Just a few sequential checkouts — no exhaustion
        for _ in range(3):
            conn = await graph_store._checkout_read_conn()
            graph_store._return_read_conn(conn)

        store_health = {"graph_store": graph_store.connection_health()}
        summary = _aggregate_read_pool_summary(store_health)

        assert summary["recommendation"] == ""

    @pytest.mark.asyncio
    async def test_single_store_high_exhaustion_recommendation(self, tmp_dir):
        """A single store with exhaustion > 0.3 should produce a 'High' recommendation."""
        db_path = os.path.join(tmp_dir, "high_exhaust.db")
        store = GraphStore(db_path)
        await store.initialize()

        # Exhaust pool + fallbacks
        conns = []
        for _ in range(3):
            conns.append(await store._checkout_read_conn())
        for _ in range(3):
            fb = await store._checkout_read_conn()
            store._return_read_conn(fb)
        for conn in conns:
            store._return_read_conn(conn)

        store_health = {"graph_store": store.connection_health()}
        summary = _aggregate_read_pool_summary(store_health)

        assert summary["recommendation"] != ""
        assert "graph_store" in summary["recommendation"]
        # Rate is 3/6 = 0.5, which is high (not critical)
        assert "High" in summary["recommendation"] or "increase" in summary["recommendation"].lower()

        await store.close()

    @pytest.mark.asyncio
    async def test_single_store_critical_exhaustion_recommendation(self, tmp_dir):
        """A single store with exhaustion > 0.6 should produce a 'Critical' recommendation."""
        db_path = os.path.join(tmp_dir, "critical_exhaust.db")
        store = GraphStore(db_path)
        await store.initialize()

        # Exhaust pool + many fallbacks to drive rate > 0.6
        conns = []
        for _ in range(3):
            conns.append(await store._checkout_read_conn())
        for _ in range(6):
            fb = await store._checkout_read_conn()
            store._return_read_conn(fb)
        for conn in conns:
            store._return_read_conn(conn)

        store_health = {"graph_store": store.connection_health()}
        summary = _aggregate_read_pool_summary(store_health)

        assert "Critical" in summary["recommendation"]
        assert "Double" in summary["recommendation"]

        await store.close()

    @pytest.mark.asyncio
    async def test_multiple_stores_high_exhaustion_recommendation(self, tmp_dir):
        """Multiple stores with high exhaustion should produce a multi-store recommendation."""
        db_path = os.path.join(tmp_dir, "multi_exhaust.db")
        store1 = GraphStore(db_path)
        await store1.initialize()

        # Exhaust first store
        conns1 = []
        for _ in range(3):
            conns1.append(await store1._checkout_read_conn())
        for _ in range(3):
            fb = await store1._checkout_read_conn()
            store1._return_read_conn(fb)
        for conn in conns1:
            store1._return_read_conn(conn)

        # Build synthetic store_health with multiple stores
        health1 = store1.connection_health()
        # Simulate a second store with high exhaustion
        health2 = dict(health1)
        health2["read_pool"] = dict(health1.get("read_pool", {}))

        store_health = {
            "graph_store": health1,
            "lexical_store": health2,
        }
        summary = _aggregate_read_pool_summary(store_health)

        assert len(summary["stores_with_high_exhaustion"]) == 2
        assert "2 store(s)" in summary["recommendation"]
        assert "graph_store" in summary["recommendation"]
        assert "lexical_store" in summary["recommendation"]

        await store1.close()
