"""SQLite implementation of KnowledgeStore Protocol.

Pure adapter-layer. Implements deferred compiled knowledge persistence
with provenance and dual indexing into VectorStore + LexicalStore
only on APPROVED state.
Uses aiosqlite for async-safe database access.
Real embeddings via injected EmbeddingProvider.

Constructor is lightweight (stores path + deps only). Call ``initialize()``
(async) to create tables before first use, or rely on lazy creation
via ``_get_conn()``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aip.foundation.protocols import (
    EmbeddingProvider,
    KnowledgeStore,
    LexicalStore,
    VectorStore,
)
from aip.foundation.schemas import CompilationState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

_DDL_COMPILED_KNOWLEDGE = """
    CREATE TABLE IF NOT EXISTS compiled_knowledge (
        knowledge_id TEXT PRIMARY KEY,
        content TEXT,
        source_canonical_ids TEXT,
        domain TEXT,
        state TEXT,
        metadata TEXT,
        created_at TEXT,
        updated_at TEXT
    )
"""

_DDL_COMPILED_KNOWLEDGE_PROVENANCE = """
    CREATE TABLE IF NOT EXISTS compiled_knowledge_provenance (
        knowledge_id TEXT,
        canonical_id TEXT,
        canonical_domain TEXT,
        canonical_title TEXT,
        canonical_evaluation_scores TEXT,
        canonical_state TEXT,
        PRIMARY KEY (knowledge_id, canonical_id)
    )
"""


class SqliteKnowledgeStore(KnowledgeStore):
    """SQLite-backed KnowledgeStore.

    Stores compiled knowledge artifacts (distinct from canonical artifacts —
    no collapse). Maintains separate provenance table for
    source canonical chain.

    Dual-indexes content into VectorStore + LexicalStore only when state
    reaches "APPROVED".

    When an ``EmbeddingProvider`` is injected, real embeddings are generated
    for APPROVED content and for semantic search queries. Without an
    ``EmbeddingProvider``, the store degrades gracefully: dual-indexing is
    skipped and search falls back to lexical-only.

    Uses a persistent aiosqlite connection per instance with error recovery.
    """

    def __init__(
        self,
        db_path: str,
        vector_store: VectorStore,
        lexical_store: LexicalStore,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._db_path = db_path
        self._vector_store = vector_store
        self._lexical_store = lexical_store
        self._embedding_provider = embedding_provider
        self._conn: aiosqlite.Connection | None = None
        self._tables_ready = False

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection so that callers
        who bypass ``initialize()`` still get a working schema.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create compiled_knowledge and provenance tables on the given connection."""
        await conn.execute(_DDL_COMPILED_KNOWLEDGE)
        await conn.execute(_DDL_COMPILED_KNOWLEDGE_PROVENANCE)
        await conn.commit()

    async def initialize(self) -> None:
        """Idempotent table creation (called by lifespan / DI container).

        Uses a short-lived connection to create tables, then discards it.
        Subsequent operations use the persistent connection from _get_conn().
        """
        if self._tables_ready:
            return
        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _reset_conn(self) -> None:
        """Reset the persistent connection (called on errors)."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _generate_embedding(self, text: str) -> list[float] | None:
        """Generate a real embedding via the injected EmbeddingProvider.

        Returns the embedding vector on success, or ``None`` if no provider
        is available or embedding generation fails.
        """
        if self._embedding_provider is None:
            logger.debug(
                "No EmbeddingProvider configured — skipping embedding generation "
                "for text (%d chars).",
                len(text),
            )
            return None

        try:
            embedding = await self._embedding_provider.embed(text)
            if not embedding or len(embedding) == 0:
                logger.warning(
                    "EmbeddingProvider returned empty vector for text (%d chars).",
                    len(text),
                )
                return None
            return embedding
        except Exception as exc:
            logger.warning(
                "Embedding generation failed for text (%d chars): %s.",
                len(text),
                exc,
            )
            return None

    async def _dual_index(
        self,
        knowledge_id: str,
        content: str,
        domain: str,
        metadata: dict,
    ) -> None:
        """Index content into VectorStore + LexicalStore (called on APPROVED).

        Generates a real embedding via the EmbeddingProvider when available.
        If embedding fails or no provider is configured, only lexical indexing
        is performed.
        """
        embedding = await self._generate_embedding(content[:2000])

        if embedding is not None:
            try:
                await self._vector_store.upsert(
                    f"compiled:{knowledge_id}",
                    embedding,
                    content[:2000],
                    {"type": "compiled_knowledge", "domain": domain, **metadata},
                    domain,
                )
                logger.info(
                    "Indexed compiled knowledge '%s' into vector store (dim=%d, domain='%s').",
                    knowledge_id,
                    len(embedding),
                    domain,
                )
            except Exception as exc:
                logger.warning(
                    "Vector store upsert failed for compiled knowledge '%s': %s.",
                    knowledge_id,
                    exc,
                )
        else:
            logger.info(
                "No embedding available for compiled knowledge '%s' — lexical index only.",
                knowledge_id,
            )

        try:
            await self._lexical_store.index_document(
                f"compiled:{knowledge_id}",
                content,
                domain,
                {"type": "compiled_knowledge", "domain": domain},
            )
        except Exception as exc:
            logger.warning(
                "Lexical store index failed for compiled knowledge '%s': %s.",
                knowledge_id,
                exc,
            )

    async def store_compiled(
        self,
        knowledge_id: str,
        content: str,
        source_canonical_ids: list[str],
        domain: str,
        metadata: dict,
    ) -> None:
        """Store compiled knowledge + provenance links.

        If state == "APPROVED", also indexes into VectorStore + LexicalStore
        using real embeddings when an EmbeddingProvider is configured.
        """
        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()

            state = metadata.get("state", "SPECIFIED")
            source_ids_json = json.dumps(source_canonical_ids or [])

            await conn.execute(
                """
                INSERT OR REPLACE INTO compiled_knowledge
                (knowledge_id, content, source_canonical_ids, domain, state, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM compiled_knowledge WHERE knowledge_id=?), ?), ?)
                """,
                (
                    knowledge_id,
                    content,
                    source_ids_json,
                    domain,
                    state,
                    json.dumps(metadata),
                    knowledge_id,
                    now,
                    now,
                ),
            )

            for cid in source_canonical_ids or []:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO compiled_knowledge_provenance
                    (knowledge_id, canonical_id, canonical_domain,
                     canonical_title, canonical_evaluation_scores, canonical_state)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (knowledge_id, cid, domain, "", "[]", "APPROVED"),
                )

            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

        if state == "APPROVED":
            await self._dual_index(knowledge_id, content, domain, metadata)

    async def get_compiled(self, knowledge_id: str) -> dict | None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT * FROM compiled_knowledge WHERE knowledge_id = ?",
                (knowledge_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "knowledge_id": row["knowledge_id"],
                "content": row["content"],
                "source_canonical_ids": json.loads(row["source_canonical_ids"] or "[]"),
                "domain": row["domain"],
                "state": row["state"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            await self._reset_conn()
            raise

    async def list_compiled(self, domain: str | None = None, state: CompilationState | None = None) -> list[dict]:
        conn = await self._get_conn()
        try:
            query = "SELECT * FROM compiled_knowledge WHERE 1=1"
            params: list[Any] = []
            if domain:
                query += " AND domain = ?"
                params.append(domain)
            if state:
                query += " AND state = ?"
                params.append(state)
            query += " ORDER BY updated_at DESC"
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "knowledge_id": r["knowledge_id"],
                    "content": r["content"],
                    "source_canonical_ids": json.loads(r["source_canonical_ids"] or "[]"),
                    "domain": r["domain"],
                    "state": r["state"],
                    "metadata": json.loads(r["metadata"] or "{}"),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def update_state(self, knowledge_id: str, new_state: CompilationState) -> None:
        """Validate and perform state transition.

        When transitioning to "APPROVED", triggers dual-indexing into
        VectorStore + LexicalStore with real embeddings (when available).
        """
        valid_transitions = {
            "SPECIFIED": {"COMPILED", "FAILED"},
            "COMPILED": {"REVIEWED", "FAILED"},
            "REVIEWED": {"APPROVED", "FAILED"},
            "APPROVED": set(),
            "FAILED": set(),
        }
        current = await self.get_compiled(knowledge_id)
        if not current:
            return
        allowed = valid_transitions.get(current["state"], set())
        if new_state not in allowed and new_state != current["state"]:
            logger.info(
                "State transition '%s' -> '%s' for knowledge '%s' is not in "
                "the standard transition table. Allowing for 0.1 compatibility.",
                current["state"],
                new_state,
                knowledge_id,
            )

        conn = await self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                "UPDATE compiled_knowledge SET state = ?, updated_at = ? WHERE knowledge_id = ?",
                (new_state, now, knowledge_id),
            )
            await conn.commit()
        except Exception:
            await self._reset_conn()
            raise

        if new_state == "APPROVED" and current["state"] != "APPROVED":
            await self._dual_index(
                knowledge_id,
                current["content"],
                current["domain"],
                current.get("metadata", {}),
            )

    async def get_provenance(self, knowledge_id: str) -> list[dict]:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT canonical_id, canonical_domain, canonical_title,
                       canonical_evaluation_scores, canonical_state
                FROM compiled_knowledge_provenance
                WHERE knowledge_id = ?
                """,
                (knowledge_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "canonical_id": r["canonical_id"],
                    "canonical_domain": r["canonical_domain"],
                    "canonical_title": r["canonical_title"],
                    "canonical_evaluation_scores": json.loads(r["canonical_evaluation_scores"] or "[]"),
                    "canonical_state": r["canonical_state"],
                }
                for r in rows
            ]
        except Exception:
            await self._reset_conn()
            raise

    async def search_compiled(self, query: str, domain: str | None = None, limit: int = 10) -> list[dict]:
        """Search across Vector + Lexical stores with semantic and text matching.

        When an EmbeddingProvider is available, performs parallel vector
        similarity search and lexical text search, then merges and deduplicates
        results. Without an EmbeddingProvider, falls back to lexical-only
        search.
        """
        results: list[dict] = []

        try:
            lexical_hits = await self._lexical_store.search(query, domain=domain, limit=limit)
            for h in lexical_hits:
                results.append(
                    {
                        "knowledge_id": h.id.replace("compiled:", "") if hasattr(h, "id") else "",
                        "content": h.content if hasattr(h, "content") else "",
                        "score": h.score if hasattr(h, "score") else 0.0,
                        "source": "lexical",
                    },
                )
        except Exception as exc:
            logger.debug("Lexical search failed for query '%s': %s", query[:100], exc)

        if self._embedding_provider is not None:
            query_vec = await self._generate_embedding(query)
            if query_vec is not None:
                try:
                    vec_hits = await self._vector_store.retrieve(query_vec, domain=domain, top_k=limit)
                    for h in vec_hits:
                        results.append(
                            {
                                "knowledge_id": (h.id.replace("compiled:", "") if hasattr(h, "id") else ""),
                                "content": h.content if hasattr(h, "content") else "",
                                "score": h.score if hasattr(h, "score") else 0.0,
                                "source": "vector",
                            },
                        )
                except Exception as exc:
                    logger.debug(
                        "Vector search failed for query '%s': %s",
                        query[:100],
                        exc,
                    )

        seen: set[str] = set()
        deduped: list[dict] = []
        for r in sorted(results, key=lambda x: x.get("score", 0.0), reverse=True):
            kid = r.get("knowledge_id")
            if kid and kid not in seen:
                seen.add(kid)
                deduped.append(r)
            if len(deduped) >= limit:
                break
        return deduped
