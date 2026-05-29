"""Tests for remaining Protocol adapters (Lexical, Canonical, Entity, AutonomyGate).

Exact per spec prose verification list.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from aip.adapter.autonomy.autonomy_gate import AutonomyGateImpl
from aip.adapter.canonical.sqlite_canonical_store import SqliteCanonicalStore
from aip.adapter.entity.sqlite_entity_store import SqliteEntityStore
from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
from aip.foundation.protocols import AutonomyGate, CanonicalStore, EntityStore, LexicalStore
from aip.foundation.schemas import Chunk


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test_state.db"
        yield str(db)


def test_lexical_store_implements_protocol(temp_db):
    store = SqliteFts5LexicalStore(temp_db)
    assert isinstance(store, LexicalStore)
    asyncio.run(store.initialize())
    asyncio.run(store.close())


@pytest.mark.asyncio
async def test_fts5_search_returns_ranked_chunks(temp_db):
    store = SqliteFts5LexicalStore(temp_db)
    await store.initialize()

    await store.index_document("doc-1", "The quick brown fox jumps over the lazy dog", "test_domain", {"k": "v"})
    await store.index_document("doc-2", "unrelated content about something else", "test_domain", {})

    results = await store.search("quick fox", domain="test_domain", limit=5)
    assert len(results) >= 1
    assert isinstance(results[0], Chunk)
    assert results[0].id == "doc-1"
    assert results[0].score != 0.0  # FTS5 rank
    assert results[0].domain == "test_domain"

    await store.delete_document("doc-1")
    results_after = await store.search("quick fox", domain="test_domain")
    assert all(r.id != "doc-1" for r in results_after)

    await store.close()


def test_canonical_store_implements_protocol_and_definer_enforcement(temp_db):
    store = SqliteCanonicalStore(temp_db)
    assert isinstance(store, CanonicalStore)
    asyncio.run(store.initialize())

    # Must reject non-definer
    with pytest.raises(PermissionError):
        asyncio.run(store.write_canonical("art-1", {"foo": "bar"}, approved_by="mcp"))

    # Valid DEFINER write
    asyncio.run(store.write_canonical("art-1", {"foo": "bar", "domain": "test"}, approved_by="definer"))
    canonical = asyncio.run(store.read_canonical("art-1"))
    assert canonical is not None
    assert canonical["approved_by"] == "definer"

    lst = asyncio.run(store.list_canonical(domain="test"))
    assert len(lst) == 1

    asyncio.run(store.close())


def test_entity_store_implements_protocol(temp_db):
    store = SqliteEntityStore(temp_db)
    assert isinstance(store, EntityStore)
    asyncio.run(store.initialize())

    asyncio.run(store.update_entity("e1", {"entity_type": "concept", "name": "Test", "metadata": {"x": 1}}))
    got = asyncio.run(store.get_entity("e1"))
    assert got["name"] == "Test"

    lst = asyncio.run(store.list_entities(entity_type="concept"))
    assert len(lst) == 1

    asyncio.run(store.close())


def test_autonomy_gate_impl_implements_protocol_and_behaviors(temp_db):
    cfg = {
        "db_path": temp_db,
        "default_level": "read",
        "escalation_requires_definer": True,
        "model_gen_assumption": "test assumption",
    }
    gate = AutonomyGateImpl(config=cfg)
    assert isinstance(gate, AutonomyGate)
    asyncio.run(gate.initialize())

    # read-level auto-granted
    esc = asyncio.run(gate.check("read_action", "res-1", "read", "mcp"))
    assert esc.granted is True

    # admin blocked without definer
    esc2 = asyncio.run(gate.escalate("approve_artifact", "art-99", "admin", "mcp"))
    assert esc2.granted is False
    assert "DEFINER" in esc2.reason

    # admin granted when requested_by=definer
    esc3 = asyncio.run(gate.escalate("approve_artifact", "art-99", "admin", "definer"))
    assert esc3.granted is True

    # audit log
    logs = asyncio.run(gate.audit_log(limit=10))
    assert len(logs) >= 2
    assert any(entry.action_type == "approve_artifact" for entry in logs)

    asyncio.run(gate.close())


def test_adapter_layer_does_not_import_orchestration():
    """Layering invariant (same as all prior gates)."""
    # The adapter modules themselves must not contain orchestration imports.
    # This is verified at runtime by the shared test_layering.py (which we run in the gate).
    # Here we just sanity-check the source of one new file.
    src = Path(__file__).parent.parent / "src/aip/adapter/autonomy/autonomy_gate.py"
    text = src.read_text()
    assert "from aip.orchestration" not in text
    assert "import aip.orchestration" not in text
