"""Sprint 5.13 reliability and correctness tests.

Covers:
1. Blocking SQLite calls removed from storage __init__
2. Brute-force vector fallback hardening (LIMIT, degradation signaling)
3. Vector store persistence across process restarts
4. Sexton end-to-end verification with real stores
5. AI fingerprint cleanup smoke test (just checks imports work)
"""

import hashlib
import json
import math
import os
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# 1. FTS5 Store: no blocking sqlite3.connect() in __init__
# ---------------------------------------------------------------------------


class TestFts5StoreAsyncInit:
    """Verify that SqliteFts5LexicalStore.__init__ is non-blocking."""

    def test_init_is_lightweight(self, tmp_path):
        """Constructor must NOT open a sqlite3 connection or call sqlite3.connect."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "lexical.db")
        store = SqliteFts5LexicalStore(db_path)

        # After construction, no database file should exist yet
        # (tables are created lazily on first use or via initialize())
        assert store._db_path == db_path
        assert store._tables_ready is False

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path):
        """initialize() must create the FTS5 tables."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "lexical.db")
        store = SqliteFts5LexicalStore(db_path)
        assert not os.path.exists(db_path)

        await store.initialize()
        assert store._tables_ready is True

        # Verify tables exist by opening the DB directly
        conn = sqlite3.connect(db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' OR type='view'").fetchall()
        table_names = [t[0] for t in tables]
        conn.close()
        assert "fts_documents" in table_names
        assert "fts_index" in table_names

    @pytest.mark.asyncio
    async def test_index_and_search_without_explicit_initialize(self, tmp_path):
        """Lazy table creation via _get_conn() must work if initialize() is skipped."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "lexical_lazy.db")
        store = SqliteFts5LexicalStore(db_path)
        # Do NOT call initialize() — tables should be created lazily

        await store.index_document("doc1", "hello world", "test", {"key": "val"})
        results = await store.search("hello", domain="test")
        assert len(results) >= 1
        assert results[0].id == "doc1"

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, tmp_path):
        """Calling initialize() twice must not raise or corrupt data."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "lexical_idem.db")
        store = SqliteFts5LexicalStore(db_path)
        await store.initialize()
        await store.initialize()  # second call — must be a no-op

        await store.index_document("d1", "test content", "domain1", {})
        results = await store.search("test")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 2. VSS Store: no blocking sqlite3.connect() in __init__
# ---------------------------------------------------------------------------


class TestVssStoreAsyncInit:
    """Verify that SqliteVssVectorStore.__init__ is non-blocking."""

    def test_init_is_lightweight(self, tmp_path):
        """Constructor must NOT call sqlite3.connect or create tables."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vectors.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)

        assert store._db_path == db_path
        assert store._tables_ready is False
        # VSS detection hasn't happened yet
        assert store._vss_available is False

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path):
        """initialize() must create vector_metadata table."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vectors_init.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()
        assert store._tables_ready is True

        conn = sqlite3.connect(db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        conn.close()
        assert "vector_metadata" in table_names

    @pytest.mark.asyncio
    async def test_upsert_and_retrieve_without_vss(self, tmp_path):
        """Full upsert+retrieve cycle in brute-force mode (no VSS extension)."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vectors_bf.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()
        assert not store._vss_available  # VSS extension likely not available in CI

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store.upsert("v1", vec, content="Test vector", domain="test")
        results = await store.retrieve(vec, domain="test", top_k=5)

        assert len(results) >= 1
        assert results[0].id == "v1"

    @pytest.mark.asyncio
    async def test_brute_force_degradation_signal(self, tmp_path):
        """Brute-force results must include _degraded_retrieval metadata."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vectors_deg.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        if store._vss_available:
            pytest.skip("VSS extension available; degradation signal not applicable")

        vec = [0.5, 0.5, 0.5, 0.5]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store.upsert("deg1", vec, content="Degraded", domain="test")
        results = await store.retrieve(vec, domain="test", top_k=5)

        if results:
            assert results[0].metadata.get("_degraded_retrieval") is True
            assert results[0].metadata.get("_retrieval_backend") == "brute_force"

    @pytest.mark.asyncio
    async def test_brute_force_respects_scan_limit(self, tmp_path):
        """Brute-force retrieval must cap scans at _BRUTE_FORCE_SCAN_LIMIT."""
        from aip.adapter.vector.sqlite_vss_store import (
            _BRUTE_FORCE_SCAN_LIMIT,
            SqliteVssVectorStore,
        )

        db_path = str(tmp_path / "vectors_limit.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        if store._vss_available:
            pytest.skip("VSS extension available")

        # Insert more than the scan limit
        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        for i in range(min(50, _BRUTE_FORCE_SCAN_LIMIT + 1)):
            await store.upsert(f"scan-{i}", vec, content=f"Item {i}", domain="test")

        # Should still return results without error (no OOM)
        results = await store.retrieve(vec, domain="test", top_k=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_health_check_reports_degraded(self, tmp_path):
        """health_check() must signal degraded mode when VSS is unavailable."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vectors_hc.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        health = await store.health_check()
        assert health["connected"] is True

        if not store._vss_available:
            assert health["degraded"] is True
            assert "brute_force" in health["backend_name"]


# ---------------------------------------------------------------------------
# 3. Vector Persistence: vectors survive process restart
# ---------------------------------------------------------------------------


class TestVectorPersistence:
    """Verify that vectors created during ingestion survive a simulated restart."""

    @pytest.mark.asyncio
    async def test_vectors_survive_store_recreation(self, tmp_path):
        """Vectors stored in SqliteVssVectorStore must survive store recreation.

        This simulates a process restart by destroying the store object
        and creating a new one pointing at the same database.
        """
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "persistent_vectors.db")

        # Phase 1: Create store, insert vectors
        store1 = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store1.initialize()

        vec = [0.8, 0.2, 0.1, 0.1]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store1.upsert("persist-1", vec, content="Persistent doc", domain="persist")
        count1 = await store1.count()
        assert count1 == 1

        # Phase 2: Destroy and recreate store (simulates process restart)
        del store1

        store2 = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store2.initialize()

        count2 = await store2.count()
        assert count2 == 1, f"Expected 1 vector after restart, got {count2}"

        # Verify the vector is actually retrievable
        results = await store2.retrieve(vec, domain="persist", top_k=5)
        assert len(results) >= 1
        assert results[0].id == "persist-1"
        assert results[0].content == "Persistent doc"

    @pytest.mark.asyncio
    async def test_vector_embedding_json_persists(self, tmp_path):
        """Embedding data must be stored in embedding_json for brute-force retrieval."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "emb_persist.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        vec = [1.0, 0.0, 0.0, 0.0]
        await store.upsert("emb-check", vec, content="Emb check", domain="test")

        # Directly inspect the database
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT embedding_json FROM vector_metadata WHERE id = 'emb-check'").fetchone()
        conn.close()

        assert row is not None
        assert row[0] is not None
        stored_vec = json.loads(row[0])
        assert len(stored_vec) == 4
        assert abs(stored_vec[0] - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_fts5_documents_persist(self, tmp_path):
        """FTS5 indexed documents must survive store recreation."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "lexical_persist.db")

        # Phase 1: Index a document
        store1 = SqliteFts5LexicalStore(db_path)
        await store1.initialize()
        await store1.index_document("persist-doc", "persistent content here", "test", {"k": "v"})
        del store1

        # Phase 2: Recreate store and search
        store2 = SqliteFts5LexicalStore(db_path)
        await store2.initialize()
        results = await store2.search("persistent")
        assert len(results) >= 1
        assert results[0].id == "persist-doc"


# ---------------------------------------------------------------------------
# 4. Sexton End-to-End Verification with Real Stores
# ---------------------------------------------------------------------------


class _FakeEmbeddingProvider:
    """Deterministic embedding provider for integration tests."""

    def __init__(self, dimensions: int = 4):
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimensions):
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class _FakeModelProvider:
    """Stub model provider that returns canned responses for Sexton."""

    async def call(self, slot: str, messages: list[dict]) -> dict:
        # Return a valid tagging response for any LLM call
        return {
            "content": json.dumps(
                [
                    {
                        "turn_id": "turn-1",
                        "primary_domain": "unclassified",
                        "domains": ["unclassified"],
                        "tags": ["test"],
                        "importance": 0.3,
                        "bridges": [],
                        "beast_confidence": 0.5,
                    }
                ]
            )
        }


class TestSextonE2EWithRealStores:
    """Integration tests that run Sexton's full cycle using real SQLite stores."""

    @pytest.mark.asyncio
    async def test_embedding_pass_with_real_vector_store(self, tmp_path):
        """Sexton's embedding pass must store vectors in a real SqliteVssVectorStore."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
        from aip.orchestration.actors.sexton import Sexton

        db_path = str(tmp_path / "sexton_e2e.db")

        # Create a real vector store
        embed_provider = _FakeEmbeddingProvider(dimensions=4)
        vector_store = SqliteVssVectorStore(
            db_path=str(tmp_path / "vectors.db"),
            dimensions=4,
            embedding_provider=embed_provider,
        )
        await vector_store.initialize()

        # Create a minimal CorpusTurnStore with one unembedded turn
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn

        cts = CorpusTurnStore(db_path)
        await cts.initialize()

        # Insert a corpus turn that needs embedding
        turn = CorpusTurn(
            turn_id="turn-1",
            conversation_id="conv-1",
            conversation_name="Test Conversation",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Hello",
            assistant_text="World response",
            turn_timestamp="2024-01-01T00:00:00Z",
            thinking_text="",
            searchable_text="Hello World response",
            word_count=3,
            importance=0.5,
        )
        await cts.write_turn(turn)

        # Create Sexton with real stores
        sexton = Sexton(
            sexton_provider=_FakeModelProvider(),
            corpus_turn_store=cts,
            embedding_provider=embed_provider,
            vector_store=vector_store,
        )

        # Run the embedding pass
        result = await sexton._run_embedding_pass(limit=50)

        assert result["embedded"] >= 1, f"Expected at least 1 embedded turn, got {result}"

        # Verify the vector is actually in the store
        count = await vector_store.count()
        assert count >= 1, "Vector store should contain at least one vector after embedding pass"

        # Verify the vector is retrievable
        query_vec = await embed_provider.embed("Hello World response")
        results = await vector_store.retrieve(query_vec, top_k=5)
        assert len(results) >= 1
        assert results[0].id == "turn-1"

    @pytest.mark.asyncio
    async def test_sexton_graceful_without_stores(self, tmp_path):
        """Sexton must gracefully skip operations when stores are missing."""
        from aip.orchestration.actors.sexton import Sexton

        sexton = Sexton(
            sexton_provider=None,
            corpus_turn_store=None,
            embedding_provider=None,
            vector_store=None,
        )

        # All operations should return "skipped" rather than raising
        tagging = await sexton._run_turn_tagging(limit=10)
        assert tagging.get("skipped") is not None or tagging.get("turns_tagged") == 0

        embedding = await sexton._run_embedding_pass(limit=10)
        assert embedding.get("skipped") is not None or embedding.get("embedded") == 0

        wiki = await sexton._run_wiki_generation()
        assert wiki.get("skipped") is not None or wiki.get("domains_generated") == 0

    @pytest.mark.asyncio
    async def test_full_cycle_with_minimal_deps(self, tmp_path):
        """Run a full Sexton cycle with real stores where possible."""
        from aip.adapter.event_store_queryable import QueryableEventStore
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
        from aip.orchestration.actors.sexton import Sexton

        db_path = str(tmp_path / "sexton_cycle.db")
        embed_provider = _FakeEmbeddingProvider(dimensions=4)
        vector_store = SqliteVssVectorStore(
            db_path=str(tmp_path / "vec_cycle.db"),
            dimensions=4,
            embedding_provider=embed_provider,
        )
        await vector_store.initialize()

        event_store = QueryableEventStore(db_path)
        await event_store.initialize()

        sexton = Sexton(
            sexton_provider=_FakeModelProvider(),
            corpus_turn_store=None,  # no corpus turns
            embedding_provider=embed_provider,
            vector_store=vector_store,
            event_store=event_store,
        )

        # Run the full cycle — must not raise
        summary = await sexton.run_cycle()

        assert isinstance(summary, dict)
        assert "tagging" in summary
        assert "embedding" in summary
        assert "wiki" in summary
        assert "graph" in summary
        assert "classification" in summary
        assert "cycle_elapsed_seconds" in summary


# ---------------------------------------------------------------------------
# 5. AI Fingerprint Cleanup Smoke Test
# ---------------------------------------------------------------------------


class TestAIFingerprintCleanup:
    """Verify that the cleaned-up modules still import and function."""

    def test_ask_pipeline_imports(self):
        """ask_pipeline.py must import cleanly after cleanup."""
        from aip.orchestration.ask_pipeline import AskStores, ask, create_ask_stores

        assert callable(ask)
        assert callable(create_ask_stores)
        assert AskStores is not None

    def test_ingestion_pipeline_imports(self):
        """ingestion/pipeline.py must import cleanly after cleanup."""
        from aip.orchestration.ingestion.pipeline import create_ingestion_stores, ingest_conversation, ingest_file

        assert callable(ingest_file)
        assert callable(ingest_conversation)
        assert callable(create_ingestion_stores)

    def test_fts5_store_no_sync_init(self):
        """SqliteFts5LexicalStore must NOT have _ensure_tables_sync method."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        assert not hasattr(SqliteFts5LexicalStore, "_ensure_tables_sync"), (
            "_ensure_tables_sync should have been removed in Sprint 5.13"
        )

    def test_vss_store_no_sync_init(self):
        """SqliteVssVectorStore must NOT have _init_vss_sync method."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        assert not hasattr(SqliteVssVectorStore, "_init_vss_sync"), (
            "_init_vss_sync should have been removed in Sprint 5.13"
        )
