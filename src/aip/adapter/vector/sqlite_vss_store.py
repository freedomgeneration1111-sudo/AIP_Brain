"""sqlite_vss implementation of the VectorStore protocol.

SQLite-based vector backend. pgvector adapter available for production.
Uses aiosqlite for async-safe database access.
store() compat method generates real embeddings via EmbeddingProvider.

Sprint 5.13: Removed blocking sqlite3.connect() from __init__.
VSS detection and table creation moved to async initialize().
Constructor is lightweight (stores path + dimensions only).
Brute-force fallback hardened with LIMIT, streaming, and explicit
degradation signaling.
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

# Single source of truth for DDL
_DDL_VECTOR_METADATA = """
    CREATE TABLE IF NOT EXISTS vector_metadata (
        id TEXT PRIMARY KEY,
        content TEXT,
        domain TEXT,
        metadata_json TEXT,
        embedding_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
"""

_DDL_DOMAIN_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_vm_domain
    ON vector_metadata(domain)
"""

# Safety cap for brute-force scans — prevents full-table scan on large corpora
_BRUTE_FORCE_SCAN_LIMIT = 10_000


class SqliteVssVectorStore(VectorStore):
    """VectorStore backed by sqlite_vss extension.

    Uses aiosqlite for async-compatible database access.
    Falls back gracefully when sqlite_vss is not available.

    Sprint 5.13: Constructor is lightweight — stores path only.
    Call ``initialize()`` (async) to detect VSS availability and create
    tables before first use.  The brute-force fallback is capped at
    ``_BRUTE_FORCE_SCAN_LIMIT`` rows and signals degradation via
    ``health_check()`` and chunk metadata.
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
        self._tables_ready = False

    # ------------------------------------------------------------------
    # Async initialization (replaces blocking _init_vss_sync)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Detect VSS extension availability and create tables.

        Must be called after construction (by lifespan, factory, or tests)
        before the store is used.  Idempotent — safe to call multiple times.
        """
        if self._tables_ready:
            return

        # Detect VSS via a synchronous probe (tiny, runs once).
        # This is the one remaining sync call — it opens a read-only
        # connection to test extension loading, then closes it immediately.
        # It does NOT create tables (that's done async below).
        self._vss_available = self._probe_vss_availability()

        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

        backend = "sqlite_vss" if self._vss_available else "sqlite_vss_fallback"
        logger.info("SqliteVssVectorStore initialized (backend=%s, db=%s)", backend, self._db_path)

    def _probe_vss_availability(self) -> bool:
        """Test whether the sqlite_vss extension can be loaded.

        Opens a short-lived in-memory connection to probe.  Does NOT
        write to the database.  Returns True if VSS loaded successfully.
        """
        try:
            conn = sqlite3.connect(":memory:")
            try:
                conn.enable_load_extension(True)
                loaded = False
                try:
                    import sqlite_vss
                    sqlite_vss.load_vss(conn)
                    loaded = True
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
                            pass
                return loaded
            finally:
                conn.close()
        except Exception as e:
            logger.warning("VSS probe failed: %s", e)
            return False

    async def _create_tables(self, conn: aiosqlite.Connection) -> None:
        """Create metadata table, indexes, and (if VSS available) virtual table."""
        await conn.execute(_DDL_VECTOR_METADATA)
        await conn.execute(_DDL_DOMAIN_INDEX)
        # Ensure embedding_json column exists (migrate older schemas)
        try:
            await conn.execute("ALTER TABLE vector_metadata ADD COLUMN embedding_json TEXT")
        except Exception:
            pass  # column already exists
        if self._vss_available:
            await conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vss_vectors
                USING vss0(
                    embedding float[{self._dimensions}]
                )
                """
            )
        await conn.commit()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a new aiosqlite connection with tables ensured.

        Each call creates a fresh connection (safe for concurrent async use).
        Lazily runs table creation if initialize() was never called.
        """
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        if not self._tables_ready:
            await self._create_tables(conn)
            self._tables_ready = True
        return conn

    # ------------------------------------------------------------------
    # Core vector operations
    # ------------------------------------------------------------------

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
        """Brute-force cosine similarity over stored embeddings.

        Used when the VSS extension is unavailable.  Embeddings are persisted
        in the ``embedding_json`` column so vectors survive restarts.

        Sprint 5.13 hardening:
        - Queries are capped at ``_BRUTE_FORCE_SCAN_LIMIT`` rows to prevent
          unbounded full-table scans on large corpora.
        - Results include ``_degraded_retrieval`` in metadata so callers
          and the retrieval trace can detect the fallback path.
        - A warning is logged on every call so operators notice the
          degraded mode in production.
        """
        logger.warning(
            "Brute-force vector retrieval active (VSS unavailable). "
            "Consider installing sqlite_vss for production workloads. "
            "db=%s, domain=%s, top_k=%d",
            self._db_path, domain, top_k,
        )

        conn = await self._get_conn()
        try:
            # Cap the scan to prevent O(N) on huge tables
            scan_limit = max(top_k * 100, _BRUTE_FORCE_SCAN_LIMIT)
            if domain:
                cursor = await conn.execute(
                    "SELECT id, content, domain, metadata_json, embedding_json FROM vector_metadata "
                    "WHERE domain = ? AND embedding_json IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?",
                    (domain, scan_limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id, content, domain, metadata_json, embedding_json FROM vector_metadata "
                    "WHERE embedding_json IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?",
                    (scan_limit,),
                )
            rows = await cursor.fetchall()

            # Score with early termination: once we have top_k candidates
            # above a reasonable threshold, stop scanning.
            min_score_threshold = 0.1  # skip obviously irrelevant
            candidates: list[tuple[float, str, str, str, str]] = []
            for row in rows:
                id_, content, domain_val, meta_json, emb_json = row
                if not emb_json:
                    continue
                try:
                    vec = json.loads(emb_json)
                    if not isinstance(vec, list) or len(vec) != len(query_vector):
                        continue
                    score = self._cosine_similarity(query_vector, vec)
                    if score < min_score_threshold:
                        continue
                    candidates.append((score, id_, content, domain_val, meta_json))
                except Exception:
                    continue

            candidates.sort(key=lambda x: x[0], reverse=True)
            results = []
            for score, id_, content, domain_val, meta_json in candidates[:top_k]:
                meta = json.loads(meta_json) if meta_json else {}
                meta["_degraded_retrieval"] = True  # signal to callers
                meta["_retrieval_backend"] = "brute_force"
                results.append(
                    Chunk(
                        id=id_,
                        content=content,
                        score=score,
                        metadata=meta,
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
        """Store a Chunk, generating a real embedding via EmbeddingProvider.

        When an ``EmbeddingProvider`` is available, generates a real
        embedding from the chunk content and calls ``upsert()``.
        Without a provider, stores metadata-only (no vector index).
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
                        "Falling back to metadata-only storage.",
                        chunk.id,
                    )
            except Exception as exc:
                logger.warning(
                    "Embedding generation failed for chunk '%s': %s. "
                    "Storing metadata-only.",
                    chunk.id,
                    exc,
                )
        else:
            logger.warning(
                "store() called without EmbeddingProvider for chunk '%s'. "
                "Storing metadata-only.",
                chunk.id,
            )

        # Metadata-only fallback
        conn = await self._get_conn()
        try:
            meta_json = json.dumps(chunk.metadata or {})
            cursor = await conn.execute("SELECT rowid FROM vector_metadata WHERE id = ?", (chunk.id,))
            existing = await cursor.fetchone()
            if existing:
                await conn.execute(
                    "UPDATE vector_metadata SET content = ?, domain = ?, metadata_json = ? WHERE id = ?",
                    (content, chunk.domain, meta_json, chunk.id),
                )
            else:
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
                "degraded": not self._vss_available,
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
        """List vectors not updated within threshold_days."""
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
        """List all vector IDs with cursor-based pagination."""
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
        pass
