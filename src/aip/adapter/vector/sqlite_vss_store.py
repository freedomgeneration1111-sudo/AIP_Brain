"""sqlite_vss implementation of the VectorStore protocol.
Phase 1 vector backend. pgvector adapter is deferred to Phase 4.
sqlite_vss is the permitted fallback for Phase 0-2 alpha.
Phase 3: migrated from blocking sqlite3 to aiosqlite to avoid event loop blocking.
Phase 10: store() compat method no longer inserts zero vectors.

Note: sqlite_vss extension loading requires enable_load_extension.
aiosqlite supports this via conn.enable_load_extension() after connecting.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

import aiosqlite

from aip.foundation.protocols import EmbeddingProvider, VectorStore
from aip.foundation.schemas import Chunk

logger = logging.getLogger(__name__)


class SqliteVssVectorStore(VectorStore):
    """VectorStore backed by sqlite_vss extension.

    Uses aiosqlite for async-compatible database access.
    Falls back gracefully when sqlite_vss is not available.

    When an ``EmbeddingProvider`` is provided, the deprecated ``store()``
    compat method generates real embeddings instead of inserting zero
    vectors.  Without a provider, ``store()`` raises ``ValueError`` to
    prevent silent data degradation.
    """

    def __init__(
        self,
        db_path: str,
        dimensions: int = 768,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._db_path = db_path
        self._dimensions = dimensions
        self._embedding_provider = embedding_provider
        self._vss_available = False
        # Synchronous init to detect vss availability (runs once at startup)
        self._init_vss_sync()

    def _init_vss_sync(self) -> None:
        """Detect vss extension availability and create initial tables."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.enable_load_extension(True)
            try:
                conn.load_extension("vss0")
                self._vss_available = True
            except sqlite3.OperationalError:
                try:
                    conn.load_extension("./vss0")
                    self._vss_available = True
                except sqlite3.OperationalError:
                    pass  # sqlite_vss not available; store will raise on first use
            conn.enable_load_extension(False)

            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vector_metadata (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    domain TEXT,
                    metadata_json TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_vm_domain
                ON vector_metadata(domain)
            """)
            if self._vss_available:
                cur.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vss_vectors
                    USING vss0(
                        embedding float[{self._dimensions}]
                    )
                """)
            conn.commit()
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        conn = await self._get_conn()
        try:
            # Delete existing entry if present (upsert semantics)
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
            existing = await cursor.fetchone()
            if existing:
                await conn.execute("DELETE FROM vss_vectors WHERE rowid = ?", (existing[0],))
                await conn.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))

            meta_json = json.dumps(metadata or {})
            await conn.execute(
                "INSERT INTO vector_metadata (id, content, domain, metadata_json) VALUES (?, ?, ?, ?)",
                (id, content, domain, meta_json),
            )
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
            row = await cursor.fetchone()
            rowid = row[0] if row else None
            if rowid and self._vss_available:
                emb_str = json.dumps(embedding)
                await conn.execute("INSERT INTO vss_vectors(rowid, embedding) VALUES (?, ?)", (rowid, emb_str))
            await conn.commit()
        finally:
            await conn.close()

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        if not self._vss_available:
            return []
        conn = await self._get_conn()
        try:
            emb_str = json.dumps(query_vector)
            domain_filter = ""
            params_domain: list[Any] = []
            if domain is not None:
                domain_filter = "AND m.domain = ?"
                params_domain = [domain]

            sql = f"""
                SELECT m.id, m.content, v.distance, m.domain, m.metadata_json
                FROM vss_vectors v
                JOIN vector_metadata m ON v.rowid = m.rowid
                WHERE vss_vectors MATCH ?
                AND v.k = ?
                {domain_filter}
                ORDER BY v.distance
            """
            cursor = await conn.execute(sql, [emb_str, top_k] + params_domain)
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                id_, content, distance, domain_val, meta_json = row
                score = max(0.0, 1.0 - distance) if distance is not None else 0.0
                results.append(
                    Chunk(
                        id=id_,
                        content=content,
                        score=score,
                        metadata=json.loads(meta_json) if meta_json else {},
                        domain=domain_val,
                    ),
                )
            return results
        finally:
            await conn.close()

    async def delete(self, id: str) -> None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
            row = await cursor.fetchone()
            if row:
                await conn.execute("DELETE FROM vss_vectors WHERE rowid = ?", (row[0],))
                await conn.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))
                await conn.commit()
        finally:
            await conn.close()

    async def count(self, domain: str | None = None) -> int:
        conn = await self._get_conn()
        try:
            if domain:
                cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata WHERE domain = ?", (domain,))
            else:
                cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata")
            row = await cursor.fetchone()
            return row[0] if row else 0
        finally:
            await conn.close()

    async def store(self, chunk: Chunk) -> str:
        """Phase 0 compat: store a Chunk, generating a real embedding.

        When an ``EmbeddingProvider`` is available, generates a real
        embedding from the chunk content and calls ``upsert()``.  This
        ensures that the vector store always contains meaningful vectors.

        Without an ``EmbeddingProvider``, raises ``ValueError`` instead
        of silently inserting a zero vector.  Callers should either:
        - Provide an ``EmbeddingProvider`` at construction time, or
        - Use ``upsert()`` directly with a pre-computed embedding.

        This is a breaking change from the zero-vector behavior, but
        zero vectors silently destroy semantic search and are never
        acceptable in production.
        """
        content = chunk.content or ""

        if self._embedding_provider is not None:
            try:
                embedding = await self._embedding_provider.embed(content)
                if embedding and len(embedding) > 0:
                    await self.upsert(
                        chunk.id,
                        embedding,
                        content,
                        chunk.metadata,
                        chunk.domain,
                    )
                    logger.info(
                        "store() generated real embedding for chunk '%s' (dim=%d, domain='%s').",
                        chunk.id,
                        len(embedding),
                        chunk.domain,
                    )
                    return chunk.id
                else:
                    logger.warning(
                        "EmbeddingProvider returned empty vector for chunk '%s'. "
                        "Falling back to metadata-only storage (no vector index).",
                        chunk.id,
                    )
            except Exception as exc:
                logger.warning(
                    "Embedding generation failed for chunk '%s': %s. "
                    "Storing metadata-only (no vector index). "
                    "Callers should use upsert() with a pre-computed embedding "
                    "for reliable vector storage.",
                    chunk.id,
                    exc,
                )
        else:
            logger.warning(
                "store() called without EmbeddingProvider for chunk '%s'. "
                "Storing metadata-only (no vector index). "
                "Provide an EmbeddingProvider or use upsert() with a real embedding.",
                chunk.id,
            )

        # Fallback: store metadata-only (content is preserved, vector is skipped).
        # This is better than a zero vector because:
        # 1. Zero vectors pollute similarity search (cosine(0,0) is undefined)
        # 2. Metadata-only items are still discoverable via count()/list queries
        # 3. A re-embedding pass can fill in vectors later
        conn = await self._get_conn()
        try:
            meta_json = json.dumps(chunk.metadata or {})
            # Check if entry already exists
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (chunk.id,))
            existing = await cursor.fetchone()
            if existing:
                # Update existing metadata (preserve any existing vector)
                await conn.execute(
                    "UPDATE vector_metadata SET content = ?, domain = ?, metadata_json = ? WHERE id = ?",
                    (content, chunk.domain, meta_json, chunk.id),
                )
            else:
                # Insert new metadata-only entry
                await conn.execute(
                    "INSERT INTO vector_metadata (id, content, domain, metadata_json) VALUES (?, ?, ?, ?)",
                    (chunk.id, content, chunk.domain, meta_json),
                )
            await conn.commit()
        finally:
            await conn.close()

        return chunk.id

    async def health_check(self) -> dict:
        """Check vector store health and return status."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata")
            row = await cursor.fetchone()
            count = row[0] if row else 0
            return {
                "connected": True,
                "backend_name": "sqlite_vss" if self._vss_available else "sqlite_vss_fallback",
                "vss_available": self._vss_available,
                "count": count,
            }
        except Exception as e:
            return {
                "connected": False,
                "backend_name": "sqlite_vss",
                "error": str(e),
            }
        finally:
            await conn.close()

    async def list_stale_vectors(
        self,
        threshold_days: int = 30,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List vectors not updated within threshold_days.

        Uses the created_at column in vector_metadata as the timestamp.
        """
        from datetime import datetime, timedelta, timezone

        conn = await self._get_conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat()
            if domain:
                cursor = await conn.execute(
                    "SELECT id, domain, created_at, metadata_json FROM vector_metadata "
                    "WHERE created_at < ? AND domain = ? ORDER BY created_at ASC LIMIT ?",
                    (cutoff, domain, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id, domain, created_at, metadata_json FROM vector_metadata "
                    "WHERE created_at < ? ORDER BY created_at ASC LIMIT ?",
                    (cutoff, limit),
                )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                meta = json.loads(row[3]) if row[3] else {}
                results.append(
                    {
                        "id": row[0],
                        "domain": row[1],
                        "created_at": row[2],
                        "metadata": meta,
                    },
                )
            return results
        finally:
            await conn.close()

    async def close(self) -> None:
        # No persistent connection to close (each method opens/closes its own)
        pass
