"""Unit tests for GraphStore async adapter.

Verifies all CRUD operations, search, extraction log,
and connection lifecycle (initialize, close, _reset_conn).
"""

from __future__ import annotations

import pytest

from aip.adapter.graph_store import GraphEdge, GraphNode, GraphStore


@pytest.fixture
async def graph_store(tmp_path):
    """Create a fresh GraphStore with a temporary database."""
    db_path = str(tmp_path / "test_graph.db")
    store = GraphStore(db_path)
    await store.initialize()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Node CRUD
# ---------------------------------------------------------------------------


class TestNodeCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get_node(self, graph_store):
        node = GraphNode(
            id="test_node_1",
            entity_type="CONCEPT",
            canonical_name="Test Concept",
            domain="testing",
            confidence=0.9,
            source="manual",
        )
        await graph_store.upsert_node(node)

        result = await graph_store.get_node("test_node_1")
        assert result is not None
        assert result.id == "test_node_1"
        assert result.canonical_name == "Test Concept"
        assert result.entity_type == "CONCEPT"
        assert result.domain == "testing"
        assert result.confidence == 0.9
        assert result.source == "manual"

    @pytest.mark.asyncio
    async def test_upsert_node_preserves_created_at(self, graph_store):
        node = GraphNode(
            id="preserve_test",
            entity_type="PERSON",
            canonical_name="Original",
        )
        await graph_store.upsert_node(node)
        original = await graph_store.get_node("preserve_test")
        assert original is not None
        original_created_at = original.created_at

        # Update the node
        node2 = GraphNode(
            id="preserve_test",
            entity_type="PERSON",
            canonical_name="Updated Name",
        )
        await graph_store.upsert_node(node2)
        updated = await graph_store.get_node("preserve_test")
        assert updated is not None
        assert updated.canonical_name == "Updated Name"
        assert updated.created_at == original_created_at
        assert updated.updated_at is not None

    @pytest.mark.asyncio
    async def test_get_node_returns_none_for_missing(self, graph_store):
        result = await graph_store.get_node("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_node(self, graph_store):
        node = GraphNode(id="delete_me", entity_type="CONCEPT", canonical_name="Delete Me")
        await graph_store.upsert_node(node)
        assert await graph_store.get_node("delete_me") is not None

        await graph_store.delete_node("delete_me")
        assert await graph_store.get_node("delete_me") is None

    @pytest.mark.asyncio
    async def test_delete_node_removes_edges(self, graph_store):
        node_a = GraphNode(id="node_a", entity_type="CONCEPT", canonical_name="A")
        node_b = GraphNode(id="node_b", entity_type="CONCEPT", canonical_name="B")
        await graph_store.upsert_node(node_a)
        await graph_store.upsert_node(node_b)

        edge = GraphEdge(
            id="node_a__CONNECTS__node_b",
            source_id="node_a",
            target_id="node_b",
            relationship_type="CONNECTS",
        )
        await graph_store.upsert_edge(edge)

        await graph_store.delete_node("node_a")
        edges = await graph_store.get_edges_for_node("node_a")
        assert len(edges) == 0

    @pytest.mark.asyncio
    async def test_node_count(self, graph_store):
        assert await graph_store.node_count() == 0

        for i in range(5):
            await graph_store.upsert_node(GraphNode(
                id=f"count_{i}", entity_type="CONCEPT", canonical_name=f"Node {i}",
            ))

        assert await graph_store.node_count() == 5

    @pytest.mark.asyncio
    async def test_get_all_nodes_with_confidence_filter(self, graph_store):
        await graph_store.upsert_node(GraphNode(
            id="high_conf", entity_type="CONCEPT", canonical_name="High", confidence=0.9,
        ))
        await graph_store.upsert_node(GraphNode(
            id="low_conf", entity_type="CONCEPT", canonical_name="Low", confidence=0.2,
        ))

        all_nodes = await graph_store.get_all_nodes(min_confidence=0.0)
        assert len(all_nodes) == 2

        high_only = await graph_store.get_all_nodes(min_confidence=0.5)
        assert len(high_only) == 1
        assert high_only[0].id == "high_conf"


# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------


class TestEdgeCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get_edges(self, graph_store):
        node_a = GraphNode(id="a", entity_type="CONCEPT", canonical_name="A")
        node_b = GraphNode(id="b", entity_type="CONCEPT", canonical_name="B")
        await graph_store.upsert_node(node_a)
        await graph_store.upsert_node(node_b)

        edge = GraphEdge(
            id="a__CONNECTS__b",
            source_id="a",
            target_id="b",
            relationship_type="CONNECTS",
            bridge_tag="a->b",
            confidence=0.8,
            evidence_turn_ids=["turn_1", "turn_2"],
            weight=2.0,
        )
        await graph_store.upsert_edge(edge)

        edges = await graph_store.get_edges_for_node("a")
        assert len(edges) == 1
        assert edges[0].source_id == "a"
        assert edges[0].target_id == "b"
        assert edges[0].bridge_tag == "a->b"
        assert edges[0].evidence_turn_ids == ["turn_1", "turn_2"]
        assert edges[0].weight == 2.0

    @pytest.mark.asyncio
    async def test_edge_count(self, graph_store):
        for i in range(3):
            await graph_store.upsert_node(GraphNode(id=f"n{i}", entity_type="CONCEPT", canonical_name=f"N{i}"))
            if i > 0:
                await graph_store.upsert_edge(GraphEdge(
                    id=f"n0__CONNECTS__n{i}",
                    source_id="n0",
                    target_id=f"n{i}",
                    relationship_type="CONNECTS",
                ))

        assert await graph_store.edge_count() == 2

    @pytest.mark.asyncio
    async def test_get_all_edges_with_confidence_filter(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="x", entity_type="CONCEPT", canonical_name="X"))
        await graph_store.upsert_node(GraphNode(id="y", entity_type="CONCEPT", canonical_name="Y"))
        await graph_store.upsert_node(GraphNode(id="z", entity_type="CONCEPT", canonical_name="Z"))

        await graph_store.upsert_edge(GraphEdge(
            id="x__CONNECTS__y", source_id="x", target_id="y",
            relationship_type="CONNECTS", confidence=0.9,
        ))
        await graph_store.upsert_edge(GraphEdge(
            id="x__CONNECTS__z", source_id="x", target_id="z",
            relationship_type="CONNECTS", confidence=0.2,
        ))

        all_edges = await graph_store.get_all_edges(min_confidence=0.0)
        assert len(all_edges) == 2

        high_only = await graph_store.get_all_edges(min_confidence=0.5)
        assert len(high_only) == 1


# ---------------------------------------------------------------------------
# Neighbors and search
# ---------------------------------------------------------------------------


class TestNeighborsAndSearch:
    @pytest.mark.asyncio
    async def test_get_neighbors(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="center", entity_type="CONCEPT", canonical_name="Center"))
        await graph_store.upsert_node(GraphNode(id="neighbor1", entity_type="CONCEPT", canonical_name="N1"))
        await graph_store.upsert_node(GraphNode(id="neighbor2", entity_type="CONCEPT", canonical_name="N2"))
        await graph_store.upsert_node(GraphNode(id="isolated", entity_type="CONCEPT", canonical_name="Isolated"))

        await graph_store.upsert_edge(GraphEdge(
            id="center__CONNECTS__neighbor1",
            source_id="center", target_id="neighbor1",
            relationship_type="CONNECTS", confidence=0.8,
        ))
        await graph_store.upsert_edge(GraphEdge(
            id="neighbor2__CONNECTS__center",
            source_id="neighbor2", target_id="center",
            relationship_type="CONNECTS", confidence=0.8,
        ))

        neighbors = await graph_store.get_neighbors("center", min_confidence=0.5)
        neighbor_ids = {n.id for n in neighbors}
        assert "neighbor1" in neighbor_ids
        assert "neighbor2" in neighbor_ids
        assert "isolated" not in neighbor_ids
        assert "center" not in neighbor_ids

    @pytest.mark.asyncio
    async def test_get_neighbors_respects_confidence(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="hub", entity_type="CONCEPT", canonical_name="Hub"))
        await graph_store.upsert_node(GraphNode(id="high", entity_type="CONCEPT", canonical_name="High"))
        await graph_store.upsert_node(GraphNode(id="low", entity_type="CONCEPT", canonical_name="Low"))

        await graph_store.upsert_edge(GraphEdge(
            id="hub__CONNECTS__high", source_id="hub", target_id="high",
            relationship_type="CONNECTS", confidence=0.9,
        ))
        await graph_store.upsert_edge(GraphEdge(
            id="hub__CONNECTS__low", source_id="hub", target_id="low",
            relationship_type="CONNECTS", confidence=0.2,
        ))

        neighbors = await graph_store.get_neighbors("hub", min_confidence=0.5)
        neighbor_ids = {n.id for n in neighbors}
        assert "high" in neighbor_ids
        assert "low" not in neighbor_ids

    @pytest.mark.asyncio
    async def test_search_nodes_substring(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="ez", entity_type="CONCEPT", canonical_name="EZ Water"))
        await graph_store.upsert_node(GraphNode(id="nbcm", entity_type="CONCEPT", canonical_name="NBCM Framework"))

        results = await graph_store.search_nodes("water")
        assert len(results) == 1
        assert results[0].canonical_name == "EZ Water"

    @pytest.mark.asyncio
    async def test_search_nodes_with_type_filter(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="concept1", entity_type="CONCEPT", canonical_name="Test Item"))
        await graph_store.upsert_node(GraphNode(id="person1", entity_type="PERSON", canonical_name="Test Person"))

        results = await graph_store.search_nodes("Test", entity_type="PERSON")
        assert len(results) == 1
        assert results[0].entity_type == "PERSON"

    @pytest.mark.asyncio
    async def test_search_nodes_with_domain_filter(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="d1", entity_type="CONCEPT", canonical_name="Item", domain="physics"))
        await graph_store.upsert_node(GraphNode(id="d2", entity_type="CONCEPT", canonical_name="Item", domain="biology"))

        results = await graph_store.search_nodes("Item", domain="physics")
        assert len(results) == 1
        assert results[0].domain == "physics"


# ---------------------------------------------------------------------------
# Extraction log
# ---------------------------------------------------------------------------


class TestExtractionLog:
    @pytest.mark.asyncio
    async def test_is_turn_extracted_false_initially(self, graph_store):
        assert await graph_store.is_turn_extracted("turn_1") is False

    @pytest.mark.asyncio
    async def test_log_and_check_turn_extracted(self, graph_store):
        await graph_store.log_turn_extracted("turn_1", entities=3, relationships=2)
        assert await graph_store.is_turn_extracted("turn_1") is True

    @pytest.mark.asyncio
    async def test_log_turn_extracted_is_idempotent(self, graph_store):
        await graph_store.log_turn_extracted("turn_1", entities=2, relationships=1)
        await graph_store.log_turn_extracted("turn_1", entities=5, relationships=3)
        assert await graph_store.is_turn_extracted("turn_1") is True


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path):
        db_path = str(tmp_path / "lifecycle.db")
        store = GraphStore(db_path)
        await store.initialize()

        # Tables should be created — verify by writing data
        await store.upsert_node(GraphNode(id="lc", entity_type="CONCEPT", canonical_name="Lifecycle"))
        node = await store.get_node("lc")
        assert node is not None
        await store.close()

    @pytest.mark.asyncio
    async def test_close_and_reopen(self, tmp_path):
        db_path = str(tmp_path / "lifecycle.db")
        store = GraphStore(db_path)
        await store.initialize()

        await store.upsert_node(GraphNode(id="persist", entity_type="CONCEPT", canonical_name="Persist"))
        await store.close()

        # Reopen and verify data persisted
        store2 = GraphStore(db_path)
        await store2.initialize()
        node = await store2.get_node("persist")
        assert node is not None
        assert node.canonical_name == "Persist"
        await store2.close()

    @pytest.mark.asyncio
    async def test_health_check(self, graph_store):
        health = await graph_store.health_check()
        assert health["connected"] is True
        assert health["nodes"] == 0
        assert health["edges"] == 0

    @pytest.mark.asyncio
    async def test_connection_health_via_mixin(self, graph_store):
        # Trigger a persistent connection by doing any operation
        await graph_store.node_count()

        health = graph_store.connection_health()
        assert health["store_type"] == "GraphStore"
        assert health["connected"] is True
        assert health["tables_ready"] is True
        assert health["connection_age_seconds"] >= 0
        assert health["resets"] == 0
        # New health metrics
        assert "seconds_since_last_op" in health
        assert "total_ops" in health
        assert "avg_op_latency_ms" in health


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


class TestBatchOperations:
    @pytest.mark.asyncio
    async def test_upsert_nodes_batch(self, graph_store):
        nodes = [
            GraphNode(id=f"batch_{i}", entity_type="CONCEPT", canonical_name=f"Batch Node {i}")
            for i in range(5)
        ]
        count = await graph_store.upsert_nodes_batch(nodes)
        assert count == 5

        for i in range(5):
            node = await graph_store.get_node(f"batch_{i}")
            assert node is not None
            assert node.canonical_name == f"Batch Node {i}"

    @pytest.mark.asyncio
    async def test_upsert_nodes_batch_empty(self, graph_store):
        count = await graph_store.upsert_nodes_batch([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_upsert_nodes_batch_preserves_created_at(self, graph_store):
        # Insert a node first
        await graph_store.upsert_node(GraphNode(id="preserve_batch", entity_type="CONCEPT", canonical_name="Original"))
        original = await graph_store.get_node("preserve_batch")
        assert original is not None
        original_created_at = original.created_at

        # Batch upsert same node with updated name
        await graph_store.upsert_nodes_batch([
            GraphNode(id="preserve_batch", entity_type="CONCEPT", canonical_name="Updated"),
        ])
        updated = await graph_store.get_node("preserve_batch")
        assert updated is not None
        assert updated.canonical_name == "Updated"
        assert updated.created_at == original_created_at

    @pytest.mark.asyncio
    async def test_upsert_edges_batch(self, graph_store):
        # Create source nodes
        for i in range(3):
            await graph_store.upsert_node(GraphNode(id=f"ebatch_{i}", entity_type="CONCEPT", canonical_name=f"N{i}"))

        edges = [
            GraphEdge(
                id=f"ebatch_0__CONNECTS__ebatch_{i}",
                source_id="ebatch_0",
                target_id=f"ebatch_{i}",
                relationship_type="CONNECTS",
                confidence=0.8 + i * 0.05,
            )
            for i in range(1, 3)
        ]
        count = await graph_store.upsert_edges_batch(edges)
        assert count == 2

        edges_for_node = await graph_store.get_edges_for_node("ebatch_0")
        assert len(edges_for_node) == 2

    @pytest.mark.asyncio
    async def test_upsert_edges_batch_empty(self, graph_store):
        count = await graph_store.upsert_edges_batch([])
        assert count == 0


# ---------------------------------------------------------------------------
# SQLITE_BUSY retry
# ---------------------------------------------------------------------------


class TestBusyRetry:
    @pytest.mark.asyncio
    async def test_retry_on_busy_error(self, graph_store):
        """Verify _execute_with_retry retries on SQLITE_BUSY and succeeds."""
        call_count = 0

        async def mock_commit():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("database is locked")

        await graph_store._execute_with_retry(mock_commit)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, graph_store):
        """Verify _execute_with_retry raises after exhausting retries."""
        async def always_busy():
            raise Exception("database is locked")

        with pytest.raises(Exception, match="database is locked"):
            await graph_store._execute_with_retry(always_busy)

    @pytest.mark.asyncio
    async def test_non_busy_error_not_retried(self, graph_store):
        """Verify non-BUSY errors are not retried."""
        call_count = 0

        async def fail_with_non_busy():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a busy error")

        with pytest.raises(ValueError, match="not a busy error"):
            await graph_store._execute_with_retry(fail_with_non_busy)
        assert call_count == 1


# ---------------------------------------------------------------------------
# Operation latency tracking
# ---------------------------------------------------------------------------


class TestOperationLatency:
    @pytest.mark.asyncio
    async def test_operation_tracking_after_upsert(self, graph_store):
        await graph_store.upsert_node(GraphNode(id="lat_test", entity_type="CONCEPT", canonical_name="Latency"))

        health = graph_store.connection_health()
        assert health["total_ops"] >= 1
        assert health["avg_op_latency_ms"] >= 0
        assert health["seconds_since_last_op"] >= 0

    @pytest.mark.asyncio
    async def test_operation_tracking_after_batch(self, graph_store):
        await graph_store.upsert_nodes_batch([
            GraphNode(id="lat_batch_1", entity_type="CONCEPT", canonical_name="Batch Latency"),
        ])

        health = graph_store.connection_health()
        assert health["total_ops"] >= 1
