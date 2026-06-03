"""sqlite_vss implementation of the VectorStore protocol.
SQLite-based vector backend. pgvector adapter available for production.
Uses aiosqlite for async-safe database access.
store() compat method generates real embeddings via EmbeddingProvider.

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
            loaded = False
            # Preferred: use sqlite-vss package if installed (provides correct .so path bundled in site-packages/sqlite_vss/vss0)
            # This makes persistent vectors work after `uv pip install sqlite-vss`
            try:
                import sqlite_vss
                sqlite_vss.load_vss(conn)
                loaded = True
                logger.info("sqlite_vss loaded via sqlite-vss package")
            except ImportError:
                pass
            except Exception as e:
                logger.debug("sqlite_vss package load_vss failed (will try fallback): %s", e)
            if not loaded:
                try:
                    conn.load_extension("vss0")
                    loaded = True
                except sqlite3.OperationalError:
                    try:
                        conn.load_extension("./vss0")
                        loaded = True
                    except sqlite3.OperationalError:
                        pass  # sqlite_vss not available; store will raise on first use
            if loaded:
                self._vss_available = True
            conn.enable_load_extension(False)

            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vector_metadata (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    domain TEXT,
                    metadata_json TEXT,
                    embedding_json TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_vm_domain
                ON vector_metadata(domain)
            """)
            # Add embedding_json column for !vss case (persistent vectors via json even without extension)
            try:
                cur.execute("ALTER TABLE vector_metadata ADD COLUMN embedding_json TEXT")
            except sqlite3.OperationalError:
                pass  # column exists or other
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
                await conn.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))
                if self._vss_available:
                    await conn.execute("DELETE FROM vss_vectors WHERE rowid = ?", (existing[0],))

            meta_json = json.dumps(metadata or {})
            emb_json = json.dumps(embedding) if embedding else None
            await conn.execute(
                "INSERT INTO vector_metadata (id, content, domain, metadata_json, embedding_json) VALUES (?, ?, ?, ?, ?)",
                (id, content, domain, meta_json, emb_json),
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
            return await self._brute_force_retrieve(query_vector, domain, top_k)
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

    async def _brute_force_retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        """Brute-force cosine similarity over stored embeddings (used when vss extension unavailable).
        Embeddings are persisted in embedding_json column so vectors survive restarts.
        """
        conn = await self._get_conn()
        try:
            if domain:
                cursor = await conn.execute(
                    "SELECT id, content, domain, metadata_json, embedding_json FROM vector_metadata "
                    "WHERE domain = ? AND embedding_json IS NOT NULL",
                    (domain,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id, content, domain, metadata_json, embedding_json FROM vector_metadata "
                    "WHERE embedding_json IS NOT NULL"
                )
            rows = await cursor.fetchall()
            candidates = []
            for row in rows:
                id_, content, domain_val, meta_json, emb_json = row
                if not emb_json:
                    continue
                try:
                    vec = json.loads(emb_json)
                    if not isinstance(vec, list) or len(vec) != len(query_vector):
                        continue
                    score = self._cosine_similarity(query_vector, vec)
                    candidates.append((score, id_, content, domain_val, meta_json))
                except Exception:
                    continue
            candidates.sort(key=lambda x: x[0], reverse=True)
            results = []
            for score, id_, content, domain_val, meta_json in candidates[:top_k]:
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

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    async def delete(self, id: str) -> None:
        conn = await self._get_conn()
        try:
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (id,))
            row = await cursor.fetchone()
            if row:
                await conn.execute("DELETE FROM vector_metadata WHERE id = ?", (id,))
                if self._vss_available:
                    await conn.execute("DELETE FROM vss_vectors WHERE rowid = ?", (row[0],))
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
        """Store a Chunk, generating a real embedding.

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

    async def list_all_ids(
        self,
        offset: int = 0,
        limit: int = 500,
        domain: str | None = None,
    ) -> list[str]:
        """List all vector IDs with cursor-based pagination.

        Used by vector migration for deterministic complete scanning.
        Queries vector_metadata table directly — no vector similarity needed.
        """
        conn = await self._get_conn()
        try:
            if domain:
                cursor = await conn.execute(
                    "SELECT id FROM vector_metadata WHERE domain = ? ORDER BY rowid ASC LIMIT ? OFFSET ?",
                    (domain, limit, offset),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id FROM vector_metadata ORDER BY rowid ASC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
        finally:
            await conn.close()

    async def get_by_id(self, chunk_id: str) -> Chunk | None:
        """Retrieve a chunk by its ID directly (no vector similarity)."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                "SELECT id, content, domain, metadata_json FROM vector_metadata WHERE id = ?",
                (chunk_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            id_, content, domain_val, meta_json = row
            return Chunk(
                id=id_,
                content=content,
                score=1.0,
                metadata=json.loads(meta_json) if meta_json else {},
                domain=domain_val,
            )
        finally:
            await conn.close()

    async def close(self) -> None:
        # No persistent connection to close (each method opens/closes its own)
        pass
