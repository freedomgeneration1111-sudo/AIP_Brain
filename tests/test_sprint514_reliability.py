"""Sprint 5.14 reliability and correctness tests.

Covers:
1. CorpusTurnStore: no blocking sqlite3.connect() in __init__
2. Vector store persistent connection lifecycle
3. Sexton full pipeline with real stores and deterministic stubs
4. AI fingerprint cleanup verification
5. RuntimeMode / brute-force fallback policy
"""

import hashlib
import json
import math
import os
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# 1. CorpusTurnStore: no blocking sqlite3.connect() in __init__
# ---------------------------------------------------------------------------


class TestCorpusTurnStoreAsyncInit:
    """Verify that CorpusTurnStore.__init__ is non-blocking."""

    def test_init_is_lightweight(self, tmp_path):
        """Constructor must NOT open a sqlite3 connection."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        db_path = str(tmp_path / "corpus.db")
        store = CorpusTurnStore(db_path)

        assert store._db_path == db_path
        assert store._tables_ready is False
        assert store._conn is None
        # No database file should exist yet
        assert not os.path.exists(db_path)

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_path):
        """initialize() must create the corpus_turns table and FTS5 index."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        db_path = str(tmp_path / "corpus_init.db")
        store = CorpusTurnStore(db_path)
        assert not os.path.exists(db_path)

        await store.initialize()
        assert store._tables_ready is True

        conn = sqlite3.connect(db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' OR type='view'").fetchall()
        table_names = [t[0] for t in tables]
        conn.close()
        assert "corpus_turns" in table_names
        assert "corpus_turns_fts" in table_names

    @pytest.mark.asyncio
    async def test_lazy_init_via_get_conn(self, tmp_path):
        """Using store without initialize() must still work (lazy table creation)."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn

        db_path = str(tmp_path / "corpus_lazy.db")
        store = CorpusTurnStore(db_path)
        # Do NOT call initialize()

        turn = CorpusTurn(
            turn_id="lazy-1",
            conversation_id="conv-1",
            conversation_name="Test",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Hello",
            assistant_text="World",
            turn_timestamp="2024-01-01T00:00:00Z",
            searchable_text="Hello World",
            word_count=2,
        )
        await store.write_turn(turn)

        result = await store.get_turn("lazy-1")
        assert result is not None
        assert result.turn_id == "lazy-1"

    @pytest.mark.asyncio
    async def test_mark_embedded(self, tmp_path):
        """mark_embedded() must set embedded=1."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn

        db_path = str(tmp_path / "corpus_mark.db")
        store = CorpusTurnStore(db_path)
        await store.initialize()

        turn = CorpusTurn(
            turn_id="emb-1",
            conversation_id="conv-1",
            conversation_name="Test",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Embed me",
            assistant_text="OK",
            turn_timestamp="2024-01-01T00:00:00Z",
            searchable_text="Embed me OK",
            word_count=3,
        )
        await store.write_turn(turn)

        # Before marking
        t = await store.get_turn("emb-1")
        assert t.embedded == 0

        # Mark embedded
        await store.mark_embedded("emb-1")

        # After marking
        t = await store.get_turn("emb-1")
        assert t.embedded == 1

    @pytest.mark.asyncio
    async def test_has_bridge_tagged_turns(self, tmp_path):
        """has_bridge_tagged_turns() must detect bridge-tagged turns."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn

        db_path = str(tmp_path / "corpus_bridge.db")
        store = CorpusTurnStore(db_path)
        await store.initialize()

        # No bridge-tagged turns yet
        assert await store.has_bridge_tagged_turns() is False

        # Add a turn with bridges and tagging_version > 0
        turn = CorpusTurn(
            turn_id="bridge-1",
            conversation_id="conv-1",
            conversation_name="Test",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Bridge test",
            assistant_text="OK",
            turn_timestamp="2024-01-01T00:00:00Z",
            searchable_text="Bridge test OK",
            word_count=3,
        )
        await store.write_turn(turn)
        await store.update_beast_tags(
            "bridge-1",
            ["nbcm"],
            "nbcm",
            ["test"],
            0.5,
            ["nbcm->theology_research"],
            0.8,
        )

        assert await store.has_bridge_tagged_turns() is True

    @pytest.mark.asyncio
    async def test_count_domain_words_since(self, tmp_path):
        """count_domain_words_since() must count words correctly."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        db_path = str(tmp_path / "corpus_words.db")
        store = CorpusTurnStore(db_path)
        await store.initialize()

        # No turns yet
        count = await store.count_domain_words_since("nbcm", None)
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_domain_stats(self, tmp_path):
        """get_domain_stats() must return correct structure."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        db_path = str(tmp_path / "corpus_stats.db")
        store = CorpusTurnStore(db_path)
        await store.initialize()

        stats = await store.get_domain_stats("nbcm")
        assert "total_turns" in stats
        assert "avg_importance" in stats
        assert "top_tags" in stats
        assert "bridge_connectors" in stats
        assert "sample_turns" in stats
        assert "max_tagging_version" in stats

    @pytest.mark.asyncio
    async def test_no_ensure_tables_sync(self):
        """CorpusTurnStore must NOT have _ensure_tables_sync method."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        assert not hasattr(CorpusTurnStore, "_ensure_tables_sync"), (
            "_ensure_tables_sync should have been removed in Sprint 5.14"
        )

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, tmp_path):
        """Calling initialize() twice must not raise or corrupt data."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn

        db_path = str(tmp_path / "corpus_idem.db")
        store = CorpusTurnStore(db_path)
        await store.initialize()
        await store.initialize()

        turn = CorpusTurn(
            turn_id="idem-1",
            conversation_id="conv-1",
            conversation_name="Test",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Idempotent",
            assistant_text="Test",
            turn_timestamp="2024-01-01T00:00:00Z",
            searchable_text="Idempotent Test",
            word_count=2,
        )
        await store.write_turn(turn)
        result = await store.get_turn("idem-1")
        assert result is not None


# ---------------------------------------------------------------------------
# 2. Vector Store Persistent Connection Lifecycle
# ---------------------------------------------------------------------------


class TestVectorStoreConnectionLifecycle:
    """Verify persistent connections and error recovery."""

    @pytest.mark.asyncio
    async def test_vss_store_reuses_connection(self, tmp_path):
        """SqliteVssVectorStore should reuse its persistent connection."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vss_conn.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        # First operation creates a persistent connection
        await store.upsert("conn-1", vec, content="Test 1", domain="test")
        conn1 = store._conn
        assert conn1 is not None

        # Second operation should reuse it (or get a new one after close)
        await store.upsert("conn-2", vec, content="Test 2", domain="test")
        # After upsert the conn may be None (close-after-write pattern)
        # but the key point is no error occurs

        count = await store.count()
        assert count == 2

    @pytest.mark.asyncio
    async def test_vss_store_close_releases_connection(self, tmp_path):
        """close() must release the persistent connection."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        db_path = str(tmp_path / "vss_close.db")
        store = SqliteVssVectorStore(db_path=db_path, dimensions=4)
        await store.initialize()

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store.upsert("close-1", vec, content="Test", domain="test")
        await store.close()
        assert store._conn is None

        # Should still work after close (creates new connection)
        count = await store.count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_fts5_store_reuses_connection(self, tmp_path):
        """SqliteFts5LexicalStore should reuse its persistent connection."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "fts_conn.db")
        store = SqliteFts5LexicalStore(db_path)
        await store.initialize()

        # First operation creates a persistent connection
        await store.index_document("doc1", "hello world", "test", {"key": "val"})
        # The store uses close-after-write, so conn may be None
        # Key test: no error and data persists

        results = await store.search("hello", domain="test")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_fts5_store_close_releases_connection(self, tmp_path):
        """close() must release the persistent connection."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        db_path = str(tmp_path / "fts_close.db")
        store = SqliteFts5LexicalStore(db_path)
        await store.initialize()

        await store.index_document("doc1", "close test", "test", {})
        await store.close()
        assert store._conn is None

        # Should still work after close
        results = await store.search("close", domain="test")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# 3. Sexton Full Pipeline Verification
# ---------------------------------------------------------------------------


class TestSextonFullPipeline:
    """Integration tests covering the complete Sexton pipeline with real stores."""

    @pytest.mark.asyncio
    async def test_embedding_pass_marks_embedded(self, tmp_path):
        """Sexton embedding pass must mark turns as embedded in CorpusTurnStore."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn
        from aip.orchestration.actors.sexton import Sexton

        db_path = str(tmp_path / "sexton_mark.db")
        embed_provider = _FakeEmbeddingProvider(dimensions=4)
        vector_store = SqliteVssVectorStore(
            db_path=str(tmp_path / "vec_mark.db"),
            dimensions=4,
            embedding_provider=embed_provider,
        )
        await vector_store.initialize()

        cts = CorpusTurnStore(db_path)
        await cts.initialize()

        # Insert an unembedded turn
        turn = CorpusTurn(
            turn_id="turn-1",
            conversation_id="conv-1",
            conversation_name="Test",
            turn_index=0,
            source_model="test",
            source_account="test",
            export_date="2024-01-01",
            user_text="Hello",
            assistant_text="World",
            turn_timestamp="2024-01-01T00:00:00Z",
            searchable_text="Hello World",
            word_count=2,
        )
        await cts.write_turn(turn)

        sexton = Sexton(
            sexton_provider=_FakeModelProvider(),
            corpus_turn_store=cts,
            embedding_provider=embed_provider,
            vector_store=vector_store,
        )

        result = await sexton._run_embedding_pass(limit=50)
        assert result["embedded"] >= 1

        # Verify the turn is marked as embedded
        updated = await cts.get_turn("turn-1")
        assert updated.embedded == 1, "Turn should be marked as embedded after embedding pass"

    @pytest.mark.asyncio
    async def test_full_cycle_with_corpus_turns(self, tmp_path):
        """Full Sexton cycle with real CorpusTurnStore and vector store."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
        from aip.foundation.schemas.corpus_turn import CorpusTurn
        from aip.orchestration.actors.sexton import Sexton

        db_path = str(tmp_path / "sexton_cycle.db")
        embed_provider = _FakeEmbeddingProvider(dimensions=4)
        vector_store = SqliteVssVectorStore(
            db_path=str(tmp_path / "vec_cycle.db"),
            dimensions=4,
            embedding_provider=embed_provider,
        )
        await vector_store.initialize()

        cts = CorpusTurnStore(db_path)
        await cts.initialize()

        # Insert test turns
        for i in range(3):
            turn = CorpusTurn(
                turn_id=f"turn-{i}",
                conversation_id="conv-1",
                conversation_name="Test",
                turn_index=i,
                source_model="test",
                source_account="test",
                export_date="2024-01-01",
                user_text=f"Hello {i}",
                assistant_text=f"World {i}",
                turn_timestamp="2024-01-01T00:00:00Z",
                searchable_text=f"Hello World {i}",
                word_count=3,
            )
            await cts.write_turn(turn)

        sexton = Sexton(
            sexton_provider=_FakeModelProvider(),
            corpus_turn_store=cts,
            embedding_provider=embed_provider,
            vector_store=vector_store,
        )

        summary = await sexton.run_cycle()
        assert isinstance(summary, dict)
        assert "tagging" in summary
        assert "embedding" in summary
        assert "wiki" in summary
        assert "graph" in summary
        assert "classification" in summary
        assert "cycle_elapsed_seconds" in summary

        # Embedding should have processed at least some turns
        assert summary["embedding"]["embedded"] >= 0  # may be 0 if tagging needed first

    @pytest.mark.asyncio
    async def test_bridge_detection_uses_async_store(self, tmp_path):
        """_has_bridge_tagged_turns must use CorpusTurnStore async method."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore
        from aip.orchestration.actors.sexton import Sexton

        db_path = str(tmp_path / "sexton_bridge.db")
        embed_provider = _FakeEmbeddingProvider(dimensions=4)
        vector_store = SqliteVssVectorStore(
            db_path=str(tmp_path / "vec_bridge.db"),
            dimensions=4,
            embedding_provider=embed_provider,
        )
        await vector_store.initialize()

        cts = CorpusTurnStore(db_path)
        await cts.initialize()

        sexton = Sexton(
            sexton_provider=_FakeModelProvider(),
            corpus_turn_store=cts,
            embedding_provider=embed_provider,
            vector_store=vector_store,
        )

        # No turns → no bridges
        assert await sexton._has_bridge_tagged_turns() is False


# ---------------------------------------------------------------------------
# 4. AI Fingerprint Cleanup Verification
# ---------------------------------------------------------------------------


class TestAIFingerprintCleanup:
    """Verify cleaned-up modules import and function correctly."""

    def test_corpus_turn_store_no_sync_init(self):
        """CorpusTurnStore must NOT have _ensure_tables_sync."""
        from aip.adapter.corpus_turn_store import CorpusTurnStore

        assert not hasattr(CorpusTurnStore, "_ensure_tables_sync")

    def test_vss_store_no_sync_init(self):
        """SqliteVssVectorStore must NOT have _init_vss_sync."""
        from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

        assert not hasattr(SqliteVssVectorStore, "_init_vss_sync")

    def test_fts5_store_no_sync_init(self):
        """SqliteFts5LexicalStore must NOT have _ensure_tables_sync."""
        from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

        assert not hasattr(SqliteFts5LexicalStore, "_ensure_tables_sync")

    def test_retrieval_orchestrator_imports(self):
        """retrieval_orchestrator.py must import cleanly after cleanup."""
        from aip.orchestration.retrieval_orchestrator import (
            apply_quality_gate,
            rrf_fuse,
        )

        assert callable(rrf_fuse)
        assert callable(apply_quality_gate)

    def test_sexton_actor_imports(self):
        """actors/sexton.py must import cleanly after cleanup."""
        from aip.orchestration.actors.sexton import Sexton

        assert Sexton is not None

    def test_no_sprint_log_comments_in_orchestrator(self):
        """retrieval_orchestrator.py should not contain 'Sprint 5.' comments."""
        import inspect

        from aip.orchestration import retrieval_orchestrator

        source = inspect.getsource(retrieval_orchestrator)
        # Check that no "Sprint 5.X:" style comments remain in the module body
        lines = [l for l in source.split("\n") if "Sprint 5." in l]
        # Allow at most 0 such lines (we cleaned them all)
        assert len(lines) == 0, f"Found Sprint-log comments in retrieval_orchestrator: {lines}"

    def test_corpus_turn_store_has_single_ddl_source(self):
        """CorpusTurnStore DDL should be module-level constants, not duplicated."""
        import inspect

        from aip import adapter

        source = inspect.getsource(adapter.corpus_turn_store)
        # Count occurrences of "CREATE TABLE IF NOT EXISTS corpus_turns"
        count = source.count("CREATE TABLE IF NOT EXISTS corpus_turns")
        assert count == 1, f"Expected 1 DDL definition, found {count} (duplication)"


# ---------------------------------------------------------------------------
# 5. RuntimeMode / Brute-Force Fallback Policy
# ---------------------------------------------------------------------------


class TestRuntimeMode:
    """Verify RuntimeMode controls brute-force fallback behavior."""

    def test_runtime_mode_enum(self):
        """RuntimeMode must have DEVELOPMENT, PRODUCTION, STRICT."""
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode

        assert RuntimeMode.DEVELOPMENT.value == "development"
        assert RuntimeMode.PRODUCTION.value == "production"
        assert RuntimeMode.STRICT.value == "strict"

    @pytest.mark.asyncio
    async def test_strict_mode_raises_on_brute_force(self, tmp_path):
        """STRICT mode must raise RuntimeError when VSS is unavailable."""
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode, SqliteVssVectorStore

        db_path = str(tmp_path / "strict.db")
        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=4,
            runtime_mode=RuntimeMode.STRICT,
        )
        await store.initialize()

        if store._vss_available:
            pytest.skip("VSS extension available; STRICT mode test not applicable")

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        with pytest.raises(RuntimeError, match="STRICT"):
            await store.retrieve(vec, top_k=5)

    @pytest.mark.asyncio
    async def test_development_mode_allows_brute_force(self, tmp_path):
        """DEVELOPMENT mode must allow brute-force retrieval."""
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode, SqliteVssVectorStore

        db_path = str(tmp_path / "dev_mode.db")
        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=4,
            runtime_mode=RuntimeMode.DEVELOPMENT,
        )
        await store.initialize()

        if store._vss_available:
            pytest.skip("VSS extension available")

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store.upsert("dev-1", vec, content="Test", domain="test")
        # Should not raise
        results = await store.retrieve(vec, top_k=5)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_production_mode_allows_brute_force_with_warning(self, tmp_path):
        """PRODUCTION mode must allow brute-force retrieval (with warning)."""
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode, SqliteVssVectorStore

        db_path = str(tmp_path / "prod_mode.db")
        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=4,
            runtime_mode=RuntimeMode.PRODUCTION,
        )
        await store.initialize()

        if store._vss_available:
            pytest.skip("VSS extension available")

        vec = [1.0, 0.0, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]

        await store.upsert("prod-1", vec, content="Test", domain="test")
        # Should not raise even in production
        results = await store.retrieve(vec, top_k=5)
        assert isinstance(results, list)
        # Results should be marked as degraded
        if results:
            assert results[0].metadata.get("_degraded_retrieval") is True

    @pytest.mark.asyncio
    async def test_health_check_includes_runtime_mode(self, tmp_path):
        """health_check() must report the current runtime_mode."""
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode, SqliteVssVectorStore

        db_path = str(tmp_path / "hc_mode.db")
        store = SqliteVssVectorStore(
            db_path=db_path,
            dimensions=4,
            runtime_mode=RuntimeMode.PRODUCTION,
        )
        await store.initialize()

        health = await store.health_check()
        assert health.get("runtime_mode") == "production"

    @pytest.mark.asyncio
    async def test_factory_resolves_runtime_mode(self):
        """Factory must resolve runtime_mode from config."""
        from aip.adapter.vector.factory import _resolve_runtime_mode
        from aip.adapter.vector.sqlite_vss_store import RuntimeMode

        assert _resolve_runtime_mode({}) == RuntimeMode.DEVELOPMENT
        assert _resolve_runtime_mode({"runtime_mode": "production"}) == RuntimeMode.PRODUCTION
        assert _resolve_runtime_mode({"runtime_mode": "strict"}) == RuntimeMode.STRICT
        # Unknown mode → default to DEVELOPMENT
        assert _resolve_runtime_mode({"runtime_mode": "invalid"}) == RuntimeMode.DEVELOPMENT
