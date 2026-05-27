"""Tests for SqliteKnowledgeStore (CHUNK-10.0b)."""

import asyncio
import tempfile
import os

import pytest

from aip.foundation.schemas import CompilationState
from aip.foundation.protocols import VectorStore, LexicalStore, KnowledgeStore
from aip.adapter.knowledge.sqlite_knowledge_store import SqliteKnowledgeStore


class FakeVectorStore(VectorStore):
    async def upsert(self, id, embedding, content, metadata, domain=None):
        pass
    async def retrieve(self, query_vector, domain=None, top_k=10):
        return []
    async def delete(self, id):
        pass


class FakeLexicalStore(LexicalStore):
    async def search(self, query, domain=None, limit=10):
        return []
    async def index_document(self, id, content, metadata, domain=None):
        pass


@pytest.fixture
def knowledge_store():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "knowledge.db")
        store = SqliteKnowledgeStore(db, FakeVectorStore(), FakeLexicalStore())
        yield store
        asyncio.get_event_loop().run_until_complete(store.close())


def test_knowledge_store_implements_protocol(knowledge_store):
    assert isinstance(knowledge_store, KnowledgeStore)


def test_store_and_get_compiled(knowledge_store):
    asyncio.get_event_loop().run_until_complete(
        knowledge_store.store_compiled(
            "k1", "Compiled summary of foo.", ["c1", "c2"], "test", {"state": "COMPILED"}
        )
    )
    got = asyncio.get_event_loop().run_until_complete(knowledge_store.get_compiled("k1"))
    assert got is not None
    assert got["content"].startswith("Compiled")
    assert got["source_canonical_ids"] == ["c1", "c2"]
    assert got["state"] == "COMPILED"


def test_provenance_and_state_transition(knowledge_store):
    asyncio.get_event_loop().run_until_complete(
        knowledge_store.store_compiled("k2", "content", ["c99"], "d", {"state": "REVIEWED"})
    )
    prov = asyncio.get_event_loop().run_until_complete(knowledge_store.get_provenance("k2"))
    assert len(prov) >= 1

    asyncio.get_event_loop().run_until_complete(
        knowledge_store.update_state("k2", "APPROVED")
    )
    got = asyncio.get_event_loop().run_until_complete(knowledge_store.get_compiled("k2"))
    assert got["state"] == "APPROVED"


def test_list_and_search(knowledge_store):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        knowledge_store.store_compiled("k3", "searchable compiled text about AIP", ["c1"], "test", {"state": "APPROVED"})
    )
    listed = loop.run_until_complete(knowledge_store.list_compiled(state="APPROVED"))
    assert any(r["knowledge_id"] == "k3" for r in listed)

    results = loop.run_until_complete(knowledge_store.search_compiled("AIP", limit=5))
    # May be empty if lexical fake returns nothing, but call succeeds
    assert isinstance(results, list)
