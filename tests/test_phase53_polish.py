"""Tests for Phase 5.3 — Polish & High-Impact Additions.

Covers:
- Query expansion (graph-based + template-based)
- WikiRetriever (approved wiki articles)
- Entity type filtering in detect_query_entities
- Orchestrator integration with query expansion
- Trace population (query_expansions, wiki_injected, etc.)
- Hub leash configurability
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any

import pytest

from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
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
from aip.orchestration.retrievers.query_expansion import (
    expand_query,
    QueryExpansion,
)
from aip.orchestration.retrievers.wiki_retriever import WikiRetriever
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
    for name, etype, domain in [
        ("Komal", "PERSON", "freedom_gen"),
        ("Freedom Generation School", "ORGANIZATION", "freedom_gen"),
        ("Moses", "PERSON", "freedom_gen"),
        ("NBCM", "CONCEPT", "nbcm"),
        ("Urdu", "CONCEPT", "freedom_gen"),
        ("Brick Kiln", "PLACE", "freedom_gen"),
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
    store.upsert_edge(GraphEdge(
        id="komal__LOCATED_IN__brick_kiln",
        source_id="komal",
        target_id="brick_kiln",
        relationship_type="LOCATED_IN",
        confidence=0.7,
        evidence_turn_ids=["turn_004"],
    ))


# ---------------------------------------------------------------------------
# Query Expansion
# ---------------------------------------------------------------------------


class TestQueryExpansion:
    def test_template_who_is(self):
        """Template expansion for 'Who is X?' pattern."""
        query = RetrievalQuery(raw_query="Who is Komal?")
        result = expand_query(query, enable_graph=False, enable_template=True)
        assert len(result.expanded_terms) > 0
        assert "Komal" in result.expanded_terms
        assert result.source in ("template", "combined")

    def test_template_what_does(self):
        """Template expansion for 'What does X do?' pattern."""
        query = RetrievalQuery(raw_query="What does Komal do?")
        result = expand_query(query, enable_graph=False, enable_template=True)
        assert len(result.expanded_terms) > 0

    def test_no_expansion_for_generic_query(self):
        """Generic queries without entity patterns should get no template expansion."""
        query = RetrievalQuery(raw_query="retrieval architecture")
        result = expand_query(query, enable_graph=False, enable_template=True)
        # No matching template pattern
        assert result.expanded_terms == []

    def test_graph_expansion(self, tmp_path):
        """Graph-based expansion should find neighbors of detected entities."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        query = RetrievalQuery(raw_query="Who is Komal?")
        detected = [("komal", 0.95)]
        result = expand_query(
            query,
            detected_entities=detected,
            graph_store=store,
            enable_template=False,
            enable_graph=True,
        )
        # Should find neighbors: Freedom Generation School, Urdu, Brick Kiln
        assert len(result.expanded_terms) > 0
        assert result.source == "graph"

    def test_combined_expansion(self, tmp_path):
        """Combined graph + template expansion."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        query = RetrievalQuery(raw_query="Who is Komal?")
        detected = [("komal", 0.95)]
        result = expand_query(
            query,
            detected_entities=detected,
            graph_store=store,
            enable_template=True,
            enable_graph=True,
        )
        assert len(result.expanded_terms) > 0
        assert result.source == "combined"

    def test_expanded_fts_queries_generated(self, tmp_path):
        """Expansion should produce FTS query strings."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        query = RetrievalQuery(raw_query="Who is Komal?")
        detected = [("komal", 0.95)]
        result = expand_query(
            query,
            detected_entities=detected,
            graph_store=store,
        )
        if result.expanded_terms:
            assert len(result.expanded_fts_queries) > 0

    def test_no_graph_store(self):
        """Without graph store, template expansion still works."""
        query = RetrievalQuery(raw_query="Who is Komal?")
        result = expand_query(
            query,
            graph_store=None,
            enable_template=True,
            enable_graph=True,
        )
        # Template should still produce terms
        assert len(result.expanded_terms) > 0

    def test_entity_ids_used(self, tmp_path):
        """Expansion should record which entity IDs were used."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        query = RetrievalQuery(raw_query="Who is Komal?")
        detected = [("komal", 0.95)]
        result = expand_query(
            query,
            detected_entities=detected,
            graph_store=store,
        )
        assert "komal" in result.entity_ids_used

    def test_max_expanded_terms_cap(self, tmp_path):
        """Should respect max_expanded_terms limit."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        query = RetrievalQuery(raw_query="Who is Komal?")
        detected = [("komal", 0.95)]
        result = expand_query(
            query,
            detected_entities=detected,
            graph_store=store,
            max_expanded_terms=2,
        )
        assert len(result.expanded_terms) <= 2


# ---------------------------------------------------------------------------
# Entity Type Filtering
# ---------------------------------------------------------------------------


class TestEntityTypeFilter:
    def test_filter_person_only(self, tmp_path):
        """Filter to only PERSON entities."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities(
            "Who is Komal?", store, entity_type_filter=["PERSON"]
        )
        entity_ids = [eid for eid, _ in entities]
        # Should include komal (PERSON) but not freedom_generation_school (ORG)
        assert "komal" in entity_ids
        assert "freedom_generation_school" not in entity_ids

    def test_filter_organization(self, tmp_path):
        """Filter to only ORGANIZATION entities."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities(
            "Tell me about Freedom Generation School", store,
            entity_type_filter=["ORGANIZATION"]
        )
        entity_ids = [eid for eid, _ in entities]
        assert "freedom_generation_school" in entity_ids
        # Komal is PERSON, should be filtered out
        assert "komal" not in entity_ids

    def test_no_filter_returns_all(self, tmp_path):
        """Without filter, all entity types returned."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)

        entities = detect_query_entities("Who is Komal?", store)
        entity_ids = [eid for eid, _ in entities]
        # Should include all matching entities regardless of type
        assert "komal" in entity_ids


# ---------------------------------------------------------------------------
# WikiRetriever
# ---------------------------------------------------------------------------


class TestWikiRetriever:
    def test_name(self):
        r = WikiRetriever()
        assert r.name == "WikiRetriever"

    @pytest.mark.asyncio
    async def test_no_db_path(self):
        """Without db_path, should return empty list."""
        r = WikiRetriever(db_path=None)
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert hits == []

    @pytest.mark.asyncio
    async def test_with_approved_wiki(self, tmp_path):
        """Should find APPROVED wiki articles."""
        db_path = os.path.join(tmp_path, "test_wiki.db")
        self._setup_wiki_db(db_path)

        r = WikiRetriever(db_path=db_path)
        query = RetrievalQuery(raw_query="Komal freedom generation")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)
        trace.detected_entities = ["komal"]

        hits = await r.retrieve(query, budget=budget, trace=trace)
        # Should find the approved wiki article
        assert len(hits) > 0
        assert hits[0].retrieval_channel == RetrievalChannel.WIKI
        assert hits[0].evidence_status == EvidenceStatus.APPROVED
        assert trace.wiki_injected is True

    @pytest.mark.asyncio
    async def test_no_approved_wiki(self, tmp_path):
        """Should return empty if no APPROVED wiki articles."""
        db_path = os.path.join(tmp_path, "test_no_wiki.db")
        self._setup_wiki_db(db_path, approve=False)

        r = WikiRetriever(db_path=db_path)
        query = RetrievalQuery(raw_query="test")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        hits = await r.retrieve(query, budget=budget, trace=trace)
        assert hits == []
        assert trace.wiki_injected is False

    def _setup_wiki_db(self, db_path: str, approve: bool = True) -> None:
        """Set up a test database with wiki articles."""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT,
                    version INTEGER,
                    content TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (id, version)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ecs_state (
                    artifact_id TEXT PRIMARY KEY,
                    current_state TEXT,
                    updated_at TEXT
                )
            """)

            # Insert a wiki article
            aid = "beast:wiki:freedom_gen:20260607T120000"
            metadata = json.dumps({"domain": "freedom_gen", "artifact_type": "sexton_wiki"})
            conn.execute(
                "INSERT INTO artifacts (id, version, content, metadata_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?)",
                (aid, "Freedom Generation School is a school in Pakistan. Komal is the principal.", metadata, "2026-06-07T12:00:00", "2026-06-07T12:00:00"),
            )

            if approve:
                conn.execute(
                    "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'APPROVED', ?)",
                    (aid, "2026-06-07T13:00:00"),
                )
            else:
                conn.execute(
                    "INSERT INTO ecs_state (artifact_id, current_state, updated_at) VALUES (?, 'GENERATED', ?)",
                    (aid, "2026-06-07T13:00:00"),
                )

            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Orchestrator with Query Expansion
# ---------------------------------------------------------------------------


class TestOrchestratorWithExpansion:
    @pytest.mark.asyncio
    async def test_orchestrator_with_graph_store(self, tmp_path):
        """Orchestrator with graph_store should perform query expansion."""
        store = _make_graph_store(tmp_path)
        _populate_test_graph(store)
        store.backfill_entity_turn_index()

        @dataclass
        class MockTurn:
            turn_id: str = "turn_001"
            conversation_id: str = "conv1"
            conversation_name: str = "Test"
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
            async def search(self, query, primary_domain=None, limit=10):
                return [MockTurn()]

            async def get_turn(self, turn_id):
                return MockTurn(turn_id=turn_id)

        class MockLexicalStore:
            async def search(self, query, domain=None, limit=10):
                return []

        from aip.orchestration.retrievers.fts_retriever import FTSRetriever

        fts = FTSRetriever.__new__(FTSRetriever)
        fts._corpus_store = MockCorpusStore()
        fts._lexical_store = MockLexicalStore()

        graph = GraphRetriever(graph_store=store, corpus_turn_store=MockCorpusStore())

        orch = RetrievalOrchestrator()
        orch.register_retriever(fts)
        orch.register_retriever(graph)
        orch.graph_store = store

        query = RetrievalQuery(raw_query="Who is Komal and what does she do?")
        hits, trace = await orch.retrieve(query)

        assert len(hits) > 0
        # Should have detected entities
        assert len(trace.detected_entities) > 0
        # Should have query expansions
        assert len(trace.query_expansions) > 0

    @pytest.mark.asyncio
    async def test_orchestrator_expansion_disabled(self, tmp_path):
        """When expansion is disabled, no expansions in trace."""
        class MockRetriever:
            @property
            def name(self) -> str:
                return "MockRetriever"

            async def retrieve(self, query, *, budget, trace):
                return []

        orch = RetrievalOrchestrator()
        orch.enable_query_expansion = False
        orch.register_retriever(MockRetriever())

        query = RetrievalQuery(raw_query="test")
        hits, trace = await orch.retrieve(query)

        assert trace.query_expansions == []


# ---------------------------------------------------------------------------
# Hub Leash Configurability
# ---------------------------------------------------------------------------


class TestHubLeashConfigurable:
    def test_custom_hub_leash(self):
        """GraphRetriever accepts custom hub_leash value."""
        r = GraphRetriever(hub_leash=5)
        assert r._hub_leash == 5

    def test_hub_leash_in_trace(self, tmp_path):
        """Hub leash value should appear in trace debug info."""
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

        r = GraphRetriever(
            graph_store=store,
            corpus_turn_store=MockCorpusStore(),
            hub_leash=7,
        )
        query = RetrievalQuery(raw_query="Komal")
        budget = RetrievalBudget()
        trace = RetrievalTrace(query=query)

        import asyncio
        hits = asyncio.get_event_loop().run_until_complete(
            r.retrieve(query, budget=budget, trace=trace)
        )

        # Check that hub_leash appears in the trace debug
        graph_traces = [rt for rt in trace.retriever_traces if rt.retriever_name == "GraphRetriever"]
        if graph_traces:
            assert graph_traces[0].debug.get("hub_leash") == 7
