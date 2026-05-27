"""Vector store factory — creates the appropriate VectorStore implementation.

Per §2.2: configuration flag switches between "pgvector" and "sqlite_vss".
Per §7.2: adapter may import foundation but not orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import PgvectorConfig

logger = logging.getLogger(__name__)


async def create_vector_store(config: Any) -> VectorStore:
    """Create the appropriate VectorStore based on config.

    Reads [vector_backend] provider from config and returns:
    - PgvectorStore for "pgvector"
    - SqliteVssVectorStore for "sqlite_vss"
    - Falls back to sqlite_vss if pgvector is unavailable

    The returned object implements the VectorStore Protocol.
    Orchestration code never knows which backend is active.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    vector_cfg = cfg.get("vector_backend", {})
    provider = vector_cfg.get("provider", "sqlite_vss")

    if provider == "pgvector":
        try:
            from aip.adapter.vector.pgvector_store import PgvectorStore

            pgconfig = PgvectorConfig(
                connection_string=vector_cfg.get("connection_string", ""),
                pool_min_size=vector_cfg.get("pgvector", {}).get("pool_min_size", 2),
                pool_max_size=vector_cfg.get("pgvector", {}).get("pool_max_size", 10),
                pool_timeout_seconds=vector_cfg.get("pgvector", {}).get("pool_timeout_seconds", 30.0),
                statement_timeout_ms=vector_cfg.get("pgvector", {}).get("statement_timeout_ms", 5000),
                hnsw_m=vector_cfg.get("pgvector", {}).get("hnsw_m", 16),
                hnsw_ef_construction=vector_cfg.get("pgvector", {}).get("hnsw_ef_construction", 64),
                hnsw_ef_search=vector_cfg.get("pgvector", {}).get("hnsw_ef_search", 40),
            )
            store = PgvectorStore(pgconfig)
            await store.initialize()
            logger.info("VectorStore: pgvector backend initialized")
            return store
        except Exception as e:
            logger.warning(
                f"pgvector unavailable ({e}), falling back to sqlite_vss"
            )
            # Graceful degradation
            return await _create_sqlite_vss(vector_cfg)

    return await _create_sqlite_vss(vector_cfg)


async def _create_sqlite_vss(vector_cfg: dict) -> VectorStore:
    """Create SqliteVssVectorStore as default or fallback."""
    from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

    db_path = vector_cfg.get("db_path", "db/vectors.db")
    store = SqliteVssVectorStore(db_path)
    logger.info("VectorStore: sqlite_vss backend initialized")
    return store
