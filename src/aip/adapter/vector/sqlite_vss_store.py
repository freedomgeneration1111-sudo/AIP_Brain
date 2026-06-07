"""sqlite_vss implementation of the VectorStore protocol.

SQLite-based vector backend with graceful degradation when the VSS
extension is unavailable.  Embeddings are persisted in the
``embedding_json`` column so vectors survive restarts even in
brute-force mode.

Constructor is lightweight (stores path + dimensions only).  Call
``initialize()`` (async) to detect VSS availability and create tables
before first use, or rely on lazy creation via ``_get_conn()``.
"""

from __future__ import annotations

import enum
import json
import logging
import sqlite3
from typing import Any

import aiosqlite

from aip.foundation.protocols import EmbeddingProvider, VectorStore
from aip.foundation.schemas import Chunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single source of truth for DDL
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Brute-force safety cap
# ---------------------------------------------------------------------------

_BRUTE_FORCE_SCAN_LIMIT = 10_000


# ---------------------------------------------------------------------------
# Runtime mode for brute-force fallback policy
# ---------------------------------------------------------------------------

class RuntimeMode(enum.Enum):
    """Controls behavior when VSS is unavailable.

    - DEVELOPMENT: Gracefully fall back to brute-force (default).
    - PRODUCTION: Log a hard warning and stamp results as degraded,
      but still return results (fail-soft with explicit signalling).
    - STRICT: Raise RuntimeError on any brute-force attempt.
    """

    DEVELOPMENT = "development"
    PRODUCTION = "production"
    STRICT = "strict"


class SqliteVssVectorStore(VectorStore):
    """VectorStore backed by sqlite_vss extension with brute-force fallback.

    Uses a persistent aiosqlite connection per instance.  Falls back to
    brute-force cosine similarity when the VSS extension is unavailable.
    The brute-force fallback behavior is governed by ``RuntimeMode``.
    """

    def __init__(
        self,
        db_path: str,
        dimensions: int = 768,
        embedding_provider: EmbeddingProvider | None = None,
        runtime_mode: RuntimeMode = RuntimeMode.DEVELOPMENT,
    ) -> None:
        self._db_path = db_path
        self._dimensions = dimensions
        self._embedding_provider = embedding_provider
        self._runtime_mode = runtime_mode
        self._vss_available = False
        self._tables_ready = False
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Async initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Detect VSS extension availability and create tables.

        Idempotent — safe to call multiple times.
        """
        if self._tables_ready:
            return

        # Detect VSS via a synchronous probe (tiny, runs once).
        self._vss_available = self._probe_vss_availability()

        conn = await aiosqlite.connect(self._db_path)
        try:
            await self._create_tables(conn)
            self._tables_ready = True
        finally:
            await conn.close()

        backend = "sqlite_vss" if self._vss_available else "brute_force"
        logger.info(
            "SqliteVssVectorStore initialized (backend=%s, db=%s, mode=%s)",
            backend, self._db_path, self._runtime_mode.value,
        )

        if not self._vss_available and self._runtime_mode == RuntimeMode.PRODUCTION:
            logger.warning(
                "PRODUCTION mode with VSS unavailable — all vector retrieval "
                "will use brute-force scan. Install sqlite_vss for production."
            )

    def _probe_vss_availability(self) -> bool:
        """Test whether the sqlite_vss extension can be loaded.

        Opens a short-lived in-memory connection to probe.
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
                    logger.debug("sqlite_vss package load_vss failed: %s", e)
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
        try:
            await conn.execute("ALTER TABLE vector_metadata ADD COLUMN embedding_json TEXT")
        except Exception:
            pass
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
        """Return a persistent connection, creating one if needed.

        Lazily ensures tables on first connection.
        """
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            if not self._tables_ready:
                await self._create_tables(self._conn)
                self._tables_ready = True
        return self._conn

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
        except Exception:
            # If the persistent conn is stale, reset it
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def _brute_force_retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        """Brute-force cosine similarity over stored embeddings.

        Used when the VSS extension is unavailable.  Behavior depends on
        ``_runtime_mode``:
        - DEVELOPMENT: Proceed with scan (default).
        - PRODUCTION: Proceed but stamp degradation explicitly.
        - STRICT: Raise RuntimeError.
        """
        if self._runtime_mode == RuntimeMode.STRICT:
            raise RuntimeError(
                "VSS extension unavailable and RuntimeMode is STRICT — "
                "brute-force retrieval is disabled. Install sqlite_vss."
            )

        if self._runtime_mode == RuntimeMode.PRODUCTION:
            logger.warning(
                "PRODUCTION: Brute-force vector retrieval active (VSS unavailable). "
                "db=%s, domain=%s, top_k=%d — results are degraded.",
                self._db_path, domain, top_k,
            )
        else:
            logger.warning(
                "Brute-force vector retrieval active (VSS unavailable). "
                "db=%s, domain=%s, top_k=%d",
                self._db_path, domain, top_k,
            )

        conn = await self._get_conn()
        try:
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

            min_score_threshold = 0.1
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
                meta["_degraded_retrieval"] = True
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
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def count(self, domain: str | None = None) -> int:
        conn = await self._get_conn()
        try:
            if domain:
                cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata WHERE domain = ?", (domain,))
            else:
                cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata")
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            await self._reset_conn()
            raise

    async def store(self, chunk: Chunk) -> str:
        """Store a Chunk, generating a real embedding via EmbeddingProvider."""
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
                        chunk.id, len(embedding), chunk.domain,
                    )
                    return chunk.id
                else:
                    logger.warning(
                        "EmbeddingProvider returned empty vector for chunk '%s'. "
                        "Falling back to metadata-only storage.", chunk.id,
                    )
            except Exception as exc:
                logger.warning(
                    "Embedding generation failed for chunk '%s': %s. "
                    "Storing metadata-only.", chunk.id, exc,
                )
        else:
            logger.warning(
                "store() called without EmbeddingProvider for chunk '%s'. "
                "Storing metadata-only.", chunk.id,
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
        except Exception:
            await self._reset_conn()
            raise

        return chunk.id

    async def health_check(self) -> dict:
        """Check vector store health and return status."""
        try:
            conn = await self._get_conn()
            cursor = await conn.execute("SELECT COUNT(*) FROM vector_metadata")
            row = await cursor.fetchone()
            count = row[0] if row else 0
            return {
                "connected": True,
                "backend_name": "sqlite_vss" if self._vss_available else "sqlite_vss_fallback",
                "vss_available": self._vss_available,
                "degraded": not self._vss_available,
                "runtime_mode": self._runtime_mode.value,
                "count": count,
            }
        except Exception as e:
            return {
                "connected": False,
                "backend_name": "sqlite_vss",
                "error": str(e),
            }

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
                    {"id": row[0], "domain": row[1], "created_at": row[2], "metadata": meta},
                )
            return results
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

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
        except Exception:
            await self._reset_conn()
            raise

    async def _reset_conn(self) -> None:
        """Reset the persistent connection (called on errors)."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
