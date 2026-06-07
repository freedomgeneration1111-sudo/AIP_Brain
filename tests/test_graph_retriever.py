"""Tests for Phase 5.2 — Entity-Turn Index + GraphRetriever.

Covers:
- GraphStore entity_turn_index methods (write, get, backfill, mention_scan)
- detect_query_entities (fuzzy/substring matching)
- apply_hub_leash
- GraphRetriever with mock stores (Zone A, Zone B, graceful degradation)
- Integration: GraphRetriever registered with RetrievalOrchestrator
"""

from __future__ import annotations

import tempfile
import os
from dataclasses import dataclass
from typing import Any

import pytest

from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
from aip.foundation.schemas.retrieval_trace import (
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)
from aip.orchestration.retrievers.graph_retriever import (
    GraphRetriever,
    detect_query_entities,
    apply_hub_leash,
)
from aip.orchestration.retrievers.orchestrator import RetrievalOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph_store(tmp_path) -> GraphStore:
    """Create a GraphStore with a temp database."""
    db_path = os.path.join(tmp_path, "test_graph.db")
    return GraphStore(db_path)


def _populate_test_graph(store: GraphStore) -> None:
    """Insert test nodes and edges for retrieval testing."""
    # Nodes
    for name, etype, domain in [
        ("Komal", "PERSON", "freedom_gen"),
        ("Freedom Generation School", "ORGANIZATION", "freedom_gen"),
        ("Moses", "PERSON", "freedom_gen"),
        ("NBCM", "CONCEPT", "nbcm"),
        ("Urdu", "CONCEPT", "freedom_gen"),
    ]:
        node_id = name.lower().replace(" ", "_")
        store.upsert_node(GraphNode(
            id=node_id,
            entity_type=etype,
            canonical_name=name,
            domain=domain,
            confidence=0.9,
            source="test",
        ))

    # Edges with evidence turns
    store.upsert_edge(GraphEdge(
        id="komal__WORKS_ON__freedom_generation_school",
        source_id="komal",
        target_id="freedom_generation_school",
        relationship_type="WORKS_ON",
        confidence=0.95,
        evidence_turn_ids=["turn_001", "turn_002"],
    ))
    store.upsert_edge(GraphEdge(
        id="komal__RELATES_TO__urdu",
        source_id="komal",
        target_id="urdu",
        relationship_type="RELATES_TO",
        confidence=0.8,
        evidence_turn_ids=["turn_003"],
    ))


# ---------------------------------------------------------------------------
# Entity-turn index
# ---------------------------------------------------------------------------


class TestEntityTurnIndex:
    def test_write_and_read(self, tmp_path):
        store = _make_graph_store(tmp_path)
        store.write_entity_turn("komal", "turn_001", confidence=0.9, source="test")
        store.write_entity_turn("komal", "turn_002", confidence=0.7, source="test")
        store.write_entity_turn("freedom_generation_school", "turn_001", confidence=0.8, source="test")

        results = store.get_turns_for_entities(["komal"])
        assert len(results) == 2
        assert results[0]["confidence"] >= results[1]["confidence"]  # sorted by conf desc

    def test_get_turns_multiple_entities(self, tmp_path):
        store = _make_graph_store(tmp_path)
        store.write_entity_turn("komal", "turn_001", confidence=0.9)
        store.write_entity_turn("freedom_generation_school", "turn_002", confidence=0.8)

        results = store.get_turns_for_entities(["komal", "freedom_generation_school"])
        assert len(results) == 2

    def test_get_entities_for_turn(self, tmp_path):
        store = _make_graph_store(tmp_path)
        store.write_entity_turn("komal", "turn_001", confidence=0.9)
        store.write_entity_turn("freedom_generation_school", "turn_001", confidence=0.8)

        results = store.get_entities_for_turn("turn_001")
        assert len(results) == 2

    def test_min_confidence_filter(self, tmp_path):
        store = _make_graph_store(tmp_path)
        store.write_entity_turn("komal", "turn_001", confidence=0.9)
        store.write_entity_turn("komal", "turn_002", confidence=0.2)

        results = store.get_turns_for_entities(["komal"], min_confidence=0.5)
        assert len(results) == 1
        assert results[0]["turn_id"] == "turn_001"

    def test_batch_write(self, tmp_path):
        store = _make_graph_store(tmp_path)
        entries = [
            ("komal", "turn_001", 0.9, "test"),
            ("komal", "turn_002", 0.8, "test"),
            ("moses", "turn_003", 0.7, "test"),
        ]
        count = store.write_entity_turn_batch(entries)
        assert count == 3
        assert store.entity_turn_count() == 3

    def test_empty_entity_list(self, tmp_path):
        store = _make_graph_store(tmp_path)
        results = store.get_turns_for_entities([])
        assert results == []

    def test_entity_turn_count(self, tmp_path):
        store = _make_graph_store(tmp_path)
        assert store.entity_turn_count() == 0
        store.write_entity_turn("komal", "turn_001")
        assert store.entity_turn_count() == 1


class TestBackfillEntityTurnIndex:
    def test_backfill_from_edges(self, tmp_path):
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        result = store.backfill_entity_turn_index()
        assert result["entities_written"] > 0
        assert result["edges_processed"] == 2  # two edges in test data

        # Verify we can now find turns
        turns = store.get_turns_for_entities(["komal"])
        assert len(turns) > 0

    def test_backfill_idempotent(self, tmp_path):
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        result1 = store.backfill_entity_turn_index()
        result2 = store.backfill_entity_turn_index()
        # Second run should skip
        assert result2["skipped"] == "already_populated"


# ---------------------------------------------------------------------------
# detect_query_entities
# ---------------------------------------------------------------------------


class TestDetectQueryEntities:
    def test_detect_known_entity(self, tmp_path):
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities("Who is Komal?", store)
        assert len(entities) > 0
        assert entities[0][0] == "komal"  # entity_id
        assert entities[0][1] >= 0.5  # confidence

    def test_detect_multi_word_entity(self, tmp_path):
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities(
            "Tell me about Freedom Generation School", store
        )
        entity_ids = [eid for eid, _ in entities]
        assert "freedom_generation_school" in entity_ids

    def test_no_match(self, tmp_path):
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities("quantum computing basics", store)
        assert len(entities) == 0

    def test_none_store(self):
        entities = detect_query_entities("test query", None)
        assert entities == []

    def test_empty_query(self, tmp_path):
        store = _make_graph_store(tmp_path)
        entities = detect_query_entities("", store)
        assert entities == []


# ---------------------------------------------------------------------------
# apply_hub_leash
# ---------------------------------------------------------------------------


class TestApplyHubLeash:
    def test_leash_applied(self):
        entries = [
            {"entity_id": "komal", "turn_id": f"turn_{i}", "confidence": 0.9, "source": "test"}
            for i in range(20)
        ]
        leashed = apply_hub_leash(entries, max_per_entity=5)
        assert len(leashed) == 5

    def test_mixed_entities(self):
        entries = [
            {"entity_id": "komal", "turn_id": "turn_1", "confidence": 0.9, "source": "test"},
            {"entity_id": "komal", "turn_id": "turn_2", "confidence": 0.9, "source": "test"},
            {"entity_id": "moses", "turn_id": "turn_3", "confidence": 0.8, "source": "test"},
            {"entity_id": "moses", "turn_id": "turn_4", "confidence": 0.8, "source": "test"},
        ]
        leashed = apply_hub_leash(entries, max_per_entity=1)
        assert len(leashed) == 2  # 1 from komal, 1 from moses

    def test_empty_input(self):
        assert apply_hub_leash([]) == []

    def test_no_leash_needed(self):
        entries = [
            {"entity_id": "komal", "turn_id": "turn_1", "confidence": 0.9, "source": "test"},
        ]
        leashed = apply_hub_leash(entries, max_per_entity=10)
        assert len(leashed) == 1


# ---------------------------------------------------------------------------
# GraphRetriever
# ---------------------------------------------------------------------------


class TestGraphRetriever:
    def test_name(self):
        r = GraphRetriever()
        assert r.name == "GraphRetriever"

    @pytest.mark.asyncio
    async def test_no_stores(self):
        """With no stores, should return empty list gracefully."""
        r = GraphRetriever()
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_with_mock_stores(self, tmp_path):
        """GraphRetriever with populated index should find turns."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)
        store.backfill_entity_turn_index()

        # Mock corpus store
        @dataclass
        class MockTurn:
            turn_id: str = "turn_001"
            conversation_id: str = "conv1"
            conversation_name: str = "Test Conv"
            primary_domain: str = "freedom_gen"
            searchable_text: str = "Komal is the principal of Freedom Generation School"
            importance: float = 0.8
            beast_confidence: float = 0.9
            turn_timestamp: str = ""
            domains: list = None
            tags: list = None
            bridges: list = None

            def __post_init__(self):
                if self.domains is None:
                    self.domains = []
                if self.tags is None:
                    self.tags = []
                if self.bridges is None:
                    self.bridges = []

        class MockCorpusStore:
            async def get_turn(self, turn_id):
                return MockTurn(turn_id=turn_id)

        r = GraphRetriever(
            graph_store=store,
            corpus_turn_store=MockCorpusStore(),
        )
        query = RetrievalQuery(raw_query="Who is Komal?")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert len(hits) > 0
        assert hits[0].retrieval_channel == RetrievalChannel.GRAPH
        assert "komal" in trace.detected_entities
        assert trace.direct_mentions_count > 0

    @pytest.mark.asyncio
    async def test_graceful_no_entities(self, tmp_path):
        """If query matches no entities, should return [] gracefully."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        r = GraphRetriever(graph_store=store, corpus_turn_store=None)
        query = RetrievalQuery(raw_query="quantum entanglement physics")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_zone_a_populates_trace(self, tmp_path):
        """Zone A hits should populate trace fields."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)
        store.backfill_entity_turn_index()

        @dataclass
        class MockTurn:
            turn_id: str = "turn_001"
            conversation_id: str = "conv1"
            conversation_name: str = "Test"
            primary_domain: str = "freedom_gen"
            searchable_text: str = "Komal works at FGS"
            importance: float = 0.8
            beast_confidence: float = 0.9
            turn_timestamp: str = ""
            domains: list = None
            tags: list = None
            bridges: list = None

            def __post_init__(self):
                if self.domains is None:
                    self.domains = []
                if self.tags is None:
                    self.tags = []
                if self.bridges is None:
                    self.bridges = []

        class MockCorpusStore:
            async def get_turn(self, turn_id):
                return MockTurn(turn_id=turn_id)

        r = GraphRetriever(graph_store=store, corpus_turn_store=MockCorpusStore())
        query = RetrievalQuery(raw_query="Komal principal role")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        hits = await r.retrieve(query, budget=budget, trace=trace)

        # Trace should have retriever trace with GraphRetriever
        assert any(rt.retriever_name == "GraphRetriever" for rt in trace.retriever_traces)
        assert len(trace.detected_entities) > 0


# ---------------------------------------------------------------------------
# Orchestrator integration
# ---------------------------------------------------------------------------


class TestGraphRetrieverOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_graph_retriever_registered(self, tmp_path):
        """GraphRetriever should participate in orchestrator fusion."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)
        store.backfill_entity_turn_index()

        @dataclass
        class MockTurn:
            turn_id: str = "turn_001"
            conversation_id: str = "conv1"
            conversation_name: str = "Test"
            primary_domain: str = "freedom_gen"
            searchable_text: str = "Komal is principal of FGS"
            importance: float = 0.8
            beast_confidence: float = 0.9
            turn_timestamp: str = ""
            domains: list = None
            tags: list = None
            bridges: list = None

            def __post_init__(self):
                if self.domains is None:
                    self.domains = []
                if self.tags is None:
                    self.tags = []
                if self.bridges is None:
                    self.bridges = []

        class MockCorpusStore:
            async def search(self, query, primary_domain=None, limit=10):
                return [MockTurn()]

            async def get_turn(self, turn_id):
                return MockTurn(turn_id=turn_id)

        class MockLexicalStore:
            async def search(self, query, domain=None, limit=10):
                return []

        # Build orchestrator with both FTS + Graph retrievers
        mock_corpus = MockCorpusStore()
        fts = FTSRetriever.__new__(FTSRetriever)
        fts._corpus_store = mock_corpus
        fts._lexical_store = MockLexicalStore()

        graph = GraphRetriever(graph_store=store, corpus_turn_store=mock_corpus)

        orch = RetrievalOrchestrator()
        orch.register_retriever(fts)
        orch.register_retriever(graph)

        query = RetrievalQuery(raw_query="Who is Komal and what does she do?")
        hits, trace = await orch.retrieve(query)

        # Should have results from both retrievers
        assert len(hits) > 0
        assert len(trace.retriever_traces) >= 2  # FTS + Graph

        # Check that graph retriever appears in trace
        graph_traces = [rt for rt in trace.retriever_traces if rt.retriever_name == "GraphRetriever"]
        assert len(graph_traces) >= 1


# Import FTSRetriever for integration test
from aip.orchestration.retrievers.fts_retriever import FTSRetriever
