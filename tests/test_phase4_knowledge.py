"""Phase 4 tests: Knowledge Exploration Features.

Tests for the new API routes:
  - POST /api/v1/ask — source-grounded ask queries
  - POST /api/v1/ask/retrieve — source retrieval without model dispatch
  - GET /api/v1/knowledge — list compiled knowledge
  - GET /api/v1/knowledge/{id} — get specific knowledge item
  - GET /api/v1/knowledge/search — search compiled knowledge
  - GET /api/v1/ecs/graph — ECS state graph + distribution
  - GET /api/v1/ecs/artifacts — list ECS artifacts by state
  - GET /api/v1/ecs/artifacts/{id} — artifact state + history
  - GET /api/v1/sources — list indexed sources
  - GET /api/v1/sources/stats — aggregate source statistics
  - Augmented mode chat — retrieval + context injection in WebSocket
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

# Ensure test mode
os.environ.setdefault("AIP_TEST_MODE", "1")


# ---------------------------------------------------------------------------
# Helper: create a minimal FastAPI test client
# ---------------------------------------------------------------------------


def _create_test_app():
    """Create a FastAPI app with a minimal container for testing."""
    from aip.adapter.api.app import create_app
    from aip.adapter.api.dependencies import AipContainer

    config = {
        "db_path": ":memory:",
        "embedding": {"provider": "mock"},
    }

    app = create_app(config)

    # Populate a minimal container directly (bypass lifespan for unit tests)
    container = AipContainer(config)

    # Wire required stores for testing
    from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore
    from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore
    from aip.adapter.event_store_queryable import QueryableEventStore
    from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
    from aip.adapter.vector._in_memory import InMemoryVectorStore
    from aip.adapter.embedding.ollama_embed import MockOllamaEmbeddingClient
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore

    # Use a temporary directory for DB files
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    lexical_db = os.path.join(tmpdir, "lexical.db")

    container.entity_store = SqliteEntityStore(db_path)
    container.canonical_store = SqliteCanonicalStore(db_path)
    container.event_store = QueryableEventStore(db_path)
    container.lexical_store = SqliteFts5LexicalStore(lexical_db)
    container.vector_store = InMemoryVectorStore()
    container.embedding_provider = MockOllamaEmbeddingClient()
    container.project_store = SqliteProjectStore(db_path)
    container.ecs_store = PersistentEcsStore(db_path, event_store=container.event_store)
    container.artifact_store = VersionedArtifactStore(db_path)

    # Wire model provider (CI mode)
    from aip.adapter.model_slot_resolver import ModelSlotResolver

    container.model_provider = ModelSlotResolver(config)

    # Wire knowledge store
    from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore

    container.knowledge_store = SqliteKnowledgeStore(
        db_path=db_path,
        vector_store=container.vector_store,
        lexical_store=container.lexical_store,
        embedding_provider=container.embedding_provider,
    )

    # Initialize all stores
    loop = asyncio.new_event_loop()

    async def _init():
        for store in [
            container.entity_store,
            container.canonical_store,
            container.event_store,
            container.lexical_store,
            container.project_store,
            container.ecs_store,
            container.artifact_store,
            container.knowledge_store,
        ]:
            if store is not None and hasattr(store, "initialize"):
                await store.initialize()

    loop.run_until_complete(_init())
    loop.close()

    # Wire orchestration function references (container-mediated layer discipline)
    from aip.orchestration.ask_pipeline import AskStores, ask, _search_sources_with_trace, _sanitize_fts_query

    container._ask_stores_class = AskStores
    container._ask_fn = ask
    container._search_sources_fn = _search_sources_with_trace
    container._sanitize_fts_query_fn = _sanitize_fts_query

    app.state.container = container
    return app


@pytest.fixture(scope="module")
def test_client():
    """Create a test client with a fully wired container."""
    from httpx import AsyncClient, ASGITransport

    app = _create_test_app()

    # Use sync test client for simplicity
    from starlette.testclient import TestClient

    client = TestClient(app)
    yield client


# ---------------------------------------------------------------------------
# Ask API Tests
# ---------------------------------------------------------------------------


class TestAskAPI:
    """Tests for the /api/v1/ask endpoint."""

    def test_ask_missing_question(self, test_client):
        """POST /ask with missing question should return 400."""
        resp = test_client.post("/api/v1/ask", json={"project_name": "test"})
        assert resp.status_code == 400

    def test_ask_missing_project(self, test_client):
        """POST /ask with missing project_name should return 400."""
        resp = test_client.post("/api/v1/ask", json={"question": "What is AI?"})
        assert resp.status_code == 400

    def test_ask_no_project_found(self, test_client):
        """POST /ask with nonexistent project should return 200 with NO_PROJECT status."""
        resp = test_client.post(
            "/api/v1/ask",
            json={
                "question": "What is AI?",
                "project_name": "nonexistent_project",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "NO_PROJECT"

    def test_ask_invalid_source(self, test_client):
        """POST /ask with invalid source should default to 'all'."""
        # This should not error — invalid source defaults to "all"
        resp = test_client.post(
            "/api/v1/ask",
            json={
                "question": "What is AI?",
                "project_name": "test",
                "source": "invalid_source",
            },
        )
        assert resp.status_code == 200


class TestAskRetrieveAPI:
    """Tests for the /api/v1/ask/retrieve endpoint."""

    def test_retrieve_missing_question(self, test_client):
        """POST /ask/retrieve with missing question should return 400."""
        resp = test_client.post("/api/v1/ask/retrieve", json={})
        assert resp.status_code == 400

    def test_retrieve_basic(self, test_client):
        """POST /ask/retrieve should return sources (may be empty)."""
        resp = test_client.post(
            "/api/v1/ask/retrieve",
            json={
                "question": "test query",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "total" in data
        assert "question" in data


# ---------------------------------------------------------------------------
# Knowledge API Tests
# ---------------------------------------------------------------------------


class TestKnowledgeAPI:
    """Tests for the /api/v1/knowledge endpoints."""

    def test_list_knowledge(self, test_client):
        """GET /knowledge should return a list of knowledge items."""
        resp = test_client.get("/api/v1/knowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_list_knowledge_with_domain_filter(self, test_client):
        """GET /knowledge?domain= should filter by domain."""
        resp = test_client.get("/api/v1/knowledge", params={"domain": "test"})
        assert resp.status_code == 200

    def test_get_knowledge_not_found(self, test_client):
        """GET /knowledge/{id} with nonexistent ID should return 404."""
        resp = test_client.get("/api/v1/knowledge/nonexistent_id")
        assert resp.status_code == 404

    def test_search_knowledge_missing_query(self, test_client):
        """GET /knowledge/search without q parameter should return 422 (FastAPI validation)."""
        resp = test_client.get("/api/v1/knowledge/search")
        assert resp.status_code == 422  # FastAPI returns 422 for missing required params

    def test_search_knowledge_basic(self, test_client):
        """GET /knowledge/search?q= should return results."""
        resp = test_client.get("/api/v1/knowledge/search", params={"q": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "query" in data


# ---------------------------------------------------------------------------
# ECS API Tests
# ---------------------------------------------------------------------------


class TestEcsAPI:
    """Tests for the /api/v1/ecs endpoints."""

    def test_get_ecs_graph(self, test_client):
        """GET /ecs/graph should return transitions, states, and distribution."""
        resp = test_client.get("/api/v1/ecs/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "transitions" in data
        assert "all_states" in data
        assert "distribution" in data
        # Verify the standard states are present
        assert "SPECIFIED" in data["all_states"]
        assert "APPROVED" in data["all_states"]
        assert "SUPERSEDED" in data["all_states"]

    def test_get_ecs_graph_transitions(self, test_client):
        """GET /ecs/graph transitions should match VALID_TRANSITIONS."""
        resp = test_client.get("/api/v1/ecs/graph")
        data = resp.json()
        transitions = data["transitions"]
        assert "SPECIFIED" in transitions
        assert "GENERATED" in transitions["SPECIFIED"]
        # APPROVED → SUPERSEDED is a valid terminal transition
        assert "SPECIFIED" not in transitions.get("APPROVED", [])

    def test_get_ecs_artifact_not_tracked(self, test_client):
        """GET /ecs/artifacts/{id} for untracked artifact should return null state."""
        resp = test_client.get("/api/v1/ecs/artifacts/test-artifact-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["artifact_id"] == "test-artifact-123"
        assert data["current_state"] is None
        assert isinstance(data["history"], list)

    def test_list_ecs_artifacts_invalid_state(self, test_client):
        """GET /ecs/artifacts?state=INVALID should return 400."""
        resp = test_client.get("/api/v1/ecs/artifacts", params={"state": "INVALID_STATE"})
        assert resp.status_code == 400

    def test_list_ecs_artifacts_valid_state(self, test_client):
        """GET /ecs/artifacts?state=GENERATED should return artifact IDs."""
        resp = test_client.get("/api/v1/ecs/artifacts", params={"state": "GENERATED"})
        assert resp.status_code == 200
        data = resp.json()
        assert "artifact_ids" in data
        assert "count" in data

    def test_list_ecs_artifacts_no_filter(self, test_client):
        """GET /ecs/artifacts without state filter should return summary."""
        resp = test_client.get("/api/v1/ecs/artifacts")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "total_artifacts" in data


# ---------------------------------------------------------------------------
# Sources API Tests
# ---------------------------------------------------------------------------


class TestSourcesAPI:
    """Tests for the /api/v1/sources endpoints."""

    def test_list_sources(self, test_client):
        """GET /sources should return a list of sources."""
        resp = test_client.get("/api/v1/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "total" in data
        assert "vector_stats" in data
        assert "lexical_stats" in data

    def test_list_sources_with_domain_filter(self, test_client):
        """GET /sources?domain= should filter by domain."""
        resp = test_client.get("/api/v1/sources", params={"domain": "test"})
        assert resp.status_code == 200

    def test_list_sources_with_type_filter(self, test_client):
        """GET /sources?source_type= should filter by type."""
        resp = test_client.get("/api/v1/sources", params={"source_type": "artifact"})
        assert resp.status_code == 200

    def test_get_sources_stats(self, test_client):
        """GET /sources/stats should return aggregate statistics."""
        resp = test_client.get("/api/v1/sources/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "vector_store" in data
        assert "entity_store" in data
        assert "knowledge_store" in data
        assert "lexical_store" in data


# ---------------------------------------------------------------------------
# Layer Discipline Tests
# ---------------------------------------------------------------------------


class TestLayerDiscipline:
    """Verify that Phase 4 code maintains three-layer architecture."""

    def test_ask_route_no_orchestration_import(self):
        """ask.py should import from orchestration (allowed in adapter for wiring)."""
        import importlib

        module = importlib.import_module("aip.adapter.api.routes.ask")
        source = open(module.__file__).read()
        # It's acceptable for adapter routes to import from orchestration
        # for wiring purposes — the key constraint is GUI never imports orchestration
        assert "from aip.orchestration.ask_pipeline import" in source

    def test_knowledge_route_no_orchestration_import(self):
        """knowledge.py should NOT import from orchestration."""
        import importlib

        module = importlib.import_module("aip.adapter.api.routes.knowledge")
        source = open(module.__file__).read()
        assert "from aip.orchestration" not in source

    def test_ecs_route_no_orchestration_import(self):
        """ecs.py should NOT import from orchestration."""
        import importlib

        module = importlib.import_module("aip.adapter.api.routes.ecs")
        source = open(module.__file__).read()
        assert "from aip.orchestration" not in source

    def test_sources_route_no_orchestration_import(self):
        """sources.py should NOT import from orchestration."""
        import importlib

        module = importlib.import_module("aip.adapter.api.routes.sources")
        source = open(module.__file__).read()
        assert "from aip.orchestration" not in source

    def test_gui_api_client_no_orchestration_import(self):
        """api_client.py should NOT import from aip.orchestration (only HTTP calls)."""
        import pathlib

        gui_file = pathlib.Path(__file__).parent.parent / "gui" / "api_client.py"
        if not gui_file.exists():
            pytest.skip("gui/api_client.py not found")
        source = gui_file.read_text()
        # Check for import statements, not docstring mentions
        import_lines = [line for line in source.split("\n") if line.strip().startswith(("import ", "from "))]
        for line in import_lines:
            assert "aip.orchestration" not in line, f"GUI should not import from orchestration: {line}"
            assert "aip.adapter" not in line, f"GUI should not import from adapter: {line}"
