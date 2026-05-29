"""pgvector VectorStore adapter — production-grade vector backend.

PostgreSQL 16 + pgvector is the required production path.
All HNSW and pool parameters toggleable via config.
Adapter may import foundation but not orchestration.
"""

from __future__ import annotations

import json
import time

import asyncpg

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import Chunk, PgvectorConfig


class PgvectorStore(VectorStore):
    """PostgreSQL + pgvector implementation of VectorStore Protocol.

    Uses asyncpg for async connectivity with connection pooling.
    HNSW index for approximate nearest neighbor search.
    Cosine distance as the default similarity metric.
    """

    def __init__(self, config: PgvectorConfig) -> None:
        self._config = config
        self._pool: asyncpg.Pool | None = None
        self._dimensions: int | None = None  # detected on first upsert

    async def initialize(self) -> None:
        """Create connection pool and ensure schema exists."""
        self._pool = await asyncpg.create_pool(
            self._config.connection_string,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
            command_timeout=self._config.pool_timeout_seconds,
        )
        async with self._pool.acquire() as conn:
            # Enable pgvector extension
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # Table will be created when dimensions are known (first upsert)
            # or via _ensure_table(conn, dimensions) call

    async def _ensure_table(self, conn: asyncpg.Connection, dimensions: int) -> None:
        """Create vectors table and indexes if not exists."""
        if self._dimensions is None:
            self._dimensions = dimensions
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    vector vector({dimensions}),
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    domain TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_vectors_hnsw
                ON vectors USING hnsw (vector vector_cosine_ops)
                WITH (m = {self._config.hnsw_m},
                      ef_construction = {self._config.hnsw_ef_construction})
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_vectors_domain
                ON vectors (domain)
            """)

    async def close(self) -> None:
        """Gracefully close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict,
        domain: str | None = None,
    ) -> None:
        """Insert or update a vector. Same semantics as SqliteVssVectorStore.

        Note: content is merged into metadata for storage (vector store focuses on
        vectors + metadata; full content lives in ArtifactStore per design).
        """
        if self._pool is None:
            raise RuntimeError("PgvectorStore not initialized. Call initialize() first.")

        # Merge content into metadata for fidelity with protocol + sqlite_vss behavior
        full_meta = dict(metadata or {})
        if content and "content" not in full_meta:
            full_meta["content"] = content

        async with self._pool.acquire() as conn:
            await self._ensure_table(conn, len(embedding))
            await conn.execute(
                """
                INSERT INTO vectors (id, vector, metadata, domain, created_at, updated_at)
                VALUES ($1, $2::vector, $3::jsonb, $4, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    vector = $2::vector,
                    metadata = $3::jsonb,
                    domain = $4,
                    updated_at = NOW()
                """,
                id,
                str(embedding),
                json.dumps(full_meta),
                domain or "",
            )

    async def batch_upsert(
        self,
        items: list[tuple[str, list[float], dict, str]],
    ) -> None:
        """Batch insert/update vectors for migration performance.

        Wraps all inserts in a single transaction.
        Batch size should be 500–1000 for optimal throughput.
        Note: this internal batch path uses (id, vector, metadata, domain) tuples.
        """
        if self._pool is None:
            raise RuntimeError("PgvectorStore not initialized. Call initialize() first.")

        if not items:
            return

        async with self._pool.acquire() as conn:
            await self._ensure_table(conn, len(items[0][1]))
            async with conn.transaction():
                for id_, vector, metadata, domain in items:
                    await conn.execute(
                        """
                        INSERT INTO vectors (id, vector, metadata, domain, created_at, updated_at)
                        VALUES ($1, $2::vector, $3::jsonb, $4, NOW(), NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            vector = $2::vector,
                            metadata = $3::jsonb,
                            domain = $4,
                            updated_at = NOW()
                        """,
                        id_,
                        str(vector),
                        json.dumps(metadata or {}),
                        domain or "",
                    )

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        """Retrieve vectors by cosine similarity, filtered by domain.

        Cosine similarity for semantic search.
        Score = 1 - cosine_distance (0–1 scale, matching RerankWeights).
        """
        if self._pool is None:
            raise RuntimeError("PgvectorStore not initialized. Call initialize() first.")

        async with self._pool.acquire() as conn:
            # Set search parameter for this query
            await conn.execute(f"SET LOCAL hnsw.ef_search = {self._config.hnsw_ef_search}")
            if domain:
                rows = await conn.fetch(
                    """
                    SELECT id, vector, metadata, domain,
                           1 - (vector <=> $1::vector) AS score
                    FROM vectors
                    WHERE domain = $2
                    ORDER BY vector <=> $1::vector
                    LIMIT $3
                    """,
                    str(query_vector),
                    domain,
                    top_k,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, vector, metadata, domain,
                           1 - (vector <=> $1::vector) AS score
                    FROM vectors
                    ORDER BY vector <=> $1::vector
                    LIMIT $2
                    """,
                    str(query_vector),
                    top_k,
                )

        return [
            Chunk(
                id=row["id"],
                content="",  # content stored in metadata or artifact store (per design)
                score=float(row["score"]),
                metadata=json.loads(row["metadata"])
                if isinstance(row["metadata"], str)
                else dict(row["metadata"] or {}),
                domain=row["domain"],
            )
            for row in rows
        ]

    async def delete(self, id: str) -> None:
        """Delete a vector by ID.

        Per Appendix D: 'Supersession ≠ deletion.' This method exists for
        administrative cleanup, not for normal workflow paths.
        """
        if self._pool is None:
            raise RuntimeError("PgvectorStore not initialized. Call initialize() first.")

        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM vectors WHERE id = $1", id)

    async def health_check(self) -> dict:
        """Check PostgreSQL connectivity and return status.

        Returns: connected, pool_size, latency_ms, backend_name, database.
        """
        if self._pool is None:
            return {
                "connected": False,
                "pool_size": 0,
                "latency_ms": -1,
                "backend_name": "pgvector",
                "database": "",
            }

        start = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            latency = int((time.monotonic() - start) * 1000)
            db_name = ""
            if self._config.connection_string:
                try:
                    db_name = self._config.connection_string.rstrip("/").split("/")[-1]
                except Exception:
                    db_name = ""
            return {
                "connected": True,
                "pool_size": self._pool.get_size(),
                "latency_ms": latency,
                "backend_name": "pgvector",
                "database": db_name,
            }
        except Exception as e:
            return {
                "connected": False,
                "pool_size": 0,
                "latency_ms": -1,
                "backend_name": "pgvector",
                "error": str(e),
            }

    async def count(self, domain: str | None = None) -> int:
        """Count vectors, optionally filtered by domain."""
        if self._pool is None:
            return 0

        async with self._pool.acquire() as conn:
            if domain:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM vectors WHERE domain = $1",
                    domain,
                )
            else:
                row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM vectors")
        return row["cnt"] if row else 0

    async def list_stale_vectors(
        self,
        threshold_days: int = 30,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List vectors not updated within threshold_days.

        Used by Beast corpus maintenance and Vigil to identify stale vectors.
        """
        if self._pool is None:
            return []

        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_days)

        async with self._pool.acquire() as conn:
            if domain:
                rows = await conn.fetch(
                    """
                    SELECT id, domain, updated_at, metadata
                    FROM vectors
                    WHERE updated_at < $1 AND domain = $2
                    ORDER BY updated_at ASC
                    LIMIT $3
                    """,
                    cutoff,
                    domain,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, domain, updated_at, metadata
                    FROM vectors
                    WHERE updated_at < $1
                    ORDER BY updated_at ASC
                    LIMIT $2
                    """,
                    cutoff,
                    limit,
                )

        results = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            elif not isinstance(meta, dict):
                meta = dict(meta or {})
            results.append(
                {
                    "id": row["id"],
                    "domain": row["domain"],
                    "updated_at": row["updated_at"].isoformat()
                    if hasattr(row["updated_at"], "isoformat")
                    else str(row["updated_at"]),
                    "metadata": meta,
                },
            )
        return results
