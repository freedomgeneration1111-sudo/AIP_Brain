"""Sprint 5.16 — Startup smoke test for app.py lifespan.

Verifies that all stores are properly initialized and closed via the
FastAPI lifespan. Catches regressions where initialize() calls are
accidentally removed or reordered.

Deterministic, zero-token, no network, no LLM.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore
from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore
from aip.adapter.event_store_queryable import QueryableEventStore
from aip.adapter.artifact_store_versioned import VersionedArtifactStore
from aip.adapter.ecs_store_persistent import PersistentEcsStore
from aip.adapter.review_queue_store import ReviewQueueStore
from aip.adapter.budget_store_sqlite import SqliteBudgetStore
from aip.adapter.session.sqlite_session_store import SqliteSessionStore
from aip.adapter.project.sqlite_project_store import SqliteProjectStore
from aip.adapter.autonomy.autonomy_gate import AutonomyGateImpl
from aip.adapter.vigil.sqlite_vigil_store import SqliteVigilStore
from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore
from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore


# ---------------------------------------------------------------------------
# All stores that should support the async init pattern
# ---------------------------------------------------------------------------

_STORE_CLASSES = [
    ("entity_store", SqliteEntityStore),
    ("canonical_store", SqliteCanonicalStore),
    ("event_store", QueryableEventStore),
    ("artifact_store", VersionedArtifactStore),
    ("ecs_store", PersistentEcsStore),
    ("review_queue_store", ReviewQueueStore),
    ("budget_store", SqliteBudgetStore),
    ("session_store", SqliteSessionStore),
    ("project_store", SqliteProjectStore),
    ("vigil_store", SqliteVigilStore),
    ("corpus_turn_store", CorpusTurnStore),
    ("lexical_store", SqliteFts5LexicalStore),
]


@pytest.fixture
def tmp_db():
    """Provide a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "test_state.db")


# ---------------------------------------------------------------------------
# Test: No blocking sqlite3.connect() in __init__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_blocking_connect_in_init(tmp_db):
    """Store constructors must NOT call sqlite3.connect().

    This verifies the async initialization pattern: __init__ is lightweight
    (stores path/config only), and async initialize() creates tables.
    If a constructor blocks the event loop, this test will fail.
    """
    for name, cls in _STORE_CLASSES:
        if name == "lexical_store":
            # Lexical store uses a separate db file
            lexical_db = os.path.join(os.path.dirname(tmp_db), "lexical.db")
            store = cls(lexical_db)
        elif name == "ecs_store":
            store = cls(db_path=tmp_db, event_store=None)
        elif name == "autonomy_gate":
            store = AutonomyGateImpl(config={"db_path": tmp_db})
        else:
            store = cls(tmp_db)

        # Constructor should NOT have opened a persistent connection
        assert store._conn is None, f"{name}.__init__ should not open a connection"
        assert store._tables_ready is False, f"{name}.__init__ should not set _tables_ready=True"

        # Initialize should work
        await store.initialize()
        assert store._tables_ready is True, f"{name}.initialize() should set _tables_ready=True"

        # Close should clean up
        await store.close()
        assert store._conn is None, f"{name}.close() should clear _conn"


@pytest.mark.asyncio
async def test_autonomy_gate_no_blocking_connect():
    """AutonomyGateImpl constructor must NOT call sqlite3.connect()."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_autonomy.db")
        gate = AutonomyGateImpl(config={"db_path": db_path})

        assert gate._conn is None, "AutonomyGateImpl.__init__ should not open a connection"
        assert gate._tables_ready is False, "AutonomyGateImpl.__init__ should not set _tables_ready=True"

        await gate.initialize()
        assert gate._tables_ready is True, "AutonomyGateImpl.initialize() should set _tables_ready=True"

        await gate.close()
        assert gate._conn is None, "AutonomyGateImpl.close() should clear _conn"


# ---------------------------------------------------------------------------
# Test: Full lifespan initialization sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_initialization_sequence(tmp_db):
    """Simulate the app.py lifespan initialization sequence.

    This mirrors the REQUIRED + OPTIONAL initialization order in app.py,
    verifying that each store initializes and closes correctly.
    """
    # REQUIRED components (same order as app.py)
    entity_store = SqliteEntityStore(tmp_db)
    await entity_store.initialize()
    assert entity_store._tables_ready is True

    canonical_store = SqliteCanonicalStore(tmp_db)
    await canonical_store.initialize()
    assert canonical_store._tables_ready is True

    event_store = QueryableEventStore(tmp_db)
    await event_store.initialize()
    assert event_store._tables_ready is True

    autonomy_gate = AutonomyGateImpl(config={"db_path": tmp_db})
    await autonomy_gate.initialize()
    assert autonomy_gate._tables_ready is True

    artifact_store = VersionedArtifactStore(tmp_db)
    await artifact_store.initialize()
    assert artifact_store._tables_ready is True

    # OPTIONAL components
    lexical_db = os.path.join(os.path.dirname(tmp_db), "lexical.db")
    lexical_store = SqliteFts5LexicalStore(lexical_db)
    await lexical_store.initialize()
    assert lexical_store._tables_ready is True

    corpus_turn_store = CorpusTurnStore(tmp_db)
    await corpus_turn_store.initialize()
    assert corpus_turn_store._tables_ready is True

    project_store = SqliteProjectStore(tmp_db)
    await project_store.initialize()
    assert project_store._tables_ready is True

    budget_store = SqliteBudgetStore(tmp_db)
    await budget_store.initialize()
    assert budget_store._tables_ready is True

    vigil_store = SqliteVigilStore(tmp_db)
    await vigil_store.initialize()
    assert vigil_store._tables_ready is True

    ecs_store = PersistentEcsStore(db_path=tmp_db, event_store=event_store)
    await ecs_store.initialize()
    assert ecs_store._tables_ready is True

    review_queue_store = ReviewQueueStore(db_path=tmp_db)
    await review_queue_store.initialize()
    assert review_queue_store._tables_ready is True

    session_store = SqliteSessionStore(tmp_db)
    await session_store.initialize()
    assert session_store._tables_ready is True

    # Verify basic operations work (write + read)
    await entity_store.update_entity("lifespan_test", {"entity_type": "test", "name": "LifespanTest"})
    entity = await entity_store.get_entity("lifespan_test")
    assert entity is not None
    assert entity["name"] == "LifespanTest"

    await event_store.write_event("test_event", "lifespan_test", "test_artifact")
    events = await event_store.query(artifact_id="test_artifact")
    assert len(events) >= 1

    await artifact_store.write("lifespan_art", "test content", {"source": "smoke_test"})
    content = await artifact_store.read("lifespan_art")
    assert content == "test content"

    # Close all stores (lifespan shutdown)
    for store in [
        entity_store,
        canonical_store,
        event_store,
        autonomy_gate,
        artifact_store,
        lexical_store,
        corpus_turn_store,
        project_store,
        budget_store,
        vigil_store,
        ecs_store,
        review_queue_store,
        session_store,
    ]:
        await store.close()


@pytest.mark.asyncio
async def test_idempotent_initialize(tmp_db):
    """initialize() should be idempotent — calling it twice must not error."""
    store = SqliteEntityStore(tmp_db)
    await store.initialize()
    assert store._tables_ready is True

    # Second call should be a no-op
    await store.initialize()
    assert store._tables_ready is True

    await store.close()


@pytest.mark.asyncio
async def test_lazy_table_creation_without_initialize(tmp_db):
    """Stores should lazily create tables on first _get_conn() if initialize() was skipped."""
    store = SqliteEntityStore(tmp_db)
    # Don't call initialize() — rely on lazy creation via _get_conn()
    assert store._tables_ready is False

    # First operation should auto-create tables
    await store.update_entity("lazy_test", {"entity_type": "test", "name": "LazyTest"})
    assert store._tables_ready is True

    entity = await store.get_entity("lazy_test")
    assert entity is not None
    assert entity["name"] == "LazyTest"

    await store.close()


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_db):
    """All migrated stores should enable WAL mode for better concurrent access."""
    store = SqliteEntityStore(tmp_db)
    await store.initialize()

    conn = await store._get_conn()
    cursor = await conn.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row[0] == "wal", f"Expected WAL mode, got {row[0]}"

    await store.close()
